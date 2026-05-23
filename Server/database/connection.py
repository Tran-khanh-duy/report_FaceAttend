#database/connection.py
import mysql.connector
from mysql.connector import Error as MySQLError
import threading
from contextlib import contextmanager
from typing import Optional
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import db_config


class DatabaseConnection:
    """
    Singleton connection manager cho SQL Server.
    Thread-safe: mỗi thread có 1 connection riêng (thread-local).
    """
    _instance: Optional["DatabaseConnection"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._conn_args = db_config.connection_args
        self._local    = threading.local()
        self._initialized = True
        logger.info("DatabaseConnection initialized")

    # ─── Internal ──────────────────────────────

    def _get_connection(self):
        """Lấy connection của thread hiện tại, tạo mới nếu chưa có."""
        if not hasattr(self._local, "conn") or self._local.conn is None or not self._local.conn.is_connected():
            try:
                conn = mysql.connector.connect(
                    **self._conn_args,
                    autocommit=True,
                    connection_timeout=30,
                )
                self._local.conn = conn
            except MySQLError as e:
                logger.error(f"Không thể kết nối MySQL: {e}")
                
                # Cung cấp gợi ý sửa lỗi cụ thể hơn
                error_msg = (
                    f"Lỗi kết nối MySQL (Host: {db_config.host}, Database: {db_config.database}).\n"
                    f"Chi tiết kỹ thuật: {e}\n\n"
                    f"Hướng dẫn khắc phục:\n"
                    f"  1. Đảm bảo dịch vụ MySQL đang chạy.\n"
                    f"  2. Kiểm tra chuỗi kết nối trong config.py.\n"
                    f"  3. Đảm bảo Database '{db_config.database}' đã được khởi tạo."
                )
                raise ConnectionError(error_msg)
        return self._local.conn

    # ─── Public API ────────────────────────────

    @contextmanager
    def get_cursor(self, commit: bool = False):
        """
        Context manager trả về cursor.
        Tự động commit/rollback và đóng cursor.

            with db.get_cursor() as cur:
                cur.execute("SELECT ...")
                rows = cur.fetchall()

            with db.get_cursor(commit=True) as cur:
                cur.execute("INSERT ...")
        """
        conn   = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except MySQLError as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.error(f"DB Error: {e}")
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def get_connection(self):
        """Lấy raw connection (dùng cho các thao tác đặc biệt)."""
        return self._get_connection()

    def execute(self, sql: str, params=None, commit: bool = False) -> list:
        """
        Chạy 1 câu SQL, trả về list of rows.
        """
        sql = sql.replace("?", "%s")
        with self.get_cursor(commit=commit) as cur:
            if params:                      # None, (), [] đều bỏ qua
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            try:
                return list(cur.fetchall())
            except mysql.connector.errors.InterfaceError:
                # INSERT/UPDATE/DELETE không có fetchall
                return []
            except MySQLError:
                return []

    def execute_insert(self, sql: str, params=None) -> int:
        """Thực thi câu lệnh INSERT và trả về lastrowid (ID vừa được tạo)."""
        sql = sql.replace("?", "%s")
        with self.get_cursor(commit=True) as cur:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur.lastrowid

    def execute_many(self, sql: str, params_list: list, commit: bool = True) -> int:
        """Bulk insert/update."""
        sql = sql.replace("?", "%s")
        with self.get_cursor(commit=commit) as cur:
            cur.executemany(sql, params_list)
            return cur.rowcount

    def call_procedure(self, proc_name: str, params: tuple = ()) -> list:
        """Gọi Stored Procedure."""
        with self.get_cursor(commit=True) as cur:
            if params:
                cur.callproc(proc_name, params)
            else:
                cur.callproc(proc_name)
            
            results = []
            for result in cur.stored_results():
                results.extend(result.fetchall())
            return results

    def test_connection(self) -> bool:
        """Kiểm tra kết nối còn sống không."""
        try:
            rows = self.execute("SELECT 1")
            return bool(rows)
        except Exception as e:
            logger.warning(f"Connection test failed: {e}")
            self.reset_connection()
            return False

    def reset_connection(self):
        """Đóng và xoá connection của thread hiện tại để tạo mới lần sau."""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
        self._local.conn = None

    def close(self):
        """Alias của reset_connection."""
        self.reset_connection()


# ─────────────────────────────────────────────
#  Singleton — dùng trong toàn project
# ─────────────────────────────────────────────
db = DatabaseConnection()


def get_db() -> DatabaseConnection:
    return db


# ─── Quick test ──────────────────────────────
if __name__ == "__main__":
    logger.info("Test kết nối MySQL...")
    try:
        if db.test_connection():
            logger.success("✅ Kết nối thành công!")
            rows = db.execute("SHOW DATABASES")
            logger.info(f"Databases: {[r[0] for r in rows]}")

            rows = db.execute("SELECT COUNT(*) FROM Students")
            logger.info(f"Students count: {rows[0][0]}")
        else:
            logger.error("❌ Kết nối thất bại")
    except ConnectionError as e:
        logger.error(str(e))