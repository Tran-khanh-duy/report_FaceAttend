"""
utils/camera_stream.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Non-blocking, Zero-Delay RTSP Camera Reader  (v2.1 — Latency Fix)

Architecture (2-thread per camera):
  Thread-A (Drain) : cv2.VideoCapture() + cap.read() as fast as possible.
                     Keeps ONLY the latest frame → drains OpenCV buffer.
                     Explicitly drops all buffered stale frames on start.
  Thread-B (caller): grabs latest_frame at any time — always gets real-time.

Key Changes vs v1:
  1. Frame-age guard   : If the frame is older than `max_frame_age_sec` it is
                         discarded so downstream always sees fresh data.
  2. FPS telemetry     : `drain_fps` property for live monitoring / Watchdog.
  3. Buffer flush      : On every reconnect the first N frames are dropped to
                         purge any frames already queued inside the driver.
  4. Thread-safe stop  : stop() issues Event + join with graceful timeout.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import threading
import time
import cv2
import numpy as np
from loguru import logger
from typing import Optional


class CameraStream:
    """
    Thread-safe, non-blocking RTSP/USB camera reader with zero-delay guarantee.

    Usage:
        stream = CameraStream("rtsp://...", camera_id="CAM_01")
        stream.start()

        frame = stream.latest_frame        # None if not yet connected
        ok    = stream.is_connected
        fps   = stream.drain_fps           # actual read-rate of drain thread

        stream.stop()
    """

    # Reconnect backoff ceiling (seconds)
    _MAX_RETRY_DELAY  = 30.0
    # OpenCV connection timeouts
    _OPEN_TIMEOUT_MS  = 8_000
    _READ_TIMEOUT_MS  = 5_000
    # How many frames to discard right after (re)connect to flush driver buffer
    _FLUSH_FRAMES     = 5

    def __init__(
        self,
        source: str,
        camera_id: str = "CAM",
        *,
        buffersize: int = 1,
        max_frame_age_sec: float = 2.0,
    ):
        self.source           = source
        self.camera_id        = camera_id
        self._buffersize      = buffersize
        # Frames older than this will be replaced with None so the AI loop
        # detects a stale-feed condition rather than processing a ghost frame.
        self._max_frame_age   = max_frame_age_sec

        # ── Shared state (all access via _lock) ──────────────────────────
        self._lock            = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._connected       = False
        self._frame_count     = 0          # total frames successfully read
        self._last_frame_t    = 0.0        # epoch of latest frame
        self._frames_dropped  = 0          # frames discarded (age guard)

        # FPS tracking (drain thread updates every second)
        self._drain_fps       = 0.0
        self._fps_count       = 0
        self._fps_ts          = 0.0

        # ── Control ───────────────────────────────────────────────────────
        self._stop_event      = threading.Event()
        self._drain_thread: Optional[threading.Thread] = None

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def start(self) -> "CameraStream":
        """Start the drain thread (non-blocking, daemon)."""
        self._stop_event.clear()
        self._drain_thread = threading.Thread(
            target=self._drain_loop,
            name=f"Drain-{self.camera_id}",
            daemon=True,
        )
        self._drain_thread.start()
        return self

    def stop(self):
        """Signal drain thread to stop and wait for clean exit."""
        self._stop_event.set()
        if self._drain_thread and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=5.0)
        with self._lock:
            self._latest_frame = None
            self._connected    = False

    @property
    def latest_frame(self) -> Optional[np.ndarray]:
        """
        Return the latest frame (thread-safe).

        Returns None if:
          • Not yet connected, OR
          • Frame age exceeds max_frame_age_sec (stale-feed guard).
        """
        with self._lock:
            if self._latest_frame is None:
                return None
            age = time.time() - self._last_frame_t
            if age > self._max_frame_age:
                # Frame is stale — signal caller without returning old data
                self._frames_dropped += 1
                return None
            return self._latest_frame

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def frame_count(self) -> int:
        """Total frames successfully read (for debug / FPS calculation)."""
        with self._lock:
            return self._frame_count

    @property
    def drain_fps(self) -> float:
        """Actual frame-read rate of the drain thread (frames per second)."""
        with self._lock:
            return self._drain_fps

    @property
    def frames_dropped(self) -> int:
        """Frames discarded by the age guard."""
        with self._lock:
            return self._frames_dropped

    @property
    def last_frame_age(self) -> float:
        """Seconds since the last successfully received frame."""
        with self._lock:
            if self._last_frame_t == 0.0:
                return float("inf")
            return time.time() - self._last_frame_t

    def clear_frame(self):
        """Force-clear current frame (e.g., on connection loss signal)."""
        with self._lock:
            self._latest_frame = None

    # ─────────────────────────────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────────────────────────────

    def _open_cap(self) -> Optional[cv2.VideoCapture]:
        """
        Open VideoCapture with appropriate timeout.
        Runs entirely inside drain thread — NEVER blocks main thread.
        """
        source = self.source
        try:
            try:
                idx = int(source)
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            except ValueError:
                cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._OPEN_TIMEOUT_MS)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC,  self._READ_TIMEOUT_MS)

            if cap.isOpened():
                # Keep internal driver buffer at 1 frame to prevent accumulation
                cap.set(cv2.CAP_PROP_BUFFERSIZE, self._buffersize)
                logger.success(
                    f"[CameraStream] [{self.camera_id}] Connected: {source}"
                )
                return cap
            else:
                cap.release()
                return None

        except Exception as exc:
            logger.warning(
                f"[CameraStream] [{self.camera_id}] _open_cap error: {exc}"
            )
            return None

    def _drain_loop(self):
        """
        THREAD A — Drain Loop.

        1. Call _open_cap() (timeout-guarded) in this thread.
        2. Flush the first _FLUSH_FRAMES frames to purge driver-internal buffer.
        3. Continuously cap.read() AS FAST AS POSSIBLE to drain the RTSP buffer.
           ──► NO sleep() here ◄──  The caller (AI loop) throttles itself.
        4. Update self._latest_frame on every successful read (overwrite-only).
        5. If cap.read() fails → set connected=False, clear frame, reconnect
           with exponential back-off.
        """
        retry_delay = 2.0

        while not self._stop_event.is_set():
            # ── Phase 1: Connect ──────────────────────────────────────────
            logger.info(
                f"[CameraStream] [{self.camera_id}] Connecting... "
                f"(retry in {retry_delay:.0f}s on failure)"
            )
            cap = self._open_cap()

            if cap is None:
                with self._lock:
                    self._connected    = False
                    self._latest_frame = None

                logger.warning(
                    f"[CameraStream] [{self.camera_id}] Connection failed. "
                    f"Retry in {retry_delay:.0f}s..."
                )
                self._stop_event.wait(timeout=retry_delay)
                retry_delay = min(retry_delay * 2, self._MAX_RETRY_DELAY)
                continue

            # Connection succeeded → reset backoff
            retry_delay = 2.0
            with self._lock:
                self._connected = True

            # ── Phase 2: Flush driver-internal buffer ─────────────────────
            # RTSP drivers often queue several seconds of frames at connect
            # time.  Discard the first N frames to ensure real-time starts
            # immediately rather than replaying old buffered footage.
            for _ in range(self._FLUSH_FRAMES):
                if self._stop_event.is_set():
                    break
                cap.grab()          # grab() is faster than read() — no decode

            # Reset FPS tracker
            with self._lock:
                self._fps_count = 0
                self._fps_ts    = time.time()

            # ── Phase 3: Drain loop ────────────────────────────────────────
            consecutive_failures = 0
            while not self._stop_event.is_set():
                ret, frame = cap.read()

                if not ret or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        logger.warning(
                            f"[CameraStream] [{self.camera_id}] "
                            f"Signal lost ({consecutive_failures} consecutive failures). "
                            f"Reconnecting..."
                        )
                        break   # Exit drain loop → back to reconnect
                    time.sleep(0.05)
                    continue

                # Reset failure counter on success
                consecutive_failures = 0
                now = time.time()

                # Update latest frame — overwrite; callers always see newest
                with self._lock:
                    self._latest_frame = frame
                    self._connected    = True
                    self._frame_count += 1
                    self._last_frame_t = now

                    # FPS telemetry (update every 1 second)
                    self._fps_count += 1
                    elapsed = now - self._fps_ts
                    if elapsed >= 1.0:
                        self._drain_fps = self._fps_count / elapsed
                        self._fps_count = 0
                        self._fps_ts    = now

                # ──► NO sleep here ◄──
                # This loop runs as fast as the hardware allows (~25-30 FPS
                # for RTSP) to keep the buffer drained at all times.

            # Exited drain loop → cleanup
            cap.release()
            with self._lock:
                self._connected    = False
                self._latest_frame = None   # Clear stale frame immediately

            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
