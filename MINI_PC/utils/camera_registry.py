"""
utils/camera_registry.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unified CameraStream Registry — resolves RTSP resource contention.

Problem:
  • The attendance pipeline (CameraWorker in headless_processor.py) opens
    a CameraStream (drain thread) for each RTSP URL.
  • The enrollment page (CaptureWorker in enroll_page.py) then tries to open
    cv2.VideoCapture() for the *same* RTSP URL a second time.
  • Most IP cameras allow only one simultaneous H.264 stream per channel.
    The second open() either fails silently or steals the stream from the
    attendance thread, breaking both feeds.

Solution — Single Source of Truth:
  • This module maintains a process-wide registry: { source_url → CameraStream }.
  • acquire(source)  : returns the existing stream if alive, otherwise creates
                       and starts a new one.  Reference-counted.
  • release(source)  : decrements ref-count; stops the stream when it hits 0.
  • CaptureWorker and CameraWorker both call acquire() / release().
    They SHARE the same drain thread and always get the latest frame.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import threading
from typing import Optional
import numpy as np
from loguru import logger

from utils.camera_stream import CameraStream


class _StreamEntry:
    """Internal bookkeeping for one camera source."""
    def __init__(self, stream: CameraStream):
        self.stream   = stream
        self.ref_count = 1


class CameraRegistry:
    """
    Process-wide registry of active CameraStream instances.
    Thread-safe — safe to call from any thread (AI, UI, Enroll).
    """

    def __init__(self):
        self._lock    = threading.Lock()
        self._entries: dict[str, _StreamEntry] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def acquire(self, source: str) -> CameraStream:
        """
        Return the shared CameraStream for `source`, creating it if needed.
        Increments the reference count.

        Thread-safe.
        """
        with self._lock:
            entry = self._entries.get(source)
            if entry and entry.stream.is_connected or \
               entry and entry.stream._drain_thread and entry.stream._drain_thread.is_alive():
                entry.ref_count += 1
                logger.debug(
                    f"[Registry] Re-using stream '{source}' "
                    f"(ref={entry.ref_count})"
                )
                return entry.stream

            # Create a new CameraStream
            logger.info(f"[Registry] Creating new CameraStream for '{source}'")
            stream = CameraStream(source=source, camera_id=source)
            stream.start()
            self._entries[source] = _StreamEntry(stream)
            return stream

    def release(self, source: str) -> None:
        """
        Decrement reference count. Stop the stream when count reaches 0.

        Thread-safe.
        """
        with self._lock:
            entry = self._entries.get(source)
            if entry is None:
                return
            entry.ref_count -= 1
            logger.debug(
                f"[Registry] Released '{source}' (ref={entry.ref_count})"
            )
            if entry.ref_count <= 0:
                logger.info(
                    f"[Registry] ref=0 — stopping and removing stream '{source}'"
                )
                try:
                    entry.stream.stop()
                except Exception as exc:
                    logger.warning(f"[Registry] Error stopping stream: {exc}")
                del self._entries[source]

    def get(self, source: str) -> Optional[CameraStream]:
        """
        Return the stream for `source` without incrementing ref-count.
        Returns None if no stream is registered.
        """
        with self._lock:
            entry = self._entries.get(source)
            return entry.stream if entry else None

    def latest_frame(self, source: str) -> Optional[np.ndarray]:
        """
        Convenience: get the latest frame for `source` without
        touching ref-counts.  Returns None if not registered or no frame.
        """
        stream = self.get(source)
        return stream.latest_frame if stream else None

    @property
    def active_sources(self) -> list[str]:
        """List of sources currently in the registry."""
        with self._lock:
            return list(self._entries.keys())

    def stop_all(self) -> None:
        """Stop all streams and clear the registry (call on shutdown)."""
        with self._lock:
            for entry in self._entries.values():
                try:
                    entry.stream.stop()
                except Exception:
                    pass
            self._entries.clear()
        logger.info("[Registry] All streams stopped.")


# ── Singleton ─────────────────────────────────────────────────────────────
camera_registry = CameraRegistry()
