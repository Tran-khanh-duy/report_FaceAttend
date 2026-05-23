import json
import threading
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
import redis

from core.config import redis_config

class RedisStateManager:
    """
    Trình quản lý trạng thái tập trung sử dụng Redis.
    Loại bỏ Global Mutable State, hỗ trợ Thread-Safe và Multi-Process cho FastAPI.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(RedisStateManager, cls).__new__(cls)
                cls._instance._init_redis()
            return cls._instance

    def _init_redis(self):
        try:
            # decode_responses=False để hỗ trợ lưu ảnh nhị phân (bytes) cho latest_frames
            self.redis = redis.Redis.from_url(redis_config.url, decode_responses=False)
            self.redis.ping()
            logger.info("✅ RedisStateManager: Kết nối Redis thành công!")
            self._init_defaults()
        except Exception as e:
            logger.error(f"❌ RedisStateManager: Không thể kết nối Redis: {e}")
            # Fallback về Mock hoặc throw exception tùy chiến lược.
            # Ở môi trường production, Redis phải available.
            raise e

    def _init_defaults(self):
        if not self.redis.exists("state:command"):
            self.set_command("STOP")
        if not self.redis.exists("state:embedding_version"):
            self.redis.set("state:embedding_version", 0)
            self.redis.set("state:embedding_updated_at", "")
            
        # [OPT] cameras_status dict đã bị thay bằng get_camera_is_active().
        # Giữ thuộc tính này như alias rỗng để tương thích với code cũ import.
        self.cameras_status = {}
        # KHÔNG khởi động camera monitor TCP thread nữa — gây CPU bottleneck.
        # Trạng thái camera được lấy từ is_active flag trong Redis heartbeat.

    def get_camera_is_active(self, cam_id: str = None, source: str = None) -> bool:
        """
        [OPT] Thay thế cameras_status TCP-check bằng đọc is_active flag từ Redis.
        Zero network I/O — chỉ đọc dữ liệu đã được Edge ghi sẵn vào Redis.
        """
        try:
            edge_status = self.get_all_edge_status()
            for dev in edge_status.values():
                import time as _t
                last_seen = dev.get("last_seen", 0)
                # Coi thiết bị offline nếu không báo tin trong 60 giây
                if _t.time() - last_seen > 60:
                    continue
                for cid, info in dev.get("camera_status", {}).items():
                    if not isinstance(info, dict):
                        continue
                    is_active = info.get("is_active", False)
                    cam_source = info.get("source", "")
                    if cam_id and (cid == cam_id or cam_source == cam_id):
                        return bool(is_active)
                    if source and cam_source == source:
                        return bool(is_active)
        except Exception:
            pass
        return False

    def get_all_cameras_is_active(self) -> Dict[str, bool]:
        """
        [OPT] Trả về dict {cam_id: is_active} cho tất cả camera từ Redis heartbeat.
        Dùng để tính online_cameras / total_cameras mà không cần TCP socket check.
        """
        import time as _t
        result: Dict[str, bool] = {}
        try:
            edge_status = self.get_all_edge_status()
            for dev in edge_status.values():
                last_seen = dev.get("last_seen", 0)
                stale = (_t.time() - last_seen) > 60
                for cid, info in dev.get("camera_status", {}).items():
                    if not isinstance(info, dict):
                        result[cid] = False
                        continue
                    result[cid] = bool(info.get("is_active", False)) and not stale
        except Exception:
            pass
        return result

    # =========================================================================
    # SYSTEM COMMAND & SESSION STATE
    # =========================================================================
    def set_command(
        self, 
        command: str, 
        session_id: Optional[int] = None, 
        class_id: Optional[int] = None, 
        target_camera: Optional[str] = None
    ):
        """Cập nhật lệnh hệ thống và các ID liên quan."""
        pipe = self.redis.pipeline()
        pipe.set("state:command", command)
        
        if session_id is not None:
            pipe.set("state:session_id", session_id)
        else:
            pipe.delete("state:session_id")
            
        if class_id is not None:
            pipe.set("state:class_id", class_id)
        else:
            pipe.delete("state:class_id")
            
        if target_camera is not None:
            pipe.set("state:target_camera", target_camera)
        else:
            pipe.delete("state:target_camera")
            
        pipe.execute()

    def get_command_state(self) -> Dict[str, Any]:
        """Lấy trạng thái lệnh hiện tại."""
        pipe = self.redis.pipeline()
        pipe.get("state:command")
        pipe.get("state:session_id")
        pipe.get("state:class_id")
        pipe.get("state:target_camera")
        res = pipe.execute()
        
        session_id_val = None
        if res[1]:
            try:
                session_id_val = int(res[1])
            except ValueError:
                session_id_val = res[1].decode("utf-8")
                
        class_id_val = None
        if res[2]:
            try:
                class_id_val = int(res[2])
            except ValueError:
                class_id_val = res[2].decode("utf-8")
        
        return {
            "command": res[0].decode("utf-8") if res[0] else "STOP",
            "session_id": session_id_val,
            "class_id": class_id_val,
            "target_camera": res[3].decode("utf-8") if res[3] else None
        }

    # =========================================================================
    # LATEST FRAMES (Lưu trữ nhị phân có TTL)
    # =========================================================================
    def set_latest_frame(self, camera_id: str, frame_bytes: bytes, detections: List[Any]):
        """Lưu khung hình mới nhất kèm thông tin nhận diện (TTL 10 giây)."""
        pipe = self.redis.pipeline()
        # Lưu hình ảnh dưới dạng bytes trực tiếp
        pipe.setex(f"frame:{camera_id}:image", 10, frame_bytes)
        # Lưu JSON detections
        pipe.setex(f"frame:{camera_id}:detections", 10, json.dumps(detections).encode("utf-8"))
        pipe.execute()

    def get_latest_frame(self, camera_id: str) -> Tuple[Optional[bytes], List[Any]]:
        """Lấy khung hình và thông tin nhận diện mới nhất."""
        pipe = self.redis.pipeline()
        pipe.get(f"frame:{camera_id}:image")
        pipe.get(f"frame:{camera_id}:detections")
        res = pipe.execute()
        
        frame_bytes = res[0]
        try:
            detections = json.loads(res[1].decode("utf-8")) if res[1] else []
        except Exception:
            detections = []
            
        return frame_bytes, detections

    def get_available_cameras(self) -> List[str]:
        """Lấy danh sách các camera đang có frame."""
        keys = self.redis.keys("frame:*:image")
        return [k.decode("utf-8").split(":")[1] for k in keys]

    # =========================================================================
    # EDGE STATUS (Sử dụng Hash Structure)
    # =========================================================================
    def update_edge_status(self, device_name: str, status_data: Dict[str, Any]):
        """Cập nhật trạng thái heartbeat của Edge Box."""
        self.redis.hset("state:edge_status", device_name, json.dumps(status_data).encode("utf-8"))

    def get_all_edge_status(self) -> Dict[str, Dict[str, Any]]:
        """Lấy toàn bộ trạng thái các Edge Box."""
        raw_data = self.redis.hgetall("state:edge_status")
        result = {}
        for k, v in raw_data.items():
            try:
                result[k.decode("utf-8")] = json.loads(v.decode("utf-8"))
            except Exception:
                pass
        return result

    # =========================================================================
    # EMBEDDING VERSION
    # =========================================================================
    def increment_embedding_version(self) -> Tuple[int, str]:
        """Tăng version của embedding (dùng khi có update từ model)."""
        from datetime import datetime
        new_version = self.redis.incr("state:embedding_version")
        updated_at = datetime.now().isoformat()
        self.redis.set("state:embedding_updated_at", updated_at)
        return new_version, updated_at

    def get_embedding_version(self) -> Tuple[int, str]:
        """Lấy phiên bản embedding hiện tại."""
        pipe = self.redis.pipeline()
        pipe.get("state:embedding_version")
        pipe.get("state:embedding_updated_at")
        res = pipe.execute()
        
        v = int(res[0]) if res[0] else 0
        dt = res[1].decode("utf-8") if res[1] else ""
        return v, dt
        
    # =========================================================================
    # CLEANUP
    # =========================================================================
    def close(self):
        """Đóng kết nối."""
        if hasattr(self, "redis"):
            self.redis.close()

state_manager = RedisStateManager()
