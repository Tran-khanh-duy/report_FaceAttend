"""
services/camera_manager.py
"""
import cv2
import time
import threading
import queue
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable
import numpy as np
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import camera_config


# ─────────────────────────────────────────────
#  Trạng thái camera
# ─────────────────────────────────────────────
class CameraStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    ERROR        = "error"
    STOPPED      = "stopped"


@dataclass
class CameraInfo:
    camera_id:   int
    name:        str
    source:      str              # "0" hoặc "rtsp://..."
    floor:       int  = 0
    status:      CameraStatus = CameraStatus.DISCONNECTED
    fps_actual:  float = 0.0
    frame_count: int   = 0
    error_msg:   str   = ""
    last_frame_time: float = 0.0

    @property
    def is_ip_camera(self) -> bool:
        return str(self.source).startswith(("rtsp://", "http://", "rtmp://"))

    @property
    def source_display(self) -> str:
        if self.is_ip_camera:
            import re
            masked = re.sub(
                r'(://[^:@/]+):[^@/]+@',   # ://user:PASS@ → ://user:***@
                r'\1:***@',
                self.source,
            )
            return masked
        return f"USB Camera #{self.source}"


# ─────────────────────────────────────────────
#  CameraThread — 1 thread cho 1 camera
# ─────────────────────────────────────────────
class CameraThread(threading.Thread):
    """
    Thread chạy nền để capture frame từ 1 camera.
    Frame mới nhất luôn sẵn sàng — UI thread lấy bất cứ lúc nào.
    """

    def __init__(
        self,
        info: CameraInfo,
        on_frame: Optional[Callable] = None,      # callback(camera_id, frame)
        on_status: Optional[Callable] = None,     # callback(camera_id, status)
    ):
        super().__init__(name=f"Camera-{info.camera_id}", daemon=True)
        self.info       = info
        self._on_frame  = on_frame
        self._on_status = on_status

        # Frame buffer — chỉ giữ frame mới nhất (queue size=1)
        self._frame_queue = queue.Queue(maxsize=1)

        self._stop_event = threading.Event()
        self._cap: Optional[cv2.VideoCapture] = None

        # FPS tracking
        self._fps_counter = 0
        self._fps_timer   = time.time()

    # ─── Public API ───────────────────────────

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Lấy frame mới nhất — non-blocking."""
        try:
            return self._frame_queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        """Dừng thread an toàn."""
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and self.is_alive()

    # ─── Thread logic ─────────────────────────

    def run(self):
        logger.info(f"[{self.info.name}] Thread started | source={self.info.source_display}")
        retry_count = 0

        while not self._stop_event.is_set():
            # Kết nối
            self._set_status(CameraStatus.CONNECTING)
            connected = self._connect()

            if not connected:
                retry_count += 1
                if retry_count > camera_config.max_reconnect_tries:
                    self._set_status(CameraStatus.ERROR,
                                     f"Thất bại sau {retry_count} lần thử")
                    logger.error(f"[{self.info.name}] Từ bỏ kết nối sau {retry_count} lần")
                    break
                wait = camera_config.reconnect_delay_sec * min(retry_count, 5)
                logger.warning(f"[{self.info.name}] Kết nối thất bại, thử lại sau {wait}s "
                                f"(lần {retry_count}/{camera_config.max_reconnect_tries})")
                self._stop_event.wait(wait)
                continue

            # Capture loop
            self._set_status(CameraStatus.CONNECTED)
            retry_count = 0
            logger.success(f"[{self.info.name}] Kết nối thành công!")
            self._capture_loop()

            # Ra khỏi capture loop → mất kết nối
            if not self._stop_event.is_set():
                logger.warning(f"[{self.info.name}] Mất kết nối — thử kết nối lại...")
                self._set_status(CameraStatus.DISCONNECTED)
                self._release()
                self._stop_event.wait(camera_config.reconnect_delay_sec)

        self._release()
        self._set_status(CameraStatus.STOPPED)
        logger.info(f"[{self.info.name}] Thread stopped")

    def _connect(self) -> bool:
        """Mở kết nối tới camera."""
        try:
            source = self.info.source
            if str(source).isdigit():
                source = int(source)

            self._cap = cv2.VideoCapture(source)

            if not self._cap.isOpened():
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  camera_config.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config.height)
            self._cap.set(cv2.CAP_PROP_FPS, camera_config.fps)

            # NÂNG CẤP: Tối ưu buffer size = 1 để giảm độ trễ cho RTSP
            if self.info.is_ip_camera:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            ret, _ = self._cap.read()
            return ret

        except Exception as e:
            logger.error(f"[{self.info.name}] Lỗi kết nối: {e}")
            return False

    def _capture_loop(self):
        """Vòng lặp đọc frame liên tục."""
        frame_skip = 0

        while not self._stop_event.is_set():
            if not self._cap or not self._cap.isOpened():
                break

            ret, frame = self._cap.read()
            if not ret:
                logger.debug(f"[{self.info.name}] Đọc frame thất bại")
                break

            self.info.frame_count  += 1
            self.info.last_frame_time = time.time()
            self._update_fps()

            # Frame skip: xử lý 1 trong N frames
            frame_skip += 1
            if frame_skip < camera_config.process_every_n_frames:
                continue
            frame_skip = 0

            # Cập nhật frame mới nhất vào queue (luôn xoá frame cũ trước)
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self._frame_queue.put_nowait(frame)

            if self._on_frame:
                try:
                    self._on_frame(self.info.camera_id, frame)
                except Exception as e:
                    logger.error(f"[{self.info.name}] Callback error: {e}")

    def _update_fps(self):
        self._fps_counter += 1
        now = time.time()
        elapsed = now - self._fps_timer
        if elapsed >= 2.0:
            self.info.fps_actual = self._fps_counter / elapsed
            self._fps_counter = 0
            self._fps_timer   = now

    def _set_status(self, status: CameraStatus, error_msg: str = ""):
        self.info.status    = status
        self.info.error_msg = error_msg
        if self._on_status:
            try:
                self._on_status(self.info.camera_id, status)
            except Exception:
                pass

    def _release(self):
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None


# ─────────────────────────────────────────────
#  CameraManager — quản lý nhiều camera
# ─────────────────────────────────────────────
class CameraManager:
    """
    Quản lý nhiều camera song song.
    Thread-safe — gọi từ UI thread an toàn.
    """

    def __init__(self):
        self._threads: dict[int, CameraThread] = {}
        self._lock    = threading.Lock()
        self._status_callbacks: list[Callable] = []

    # ─── Thêm / xoá camera ────────────────────

    def add_camera(
        self,
        camera_id: int,
        name:      str,
        source:    str,
        floor:     int = 0,
    ) -> bool:
        with self._lock:
            if camera_id in self._threads:
                logger.warning(f"Camera {camera_id} đã tồn tại — bỏ qua")
                return False

            info = CameraInfo(
                camera_id=camera_id,
                name=name,
                source=str(source),
                floor=floor,
            )
            thread = CameraThread(
                info=info,
                on_status=self._handle_status_change,
            )
            self._threads[camera_id] = thread
            logger.info(f"Đã thêm camera: [{camera_id}] {name} | {info.source_display}")
            return True

    def add_camera_from_config(self, cam_dict: dict) -> bool:
        return self.add_camera(
            camera_id=cam_dict["id"],
            name=cam_dict["name"],
            source=cam_dict["source"],
            floor=cam_dict.get("floor", 0),
        )

    def remove_camera(self, camera_id: int):
        with self._lock:
            thread = self._threads.pop(camera_id, None)
        if thread:
            thread.stop()
            thread.join(timeout=3)
            logger.info(f"Đã xoá camera {camera_id}")

    # ─── Start / Stop ─────────────────────────

    def start_camera(self, camera_id: int) -> bool:
        with self._lock:
            thread = self._threads.get(camera_id)
        if not thread:
            logger.error(f"Camera {camera_id} không tồn tại")
            return False
        if thread.is_alive():
            logger.warning(f"Camera {camera_id} đã đang chạy")
            return True
        thread.start()
        logger.info(f"Đã start camera {camera_id}")
        return True

    def start_all(self):
        with self._lock:
            ids = list(self._threads.keys())
        for cid in ids:
            self.start_camera(cid)
        logger.info(f"Đã start {len(ids)} camera(s)")

    def stop_camera(self, camera_id: int):
        with self._lock:
            thread = self._threads.get(camera_id)
        if thread:
            thread.stop()
            thread.join(timeout=3)
            logger.info(f"Đã stop camera {camera_id}")

    def stop_all(self):
        with self._lock:
            threads = list(self._threads.values())
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=3)
        logger.info("Đã stop tất cả camera")

    # ─── Lấy frame ────────────────────────────

    def get_frame(self, camera_id: int) -> Optional[np.ndarray]:
        with self._lock:
            thread = self._threads.get(camera_id)
        if thread:
            return thread.get_latest_frame()
        return None

    def get_frame_any(self) -> tuple[int, Optional[np.ndarray]]:
        with self._lock:
            threads = list(self._threads.items())
        for cid, thread in threads:
            if thread.info.status == CameraStatus.CONNECTED:
                frame = thread.get_latest_frame()
                if frame is not None:
                    return cid, frame
        return -1, None

    # ─── Thông tin trạng thái ─────────────────

    def get_status(self, camera_id: int) -> Optional[CameraStatus]:
        with self._lock:
            thread = self._threads.get(camera_id)
        return thread.info.status if thread else None

    def get_all_info(self) -> list[CameraInfo]:
        with self._lock:
            return [t.info for t in self._threads.values()]

    def get_connected_count(self) -> int:
        with self._lock:
            return sum(
                1 for t in self._threads.values()
                if t.info.status == CameraStatus.CONNECTED
            )

    @property
    def camera_ids(self) -> list[int]:
        with self._lock:
            return list(self._threads.keys())

    def on_status_change(self, callback: Callable):
        self._status_callbacks.append(callback)

    def _handle_status_change(self, camera_id: int, status: CameraStatus):
        for cb in self._status_callbacks:
            try:
                cb(camera_id, status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    # ─── Snapshot ─────────────────────────────

    def capture_snapshot(
        self,
        camera_id: int,
        save_path: str,
        frame: Optional[np.ndarray] = None,
    ) -> bool:
        if frame is None:
            frame = self.get_frame(camera_id)
        if frame is None:
            logger.warning(f"Không có frame để snapshot (camera {camera_id})")
            return False
        try:
            cv2.imwrite(save_path, frame)
            logger.debug(f"Snapshot saved: {save_path}")
            return True
        except Exception as e:
            logger.error(f"Lỗi lưu snapshot: {e}")
            return False


# ─────────────────────────────────────────────
#  Singleton instance
# ─────────────────────────────────────────────
camera_manager = CameraManager()