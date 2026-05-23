"""
patch_detect_loop.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fix 2 loi trong _detect_loop cua headless_processor.py:

LOI 1 (dong 204-206): khi _attendance_enabled=False,
  code clear _last_known_faces va continue -> khong detect -> mat box.
  Fix: Tach viec detect ra khoi dieu kien attendance.
  Detect van chay de hien thi box "Unknown" (xanh nhat).
  Chi skip gui len API khi attendance=False.

LOI 2 (dong 220-221): khi detected=0,
  clear _last_known_faces ngay lap tuc -> man hinh nhap nhay.
  Fix: Them stale-frame timeout 2s. Neu trong 2s lien tuc khong
  detect duoc gi moi clear, tranh nhap nhay.

DEBUG LOG: Them 1 dong log debug sau khi detect de xem
  detected=N | cache=M SV.

Chay: python patch_detect_loop.py
"""
from pathlib import Path
import re

TARGET = Path(__file__).parent / "headless_processor.py"
content = TARGET.read_bytes()

# ── Patch 1: Fix _detect_loop ─────────────────────────────────────────────
# Tim doan cu (CRLF)
OLD = (
    b"    def _detect_loop(self):\r\n"
    b"        \"\"\"THREAD 2: Detect Queue -> Detections -> Recognize Queue\"\"\"\r\n"
    b"        while not self._stop_event.is_set():\r\n"
    b"            try:\r\n"
    b"                frame, capture_time = self.detect_queue.get(timeout=0.5)\r\n"
    b"            except queue.Empty:\r\n"
    b"                continue\r\n"
    b"                \r\n"
    b"            if not self._active or not self._attendance_enabled:\r\n"
    b"                self._last_known_faces = []\r\n"
    b"                continue\r\n"
    b"\r\n"
    b"            try:\r\n"
    b"                detected = face_engine.detect_faces(frame)\r\n"
    b"                if detected:\r\n"
    b"                    # Gửi sang luồng Recognize\r\n"
    b"                    try:\r\n"
    b"                        if self.recognize_queue.full():\r\n"
    b"                            self.recognize_queue.get_nowait()\r\n"
    b"                        self.recognize_queue.put_nowait((frame, detected, capture_time))\r\n"
    b"                    except queue.Empty:\r\n"
    b"                        pass\r\n"
    b"                    except queue.Full:\r\n"
    b"                        pass\r\n"
    b"                else:\r\n"
    b"                    self._last_known_faces = []\r\n"
    b"            except Exception as e:\r\n"
    b"                logger.error(f\"\u274c Detect Loop Error [{self.camera_id}]: {e}\")\r\n"
)

NEW = (
    b"    def _detect_loop(self):\r\n"
    b"        \"\"\"THREAD 2: Detect Queue -> Detections -> Recognize Queue\"\"\"\r\n"
    b"        # [FIX] Stale-frame timeout: chi clear box sau N giay khong detect duoc\r\n"
    b"        _STALE_TIMEOUT_SEC = 2.0\r\n"
    b"        _last_detected_time = time.time()\r\n"
    b"\r\n"
    b"        while not self._stop_event.is_set():\r\n"
    b"            try:\r\n"
    b"                frame, capture_time = self.detect_queue.get(timeout=0.5)\r\n"
    b"            except queue.Empty:\r\n"
    b"                continue\r\n"
    b"\r\n"
    b"            # [FIX LOI 1] KHONG block detect khi attendance=False.\r\n"
    b"            # Detect van chay de hien thi bbox 'Unknown' cho nguoi dung biet camera dang hoat dong.\r\n"
    b"            # Chi skip khi camera hoan toan tat (_active=False va _is_previewing=False).\r\n"
    b"            if not self._active and not self._is_previewing:\r\n"
    b"                self._last_known_faces = []\r\n"
    b"                continue\r\n"
    b"\r\n"
    b"            try:\r\n"
    b"                detected = face_engine.detect_faces(frame)\r\n"
    b"\r\n"
    b"                # [DEBUG LOG] Xem AI co nhin thay khuon mat khong\r\n"
    b"                cache = edge_client.get_cache(self.camera_id)\r\n"
    b"                cache_size = cache.size if cache else 0\r\n"
    b"                logger.debug(\r\n"
    b"                    f\"[{self.camera_id}] detect={len(detected)} faces \"\r\n"
    b"                    f\"| cache={cache_size} SV \"\r\n"
    b"                    f\"| attendance={'ON' if self._attendance_enabled else 'OFF'}\"\r\n"
    b"                )\r\n"
    b"\r\n"
    b"                if detected:\r\n"
    b"                    _last_detected_time = time.time()  # Reset stale timer\r\n"
    b"                    # Gui sang luong Recognize\r\n"
    b"                    try:\r\n"
    b"                        if self.recognize_queue.full():\r\n"
    b"                            self.recognize_queue.get_nowait()\r\n"
    b"                        self.recognize_queue.put_nowait((frame, detected, capture_time))\r\n"
    b"                    except queue.Empty:\r\n"
    b"                        pass\r\n"
    b"                    except queue.Full:\r\n"
    b"                        pass\r\n"
    b"                else:\r\n"
    b"                    # [FIX LOI 2] Stale-frame: KHONG clear bbox ngay.\r\n"
    b"                    # Giu lai box cu trong _STALE_TIMEOUT_SEC de tranh nhap nhay.\r\n"
    b"                    if time.time() - _last_detected_time > _STALE_TIMEOUT_SEC:\r\n"
    b"                        self._last_known_faces = []\r\n"
    b"            except Exception as e:\r\n"
    b"                logger.error(f\"\u274c Detect Loop Error [{self.camera_id}]: {e}\")\r\n"
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    TARGET.write_bytes(content)
    print("=" * 60)
    print("OK  Patch thanh cong! 3 thay doi da ap dung:")
    print()
    print("  [LOI 1 FIXED] _detect_loop khong con block khi")
    print("    attendance_enabled=False. Gio detect chay lien tuc")
    print("    -> bbox 'Unknown' hien thi khi chua bat phien diem danh.")
    print()
    print("  [LOI 2 FIXED] Stale-frame 2s: bbox khong bi xoa ngay")
    print("    khi detected=0. Giu lai 2s tranh nhap nhay man hinh.")
    print()
    print("  [DEBUG ADDED] Log moi frame:")
    print("    [CAM_01] detect=1 faces | cache=47 SV | attendance=OFF")
    print("=" * 60)
    print()
    print("Cach doc log:")
    print("  detect=0  -> RetinaFace khong thay mat (anh sang, goc do)")
    print("  detect=1+ -> AI thay mat -> box phai hien, neu khong la loi draw")
    print("  cache=0   -> Chua sync embedding -> chay python force_sync.py")
else:
    print("=" * 60)
    print("WARN: Khong tim thay doan code target trong file.")
    print("      File co the da duoc patch hoac co thay doi khac.")
    print()
    print("Hay sua thu cong trong _detect_loop:")
    print("  1. Doi dieu kien:")
    print("     TU: if not self._active or not self._attendance_enabled:")
    print("     THANH: if not self._active and not self._is_previewing:")
    print()
    print("  2. Doi clear immediate:")
    print("     TU: else: self._last_known_faces = []")
    print("     THANH: if time.time() - _last_detected_time > 2.0:")
    print("                self._last_known_faces = []")
    print("=" * 60)
