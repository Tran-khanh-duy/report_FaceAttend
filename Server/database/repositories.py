#database/repositories.py
# Kết nối với database qlsv:
#   lop(IDLop, TenLop)
#   hocvien(id AUTO_INC, MaHV, HoTen, GioiTinh, SoDienThoai, IDLop, face_enrolled, ...)
import numpy as np
from datetime import datetime, date
from typing import Optional
from loguru import logger

from .connection import get_db
from .models import (
    Class, Student, FaceEmbedding, AttendanceSession,
    AttendanceRecord, Camera, EmbeddingCache, Building, Room
)


# ══════════════════════════════════════════════
#  CLASS REPOSITORY  (bảng: lop)
# ══════════════════════════════════════════════
class ClassRepository:
    # IDLop dùng làm cả class_id lẫn class_code; TenLop → class_name
    _SQL = """
        SELECT IDLop, IDLop, TenLop, NULL, NULL, 1, NULL
        FROM lop
    """

    def get_all(self, active_only: bool = True) -> list:
        rows = get_db().execute(f"{self._SQL} ORDER BY TenLop")
        return [Class(*r) for r in rows]

    def get_by_id(self, class_id) -> Optional[Class]:
        rows = get_db().execute(
            f"{self._SQL} WHERE IDLop = ?", (class_id,)
        )
        return Class(*rows[0]) if rows else None

    def create(self, class_code: str, class_name: str,
               teacher_name: str = None, academic_year: str = None) -> int:
        get_db().execute(
            "INSERT INTO lop (IDLop, TenLop) VALUES (?, ?)",
            (class_code, class_name), commit=True,
        )
        logger.info(f"Created lop [{class_code}] {class_name}")
        return class_code  # IDLop là VARCHAR

    def update(self, class_id, **kwargs) -> bool:
        allowed = {"TenLop": "class_name"}
        cols = {}
        if "class_name" in kwargs:
            cols["TenLop"] = kwargs["class_name"]
        if not cols:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in cols)
        params = list(cols.values()) + [class_id]
        get_db().execute(
            f"UPDATE lop SET {set_clause} WHERE IDLop = ?",
            tuple(params), commit=True,
        )
        return True


# ══════════════════════════════════════════════
#  STUDENT REPOSITORY  (bảng: hocvien)
# ══════════════════════════════════════════════
class StudentRepository:
    # 14 cols theo thứ tự Student dataclass:
    #  student_id, student_code, full_name, gender, date_of_birth,
    #  phone, email, class_id, class_name, building, floor, room,
    #  face_enrolled, created_at
    _SQL = """
        SELECT hv.id, hv.MaHV, hv.HoTen,
               hv.GioiTinh, hv.date_of_birth,
               hv.SoDienThoai, hv.email, hv.IDLop,
               l.TenLop, hv.building, hv.floor, hv.room,
               hv.face_enrolled, hv.created_at
        FROM hocvien hv
        LEFT JOIN lop l ON l.IDLop = hv.IDLop
    """

    def get_all(self, class_id=None) -> list:
        where_parts, params = [], []
        if class_id is not None:
            where_parts.append("hv.IDLop = ?")
            params.append(class_id)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows = get_db().execute(
            f"{self._SQL} {where} ORDER BY hv.HoTen",
            tuple(params) if params else None,
        )
        return [Student(*r) for r in rows]

    def get_by_id(self, student_id: int) -> Optional[Student]:
        rows = get_db().execute(
            f"{self._SQL} WHERE hv.id = ?", (student_id,)
        )
        return Student(*rows[0]) if rows else None

    def get_by_code(self, student_code: str) -> Optional[Student]:
        rows = get_db().execute(
            f"{self._SQL} WHERE hv.MaHV = ?", (student_code,)
        )
        return Student(*rows[0]) if rows else None

    def create(self, student_code: str, full_name: str,
               class_id=None, gender: str = None,
               phone: str = None, email: str = None,
               class_name: str = None, building: str = None,
               floor: str = None, room: str = None) -> int:
        sid = get_db().execute_insert(
            """
            INSERT INTO hocvien (MaHV, HoTen, GioiTinh, IDLop, SoDienThoai, email, building, floor, room)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (student_code, full_name, gender, class_id, phone, email, building, floor, room)
        )
        logger.info(f"Created hocvien [{student_code}] {full_name} id={sid}")
        return sid

    def update(self, student_id: int, **kwargs) -> bool:
        # Ánh xạ từ tên field Python → tên cột MySQL
        col_map = {
            "student_code":  "MaHV",
            "full_name":     "HoTen",
            "gender":        "GioiTinh",
            "date_of_birth": "date_of_birth",
            "phone":         "SoDienThoai",
            "email":         "email",
            "class_id":      "IDLop",
            "building":      "building",
            "floor":         "floor",
            "room":          "room",
        }
        cols = {col_map[k]: v for k, v in kwargs.items() if k in col_map}
        if not cols:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in cols)
        params = list(cols.values()) + [student_id]
        get_db().execute(
            f"UPDATE hocvien SET {set_clause} WHERE id = ?",
            tuple(params), commit=True,
        )
        return True

    def update_enrollment_status(self, student_id: int, enrolled: bool = True) -> bool:
        get_db().execute(
            "UPDATE hocvien SET face_enrolled = ? WHERE id = ?",
            (1 if enrolled else 0, student_id), commit=True,
        )
        logger.info(f"hocvien id={student_id} face_enrolled → {enrolled}")
        return True

    def search(self, keyword: str) -> list:
        kw = f"%{keyword}%"
        rows = get_db().execute(
            f"{self._SQL} WHERE hv.HoTen LIKE ? OR hv.MaHV LIKE ? ORDER BY hv.HoTen",
            (kw, kw),
        )
        return [Student(*r) for r in rows]

    def get_student_count_by_camera(self, camera_source: str) -> int:
        db = get_db()
        rows = db.execute(
            "SELECT area_id FROM Cameras WHERE camera_name = ? OR CAST(camera_id AS CHAR) = ? OR rtsp_url = ?",
            (camera_source, camera_source, camera_source),
        )
        if rows and rows[0][0]:
            area_id = rows[0][0]
            parts = area_id.split("_")
            bld = parts[0].strip() if len(parts) > 0 else None
            flr = parts[1].strip() if len(parts) > 1 else None
            query = "SELECT COUNT(*) FROM hocvien WHERE 1=1"
            params = []
            if bld:
                query += " AND building = ?"
                params.append(bld)
            if flr:
                query += " AND floor = ?"
                params.append(flr)
            try:
                return db.execute(query, tuple(params))[0][0]
            except Exception as e:
                logger.error(f"Lỗi đếm số lượng: {e}")
                return 0
        try:
            return db.execute("SELECT COUNT(*) FROM hocvien")[0][0]
        except Exception:
            return 0

    def get_count_by_class(self, class_id) -> int:
        rows = get_db().execute(
            "SELECT COUNT(*) FROM hocvien WHERE IDLop = ?", (class_id,)
        )
        return rows[0][0] if rows else 0

    def delete(self, student_id: int) -> bool:
        try:
            db = get_db()
            db.execute("DELETE FROM AttendanceRecords WHERE student_id = ?", (student_id,), commit=True)
            db.execute("DELETE FROM FaceEmbeddings WHERE student_id = ?", (student_id,), commit=True)
            db.execute("DELETE FROM hocvien WHERE id = ?", (student_id,), commit=True)
            return True
        except Exception as e:
            logger.error(f"Cannot delete hocvien id={student_id}: {e}")
            return False


# ══════════════════════════════════════════════
#  FACE EMBEDDING REPOSITORY
# ══════════════════════════════════════════════
class FaceEmbeddingRepository:

    def save_embedding(self, student_id: int, embedding: np.ndarray,
                       model_version: str = "buffalo_l") -> bool:
        get_db().execute(
            "UPDATE FaceEmbeddings SET is_active = 0 WHERE student_id = ?",
            (student_id,), commit=True,
        )
        get_db().execute(
            "INSERT INTO FaceEmbeddings (student_id, embedding_vector, model_version) VALUES (?, ?, ?)",
            (student_id, embedding.astype(np.float32).tobytes(), model_version),
            commit=True,
        )
        logger.info(f"Saved embedding: student_id={student_id}")
        return True

    def save(self, student_id: int, embedding: np.ndarray,
             model_version: str = "buffalo_l") -> int:
        self.save_embedding(student_id, embedding, model_version)
        rows = get_db().execute(
            "SELECT embedding_id FROM FaceEmbeddings "
            "WHERE student_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
            (student_id,),
        )
        return rows[0][0] if rows else -1

    def load_all_to_cache(self) -> EmbeddingCache:
        rows = get_db().execute(
            """
            SELECT hv.id, hv.MaHV, hv.HoTen, fe.embedding_vector,
                   hv.IDLop, l.TenLop, l.IDLop
            FROM FaceEmbeddings fe
            JOIN hocvien hv ON hv.id = fe.student_id
            LEFT JOIN lop l ON l.IDLop = hv.IDLop
            WHERE fe.is_active = 1
            ORDER BY fe.student_id
            """
        )
        if not rows:
            logger.warning("Khong co embedding nao trong database!")
            return EmbeddingCache()

        cache = EmbeddingCache()
        vecs = []
        for row in rows:
            student_id, student_code, full_name, emb_bytes, class_id, class_name, class_code = row[:7]
            vec = np.frombuffer(emb_bytes, dtype=np.float32).copy()
            if vec.shape[0] != 512:
                logger.warning(f"Skip student_id={student_id}: shape={vec.shape}")
                continue
            cache.student_ids.append(int(student_id))
            cache.student_codes.append(str(student_code or ""))
            cache.full_names.append(str(full_name or ""))
            cache.class_ids.append(str(class_id or ""))
            cache.class_names.append(str(class_name or ""))
            cache.class_codes.append(str(class_code or ""))
            vecs.append(vec)

        if vecs:
            mat = np.vstack(vecs).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            cache.embeddings = mat / np.maximum(norms, 1e-8)

        logger.success(f"Loaded {cache.size} embeddings vao RAM")
        return cache

    def get_all_active(self) -> list:
        try:
            rows = get_db().call_procedure("sp_GetAllEmbeddings")
            return rows if rows else []
        except Exception:
            rows = get_db().execute(
                """
                SELECT hv.id, hv.MaHV, hv.HoTen, fe.embedding_vector
                FROM FaceEmbeddings fe
                JOIN hocvien hv ON hv.id = fe.student_id
                WHERE fe.is_active = 1
                """
            )
            return rows or []

    def has_embedding(self, student_id: int) -> bool:
        rows = get_db().execute(
            "SELECT COUNT(*) FROM FaceEmbeddings WHERE student_id = ? AND is_active = 1",
            (student_id,),
        )
        return bool(rows and rows[0][0] > 0)

    def delete_by_student(self, student_id: int) -> bool:
        get_db().execute(
            "UPDATE FaceEmbeddings SET is_active = 0 WHERE student_id = ?",
            (student_id,), commit=True,
        )
        return True


# ══════════════════════════════════════════════
#  CAMERA REPOSITORY
# ══════════════════════════════════════════════
class CameraRepository:
    # SELECT đủ 13 cột (8 cột gốc + 5 cột từ migration 004)
    _SQL = """
        SELECT camera_id, camera_name, location_desc,
               rtsp_url, ip_address, resolution, area_id, is_active,
               COALESCE(username, 'admin'),
               password,
               floor,
               COALESCE(rtsp_port, 554),
               device_group
        FROM Cameras
    """

    def _row_to_camera(self, r) -> 'Camera':
        """Map một DB row (13 cột) → Camera dataclass."""
        return Camera(
            camera_id    = r[0],
            camera_name  = r[1],
            location_desc= r[2],
            rtsp_url     = r[3],
            ip_address   = r[4],
            resolution   = r[5] or '1280x720',
            area_id      = r[6],
            is_active    = bool(r[7]),
            username     = r[8] or 'admin',
            password     = r[9],
            floor        = r[10],
            rtsp_port    = int(r[11]) if r[11] else 554,
            device_group = r[12],
        )

    def get_all(self, active_only: bool = True) -> list:
        if active_only:
            rows = get_db().execute(f"{self._SQL} WHERE is_active = 1 ORDER BY floor, camera_id")
        else:
            rows = get_db().execute(f"{self._SQL} ORDER BY floor, camera_id")
        return [self._row_to_camera(r) for r in rows]

    def get_by_id(self, camera_id: int) -> Optional[Camera]:
        rows = get_db().execute(f"{self._SQL} WHERE camera_id = ?", (camera_id,))
        return self._row_to_camera(rows[0]) if rows else None

    def get_for_edge(self, device_group: str = None) -> list:
        """
        Trả về list dict để gửi cho Mini PC qua /api/system/cameras/edge-list.
        Bao gồm RTSP URL đầy đủ (build từ credentials nếu chưa có sẵn).
        Nếu có device_group, chỉ lấy camera thuộc nhóm đó.
        """
        if device_group:
            rows = get_db().execute(
                f"{self._SQL} WHERE is_active = 1 AND device_group = ? ORDER BY floor, camera_id",
                (device_group,)
            )
        else:
            rows = get_db().execute(
                f"{self._SQL} WHERE is_active = 1 ORDER BY floor, camera_id"
            )
        return [self._row_to_camera(r).to_edge_dict() for r in rows]

    def create(self, camera_name: str, location_desc: str = None,
               rtsp_url: str = None, ip_address: str = None,
               resolution: str = '1280x720', area_id: str = None,
               username: str = 'admin', password: str = None,
               floor: int = None, rtsp_port: int = 554,
               device_group: str = None) -> int:
        return get_db().execute_insert(
            """
            INSERT INTO Cameras
                (camera_name, location_desc, rtsp_url, ip_address,
                 resolution, area_id, username, password, floor, rtsp_port, device_group)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (camera_name, location_desc, rtsp_url, ip_address,
             resolution, area_id, username, password, floor, rtsp_port, device_group),
        )

    def update(self, camera_id: int, **kwargs) -> bool:
        allowed = {
            'camera_name', 'location_desc', 'rtsp_url', 'ip_address',
            'resolution', 'area_id', 'is_active',
            'username', 'password', 'floor', 'rtsp_port', 'device_group'
        }
        cols = {k: v for k, v in kwargs.items() if k in allowed}
        if not cols:
            return False
        set_clause = ', '.join(f'{k} = ?' for k in cols)
        params = list(cols.values()) + [camera_id]
        get_db().execute(
            f'UPDATE Cameras SET {set_clause} WHERE camera_id = ?',
            tuple(params), commit=True
        )
        return True

    def delete(self, camera_id: int) -> bool:
        get_db().execute('DELETE FROM Cameras WHERE camera_id = ?', (camera_id,), commit=True)
        return True


# ══════════════════════════════════════════════
#  SESSION REPOSITORY  (bảng: AttendanceSessions + lop)
# ══════════════════════════════════════════════
def _row_to_session(r) -> AttendanceSession:
    return AttendanceSession(
        session_id    = r[0],
        session_code  = r[1],
        class_id      = r[2],
        subject_name  = r[3],
        session_date  = r[4],
        start_time    = r[5],
        end_time      = r[6],
        status        = r[7]  or "PENDING",
        present_count = int(r[8]  or 0),
        absent_count  = int(r[9]  or 0),
        created_at    = r[10],
        class_name    = r[11],
        class_code    = r[12] or "",
    )

_SESSION_SQL = """
    SELECT s.session_id, s.session_code, s.class_id, s.subject_name,
           s.session_date, s.start_time, s.end_time,
           s.status, s.present_count, s.absent_count, s.created_at,
           l.TenLop, l.IDLop
    FROM AttendanceSessions s
    LEFT JOIN lop l ON l.IDLop = s.class_id
"""


class SessionRepository:

    def get_all(self, limit: int = 200) -> list:
        rows = get_db().execute(
            f"{_SESSION_SQL} ORDER BY s.session_id DESC LIMIT ?", (limit,)
        )
        return [_row_to_session(r) for r in rows]

    def get_by_id(self, session_id: int) -> Optional[AttendanceSession]:
        rows = get_db().execute(
            f"{_SESSION_SQL} WHERE s.session_id = ?", (session_id,)
        )
        return _row_to_session(rows[0]) if rows else None

    def get_recent(self, limit: int = 20) -> list:
        rows = get_db().execute(
            f"{_SESSION_SQL} ORDER BY s.session_id DESC LIMIT ?", (limit,)
        )
        return [_row_to_session(r) for r in rows]

    def create(self, class_id, subject_name: str,
               session_date=None, created_by: str = None) -> int:
        return self.create_session(class_id, subject_name, session_date)

    def create_session(self, class_id, subject_name: str, session_date=None) -> int:
        # --- NÂNG CẤP: Hỗ trợ điểm danh theo Tòa nhà ---
        # Đảm bảo class_id (có thể là MaToa) tồn tại trong bảng lop để không lỗi Foreign Key
        if class_id is None or class_id == "" or class_id == "ALL":
            class_id = "ALL"
            ten_hien_thi = "Tất cả tòa nhà"
        else:
            ten_hien_thi = f"Nhóm {class_id}"

        db = get_db()
        exists = db.execute("SELECT 1 FROM lop WHERE IDLop = ?", (class_id,))
        if not exists:
            # Thử tìm tên tòa nhà nếu class_id là MaToa
            bld_row = db.execute("SELECT TenToa FROM ToaNha WHERE MaToa = ?", (class_id,))
            if bld_row:
                ten_hien_thi = bld_row[0][0]
            db.execute("INSERT INTO lop (IDLop, TenLop) VALUES (?, ?)", (class_id, ten_hien_thi), commit=True)
            logger.info(f"Đã tự động tạo 'Lớp ảo' cho Tòa nhà/Nhóm: {class_id}")

        session_code = (
            f"{class_id}-{session_date.strftime('%Y%m%d')}"
            f"-{datetime.now().strftime('%H%M%S')}"
        )
        session_id = get_db().execute_insert(
            """
            INSERT INTO AttendanceSessions
                (session_code, class_id, subject_name, session_date, status)
            VALUES (?, ?, ?, ?, 'PENDING')
            """,
            (session_code, class_id, subject_name, session_date),
        )
        if session_id > 0:
            self._prefill_absent(session_id, class_id)
        logger.info(f"Created session: {session_code} (id={session_id})")
        return session_id

    def _prefill_absent(self, session_id: int, class_id):
        # Chèn tất cả học viên thuộc lớp HOẶC tòa nhà này vào bảng điểm danh với trạng thái ABSENT
        # Nếu class_id là "ALL", chèn TẤT CẢ học viên
        if class_id == "ALL" or class_id is None:
            get_db().execute(
                """
                INSERT INTO AttendanceRecords (session_id, student_id, status)
                SELECT ?, hv.id, 'ABSENT' FROM hocvien hv
                """,
                (session_id,), commit=True,
            )
        else:
            get_db().execute(
                """
                INSERT INTO AttendanceRecords (session_id, student_id, status)
                SELECT ?, hv.id, 'ABSENT' FROM hocvien hv 
                WHERE hv.IDLop = ? OR hv.building = ?
                """,
                (session_id, class_id, class_id), commit=True,
            )

    def start_session(self, session_id: int) -> bool:
        get_db().execute(
            "UPDATE AttendanceSessions SET status='COMPLETED', end_time=? WHERE status='ACTIVE' AND session_id != ?",
            (datetime.now(), session_id), commit=True,
        )
        get_db().execute(
            "UPDATE AttendanceSessions SET status='ACTIVE', start_time=? WHERE session_id=?",
            (datetime.now(), session_id), commit=True,
        )
        logger.info(f"Session {session_id} → ACTIVE")
        return True

    def end_session(self, session_id: int) -> bool:
        get_db().execute(
            """
            UPDATE AttendanceSessions SET
                status        = 'COMPLETED', end_time = ?,
                present_count = (SELECT COUNT(*) FROM AttendanceRecords WHERE session_id = ? AND status = 'PRESENT'),
                absent_count  = (SELECT COUNT(*) FROM AttendanceRecords WHERE session_id = ? AND status = 'ABSENT')
            WHERE session_id = ?
            """,
            (datetime.now(), session_id, session_id, session_id), commit=True,
        )
        logger.info(f"Session {session_id} → COMPLETED")
        return True

    def update_status(self, session_id: int, status: str) -> bool:
        get_db().execute(
            "UPDATE AttendanceSessions SET status = ? WHERE session_id = ?",
            (status, session_id), commit=True,
        )
        return True

    def update_present_count(self, session_id: int) -> int:
        get_db().execute(
            """
            UPDATE AttendanceSessions
            SET present_count = (
                SELECT COUNT(*) FROM AttendanceRecords WHERE session_id = ? AND status = 'PRESENT'
            ) WHERE session_id = ?
            """,
            (session_id, session_id), commit=True,
        )
        rows = get_db().execute(
            "SELECT present_count FROM AttendanceSessions WHERE session_id = ?", (session_id,)
        )
        return rows[0][0] if rows else 0


# ══════════════════════════════════════════════
#  ATTENDANCE RECORD REPOSITORY
# ══════════════════════════════════════════════
class AttendanceRecordRepository:

    def record_attendance(self, session_id: int, student_id: int,
                          recognition_score: float,
                          snapshot_path: str = None,
                          camera_id: int = None) -> bool:
        try:
            now = datetime.now()
            existing = get_db().execute(
                "SELECT 1 FROM AttendanceRecords WHERE session_id=? AND student_id=?",
                (session_id, student_id),
            )
            if not existing:
                get_db().execute(
                    "INSERT INTO AttendanceRecords (session_id, student_id, status) VALUES (?, ?, 'ABSENT')",
                    (session_id, student_id), commit=True,
                )
            rid = self.upsert(
                session_id=session_id, student_id=student_id,
                status="PRESENT", check_in_time=now,
                recognition_score=recognition_score,
                camera_id=camera_id,
            )
            if rid < 0:
                logger.error(f"record_attendance: upsert failed s={session_id} u={student_id}")
                return False
            get_db().execute(
                """
                UPDATE AttendanceSessions
                SET present_count = (
                    SELECT COUNT(*) FROM AttendanceRecords WHERE session_id = ? AND status = 'PRESENT'
                ) WHERE session_id = ?
                """,
                (session_id, session_id), commit=True,
            )
            logger.success(f"✅ record_attendance OK: s={session_id} u={student_id}")
            return True
        except Exception as e:
            logger.error(f"record_attendance error: {e}")
            return False

    def upsert(self, session_id: int, student_id: int,
               status: str, check_in_time=None,
               recognition_score: float = 0,
               camera_id: int = None) -> int:
        get_db().execute(
            """
            INSERT INTO AttendanceRecords
                (session_id, student_id, status, check_in_time, recognition_score, camera_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                status = ?, check_in_time = ?, recognition_score = ?, camera_id = ?
            """,
            (session_id, student_id, status, check_in_time, recognition_score, camera_id,
             status, check_in_time, recognition_score, camera_id),
            commit=True,
        )
        rows = get_db().execute(
            "SELECT record_id FROM AttendanceRecords WHERE session_id=? AND student_id=?",
            (session_id, student_id),
        )
        return rows[0][0] if rows else -1

    def get_session_report(self, session_id: int) -> list:
        rows = get_db().execute(
            """
            SELECT hv.MaHV, hv.HoTen, l.TenLop,
                   ar.status, ar.check_in_time, ar.recognition_score,
                   ar.snapshot_path, hv.GioiTinh, ar.camera_id, hv.id,
                   hv.building, hv.room
            FROM AttendanceRecords ar
            INNER JOIN hocvien hv ON hv.id    = ar.student_id
            LEFT  JOIN lop     l  ON l.IDLop  = hv.IDLop
            WHERE ar.session_id = ?
            ORDER BY l.TenLop, ar.status DESC, ar.check_in_time
            """,
            (session_id,),
        )
        result = []
        for r in rows:
            t = r[4]
            time_str = t.strftime("%H:%M:%S") if hasattr(t, "strftime") else str(t or "")
            result.append({
                "student_code":       r[0] or "",
                "full_name":          r[1] or "",
                "class_name":         r[2] or "Khác",
                "status":             r[3] or "ABSENT",
                "check_in_time":      time_str,
                "recognition_score":  float(r[5] or 0),
                "recognition_method": "FACE",
                "gender":             r[7] or "",
                "camera_id":          r[8] or 1,
                "student_id":         r[9] or 0,
                "building":           r[10] or "",
                "room":               r[11] or "",
            })
        return result

    def get_class_absent_count(self, session_id: int, class_name: str) -> int:
        rows = get_db().execute(
            """
            SELECT COUNT(*)
            FROM AttendanceRecords ar
            INNER JOIN hocvien hv ON hv.id = ar.student_id
            LEFT JOIN lop l ON l.IDLop = hv.IDLop
            WHERE ar.session_id = ? AND ar.status = 'ABSENT' AND l.TenLop = ?
            """,
            (session_id, class_name),
        )
        return rows[0][0] if rows else 0

    def is_already_recorded(self, session_id: int, student_id: int) -> bool:
        rows = get_db().execute(
            "SELECT status FROM AttendanceRecords WHERE session_id=? AND student_id=?",
            (session_id, student_id),
        )
        return bool(rows and rows[0][0] == "PRESENT")

    def get_present_list(self, session_id: int) -> list:
        rows = get_db().execute(
            """
            SELECT hv.MaHV, hv.HoTen, ar.check_in_time, ar.recognition_score, l.IDLop,
                   hv.building, hv.room
            FROM AttendanceRecords ar
            INNER JOIN hocvien hv ON hv.id   = ar.student_id
            LEFT  JOIN lop     l  ON l.IDLop = hv.IDLop
            WHERE ar.session_id = ? AND ar.status = 'PRESENT'
            ORDER BY ar.check_in_time
            """,
            (session_id,),
        )
        return [
            {
                "code": r[0], "name": r[1], "time": r[2], "score": float(r[3] or 0), 
                "class_code": r[4], "building": r[5], "room": r[6]
            }
            for r in rows
        ]

    def get_total_count(self, session_id: int) -> int:
        """Lấy tổng số học viên dự kiến trong phiên này (tổng số records)."""
        rows = get_db().execute(
            "SELECT COUNT(*) FROM AttendanceRecords WHERE session_id = ?", (session_id,)
        )
        return rows[0][0] if rows else 0

# ══════════════════════════════════════════════
#  BUILDING REPOSITORY (ToaNha)
# ══════════════════════════════════════════════
class BuildingRepository:
    def get_all(self) -> list:
        rows = get_db().execute("SELECT MaToa, TenToa FROM ToaNha ORDER BY MaToa")
        return [Building(*r) for r in rows]

# ══════════════════════════════════════════════
#  ROOM REPOSITORY (Phong)
# ══════════════════════════════════════════════
class RoomRepository:
    def get_all(self) -> list:
        rows = get_db().execute("SELECT MaPhong, Tang, MaToa, TenPhong FROM Phong ORDER BY MaPhong")
        return [Room(*r) for r in rows]

    def get_by_building(self, ma_toa: str) -> list:
        rows = get_db().execute(
            "SELECT MaPhong, Tang, MaToa, TenPhong FROM Phong WHERE MaToa = ? ORDER BY MaPhong",
            (ma_toa,)
        )
        return [Room(*r) for r in rows]

    def get_by_building_and_floor(self, ma_toa: str, tang: int) -> list:
        rows = get_db().execute(
            "SELECT MaPhong, Tang, MaToa, TenPhong FROM Phong WHERE MaToa = ? AND Tang = ? ORDER BY MaPhong",
            (ma_toa, tang)
        )
        return [Room(*r) for r in rows]

    def get_floors_by_building(self, ma_toa: str) -> list:
        rows = get_db().execute(
            "SELECT DISTINCT Tang FROM Phong WHERE MaToa = ? ORDER BY Tang",
            (ma_toa,)
        )
        return [r[0] for r in rows]

# ─────────────────────────────────────────────
#  Singleton instances
# ─────────────────────────────────────────────
class_repo     = ClassRepository()
student_repo   = StudentRepository()
embedding_repo = FaceEmbeddingRepository()
camera_repo    = CameraRepository()
session_repo   = SessionRepository()
record_repo    = AttendanceRecordRepository()
building_repo  = BuildingRepository()
room_repo      = RoomRepository()