"""
services/frame_processor.py
Kết nối Camera → AI Engine → Attendance Service

Chạy trong QThread riêng (để UI không bị freeze).
Liên tục:
  1. Lấy frame từ CameraManager
  2. Đưa qua FaceEngine để nhận diện
  3. Phát signal lên UI để hiển thị
  4. Gửi kết quả đến AttendanceService để ghi DB
"""
import time
import threading
from typing import Optional, Callable
import numpy as np
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.face_engine import face_engine, RecognitionResult
from services.camera_manager import camera_manager, CameraStatus
from services.embedding_cache_manager import cache_manager


class FrameProcessor:
    """
    Vòng lặp xử lý frame — chạy trong thread riêng.
    Kết nối CameraManager → FaceEngine → callbacks.

    Sử dụng:
        processor = FrameProcessor(camera_id=1)
        processor.on_result = lambda results, frame: ...
        processor.start()
        ...
        processor.stop()
    """

    def __init__(self, camera_id: int = 1):
        self.camera_id   = camera_id
        self._running    = False
        self._thread: Optional[threading.Thread] = None
        self._pause      = False

        # Callbacks — gán từ bên ngoài
        self.on_result:  Optional[Callable] = None  # (results, annotated_frame, elapsed_ms)
        self.on_frame:   Optional[Callable] = None  # (raw_frame) — để UI hiển thị live

        # Thống kê
        self._proc_count  = 0
        self._recog_count = 0
        self._last_fps    = 0.0
        self._fps_timer   = time.time()
        self._fps_frames  = 0

    # ─── Start / Stop / Pause ─────────────────

    def start(self):
        if self._running:
            return
        if not face_engine.is_ready:
            logger.error("FaceEngine chưa load model — không thể start FrameProcessor")
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            name=f"FrameProc-cam{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"FrameProcessor started (camera_id={self.camera_id})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info(f"FrameProcessor stopped (camera_id={self.camera_id})")

    def pause(self):
        """Tạm dừng xử lý (nhưng camera vẫn capture)."""
        self._pause = True

    def resume(self):
        self._pause = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ─── Vòng lặp chính ───────────────────────

    def _loop(self):
        logger.info(f"FrameProcessor loop started")
        no_frame_count = 0

        while self._running:
            # Lấy frame từ camera
            frame = camera_manager.get_frame(self.camera_id)

            if frame is None:
                no_frame_count += 1
                if no_frame_count % 50 == 0:
                    status = camera_manager.get_status(self.camera_id)
                    logger.debug(f"Chờ frame từ camera {self.camera_id} "
                                 f"(status={status}, count={no_frame_count})")
                time.sleep(0.02)   # 20ms wait
                continue

            no_frame_count = 0

            # Gửi raw frame lên UI để hiển thị live (không cần xử lý AI)
            if self.on_frame:
                try:
                    self.on_frame(frame)
                except Exception as e:
                    logger.error(f"on_frame callback error: {e}")

            # Tạm dừng AI processing nếu cần
            if self._pause:
                time.sleep(0.03)
                continue

            # Lấy cache embeddings
            cache = cache_manager.get_cache()

            # Nếu cache rỗng thì không cần xử lý AI
            if cache.is_empty:
                time.sleep(0.05)
                continue

            # ── AI Processing ──
            results, elapsed_ms = face_engine.process_frame(frame, cache)

            # Cập nhật thống kê
            self._proc_count  += 1
            self._recog_count += sum(1 for r in results if r.recognized)
            self._update_fps()

            if results:
                logger.debug(
                    f"cam={self.camera_id} | "
                    f"{len(results)} face(s) | "
                    f"{sum(1 for r in results if r.recognized)} recognized | "
                    f"{elapsed_ms:.1f}ms"
                )

            # Vẽ kết quả lên frame
            annotated = face_engine.draw_results(frame, results, elapsed_ms)

            # Gửi kết quả lên UI/AttendanceService
            if self.on_result and results:
                try:
                    self.on_result(results, annotated, elapsed_ms)
                except Exception as e:
                    logger.error(f"on_result callback error: {e}")
            elif self.on_result:
                # Gửi frame annotated dù không có khuôn mặt
                try:
                    self.on_result([], annotated, elapsed_ms)
                except Exception as e:
                    logger.error(f"on_result callback error: {e}")

        logger.info("FrameProcessor loop ended")

    def _update_fps(self):
        self._fps_frames += 1
        now = time.time()
        elapsed = now - self._fps_timer
        if elapsed >= 2.0:
            self._last_fps   = self._fps_frames / elapsed
            self._fps_frames = 0
            self._fps_timer  = now

    def get_stats(self) -> dict:
        return {
            "camera_id":    self.camera_id,
            "processed":    self._proc_count,
            "recognized":   self._recog_count,
            "fps":          round(self._last_fps, 1),
            "is_running":   self._running,
            "is_paused":    self._pause,
        }
