"""
services/headless_processor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Headless AI Processor for Multi-Camera (Edge Box)
- Multi-threaded: Mỗi camera 1 thread xử lý riêng.
- Shared AI models và shared Edge Client.
- Tích hợp vẽ Bounding Box có Tracking nội suy.
- Hỗ trợ Render Font Tiếng Việt chuẩn xác qua Pillow (Font To, Đậm).
- Chế độ Idle: Nằm im chờ lệnh START từ Server.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import time
import cv2
import numpy as np
import threading
from datetime import datetime
import requests
import base64
import psutil
from loguru import logger
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from config import edge_config, ai_config, anti_spoof_config, camera_config
# pyrefly: ignore [missing-import]
from services.face_engine import face_engine

# [FIX TASK 3] Tạo cầu nối Tín hiệu PyQt cho Local UI
try:
    from PyQt6.QtCore import QObject, pyqtSignal
    class ProcessorSignals(QObject):
        camera_status_changed = pyqtSignal(str, bool)
    processor_signals = ProcessorSignals()
except ImportError:
    class DummySignals:
        def emit(self, *args, **kwargs): pass
    processor_signals = DummySignals()
    processor_signals.camera_status_changed = processor_signals
try:
    # pyrefly: ignore [missing-import]
    from services.anti_spoof_service import anti_spoof_service  
    ANTI_SPOOF_AVAILABLE = anti_spoof_service.available
    if ANTI_SPOOF_AVAILABLE:
        logger.success("🚀 [Edge] Anti-Spoofing đã sẵn sàng!")
    else:
        logger.warning("⚠️ [Edge] Anti-Spoofing không khả dụng.")
except Exception as e:
    logger.warning(f"[Edge] Anti-Spoofing import thất bại: {e}")
    anti_spoof_service = None
    ANTI_SPOOF_AVAILABLE = False

from edge_client import edge_client
from local_cache.attendance_cache import attendance_cache

# Registry toàn cục để theo dõi các cổng phần cứng đang bận
os.environ["OPENCV_VIDEOIO_PRIORITY_OBSENSOR"] = "0" 
# ÉP FFMPEG CHẠY CHẾ ĐỘ LOW LATENCY CỰC ĐOAN
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|rtsp_flags;nobuffer|probesize;32|analyzeduration;0|fflags;nobuffer|flags;low_delay"

ACTIVE_SOURCES = set()
SOURCES_LOCK = threading.Lock()

import queue

class CameraWorker:
    """
    Luồng xử lý Camera theo kiến trúc Pipeline (Non-blocking):
    Thread 1 (Capture) -> Thread 2 (Detection) -> Thread 3 (Recognition) -> Thread 4 (API Sender)
    """
    
    def __init__(self, camera_id: str, source: str, db_camera_id: int = None):
        self.camera_id = camera_id        # VD: "CAM_01" — dùng cho hiển thị/live view
        self.db_camera_id = db_camera_id  # VD: 1 (int) — dùng để gửi lên attendance API
        self.source = source
        self._running = False
        self._stop_event = threading.Event()
        
        # Trạng thái điều khiển
        self._active = False 
        self._is_previewing = False 
        self._attendance_enabled = False 
        
        # Buffers cho Live View
        self._latest_frame = None
        self._last_known_faces = []
        self._frame_lock = threading.Lock()
        
        # Trí nhớ ngắn hạn cho chống giả mạo / nhận diện liên tiếp
        self._real_face_history = {}
        self._spoof_log_cache = {}

        # 🚀 Pipeline Queues (Kích thước nhỏ để rơi frame cũ, đảm bảo realtime)
        self.detect_queue   = queue.Queue(maxsize=2)
        self.recognize_queue = queue.Queue(maxsize=2)
        self.api_queue      = queue.Queue(maxsize=10)

        # [FIX #1] Upload Queue — persistent upload worker thay vì per-frame thread
        # maxsize=2: nếu upload chậm, chỉ giữ frame mới nhất, bỏ frame cũ
        self._upload_queue = queue.Queue(maxsize=2)

        # [FIX #2] Cache PIL font tại instance level — chỉ load 1 lần duy nhất
        self._pil_font = None
        self._pil_font_loaded = False

        # [FIX #11] Pre-allocated buffer cho live loop — tránh cấp phát heap mới mỗi frame
        # Buffer sẽ được khởi tạo lazy khi nhận frame đầu tiên (chưa biết shape)
        self._live_frame_buf: np.ndarray | None = None
        self._live_buf_shape: tuple | None = None

        # Threads
        self.threads = []
        self._capture_frame_count = 0

        # Watchdog Health Metrics
        self.last_capture_time = time.time()
        self.last_ai_time      = time.time()
        # [FIX WATCHDOG LOOP] Grace period sau khi restart:
        # RTSP camera cần thời gian để kết nối lại (có thể >8s).
        # Watchdog không được tính timeout trong 30s đầu sau khi worker được tạo.
        self._watchdog_grace_until: float = time.time() + 30.0

    def set_active(self, active: bool):
        self._active = active

    def set_previewing(self, previewing: bool):
        self._is_previewing = previewing

    def set_attendance_enabled(self, enabled: bool):
        self._attendance_enabled = enabled
        if enabled:
            # Xoá rác từ phiên cũ để tránh ghi nhận lệch phiên
            self._real_face_history.clear()
            self._spoof_log_cache.clear()
            
            # Reset cooldown trên edge client
            from edge_client import edge_client
            edge_client.reset_cooldown()
            
            # Xoá sạch các hàng đợi để tránh "bóng ma" từ phiên trước
            import queue
            for q in [self.detect_queue, self.recognize_queue, self.api_queue]:
                try:
                    while True: q.get_nowait()
                except queue.Empty:
                    pass

    def _get_pil_font(self):
        """[FIX #2] Lazy-load PIL font 1 lần duy nhất, cache tại instance."""
        if not self._pil_font_loaded:
            try:
                self._pil_font = ImageFont.truetype("arialbd.ttf", 36)
            except Exception:
                try:
                    self._pil_font = ImageFont.truetype("arial.ttf", 36)
                except Exception:
                    self._pil_font = ImageFont.load_default()
            self._pil_font_loaded = True
        return self._pil_font

    def start(self):
        if self._running: return
        self._running = True
        self._stop_event.clear()

        # [FIX #1] 6 Luồng: 4 Pipeline + 1 Live View + 1 Upload Worker (persistent)
        self.threads = [
            threading.Thread(target=self._capture_loop,  name=f"Cap-{self.camera_id}",    daemon=True),
            threading.Thread(target=self._detect_loop,   name=f"Det-{self.camera_id}",    daemon=True),
            threading.Thread(target=self._recognize_loop,name=f"Rec-{self.camera_id}",    daemon=True),
            threading.Thread(target=self._api_loop,      name=f"Api-{self.camera_id}",    daemon=True),
            threading.Thread(target=self._live_loop,     name=f"Liv-{self.camera_id}",    daemon=True),
            threading.Thread(target=self._upload_worker, name=f"Upl-{self.camera_id}",    daemon=True),
        ]
        
        for t in self.threads:
            t.start()
        
        # Khởi tạo cache độc lập cho camera này (dùng db_camera_id để filter floor trên Server)
        # [FIX OFFLINE] pull_embeddings() tự kiểm tra circuit_open:
        #   - Server online  → pull từ Server
        #   - Server offline → load offline cache từ đĩa, không gửi request
        threading.Thread(
            target=lambda: edge_client.pull_embeddings(self.camera_id, db_camera_id=self.db_camera_id),
            daemon=True
        ).start()
        
        logger.info(f"🚀 CameraWorker {self.camera_id} khởi động kiến trúc Multi-thread Pipeline (6 luồng: +UploadWorker).")

    def _capture_loop(self):
        """THREAD 1: Camera Capture -> Detect Queue"""
        cap = None
        _prev_active = False # [FIX TASK 3] Theo dõi trạng thái kết nối nhịp trước

        while not self._stop_event.is_set():
            if not self._active and not self._is_previewing:
                if cap:
                    cap.release()
                    cap = None
                    edge_client.update_active_status(self.camera_id, False)
                    _prev_active = False
                    with SOURCES_LOCK:
                        if self.source in ACTIVE_SOURCES: ACTIVE_SOURCES.remove(self.source)
                time.sleep(0.5)
                continue

            if cap is None or not cap.isOpened():
                cap = self._open_camera_backend()
                if cap is None:
                    time.sleep(2)
                    continue
                # [FIX #7] Flush driver-internal buffer ngay sau khi kết nối lại
                # để tránh nhận frame cũ đã queue sẵn trong driver
                for _ in range(5):
                    cap.grab()

            # [FIX TASK 3] Không gọi update_active_status(True) mù quáng mỗi vòng lặp
            # Chỉ cập nhật khi cap.read() thực sự thành công ở dưới.
            ret, frame = cap.read()
            
            # [FIX LAG] Cập nhật Watchdog TRƯỚC khi kiểm tra ret
            # để tránh trường hợp Watchdog timeout trùng với reconnect
            self.last_capture_time = time.time()
            if not ret or frame is None:
                if _prev_active:
                    logger.warning(f"⚠️ Camera {self.camera_id}: Mất tín hiệu, thả cap và thử lại...")
                cap.release()  # Thả ngay — tránh treo vào cap.read() lần sau
                cap = None
                # Xóa frame buffer để UI không hiển thị frame ma
                with self._frame_lock:
                    self._latest_frame = None
                    
                edge_client.update_active_status(self.camera_id, False)
                if _prev_active:
                    processor_signals.camera_status_changed.emit(self.camera_id, False)
                _prev_active = False
                time.sleep(1)
                continue

            # [FIX TASK 3] Force Local Cache Update on Recovery
            if not _prev_active:
                logger.info(f"🟢 Camera {self.camera_id}: Khôi phục tín hiệu, cập nhật cache Local UI tức thì.")
                # Ép cập nhật local dictionary ngay lập tức TRƯỚC khi thread khác kịp đọc
                edge_client._active_status_cache[self.camera_id] = True
                edge_client.update_active_status(self.camera_id, True)
                # Báo cho PyQt UI (nếu có) vẽ lại ngay lập tức
                processor_signals.camera_status_changed.emit(self.camera_id, True)
                _prev_active = True

            # Update Live View Buffer (Hiển thị 30 FPS mượt mà)
            with self._frame_lock:
                self._latest_frame = frame

            # Đẩy vào Detect Queue với frame_skip (Giảm tải CPU/GPU)
            self._capture_frame_count += 1
            if self._capture_frame_count % edge_config.frame_skip != 0:
                continue

            try:
                if self.detect_queue.full():
                    self.detect_queue.get_nowait()
                capture_time = datetime.now().isoformat()
                self.detect_queue.put_nowait((frame.copy(), capture_time))
            except queue.Empty:
                pass
            except queue.Full:
                pass

        if cap: cap.release()

    def _detect_loop(self):
        """THREAD 2: Detect Queue -> Detections -> Recognize Queue"""
        while not self._stop_event.is_set():
            try:
                frame, capture_time = self.detect_queue.get(timeout=0.5)
            except queue.Empty:
                continue
                
            if not self._active or not self._attendance_enabled:
                self._last_known_faces = []
                continue

            try:
                detected = face_engine.detect_faces(frame)
                if detected:
                    # [FIX] Sắp xếp theo diện tích mặt từ LỚN → NHỎ và giới hạn tối đa 4 mặt
                    # Ưu tiên mặt lớn (gần camera) trước — độ chính xác cao hơn
                    # Bỏ các mặt nhỏ ở background để giảm tải GPU/CPU
                    if len(detected) > 1:
                        detected.sort(
                            key=lambda df: (df.bbox[2] - df.bbox[0]) * (df.bbox[3] - df.bbox[1]),
                            reverse=True  # True = lớn nhất trước
                        )
                    max_faces = getattr(ai_config, "max_faces_per_frame", 4)
                    if len(detected) > max_faces:
                        detected = detected[:max_faces]
                    # Gửi sang luồng Recognize
                    try:
                        if self.recognize_queue.full():
                            self.recognize_queue.get_nowait()
                        self.recognize_queue.put_nowait((frame, detected, capture_time))
                    except queue.Empty:
                        pass
                    except queue.Full:
                        pass
                else:
                    self._last_known_faces = []
            except Exception as e:
                logger.error(f"❌ Detect Loop Error [{self.camera_id}]: {e}")

    def _recognize_loop(self):
        """THREAD 3: Recognize Queue -> Recognition -> API Queue"""
        while not self._stop_event.is_set():
            try:
                frame, detected, capture_time = self.recognize_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                cache = edge_client.get_cache(self.camera_id)
                results = face_engine.recognize_batch(detected, cache)
                
                new_known_faces = []
                for i, res in enumerate(results):
                    # Xử lý Anti-spoofing trực tiếp trong Thread 3 (GPU bound)
                    is_real = True
                    spoof_score = 1.0
                    
                    if res.recognized:
                        # Fast-Path Cooldown: Chi quet chong gia mao neu hoc sinh CHUA diem danh
                        remaining = edge_client.check_cooldown(res.student_id, self.camera_id)
                        if remaining <= 0:
                            if self._attendance_enabled and ANTI_SPOOF_AVAILABLE and anti_spoof_service:
                                # Anti-Spoof kha dung: ket qua thuc te
                                is_real, spoof_score = anti_spoof_service.is_real(frame, res.bbox)
                                res.is_real    = is_real
                                res.spoof_score = spoof_score
                            else:
                                # Anti-Spoof KHONG kha dung: mac dinh cho phep qua
                                # ROOT CAUSE FIX: neu khong set is_real=True o day,
                                # res.is_real van la None -> _api_loop check 'res.is_real'
                                # se False -> diem danh bi block hoan toan!
                                res.is_real    = True
                                res.spoof_score = 1.0
                        else:
                            # Da diem danh xong -> bo qua Anti-Spoofing nang ne
                            res.is_real    = True
                            res.spoof_score = 1.0
                    
                    color_val = "unknown"
                    if res.recognized:
                        color_val = "success" if res.is_real else "danger"
                    
                    new_known_faces.append({
                        "bbox": detected[i].bbox,
                        "name": res.display_name if res.recognized else "Unknown",
                        "color_type": color_val
                    })

                    if res.recognized:
                        payload = {
                            "result": res,
                            "embedding": detected[i].embedding,
                            "camera_id": self.camera_id,
                            "timestamp": capture_time
                        }
                        try:
                            self.api_queue.put_nowait(payload)
                        except queue.Full:
                            logger.warning("⚠️ API Queue đầy, bỏ qua nhận diện hiện tại.")

                self._last_known_faces = new_known_faces
                self.last_ai_time = time.time() # Update Watchdog
            except Exception as e:
                logger.error(f"❌ Recognize Loop Error [{self.camera_id}]: {e}")

    def _api_loop(self):
        """THREAD 4: API Queue -> HTTP Request (I/O Bound)"""
        while not self._stop_event.is_set():
            try:
                payload = self.api_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            res = payload["result"]
            embedding = payload["embedding"]
            cam_id = payload["camera_id"]
            capture_time = payload["timestamp"]

            try:
                if self._attendance_enabled and res.is_real:
                    current_count = self._real_face_history.get(res.student_id, 0)
                    self._real_face_history[res.student_id] = current_count + 1

                    needed = getattr(edge_config, "accumulation_frames", 3)
                    accumulated = self._real_face_history[res.student_id]
                    logger.debug(
                        f"[API-LOOP] [{self.camera_id}] '{res.display_name}' "
                        f"tich luy {accumulated}/{needed} frame(s)"
                    )

                    if accumulated >= needed:
                        remaining = edge_client.check_cooldown(res.student_id, cam_id)
                        if remaining <= 0:
                            # 1. OFFLINE-FIRST: Luu vao SQLite ngay lap tuc
                            record_id = attendance_cache.save_pending(
                                camera_id=cam_id,
                                embedding=embedding,
                                liveness_score=res.spoof_score,
                                liveness_checked=ANTI_SPOOF_AVAILABLE,
                                timestamp=capture_time
                            )

                            # 2. Gui len Server API
                            api_cam_id = self.db_camera_id if self.db_camera_id else cam_id
                            logger.info(
                                f"[API-LOOP] [{self.camera_id}] Gui diem danh: "
                                f"'{res.display_name}' | cam_api={api_cam_id} "
                                f"| spoof={res.spoof_score:.2f} "
                                f"| anti_spoof_active={ANTI_SPOOF_AVAILABLE}"
                            )
                            result = edge_client.send_attendance_raw(
                                embedding=embedding,
                                camera_id=str(api_cam_id),
                                liveness_score=res.spoof_score,
                                liveness_checked=ANTI_SPOOF_AVAILABLE,
                                timestamp=capture_time
                            )

                            status = result.get("status", "unknown")
                            if status in ("success", "ignored", "queued"):
                                attendance_cache.remove_pending(record_id)
                                logger.success(
                                    f"[API-LOOP] [{self.camera_id}] Server chap nhan: "
                                    f"'{res.display_name}' | status='{status}'"
                                )
                            else:
                                attendance_cache.mark_failed(record_id, result.get("message", "API Error"))
                                logger.error(
                                    f"[API-LOOP] [{self.camera_id}] Server TU CHOI: "
                                    f"'{res.display_name}' | status='{status}' "
                                    f"| message='{result.get('message', '')}'"
                                )

                            edge_client.set_cooldown(res.student_id, cam_id)
                            self._real_face_history[res.student_id] = 0
                        else:
                            logger.debug(
                                f"[API-LOOP] [{self.camera_id}] '{res.display_name}' "
                                f"dang trong COOLDOWN con {remaining:.0f}s"
                            )
                elif self._attendance_enabled and not res.is_real:
                    self._log_spoof(res)
                    self._real_face_history[res.student_id] = 0
                elif self._attendance_enabled and res.is_real is None:
                    # Guard: is_real chua duoc set (bug) - log de phat hien
                    logger.warning(
                        f"[API-LOOP] [{self.camera_id}] BUG: res.is_real=None cho "
                        f"'{res.display_name}' — diem danh bi bo qua! "
                        f"Kiem tra _recognize_loop anti-spoof path."
                    )
            except Exception as api_err:
                logger.exception(f"[API-LOOP] [{self.camera_id}] CRITICAL exception: {api_err}")

            finally:
                self.api_queue.task_done()

    def _live_loop(self):
        """
        THREAD 5: Stream frame len Server.

        [FIX] Logic cu: chi upload khi self._is_previewing == True.
        Dieu nay co nghia la frame:* keys trong Redis luon TRONG khi
        khong co 'target_camera' tu Server -> get_available_cameras() tra []
        -> UI bao 'Server chua co hinh (San co: [])'.

        [FIX MO] Upload moi khi camera DANG HOAT DONG (_active == True),
        du co ai dang xem hay khong:
          - _is_previewing=True  : Upload 20 FPS (full rate)
          - _active=True only    : Upload 5 FPS (background heartbeat)
        Dieu nay dam bao Redis luon co frame:* va get_available_cameras()
        tra ve danh sach chinh xac.
        """
        last_upload = 0.0
        upload_log_counter = 0
        _last_frame_seen = 0.0  # Theo dõi lần cuối cùng có frame

        while not self._stop_event.is_set():
            # Quyết định FPS dựa trên trạng thái
            if self._is_previewing:
                target_interval = 0.05   # 20 FPS — full preview
            elif self._active:
                # [FIX LAG] Background heartbeat thích nghi:
                # - Có frame: 5 FPS (upload bình thường)
                # - Mất frame >2s (camera offline): 0.5 FPS (giảm 10x tải)
                # Tránh 5 camera × 5FPS = 25 request/s khi 1 camera bị rút dây
                if time.time() - _last_frame_seen > 2.0:
                    target_interval = 2.0  # 0.5 FPS khi camera offline
                else:
                    target_interval = 0.20  # 5 FPS bình thường
            else:
                # Camera tạm dừng hoàn toàn: không cần stream
                time.sleep(0.5)
                continue

            now = time.time()
            if now - last_upload < target_interval:
                time.sleep(0.05)  # [FIX] 50ms thay vì 10ms — giảm CPU spin
                continue


            frame = None
            with self._frame_lock:
                if self._latest_frame is not None:
                    src = self._latest_frame
                    # [FIX #11] Dung lai buffer da cap phat neu shape khop
                    # Tranh allocate 700KB heap moi moi frame x 20 FPS
                    if (self._live_frame_buf is not None
                            and self._live_buf_shape == src.shape):
                        np.copyto(self._live_frame_buf, src)   # reuse, khong allocate
                        frame = self._live_frame_buf
                    else:
                        # Lan dau hoac camera doi resolution -> allocate va cache
                        self._live_frame_buf  = src.copy()
                        self._live_buf_shape  = src.shape
                        frame = self._live_frame_buf

            if frame is None:
                # [FIX LAG] Camera mất kết nối: không có frame → ngủ 500ms thay vì 50ms
                # Trước: spin 20Hz vô nghĩa khi camera bị rút dây → chiếm CPU
                # Sau : chỉ check 2Hz → giảm 10x tải CPU cho camera bị ngắt
                time.sleep(0.5)
                continue

            # Cập nhật thời điểm có frame → adaptive interval biết camera đang online
            _last_frame_seen = now
            last_upload = now

            try:
                known_faces = self._last_known_faces
                dets_payload = []
                if known_faces:
                    for face in known_faces:
                        x1, y1, x2, y2 = face['bbox']
                        name = face['name']
                        color_type = face['color_type']
                        dets_payload.append([int(x1), int(y1), int(x2), int(y2), name, color_type])

                # [TASK 1] Log moi 100 frames de xac nhan stream dang chay
                upload_log_counter += 1
                if upload_log_counter % 100 == 1:
                    mode = 'PREVIEW(20fps)' if self._is_previewing else 'HEARTBEAT(5fps)'
                    logger.debug(
                        f"[STREAM] Pushing frame #{upload_log_counter} "
                        f"for camera '{self.camera_id}' to Server "
                        f"| mode={mode} | faces={len(dets_payload)}"
                    )

                # [FIX #1] Đẩy vào upload_queue thay vì tạo thread mới
                # maxsize=2: nếu queue đầy, drop frame cũ nhất để giữ realtime
                try:
                    if self._upload_queue.full():
                        try:
                            self._upload_queue.get_nowait()  # Drop frame cũ
                        except queue.Empty:
                            pass
                    self._upload_queue.put_nowait((frame, dets_payload))
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"❌ Live Loop Error [{self.camera_id}]: {e}")


    def _open_camera_backend(self):
        """
        [FIX LAG] Mở VideoCapture trong 1 thread nền với hard-timeout 5 giây.
        Nếu camera mất điện/LAN, cv2.VideoCapture() RTSP sẽ block rất lâu
        (FFMPEG mặc định ~60s). Giải pháp: chạy trong thread riêng + join(timeout=5).
        """
        source = self.source
        with SOURCES_LOCK:
            if source in ACTIVE_SOURCES: return None
            ACTIVE_SOURCES.add(source)

        result_container = [None]  # Shared container để nhận kết quả từ thread

        def _do_open():
            try:
                try:
                    cam_idx = int(source)
                    cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
                except ValueError:
                    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
                    # [FIX] Set cả OPEN và READ timeout để tránh block vô hạn
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)

                if cap and cap.isOpened():
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    result_container[0] = cap
                else:
                    if cap: cap.release()
            except Exception as exc:
                logger.debug(f"[{self.camera_id}] _do_open error: {exc}")

        open_thread = threading.Thread(target=_do_open, daemon=True)
        open_thread.start()
        open_thread.join(timeout=5.0)  # Hard-timeout 5s — không block capture loop

        cap = result_container[0]
        if cap is None:
            with SOURCES_LOCK:
                if source in ACTIVE_SOURCES: ACTIVE_SOURCES.remove(source)
        return cap

    def _upload_worker(self):
        """
        [FIX #1] THREAD 6: Persistent Upload Worker — thay thế per-frame thread.
        Chạy vòng lặp vô hạn, lấy (frame, dets) từ _upload_queue và gửi lên Server.
        Không tạo thread mới → không overhead thread creation mỗi frame.
        """
        cam_id = self.camera_id
        server_url = edge_client.server_url
        # Dùng Session riêng cho upload worker để reuse TCP connection
        upload_session = requests.Session()
        headers = {"X-DEVICE-TOKEN": edge_client._headers()["X-DEVICE-TOKEN"]}
        url = f"{server_url}/api/system/frame"

        while not self._stop_event.is_set():
            try:
                frame, dets = self._upload_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if not isinstance(frame, np.ndarray) or frame.size == 0:
                    continue

                frame_copy = frame  # đã copy ở _live_loop rồi

                # [FIX #2] Vẽ bounding box + text với font đã cache
                if dets:
                    for det in dets:
                        x1, y1, x2, y2, name, color_type = det
                        color_cv = (
                            (0, 255, 0)   if color_type == "success"
                            else (0, 0, 255)   if color_type == "danger"
                            else (0, 255, 255)
                        )
                        cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color_cv, 3)

                    # PIL render tiếng Việt — dùng font đã cache (load 1 lần)
                    font = self._get_pil_font()
                    frame_rgb = cv2.cvtColor(frame_copy, cv2.COLOR_BGR2RGB)
                    pil_img   = Image.fromarray(frame_rgb)
                    draw      = ImageDraw.Draw(pil_img)

                    for det in dets:
                        x1, y1, x2, y2, name, color_type = det
                        color_pil = (
                            (0, 255, 0)   if color_type == "success"
                            else (255, 0, 0)   if color_type == "danger"
                            else (255, 255, 0)
                        )
                        try:
                            bbox = font.getbbox(name)
                            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                        except Exception:
                            tw, th = 120, 20
                        bg_x1 = max(0, int(x1))
                        bg_y1 = max(0, int(y1) - th - 16)
                        draw.rectangle(
                            [(bg_x1, bg_y1), (bg_x1 + tw + 16, bg_y1 + th + 16)],
                            fill=(0, 0, 0)
                        )
                        draw.text((bg_x1 + 8, bg_y1 + 4), name, font=font, fill=color_pil)

                    frame_copy = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

                # Resize về 640p và JPEG encode
                h, w = frame_copy.shape[:2]
                if w != 640:
                    sh = int(h * (640 / w))
                    frame_copy = cv2.resize(frame_copy, (640, sh), interpolation=cv2.INTER_LINEAR)

                ok, buffer = cv2.imencode(".jpg", frame_copy, [cv2.IMWRITE_JPEG_QUALITY, 72])
                if not ok or buffer is None or len(buffer) == 0:
                    continue

                img_b64 = base64.b64encode(buffer).decode("utf-8")
                payload = {"image_b64": img_b64, "camera_id": cam_id, "detections": dets or []}

                try:
                    resp = upload_session.post(url, json=payload, headers=headers, timeout=2)
                    if resp.status_code != 200:
                        logger.warning(
                            f"[STREAM] [{cam_id}] Server HTTP {resp.status_code}: {resp.text[:80]}"
                        )
                except requests.exceptions.ConnectionError:
                    logger.debug(f"[STREAM] [{cam_id}] ConnectionError — bỏ qua frame.")
                except requests.exceptions.Timeout:
                    logger.debug(f"[STREAM] [{cam_id}] Timeout upload frame.")

            except Exception as exc:
                logger.error(f"[STREAM] [{cam_id}] _upload_worker error: {exc}")
            finally:
                self._upload_queue.task_done()

    def _log_spoof(self, res):
        now = time.time()
        # Chỉ log spoof cho học viên đã nhận diện được, với cooldown 30 giây
        if not res.recognized:
            return
        key = res.student_id
        if now - self._spoof_log_cache.get(key, 0) > 30.0:
            logger.warning(f"🚫 [{self.camera_id}] Phát hiện GIẢ MẠO: {res.display_name} | Score: {res.spoof_score:.1%}")
            self._spoof_log_cache[key] = now

class HeadlessProcessor:
    def __init__(self):
        self._workers = {}
        self._running = False
        self._current_command = "STOP" # Trạng thái ban đầu luôn là STOP
        self._target_camera_view = None
        self._last_embed_refresh = 0.0

    def start(self):
        if self._running: return
        logger.info("🚀 Headless Processor (Multi-Cam) đang khởi động...")
        if not face_engine.load_model():
            logger.error("❌ Không thể nạp model AI.")
            return

        cam_list = getattr(edge_config, "camera_list", [])
        if not cam_list:
            logger.warning("⚠️ camera_list rỗng — đang chờ EdgeClient pull từ Server (tối đa 5 phút)...")
            # [FIX] EdgeClient pull trong thread nền → đây chờ thay vì thoát ngay
            # Vòng lặp retry mỗi 5s, tối đa 60 lần = 5 phút
            for attempt in range(60):
                time.sleep(5)
                cam_list = getattr(edge_config, "camera_list", [])
                if cam_list:
                    logger.success(f"✅ Đã nhận {len(cam_list)} camera từ Server (sau {(attempt+1)*5}s).")
                    break
                if attempt % 6 == 5:  # log mỗi 30s
                    logger.info(f"⏳ Vẫn chờ camera list... ({(attempt+1)*5}s đã trôi qua)")
        if not cam_list:
            logger.error("❌ Không có camera nào sau 5 phút. Kiểm tra: (1) Server đang chạy, (2) Bảng Cameras có dữ liệu trong DB.")
            return


        # Doc tu config: neu EDGE_AUTO_START=true, camera tu dong bat ngay khi khoi dong
        is_auto = getattr(edge_config, 'auto_start', False)
        if is_auto:
            logger.info("🟢 Auto-start = True: Camera se bat ngay khi khoi dong.")
        else:
            logger.info("⏸️ Mini PC da san sang va dang CHO LENH. Hay bam Bat dau tren Server...")

        
        for cam in cam_list:
            cid = cam["id"]             # "CAM_01"
            src = cam["source"]         # rtsp://...
            db_id = cam.get("camera_id")  # 1 (int từ DB)
            worker = CameraWorker(cid, src, db_camera_id=db_id)
            worker.set_active(is_auto)
            worker.set_attendance_enabled(is_auto)
            self._workers[cid] = worker
            worker.start()
            time.sleep(0.2)

        self._running = True
        
        # Bật Watchdog
        threading.Thread(target=self._watchdog_loop, name="Watchdog", daemon=True).start()
        
        self._run_control_loop()

    def _run_control_loop(self):
        last_command_check = 0
        # [FIX OFFLINE] Polling thích nghi: 1s khi online, 10s khi server tắt
        _poll_interval = 1.0
        while self._running:
            now = time.time()
            
            # Cứ mỗi _poll_interval giây, Mini PC sẽ gọi API lên Server để "hỏi" xem có lệnh mới không
            if now - last_command_check >= _poll_interval:
                last_command_check = now
                try:
                    cmd_data = edge_client.get_system_command_raw()
                    new_cmd = cmd_data.get("command", "STOP")
                    actual_target = cmd_data.get("target_camera")

                    # NẾU PHÁT HIỆN LỆNH MỚI TỪ SERVER
                    if new_cmd != self._current_command:
                        if new_cmd == "RETRY_CAMERA" and actual_target:
                            logger.warning(f"🔄 NHẬN LỆNH [RETRY]: Khởi động lại Camera {actual_target}")
                            target_key = None
                            for cid, worker in self._workers.items():
                                if str(cid).upper() == str(actual_target).upper() or str(worker.source).upper() == str(actual_target).upper():
                                    target_key = cid
                                    worker.set_active(False)
                                    worker._stop_event.set()
                                    break
                            
                            if target_key:
                                old_worker = self._workers[target_key]
                                new_worker = CameraWorker(
                                    camera_id=target_key,
                                    source=old_worker.source,
                                    db_camera_id=old_worker.db_camera_id
                                )
                                new_worker.set_active(True)
                                new_worker.set_attendance_enabled(True)
                                new_worker.set_previewing(True)
                                self._workers[target_key] = new_worker
                                new_worker.start()
                                
                            # Reset lệnh retry ngay để không lặp lại
                            try: requests.post(f"{edge_client.server_url}/api/system/command", json={"command": "START"}, headers=edge_client._headers(), timeout=1)
                            except: pass
                        else:
                            self._current_command = new_cmd
                            is_start = (new_cmd == "START")
                            
                            # In log thông báo trạng thái
                            if is_start:
                                logger.info("🟢 NHẬN LỆNH [START]: Đánh thức Camera, bắt đầu điểm danh!")
                                face_engine.load_model()
                                edge_client.reset_cooldown()
                            else:
                                logger.info("🔴 NHẬN LỆNH [STOP]: Tạm dừng điểm danh, giải phóng Camera.")
                                # giúp điểm danh khởi động lại ngay lập tức (không bị delay load vài giây).
                                # face_engine.unload_model()

                            # Đẩy lệnh xuống điều khiển tất cả các luồng camera
                            for worker in self._workers.values():
                                worker.set_active(is_start)
                                worker.set_attendance_enabled(is_start)

                    # Cập nhật xem Server có đang muốn xem trước (Preview) camera nào không
                    self._target_camera_view = actual_target
                    
                    # [NEW] Khởi tạo Worker cho các camera mới từ DB (dựa trên all_cameras trả về)
                    all_cams = cmd_data.get("all_cameras", [])
                    
                    # Fallback: đảm bảo target_camera cũng được khởi tạo
                    if actual_target and isinstance(actual_target, str):
                        if actual_target not in all_cams:
                            all_cams.append(actual_target)
                            
                    for cam_url in all_cams:
                        if isinstance(cam_url, str):
                            source_exists = any(w.source == cam_url for w in self._workers.values())
                            if cam_url not in self._workers and not source_exists:
                                # Tìm camera_id tương ứng trong DB list
                                cam_info = next(
                                    (c for c in edge_config.camera_list if c.get("source") == cam_url),
                                    None
                                )
                                cam_id = cam_info["id"] if cam_info else cam_url
                                cam_name = cam_info["name"] if cam_info else cam_url
                                db_id = cam_info.get("camera_id") if cam_info else None
                                logger.info(f"✨ Khởi tạo on-the-fly Worker: [{cam_id}] {cam_name}")
                                new_worker = CameraWorker(camera_id=cam_id, source=cam_url, db_camera_id=db_id)
                                is_sys_start = (self._current_command == "START")
                                new_worker.set_active(is_sys_start) 
                                new_worker.set_attendance_enabled(is_sys_start) 
                                self._workers[cam_id] = new_worker
                                new_worker.start()

                    for cid, worker in self._workers.items():
                        is_match = (cid.upper() == str(actual_target).upper() or str(actual_target).upper() == str(worker.source).upper())
                        worker.set_previewing(is_match)
                        
                    # [NEW] Tự động khởi tạo Worker cho các IP Camera được phát hiện tự động
                    for rtsp_url in edge_client._discovered_rtsp:
                        source_exists = any(w.source == rtsp_url for w in self._workers.values())
                        if rtsp_url not in self._workers and not source_exists:
                            logger.info(f"✨ Tự động nhận diện IP Camera mới: {rtsp_url}")
                            new_worker = CameraWorker(camera_id=rtsp_url, source=rtsp_url)
                            is_sys_start = (self._current_command == "START")
                            new_worker.set_active(is_sys_start)
                            # is_sys_start sẽ được truyền vào set_attendance_enabled, 
                            # bên trong method này nó đã tự lọc chỉ chạy cho rtsp:// rồi
                            new_worker.set_attendance_enabled(is_sys_start)
                            self._workers[rtsp_url] = new_worker
                            new_worker.start()
                            
                except Exception as e:
                    logger.error(f"Lỗi poll lệnh server: {e}")
                    
                # [FIX OFFLINE] Điều chỉnh poll interval dựa trên trạng thái circuit breaker
                if edge_client._circuit_open:
                    # Server tắt: tăng interval lên 10s, giảm tải đáng kể
                    if _poll_interval < 10.0:
                        _poll_interval = 10.0
                        logger.info("⏰ Server offline — giảm tần số poll xuống 10s/lần.")
                else:
                    # Server online: phục hồi về 1s
                    if _poll_interval > 1.0:
                        _poll_interval = 1.0
                        logger.info("⚡ Server online — khôi phục tần số poll 1s/lần.")

            if now - self._last_embed_refresh >= 600:
                self._last_embed_refresh = now
                edge_client.pull_embeddings()

            time.sleep(0.1)

    def _watchdog_loop(self):
        """THREAD 6: Auto Recovery Watchdog"""
        logger.info("🛡️ Watchdog đã khởi động, giám sát RAM, FPS và Timeout.")
        while self._running:
            time.sleep(5.0)
            now = time.time()
            
            try:
                # 1. Kiểm tra RAM
                ram_percent = psutil.virtual_memory().percent
                if ram_percent > 90.0:
                    logger.critical(f"🔥 BÁO ĐỘNG: RAM quá tải ({ram_percent}%) - Kích hoạt Garbage Collector!")
                    import gc
                    gc.collect()

                # 2. Kiểm tra Health từng Worker
                workers_to_restart = []
                for cid, worker in list(self._workers.items()):
                    if not worker._active and not worker._is_previewing:
                        # Update time liên tục nếu đang dừng để tránh bị tính là timeout
                        worker.last_capture_time = now
                        worker.last_ai_time = now
                        continue

                    # [FIX WATCHDOG LOOP] Grace period: bỏ qua timeout check trong 30s đầu
                    # sau khi worker được tạo mới (lần đầu hoặc sau auto-restart).
                    # RTSP camera có thể cần >8s để negotiate stream, nếu không có grace
                    # period thì watchdog sẽ kích hoạt lại → tạo vòng lặp restart vô tận.
                    if now < worker._watchdog_grace_until:
                        remaining = worker._watchdog_grace_until - now
                        logger.debug(f"[Watchdog] {cid}: Grace period còn {remaining:.0f}s, bỏ qua timeout check.")
                        continue

                    # Capture Timeout: camera không trả frame trong 8s (sau grace period)
                    if now - worker.last_capture_time > 8.0:
                        logger.error(f"💀 Watchdog: Camera {cid} bị treo Capture (>8s). Lên lịch Restart...")
                        workers_to_restart.append(cid)
                        continue
                    
                    # Inference Timeout (>20s không xử lý xong nhưng hàng đợi vẫn còn ảnh)
                    if now - worker.last_ai_time > 20.0 and not worker.recognize_queue.empty():
                        logger.error(f"💀 Watchdog: Camera {cid} bị treo AI Inference (>20s). Lên lịch Restart...")
                        workers_to_restart.append(cid)
                        continue

                # 3. Tự động Restart
                for cid in workers_to_restart:
                    self._restart_worker(cid)
                    
            except Exception as e:
                logger.error(f"Lỗi Watchdog: {e}")

    def _restart_worker(self, cid):
        logger.warning(f"🔄 [AUTO RECOVERY] Đang khởi động lại Camera: {cid}")
        old_worker = self._workers.get(cid)
        if not old_worker: return
        
        # Dừng worker cũ
        old_worker.set_active(False)
        old_worker._stop_event.set()
        
        # [FIX] Giữ lại db_camera_id từ worker cũ — tránh mất thông tin DB mapping
        new_worker = CameraWorker(
            camera_id=cid,
            source=old_worker.source,
            db_camera_id=old_worker.db_camera_id,  # [FIX] trước đây bị bỏ sót
        )
        is_sys_start = (self._current_command == "START")
        new_worker.set_active(is_sys_start)
        new_worker.set_attendance_enabled(is_sys_start)
        new_worker.set_previewing(old_worker._is_previewing)
        # [FIX WATCHDOG LOOP] _watchdog_grace_until đã được set trong __init__ (30s)
        # → Watchdog sẽ không trigger lại ngay lập tức sau khi restart thành công
        
        self._workers[cid] = new_worker
        new_worker.start()
        logger.success(f"✅ [AUTO RECOVERY] Đã Restart thành công Camera Worker: {cid} (grace=30s)")

    def stop(self):
        self._running = False
        for worker in self._workers.values():
            worker._stop_event.set()
        
        # Đợi các worker thread dừng hẳn
        for worker in self._workers.values():
            for t in worker.threads:
                if t.is_alive():
                    t.join(timeout=1.0)

headless_processor = HeadlessProcessor()