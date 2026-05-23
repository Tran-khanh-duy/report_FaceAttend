
"""
edge_client.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Edge AI Service — Chạy trên Mini PC
Nhiệm vụ:
  1. Kéo embedding vectors từ Server API về RAM
  2. Đọc Camera → Nhận diện khuôn mặt (InsightFace)
  3. Chống giả mạo (Anti-Spoofing)
  4. Gửi kết quả điểm danh về Server qua REST API
  5. Nếu mất mạng → lưu vào SQLite offline queue → tự đồng bộ khi có lại

Khởi động:
    python edge_client.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import time
import base64
import sqlite3
import threading
import requests
import cv2
import os
import numpy as np
from datetime import datetime
from loguru import logger
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent / "Server"))

# Tắt log nhiễu của OpenCV (index out of range)
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"

from config import edge_config, ai_config, anti_spoof_config
# pyrefly: ignore [missing-import]
from database.models import EmbeddingCache
# pyrefly: ignore [missing-import]
from utils.camera_utils import detect_available_cameras
# pyrefly: ignore [missing-import]
from utils.camera_utils import discover_network_cameras, generate_rtsp_links
from local_cache.embedding_sync import embedding_sync


class EdgeClient:
    """
    Client kết nối tới Server API.
    Quản lý: Pull embeddings, Push attendance, Offline queue.
    """

    def __init__(self):
        self.server_url = edge_config.server_url.rstrip("/")
        self.api_key = edge_config.api_key
        # camera_id fallback cho global embedding cache
        self.camera_id = "GLOBAL"

        # Embedding cache cục bộ
        # Global cache cũ (nếu không có camera_id)
        self._cache: EmbeddingCache = EmbeddingCache()
        
        # [NEW] Multi-camera caches
        self._multi_caches = {}  # { camera_id: EmbeddingCache }
        self._multi_cache_times = {} # { camera_id: float (timestamp) }
        
        self._cache_lock = threading.Lock()

        # Trạng thái kết nối
        self._server_online = False
        self._last_embed_pull = 0.0
        
        # Setup Session với Retry logic — CHỈ dùng cho các lệnh QUAN TRỌNG
        # (pull_embeddings, send_attendance) — KHÔNG dùng cho poll tần số cao
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        self._session = requests.Session()
        retries = Retry(
            total=3, 
            backoff_factor=0.5, 
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        # [FIX OFFLINE] Session KHÔNG retry — dùng cho poll tần số cao
        # (get_system_command_raw, check_server) để tránh tích lũy connection objects
        self._fast_session = requests.Session()
        _no_retry = Retry(total=0, raise_on_status=False)
        _fast_adapter = HTTPAdapter(max_retries=_no_retry, pool_connections=2, pool_maxsize=4)
        self._fast_session.mount("http://", _fast_adapter)
        self._fast_session.mount("https://", _fast_adapter)

        # [FIX OFFLINE] Circuit-breaker: đếm số lần liên tiếp server offline
        # Khi >= 3 lần offline → bỏ qua poll, trả về STOP ngay lập tức
        self._consecutive_offline_count: int = 0
        self._circuit_open: bool = False  # True = ngừng poll hoàn toàn
        self._current_offline_cmd: str = "STOP"  # Lệnh cuối cùng nhận được (dùng khi offline)

        self._active_status_cache = {} # Cache trạng thái từ HeadlessProcessor

        # Offline queue (SQLite)
        self._db_path = Path(__file__).parent / "database" / "edge_offline.db"
        self._init_offline_db()

        # Cooldown tracking
        self._cooldown_map: dict[int, float] = {}
        self._cooldown_lock = threading.Lock()

        # [FIX] Theo dõi phiên bản embedding — phát hiện có học viên mới ngay lập tức
        self._known_embedding_version: int = -1
        self._last_version_check: float = 0.0

        # Background sync thread
        self._stop_event = threading.Event()
        self._sync_thread = threading.Thread(
            target=self._sync_loop, name="Edge-Sync", daemon=True
        )
        self._sync_thread.start()

        # [NEW] Background IP Camera Discovery thread
        self._discovered_rtsp = []
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop, name="Edge-Discovery", daemon=True
        )
        self._discovery_thread.start()

        # [NEW] Trạng thái camera sẽ được báo cáo định kỳ trong _sync_loop
        logger.info(f"EdgeClient khởi tạo | Server: {self.server_url} | Device: {edge_config.device_name}")

        # [FIX IMPORT] Pull camera list trong thread nền — KHÔNG blocking __init__
        # Nếu gọi trực tiếp: _session (Retry=3, timeout=10s) = 30s blocking khi Server tắt
        # → Python báo ImportError vì module import bị timeout giữa chừng
        threading.Thread(
            target=self.pull_camera_list,
            name="Edge-CamListPull",
            daemon=True
        ).start()

    # ─── Offline DB ───────────────────────────

    def _init_offline_db(self):
        """Khởi tạo SQLite queue cho offline records."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS offline_attendance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        camera_id TEXT,
                        timestamp TEXT,
                        embedding BLOB,
                        liveness_score REAL,
                        liveness_checked INTEGER DEFAULT 0,
                        synced INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            logger.info(f"Offline DB sẵn sàng: {self._db_path}")
        except Exception as e:
            logger.error(f"Lỗi init offline DB: {e}")

    # ─── Server Communication ─────────────────

    def _headers(self) -> dict:
        return {"X-DEVICE-TOKEN": self.api_key, "Content-Type": "application/json"}

    def check_server(self) -> bool:
        """Kiểm tra server còn online không (dùng fast_session — không retry)."""
        try:
            resp = self._fast_session.get(
                f"{self.server_url}/api/health",
                timeout=2,
            )
            online = (resp.status_code == 200)
            if online:
                # Server trở lại online → reset circuit breaker
                if not self._server_online:
                    logger.info("🌐 Server Online - Đang báo cáo trạng thái...")
                    threading.Thread(target=self.report_status, daemon=True).start()
                self._consecutive_offline_count = 0
                self._circuit_open = False
            else:
                self._consecutive_offline_count += 1
            self._server_online = online
            return online
        except Exception:
            self._consecutive_offline_count += 1
            # Mở circuit breaker sau 5 lần thất bại liên tiếp
            if self._consecutive_offline_count >= 5 and not self._circuit_open:
                logger.warning(
                    f"⚡ Circuit Breaker MỞ sau {self._consecutive_offline_count} lần thất bại — "
                    f"tạm dừng poll đến khi server online trở lại."
                )
                self._circuit_open = True
            self._server_online = False
            return False

    @property
    def is_server_online(self) -> bool:
        return self._server_online

    # ─── Dynamic Camera Detection ──────────────

    def _get_local_ip(self) -> str:
        """Lấy địa chỉ IP mạng nội bộ của máy."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Không cần kết nối thực sự, chỉ để lấy IP interface
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    def report_status(self):
        """Kiểm tra các cổng camera và báo cáo về Server."""
        try:
            status_map = {}

            # [FIX] Gửi cả Tên và Source của Camera
            for cam in edge_config.camera_list:
                is_active = self._active_status_cache.get(cam["id"], False)
                if not is_active:
                    is_active = self._active_status_cache.get(cam["source"], False)
                    
                status_map[cam["id"]] = {
                    "name": cam["name"],
                    "source": cam["source"],
                    "is_active": is_active
                }

            # Bổ sung các camera đang hoạt động khác nếu có
            for cam_id, is_active in self._active_status_cache.items():
                if is_active and cam_id not in status_map:
                    status_map[cam_id] = {
                        "name": f"Auto {cam_id}",
                        "source": cam_id,
                        "is_active": True
                    }

            payload = {
                "device_name": edge_config.device_name,
                "camera_status": status_map,
                "ip_address": self._get_local_ip(), # Vẫn giữ IP Box để tham khảo
                "timestamp": datetime.now().isoformat()
            }
            
            # [FIX TASK 3] Lặp retry logic kiểm tra xem Server đã lưu chưa
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    resp = self._session.post(
                        f"{self.server_url}/api/system/edge_status",
                        json=payload,
                        headers=self._headers(),
                        timeout=5
                    )
                    
                    if resp.status_code == 200:
                        resp_data = resp.json()
                        stored_status = resp_data.get("stored", {})
                        
                        # Xác minh server đã ghi nhận đúng trạng thái của tất cả camera
                        all_match = True
                        for cid, info in status_map.items():
                            expected_active = info["is_active"]
                            actual_active = False
                            if cid in stored_status and isinstance(stored_status[cid], dict):
                                actual_active = stored_status[cid].get("is_active", False)
                            
                            if expected_active != actual_active:
                                all_match = False
                                break
                                
                        if all_match:
                            logger.debug(f"📡 Báo cáo trạng thái thành công: {list(status_map.keys())}")
                            break  # Thành công, thoát vòng lặp
                        else:
                            logger.warning(f"⚠️ Server chưa lưu đúng trạng thái, thử lại... ({attempt+1}/{max_retries})")
                            import time as _t
                            _t.sleep(1)
                    else:
                        logger.warning(f"⚠️ Lỗi HTTP {resp.status_code} khi báo cáo trạng thái, thử lại... ({attempt+1}/{max_retries})")
                        import time as _t
                        _t.sleep(1)
                except requests.exceptions.RequestException as e:
                    logger.warning(f"⚠️ Lỗi mạng khi báo cáo trạng thái: {e}, thử lại... ({attempt+1}/{max_retries})")
                    import time as _t
                    _t.sleep(1)
                    
        except Exception as e:
            logger.error(f"Lỗi tổng hợp khi report_status: {e}")

    def update_active_status(self, camera_id: str, is_active: bool):
        """Cập nhật bộ đệm trạng thái từ Processor."""
        self._active_status_cache[camera_id] = is_active

    # ─── Pull Camera List từ DB ─────────────────

    def pull_camera_list(self) -> bool:
        """
        Kéo danh sách camera từ Server (dữ liệu từ bảng Cameras trong DB).
        RTSP URL được build trên Server từ credentials lưu trong DB.
        Cập nhật edge_config.camera_list để HeadlessProcessor dùng.
        """
        try:
            params = {}
            if getattr(edge_config, 'device_group', ''):
                params['device_group'] = edge_config.device_group

            resp = self._fast_session.get(
                f"{self.server_url}/api/system/cameras/edge-list",
                params=params,
                headers=self._headers(),
                timeout=3,  # [FIX] 3s — fail fast, không có retry
            )
            if resp.status_code != 200:
                logger.error(f"pull_camera_list: Server trả lỗi {resp.status_code}")
                return False

            data = resp.json()
            cameras = data.get("cameras", [])
            if not cameras:
                logger.warning("⚠️ pull_camera_list: Server không có camera nào trong DB!")
                return False

            # Cập nhật danh sách runtime
            edge_config.camera_list = cameras
            names = [f"{c.get('name','?')} -> {c.get('source','?')}" for c in cameras]
            logger.success(
                f"✅ pull_camera_list: Đã tải {len(cameras)} camera từ DB:"
            )
            for n in names:
                logger.info(f"   {n}")
            return True

        except requests.ConnectionError:
            logger.warning("❌ pull_camera_list: Không thể kết nối Server (sẽ thử lại sau)")
            self._server_online = False
            return False
        except Exception as e:
            logger.error(f"pull_camera_list lỗi: {e}")
            return False

    # ─── Pull Embeddings ──────────────────────

    def pull_embeddings(self, target_camera_id: str = None, db_camera_id: int = None, force: bool = False) -> bool:
        """
        Kéo embedding vectors từ Server về RAM.
        - target_camera_id: display ID ("CAM_01") - dùng để lưu vào cache
        - db_camera_id: numeric DB ID (1) - dùng để query filter theo floor trên Server
        """
        cam_id = target_camera_id or self.camera_id
        # Ưu tiên dùng numeric ID để filter floor; fallback sang display ID
        server_query_id = str(db_camera_id) if db_camera_id else cam_id
        
        # 1. Thử load từ đĩa (Offline-first) nếu trong RAM chưa có
        with self._cache_lock:
            has_ram_cache = cam_id in self._multi_caches if target_camera_id else self._cache is not None
            
        if not has_ram_cache:
            offline_cache, offline_ver = embedding_sync.load_cache_by_camera(cam_id)
            if offline_cache:
                with self._cache_lock:
                    if target_camera_id:
                        self._multi_caches[target_camera_id] = offline_cache
                        self._multi_cache_times[target_camera_id] = time.time()
                    else:
                        self._cache = offline_cache
                        self._last_embed_pull = time.time()
                self._known_embedding_version = offline_ver
                logger.info(f"⚡ Đã load Offline Cache cho {cam_id} (Version: {offline_ver})")

        # [FIX OFFLINE] Circuit breaker: nếu Server đang tắt → bỏ qua hoàn toàn
        # Offline cache đã được load ở bước trên, hệ thống vẫn hoạt động bình thường
        if self._circuit_open and not force:
            logger.debug(f"[Offline] Bỏ qua pull_embeddings cho {cam_id} — Server đang offline.")
            return False

        try:
            logger.info(f"📥 Đồng bộ embeddings Server — camera_id={server_query_id} (display={cam_id})...")
            
            # Lấy version trước
            ver_resp = self._fast_session.get(f"{self.server_url}/api/embeddings/version", headers=self._headers(), timeout=3)
            server_ver = 0
            if ver_resp.status_code == 200:
                server_ver = ver_resp.json().get("embedding_version", 0)
                
            # Nếu version trên Server = bản Local -> Không cần tải lại
            if server_ver > 0 and server_ver == self._known_embedding_version and has_ram_cache:
                logger.info(f"✅ Embeddings cho {cam_id} đã ở phiên bản mới nhất ({server_ver}). Bỏ qua tải.")
                return True

            resp = self._session.get(
                f"{self.server_url}/api/embeddings",
                params={"camera_id": server_query_id},
                headers=self._headers(),
                timeout=30,
            )

            if resp.status_code != 200:
                logger.error(f"Server trả về lỗi {resp.status_code}: {resp.text}")
                return False

            data = resp.json()
            if data.get("count", 0) == 0:
                logger.warning(f"Server chưa có embedding nào cho {cam_id}!")
                with self._cache_lock:
                    if target_camera_id:
                        self._multi_caches[target_camera_id] = EmbeddingCache()
                        self._multi_cache_times[target_camera_id] = time.time()
                    else:
                        self._cache = EmbeddingCache()
                return True

            # Parse embeddings
            new_cache = EmbeddingCache()
            vecs = []
            for s in data["students"]:
                emb_bytes = base64.b64decode(s["embedding_b64"])
                vec = np.frombuffer(emb_bytes, dtype=np.float32).copy()
                if vec.shape[0] != 512:
                    logger.warning(f"Skip student {s['student_id']}: shape={vec.shape}")
                    continue

                new_cache.student_ids.append(s["student_id"])
                new_cache.student_codes.append(s["student_code"])
                new_cache.full_names.append(s["full_name"])
                new_cache.class_ids.append(s.get("class_id", 0))
                new_cache.class_names.append(s.get("class_name", ""))
                new_cache.class_codes.append(s.get("class_code", ""))
                vecs.append(vec)

            if vecs:
                mat = np.vstack(vecs).astype(np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                new_cache.embeddings = mat / np.maximum(norms, 1e-8)

            with self._cache_lock:
                if target_camera_id:
                    self._multi_caches[target_camera_id] = new_cache
                    self._multi_cache_times[target_camera_id] = time.time()
                else:
                    self._cache = new_cache
                    self._last_embed_pull = time.time()

            # 2. Lưu xuống đĩa (.pkl) để dùng offline cho lần khởi động sau
            self._known_embedding_version = server_ver if server_ver > 0 else (self._known_embedding_version + 1)
            embedding_sync.save_cache(cam_id, new_cache, self._known_embedding_version)

            self._server_online = True
            logger.success(f"✅ Đã tải {new_cache.size} khuôn mặt (Camera {cam_id}) từ Server vào RAM")
            return True

        except requests.ConnectionError:
            logger.warning("❌ Không thể kết nối Server — làm việc offline")
            self._server_online = False
            return False
        except Exception as e:
            logger.error(f"Lỗi pull embeddings: {e}")
            self._server_online = False
            return False

    def get_cache(self, camera_id: str = None) -> EmbeddingCache:
        """Lấy cache hiện tại (thread-safe). Lấy cache riêng của camera nếu có."""
        with self._cache_lock:
            if camera_id and camera_id in self._multi_caches:
                return self._multi_caches[camera_id]
            return self._cache

    def should_refresh_embeddings(self, camera_id: str = None) -> bool:
        """Kiểm tra đã đến lúc refresh embeddings chưa."""
        if camera_id and camera_id in self._multi_cache_times:
            last_pull = self._multi_cache_times[camera_id]
        else:
            last_pull = self._last_embed_pull
            
        elapsed_min = (time.time() - last_pull) / 60
        return elapsed_min >= edge_config.embedding_refresh_min

    # ─── Push Attendance ──────────────────────

    def send_attendance_raw(
        self,
        embedding: np.ndarray,
        camera_id: str = None,
        liveness_score: float = 1.0,
        liveness_checked: bool = False,
        timestamp: str = None,
    ) -> dict:
        """
        Gửi thẳng HTTP POST, KHÔNG LƯU SQLITE TẠI ĐÂY NỮA
        (Do đã được xử lý bởi Pipeline Offline-First ở HeadlessProcessor).
        """
        payload = {
            "camera_id": camera_id or self.camera_id,
            "timestamp": timestamp or datetime.now().isoformat(),
            "embedding": embedding.tolist(),
            "liveness_score": liveness_score,
            "liveness_checked": liveness_checked,
        }

        try:
            resp = self._session.post(
                f"{self.server_url}/api/attendance",
                json=payload,
                headers=self._headers(),
                timeout=5,
            )

            if resp.status_code == 200:
                self._server_online = True
                return resp.json()
            else:
                self._server_online = False
                return {"status": "error", "message": f"HTTP {resp.status_code}"}

        except requests.ConnectionError:
            self._server_online = False
            return {"status": "offline", "message": "ConnectionError"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _save_offline(self, payload, embedding, liveness_score, liveness_checked):
        """Lưu bản ghi vào SQLite khi mất mạng."""
        try:
            emb_bytes = embedding.astype(np.float32).tobytes()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO offline_attendance 
                       (camera_id, timestamp, embedding, liveness_score, liveness_checked)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        payload["camera_id"],
                        payload["timestamp"],
                        emb_bytes,
                        liveness_score,
                        1 if liveness_checked else 0,
                    ),
                )
            logger.info("💾 Đã lưu vào offline queue")
        except Exception as e:
            logger.error(f"Lỗi lưu offline: {e}")

    # ─── Cooldown ─────────────────────────────

    def check_cooldown(self, student_id: int, camera_id: str) -> float:
        """Trả về thời gian cooldown còn lại (giây). 0 = hết cooldown."""
        with self._cooldown_lock:
            key = f"{student_id}_{camera_id}"
            last = self._cooldown_map.get(key, 0)
            elapsed = time.time() - last
            remaining = max(0.0, edge_config.attendance_cooldown - elapsed)
            return remaining

    def set_cooldown(self, student_id: int, camera_id: str):
        with self._cooldown_lock:
            key = f"{student_id}_{camera_id}"
            self._cooldown_map[key] = time.time()

    def reset_cooldown(self):
        """Xóa toàn bộ lịch sử cooldown khi bắt đầu phiên mới."""
        with self._cooldown_lock:
            self._cooldown_map.clear()

    # ─── Background Sync ─────────────────────

    def _sync_loop(self):
        """Vòng lặp nền: đồng bộ offline records + refresh embeddings thông minh."""
        time.sleep(5)  # Chờ khởi động ổn định
        while not self._stop_event.is_set():
            try:
                # 1. Sync offline records
                if self.check_server():
                    self._push_offline_records_new()

                    # Refresh camera list từ DB mỗi 5 phút
                    # [FIX] _last_cam_list_pull = -300 → pull ngay khi server online lần đầu
                    now = time.time()
                    if not hasattr(self, '_last_cam_list_pull'):
                        self._last_cam_list_pull = -300.0  # trigger ngay
                    if now - self._last_cam_list_pull >= 300:
                        if self.pull_camera_list():
                            self._last_cam_list_pull = now

                    # Thường xuyên cập nhật danh sách camera động
                    self.report_status()

                    # [FIX] Kiểm tra phiên bản embedding mỗi 10 giây
                    # Thay vì chờ 10 phút, chỉ pull khi có phân bản mới
                    now = time.time()
                    if now - self._last_version_check >= 10.0:
                        self._last_version_check = now
                        self._check_embedding_version()

                # 2. Refresh embeddings định kỳ dự phòng
                if self._server_online:
                    # Refresh global cache
                    if self.should_refresh_embeddings():
                        self.pull_embeddings()
                        
                    # Refresh multi-caches cho các camera đang kết nối
                    for cam_id in list(self._multi_caches.keys()):
                        if self.should_refresh_embeddings(cam_id):
                            self.pull_embeddings(cam_id)

            except Exception as e:
                logger.debug(f"Sync loop error: {e}")

            # [FIX OFFLINE] Back-off thông minh:
            # - Online: chu kỳ bình thường (sync_interval_sec, mặc định 5s)
            # - Offline: ngủ 30s để tránh hammer kết nối liên tục gây đầy bộ nhớ
            sleep_sec = edge_config.sync_interval_sec if self._server_online else 30
            for _ in range(sleep_sec):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _check_embedding_version(self):
        """
        [FIX] Kiểm tra nhanh xem có học viên mới đăng ký không.
        Nếu có phân bản mới → pull lại toàn bộ embeddings — không cần restart Mini PC.

        [FIX OFFLINE] Dùng _fast_session + circuit breaker:
        - Không retry khi offline → không gây spam WARNING log
        - Bỏ qua hoàn toàn nếu circuit đang mở
        """
        # Circuit breaker: đã biết offline → không thử
        if self._circuit_open:
            return
        try:
            resp = self._fast_session.get(
                f"{self.server_url}/api/embeddings/version",
                headers=self._headers(),
                timeout=2,
            )
            if resp.status_code == 200:
                data = resp.json()
                server_version = data.get("embedding_version", 0)
                if server_version != self._known_embedding_version:
                    logger.info(f"📥 Phát hiện embedding_version mới: {self._known_embedding_version} → {server_version} — Đang pull...") 
                    # [FIX] Cập nhật _known_embedding_version TRƯỚC khi pull per-camera
                    # để tránh pull_embeddings() global cập nhật version, sau đó
                    # pull_embeddings(cam_id) thấy version đã khớp → BỎ QUA per-camera cache
                    self._known_embedding_version = server_version
                    self.pull_embeddings(force=True)                          # Global cache
                    for cam_id in list(self._multi_caches.keys()):
                        self.pull_embeddings(cam_id, force=True)             # Per-camera (force bypass version check)
        except Exception as e:
            logger.debug(f"_check_embedding_version error: {e}")


    def _discovery_loop(self):
        """Vòng lặp nền quét mạng LAN tìm IP Camera."""
        time.sleep(2) # Chờ app khởi động
        while not self._stop_event.is_set():
            try:
                cam_details = discover_network_cameras(timeout=3.0)
                links = generate_rtsp_links(cam_details)
                if links:
                    self._discovered_rtsp = links
            except Exception as e:
                logger.debug(f"Discovery error: {e}")
            
            # Quét định kỳ mỗi 60 giây
            for _ in range(60):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _push_offline_records_new(self):
        """Đọc từ attendance_cache (Offline-First) và push lên Server."""
        from local_cache.attendance_cache import attendance_cache
        
        pending_records = attendance_cache.get_all_pending(limit=20)
        if not pending_records:
            return

        logger.info(f"🔄 Đang đồng bộ {len(pending_records)} bản ghi offline...")
        
        success_count = 0
        for record in pending_records:
            result = self.send_attendance_raw(
                embedding=record["embedding"],
                camera_id=record["camera_id"],
                liveness_score=record["liveness_score"],
                liveness_checked=record["liveness_checked"],
                timestamp=record["timestamp"]
            )
            
            if result.get("status") in ("success", "ignored", "queued"):
                attendance_cache.remove_pending(record["id"])
                success_count += 1
            else:
                attendance_cache.mark_failed(record["id"], result.get("message", "API Error"))

        if success_count > 0:
            logger.success(f"✅ Đã đồng bộ thành công {success_count} bản ghi.")

    def get_offline_count(self) -> int:
        """Đếm số bản ghi chờ đồng bộ."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM offline_attendance WHERE synced = 0"
                ).fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def get_system_command(self) -> str:
        """Lấy lệnh hệ thống từ Server (START/STOP)."""
        data = self.get_system_command_raw()
        return data.get("command", "STOP")

    def get_system_command_raw(self) -> dict:
        """Lấy toàn bộ dữ liệu lệnh từ Server (bao gồm cả target_camera).

        [FIX OFFLINE] Dùng _fast_session (không retry) + Circuit Breaker:
        - Khi server offline >= 5 lần liên tiếp → trả về STOP ngay, không request
        - Tránh tích lũy connection pool objects gây đầy bộ nhớ
        """
        _default = {"command": self._current_offline_cmd, "target_camera": None}

        # Circuit breaker: nếu đã biết offline → không thử nữa
        if self._circuit_open:
            return _default

        try:
            resp = self._fast_session.get(
                f"{self.server_url}/api/system/command",
                headers=self._headers(),
                timeout=2,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Lưu lại lệnh cuối cùng nhận được để dùng khi offline
                self._current_offline_cmd = data.get("command", "STOP")
                self._consecutive_offline_count = 0
                self._circuit_open = False
                self._server_online = True
                return data
            else:
                self._consecutive_offline_count += 1
        except Exception:
            self._consecutive_offline_count += 1
            if self._consecutive_offline_count >= 5 and not self._circuit_open:
                logger.warning(
                    "⚡ Circuit Breaker MỞ — Server offline. "
                    "Sẽ thử lại sau khi check_server() xác nhận online."
                )
                self._circuit_open = True
            self._server_online = False

        return _default

    def stop(self):
        """Dừng background sync."""
        self._stop_event.set()
        if self._sync_thread.is_alive():
            self._sync_thread.join(timeout=3)
        logger.info("EdgeClient đã dừng")


# Singleton
edge_client = EdgeClient()