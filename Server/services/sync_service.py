import sqlite3
import threading
import time
from datetime import datetime
from loguru import logger
from pathlib import Path
import sys

# Ensure root in path for relative imports
ROOT = Path(__file__).parent.parent
# if str(ROOT) not in sys.path:
#     sys.path.insert(0, str(ROOT))

from database.repositories import record_repo
from core.config import BASE_DIR

class SyncService:
    """
    Dịch vụ đồng bộ dữ liệu (Edge Processing).
    Lưu trữ tạm thời vào SQLite khi mất kết nối hoặc để giảm độ trễ,
    sau đó tự động đẩy lên SQL Server khi có mạng.
    """
    def __init__(self):
        self.db_path = BASE_DIR / "database" / "local_buffer.db"
        self._init_db()
        self._stop_event = threading.Event()
        self._sync_thread = threading.Thread(target=self._sync_loop, name="Sync-Thread", daemon=True)
        self._sync_thread.start()

    def _init_db(self):
        """Khởi tạo cấu trúc bảng SQLite cục bộ."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS offline_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER,
                        student_id INTEGER,
                        check_in_time TEXT,
                        recognition_score REAL,
                        snapshot_path TEXT,
                        camera_id INTEGER,
                        synced INTEGER DEFAULT 0
                    )
                """)
            logger.info(f"Đã khởi tạo DB local buffer: {self.db_path}")
        except Exception as e:
            logger.error(f"Lỗi khởi tạo DB local: {e}")

    def save_offline(self, session_id, student_id, check_in_time, score, snapshot_path, camera_id):
        """Lưu bản ghi vào DB cục bộ ngay lập tức."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO offline_records 
                    (session_id, student_id, check_in_time, recognition_score, snapshot_path, camera_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    session_id, 
                    student_id, 
                    check_in_time.isoformat() if hasattr(check_in_time, "isoformat") else str(check_in_time), 
                    score, 
                    snapshot_path, 
                    camera_id
                ))
            logger.info(f"➜ [OFFLINE] Đã lưu đệm student_id={student_id} vào SQLite")
            return True
        except Exception as e:
            logger.error(f"Lỗi lưu offline record: {e}")
            return False

    def _sync_loop(self):
        """Vòng lặp chạy ngầm kiểm tra và đồng bộ."""
        # Chờ hệ thống khởi động ổn định
        time.sleep(5)
        while not self._stop_event.is_set():
            try:
                self._do_sync()
            except Exception as e:
                logger.debug(f"Chu kỳ sync tạm dừng (mất mạng?): {e}")
            
            # Kiểm tra mỗi 30 giây
            for _ in range(30):
                if self._stop_event.is_set(): break
                time.sleep(1)

    def _do_sync(self):
        """Thuc hien day du lieu tu SQLite len SQL Server."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, session_id, student_id, check_in_time, "
                "recognition_score, snapshot_path, camera_id "
                "FROM offline_records WHERE synced = 0"
            ).fetchall()

            if not rows:
                return

            logger.info(f"[SYNC] Dang dong bo {len(rows)} ban ghi len Server...")

            for row in rows:
                rid, sid, stid, ts_str, score, path, cam = row

                try:
                    ts = datetime.fromisoformat(ts_str)

                    # TASK 3 FIX: record_repo.upsert() tra ve int (record_id hoac -1),
                    # KHONG phai bool. Gia tri -1 la truthy trong Python!
                    # => Phai kiem tra record_id >= 0 thay vi 'if success'.
                    record_id = record_repo.upsert(
                        session_id=sid,
                        student_id=stid,
                        status="PRESENT",
                        check_in_time=ts,
                        recognition_score=score,
                        camera_id=cam,
                    )

                    if record_id >= 0:
                        # Danh dau da dong bo trong SQLite buffer
                        conn.execute(
                            "UPDATE offline_records SET synced = 1 WHERE id = ?",
                            (rid,)
                        )
                        conn.commit()
                        logger.success(
                            f"[SYNC] OK: student_id={stid} | "
                            f"session={sid} | record_id={record_id}"
                        )
                    else:
                        # TASK 3: record_id = -1 = upsert that bai
                        # Nguyen nhan hang dau: student_id khong co trong
                        # AttendanceRecords cua session nay (prefill ABSENT that bai).
                        logger.error(
                            f"[SYNC] FAIL: upsert tra ve -1 (rowcount=0)!\n"
                            f"  student_id : {stid}\n"
                            f"  session_id : {sid}\n"
                            f"  Kiem tra: SELECT * FROM AttendanceRecords "
                            f"WHERE session_id={sid} AND student_id={stid}\n"
                            f"  Neu khong co dong nao: hoc vien nay KHONG thuoc "
                            f"session {sid} (prefill ABSENT chua chay hoac "
                            f"class_id cua hoc vien khac voi class_id cua session)."
                        )
                        # Khong break: thu dong bo cac ban ghi khac
                        # (ban ghi nay se thu lai o chu ky sau)

                except Exception as sync_err:
                    logger.error(f"[SYNC] Loi khi dong bo dong {rid}: {sync_err}")
                    break  # Dung batch, doi chu ky sau


    def flush_stale_records(self, current_session_id: int):
        """
        Xóa toàn bộ records chưa sync (synced=0) KHÔNG thuộc session hiện tại.
        Gọi ngay khi bắt đầu session mới để tránh dữ liệu cũ bị sync nhầm vào.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                result = conn.execute(
                    "DELETE FROM offline_records WHERE synced = 0 AND session_id != ?",
                    (current_session_id,)
                )
                conn.commit()
                deleted = result.rowcount
                if deleted > 0:
                    logger.warning(
                        f"[SYNC] flush_stale_records: Đã xóa {deleted} record cũ (synced=0) "
                        f"từ các session khác. Tránh bị đồng bộ nhầm vào session {current_session_id}."
                    )
                else:
                    logger.debug(f"[SYNC] flush_stale_records: Không có record cũ cần xóa.")
        except Exception as e:
            logger.error(f"[SYNC] Lỗi khi flush stale records: {e}")

    def stop(self):
        self._stop_event.set()
        if self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2)

# Singleton
sync_service = SyncService()
