"""
services/attendance_service.py
Logic nghiep vu diem danh — da nang cap voi Debug Logging day du.
"""
import time
import threading
import queue
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import ai_config, app_config
from database.repositories import session_repo, record_repo
from database.models import AttendanceSession
from services.face_engine import RecognitionResult
try:
    from services.anti_spoof_service import anti_spoof_service
except ImportError:
    anti_spoof_service = None


# ─────────────────────────────────────────────
@dataclass
class AttendanceEvent:
    student_id:    int
    student_code:  str
    full_name:     str
    class_id:      int
    class_name:    str
    class_code:    str
    check_in_time: datetime
    similarity:    float
    snapshot_path: Optional[str]
    session_id:    int

    @property
    def time_str(self) -> str:
        return self.check_in_time.strftime("%H:%M:%S")

    @property
    def score_pct(self) -> str:
        return f"{self.similarity * 100:.1f}%"


# ─────────────────────────────────────────────
class AttendanceService:

    def __init__(self):
        self._session_id:   Optional[int] = None
        self._session:      Optional[AttendanceSession] = None
        self._active:       bool = False

        self._cooldown_map: dict[int, float] = {}
        self._cooldown_lock = threading.Lock()

        self.on_attendance: Optional[Callable[[AttendanceEvent], None]] = None
        self.on_duplicate:  Optional[Callable[[str, float], None]] = None

        self._stats = {
            "total_recognized": 0,
            "total_recorded":   0,
            "total_duplicate":  0,
            "session_start":    None,
        }

        # Luong chay ngam de ghi Database
        self._db_queue      = queue.Queue()
        self._stop_worker   = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._db_worker, name="DB-Worker", daemon=True
        )
        self._worker_thread.start()

    # ─── DB Worker (TASK 2) ───────────────────

    def _db_worker(self):
        """
        Luong chay ngam: Lay du lieu tu hang doi va ghi vao SQL Server / O cung.
        KHONG DUOC de luong nay chet ngam — moi task deu duoc boc trong try/except.
        """
        logger.info("DB-Worker: Luong ghi DB da khoi dong.")
        while not self._stop_worker.is_set():
            try:
                task = self._db_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # Giu ref event de log neu except xay ra
            event = None
            try:
                event, frame_crop, camera_id = task
                snapshot_path = None

                # 1. Luu anh ra o cung (Async)
                if app_config.save_snapshots and frame_crop is not None:
                    try:
                        ts    = event.check_in_time.strftime("%H%M%S")
                        fname = f"{event.student_code}_{ts}_cam{camera_id}.jpg"
                        path  = app_config.snapshot_dir / fname
                        cv2.imwrite(str(path), frame_crop)
                        snapshot_path       = str(path)
                        event.snapshot_path = snapshot_path
                        logger.debug(f"DB-Worker: Luu snapshot -> {path}")
                    except Exception as snap_err:
                        logger.error(f"DB-Worker: Loi luu snapshot: {snap_err}")

                # 2. Ghi vao SQL Server qua SyncService
                from services.sync_service import sync_service
                logger.debug(
                    f"DB-Worker: Dang ghi student_id={event.student_id} "
                    f"('{event.full_name}') vao session_id={event.session_id}..."
                )
                success = sync_service.save_offline(
                    session_id=event.session_id,
                    student_id=event.student_id,
                    check_in_time=event.check_in_time,
                    score=event.similarity,
                    snapshot_path=snapshot_path,
                    camera_id=camera_id,
                )

                if success:
                    self._stats["total_recorded"] += 1
                    logger.success(
                        f"DB-Worker: [OK] Ghi thanh cong | "
                        f"student='{event.full_name}' (id={event.student_id}) | "
                        f"session={event.session_id} | score={event.similarity:.3f}"
                    )
                else:
                    # TASK 2: Log chi tiet nguyen nhan that bai
                    logger.error(
                        f"DB-Worker: [FAIL] GHI DB THAT BAI!\n"
                        f"  student_id  : {event.student_id} ('{event.full_name}')\n"
                        f"  session_id  : {event.session_id}\n"
                        f"  Nguyen nhan co the:\n"
                        f"  1) student_id={event.student_id} KHONG ton tai trong "
                        f"AttendanceRecords cua session {event.session_id} "
                        f"(prefill ABSENT co chay khong?).\n"
                        f"  2) session_id={event.session_id} da COMPLETED/PENDING "
                        f"(chua ACTIVE hoac da ket thuc).\n"
                        f"  3) Mat ket noi SQL Server -> kiem tra sync_service._do_sync.\n"
                        f"  4) SQLite buffer luu thanh cong nhung sync len Server that bai "
                        f"-> kiem tra log 'Dong bo that bai' cua SyncService."
                    )

            except Exception as worker_err:
                # CRITICAL: In full stack trace de debug ngay tren Terminal
                sid = getattr(event, "student_id", "?") if event else "?"
                logger.exception(
                    f"CRITICAL: Loi sap luong ghi DB (DB-Worker se TIEP TUC chay)! | "
                    f"student_id={sid} | loi: {worker_err}"
                )
                # KHONG re-raise: Worker phai tiep tuc song de xu ly task tiep theo

            finally:
                self._db_queue.task_done()

        logger.warning("DB-Worker: Luong ghi DB da DUNG (stop_worker duoc set).")

    # ─── Quan ly Session ──────────────────────

    def create_session(
        self,
        class_id:     int,
        subject_name: str,
        session_date: date = None,
    ) -> int:
        """Tao buoi diem danh moi, tao san records ABSENT cho toan bo lop."""
        sid = session_repo.create_session(
            class_id=class_id,
            subject_name=subject_name,
            session_date=session_date,
        )
        logger.info(f"Tao session: id={sid}, class={class_id}, subject={subject_name}")
        return sid

    def start_session(self, session_id: int) -> bool:
        """PENDING -> ACTIVE."""
        if self._active:
            logger.warning("Dang co buoi diem danh, ket thuc buoi cu truoc!")
            return False

        self._session = session_repo.get_by_id(session_id)
        if not self._session:
            logger.error(f"Khong tim thay session {session_id}")
            return False

        # ── QUAN TRỌNG: Xóa các record cũ chưa sync trước khi bắt đầu session mới
        # Ngăn chặn: _do_sync() đồng bộ nhầm data cũ vào session mới có cùng session_id
        try:
            from services.sync_service import sync_service as _sync
            _sync.flush_stale_records(session_id)
        except Exception as flush_err:
            logger.warning(f"Không thể flush stale records: {flush_err}")

        session_repo.start_session(session_id)
        self._session_id = session_id
        self._active     = True

        with self._cooldown_lock:
            self._cooldown_map.clear()
        self._reset_stats()

        logger.success(
            f"[START] Bắt đầu điểm danh: [{self._session.subject_name}] "
            f"| Session ID: {session_id}"
        )

        # Gui Telegram bat dau phien
        try:
            import sys
            from pathlib import Path
            root_dir = Path(__file__).parent.parent.parent
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))
            from telegram_notifier import send_telegram_msg
            import threading
            msg = (
                f"Bắt đầu phiên điểm danh:\n"
                f"Thời gian: {self._session.session_date}\n"
            )
            threading.Thread(target=send_telegram_msg, args=(msg,), daemon=True).start()
        except Exception as tg_err:
            logger.error(f"Loi gui Telegram: {tg_err}")

        return True

    def end_session(self) -> Optional[AttendanceSession]:
        """ACTIVE -> COMPLETED. Tra ve thong tin tong ket."""
        if not self._active or not self._session_id:
            logger.warning("Khong co buoi diem danh nao dang chay")
            return None

        # Doi cac task ghi DB chay ngam hoan tat truoc khi dong session
        logger.info("Dang cho cac ban ghi DB cuoi cung luu xong...")
        self._db_queue.join()

        ended_session_id = self._session_id
        session_repo.end_session(ended_session_id)
        session = session_repo.get_by_id(ended_session_id)
        self._active     = False
        self._session_id = None   # Reset rõ ràng để UI không poll nhầm session cũ
        self._session    = None

        logger.success(
            f"[STOP] Ket thuc diem danh | "
            f"Co mat: {session.present_count} | "
            f"Vang: {session.absent_count}"
        )

        # Gui Telegram ket thuc phien
        try:
            import sys
            from pathlib import Path
            root_dir = Path(__file__).parent.parent.parent
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))
            from telegram_notifier import send_telegram_msg
            import threading
            from database.repositories import record_repo

            report = record_repo.get_session_report(session.session_id)
            absent_by_class = {}
            for r in report:
                if r["status"] == "ABSENT":
                    c_name     = r["class_name"]
                    name_parts = r["full_name"].strip().split()
                    short_name = name_parts[-1] if name_parts else r["full_name"]
                    if c_name not in absent_by_class:
                        absent_by_class[c_name] = []
                    absent_by_class[c_name].append(short_name)

            if absent_by_class:
                for c_name, absent_list in absent_by_class.items():
                    # TASK 1: Sử dụng chuỗi f-string chuẩn có dấu tiếng Việt (UTF-8)
                    msg = f"Lớp {c_name} vắng: {', '.join(absent_list)}"
                    threading.Thread(target=send_telegram_msg, args=(msg,), daemon=True).start()
            else:
                msg = (
                    f"✅ Kết thúc phiên điểm danh môn {session.subject_name}.\n"
                    "Tất cả các lớp đều đi đủ!"
                )
                threading.Thread(target=send_telegram_msg, args=(msg,), daemon=True).start()

        except Exception as tg_err:
            logger.error(f"Loi gui Telegram khi ket thuc: {tg_err}")

        return session

    # ─── Xu ly ket qua nhan dien (TASK 1) ────

    def process_recognition(
        self,
        result:    RecognitionResult,
        frame:     Optional[np.ndarray] = None,
        camera_id: int = 1,
    ) -> Optional[AttendanceEvent]:
        """
        Xu ly ket qua nhan dien 1 khuon mat.
        TASK 1: Moi lenh return None deu duoc log ro rang de trace loi khong ghi DB.
        """
        # ── Chot chan 1: Chua Active ──────────────────────────────────────────
        if not self._active:
            logger.debug(
                "[SKIP] Phien diem danh CHUA ACTIVE "
                "(hay goi start_session() truoc). Ignore recognition result."
            )
            return None

        # ── Chot chan 2: Khong nhan dien duoc ────────────────────────────────
        if not result.recognized:
            return None   # Nguoi la — khong can log spam

        self._stats["total_recognized"] += 1
        student_id = result.student_id

        # ── Chot chan 3: Cooldown ──────────────────────────────────────────────
        remaining = self._get_cooldown_remaining(student_id)
        if remaining > 0:
            self._stats["total_duplicate"] += 1
            logger.debug(
                f"[SKIP] [{result.full_name}] dang trong COOLDOWN "
                f"— con {remaining:.0f}s "
                f"(cau hinh: {ai_config.attendance_cooldown_sec}s)"
            )
            if self.on_duplicate:
                try:
                    self.on_duplicate(result.full_name, remaining)
                except Exception:
                    pass
            return None

        # ── Chot chan 4: Anti-Spoofing ─────────────────────────────────────────
        if anti_spoof_service and anti_spoof_service.available:
            try:
                if frame is not None:
                    is_real, s_score = anti_spoof_service.is_real(frame, result.bbox)
                    result.is_real    = is_real
                    result.spoof_score = s_score
                    if not is_real:
                        logger.warning(
                            f"[SKIP] [{result.full_name}]: "
                            f"Phat hien GIA MAO (Anti-Spoofing) | "
                            f"spoof_score={s_score:.3f} "
                            f"(threshold={getattr(anti_spoof_service, 'threshold', '?')})"
                        )
                        return None
                else:
                    logger.debug(
                        "[Anti-Spoof] frame=None -> bo qua kiem tra "
                        "(khong co frame dau vao)."
                    )
            except Exception as spoof_err:
                logger.error(
                    f"[Anti-Spoof] Loi runtime [{result.full_name}]: {spoof_err} "
                    f"— Cho phep diem danh tiep tuc."
                )

        # Dat cooldown NGAY truoc khi tao event (tranh race condition)
        self._set_cooldown(student_id)

        # ── Chot chan 5: session_id None du _active=True (Bug guard) ──────────
        if not self._session_id:
            logger.error(
                f"[BUG] session_id = None du _active = True! "
                f"student='{result.full_name}' — "
                f"Kiem tra lai start_session()."
            )
            return None

        # ── Tao AttendanceEvent ───────────────────────────────────────────────
        event = AttendanceEvent(
            student_id=student_id,
            student_code=result.student_code,
            full_name=result.full_name,
            class_id=result.class_id,
            class_name=result.class_name or "",
            class_code=result.class_code or "",
            check_in_time=datetime.now(),
            similarity=result.similarity,
            snapshot_path=None,
            session_id=self._session_id,
        )

        # Cat anh tren RAM (viec ghi dia se do _db_worker thuc hien)
        face_crop = None
        if app_config.save_snapshots and frame is not None:
            try:
                x1, y1, x2, y2 = result.bbox
                pad = 20
                h, w = frame.shape[:2]
                x1 = max(0, x1 - pad);  y1 = max(0, y1 - pad)
                x2 = min(w, x2 + pad);  y2 = min(h, y2 + pad)
                face_crop = frame[y1:y2, x1:x2].copy()
            except Exception as crop_err:
                logger.error(f"Loi khi cat anh: {crop_err}")

        # Day vao queue de luong phu luu DB va anh
        self._db_queue.put((event, face_crop, camera_id))
        logger.debug(
            f"DB-Queue: Da day vao hang doi "
            f"(queue_size={self._db_queue.qsize()}) | student='{event.full_name}'"
        )

        logger.success(
            f"[ATTEND] [{result.student_code}] {result.full_name} "
            f"| Score: {result.similarity:.3f} "
            f"| Session: {self._session_id} "
            f"| Time: {event.time_str}"
        )

        if self.on_attendance:
            try:
                self.on_attendance(event)
            except Exception as cb_err:
                logger.error(f"on_attendance callback error: {cb_err}")

        return event

    def process_frame_results(
        self,
        results:   list[RecognitionResult],
        frame:     Optional[np.ndarray] = None,
        camera_id: int = 1,
    ) -> list[AttendanceEvent]:
        events = []
        for result in results:
            event = self.process_recognition(result, frame, camera_id)
            if event:
                events.append(event)
        return events

    # ─── Cooldown ─────────────────────────────

    def _get_cooldown_remaining(self, student_id: int) -> float:
        with self._cooldown_lock:
            last_time = self._cooldown_map.get(student_id, 0)
            elapsed   = time.time() - last_time
            return max(0.0, ai_config.attendance_cooldown_sec - elapsed)

    def _set_cooldown(self, student_id: int):
        with self._cooldown_lock:
            self._cooldown_map[student_id] = time.time()

    def reset_cooldown(self, student_id: int = None):
        with self._cooldown_lock:
            if student_id:
                self._cooldown_map.pop(student_id, None)
            else:
                self._cooldown_map.clear()
        logger.info(
            f"Reset cooldown: {'tat ca' if not student_id else f'student {student_id}'}"
        )

    # ─── Truy van trang thai ──────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    @property
    def current_session(self) -> Optional[AttendanceSession]:
        return self._session

    def get_present_list(self) -> list[dict]:
        if not self._session_id:
            return []
        return record_repo.get_present_list(self._session_id)

    def get_stats(self) -> dict:
        stats = self._stats.copy()
        if stats["session_start"]:
            elapsed = (datetime.now() - stats["session_start"]).seconds
            stats["elapsed_sec"] = elapsed
            stats["elapsed_str"] = f"{elapsed//60:02d}:{elapsed%60:02d}"
        stats["session_id"]  = self._session_id
        stats["is_active"]   = self._active
        with self._cooldown_lock:
            stats["in_cooldown"] = len(self._cooldown_map)
        return stats

    def _reset_stats(self):
        self._stats = {
            "total_recognized": 0,
            "total_recorded":   0,
            "total_duplicate":  0,
            "session_start":    datetime.now(),
        }


# ─────────────────────────────────────────────
attendance_service = AttendanceService()