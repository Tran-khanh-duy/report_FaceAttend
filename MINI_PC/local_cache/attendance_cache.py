import sqlite3
import threading
import json
import time
from datetime import datetime
from loguru import logger
from pathlib import Path
import numpy as np

CACHE_DIR = Path(__file__).parent
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = CACHE_DIR / "attendance_queue.db"

class AttendanceCacheManager:
    """
    Offline-first Local Cache cho Attendance.
    Luôn lưu xuống SQLite trước khi gửi, đảm bảo không mất dữ liệu dù sập nguồn.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        self._db_lock = threading.Lock()
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_attendance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        camera_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        embedding BLOB NOT NULL,
                        liveness_score REAL NOT NULL,
                        liveness_checked INTEGER NOT NULL,
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT
                    )
                """)
                conn.commit()

    def save_pending(self, camera_id: str, embedding: np.ndarray, liveness_score: float, liveness_checked: bool, timestamp: str = None) -> int:
        """Lưu kết quả điểm danh vào Local DB ngay lập tức (Offline-first). Trả về record_id."""
        emb_bytes = embedding.astype(np.float32).tobytes()
        ts = timestamp or datetime.now().isoformat()
        
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("""
                    INSERT INTO pending_attendance 
                    (camera_id, timestamp, embedding, liveness_score, liveness_checked)
                    VALUES (?, ?, ?, ?, ?)
                """, (camera_id, ts, emb_bytes, liveness_score, 1 if liveness_checked else 0))
                conn.commit()
                return cursor.lastrowid

    def remove_pending(self, record_id: int):
        """Xóa record khi Server đã nhận thành công."""
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM pending_attendance WHERE id = ?", (record_id,))
                conn.commit()

    def mark_failed(self, record_id: int, error_msg: str):
        """Đánh dấu gửi thất bại để Retry sau."""
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE pending_attendance 
                    SET retry_count = retry_count + 1, last_error = ? 
                    WHERE id = ?
                """, (error_msg, record_id))
                conn.commit()

    def get_all_pending(self, limit: int = 50):
        """Lấy danh sách các record đang chờ gửi (ưu tiên cũ nhất)."""
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("""
                    SELECT id, camera_id, timestamp, embedding, liveness_score, liveness_checked, retry_count
                    FROM pending_attendance
                    ORDER BY id ASC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()

        results = []
        for row in rows:
            record_id, cam_id, ts, emb_bytes, score, checked, retries = row
            emb = np.frombuffer(emb_bytes, dtype=np.float32)
            results.append({
                "id": record_id,
                "camera_id": cam_id,
                "timestamp": ts,
                "embedding": emb,
                "liveness_score": score,
                "liveness_checked": bool(checked),
                "retry_count": retries
            })
        return results

    def get_queue_size(self):
        with self._db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM pending_attendance")
                return cursor.fetchone()[0]

attendance_cache = AttendanceCacheManager()
