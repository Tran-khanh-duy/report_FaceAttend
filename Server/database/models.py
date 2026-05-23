#database/models.py
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import numpy as np


# ─────────────────────────────────────────────
@dataclass
class Class:
    class_id:      str               # IDLop — VARCHAR trong bảng lop
    class_code:    str               # IDLop (alias)
    class_name:    str               # TenLop
    teacher_name:  Optional[str]      = None
    academic_year: Optional[str]      = None
    is_active:     bool               = True
    created_at:    Optional[datetime] = None

    def __post_init__(self):
        self.is_active = bool(self.is_active)

    def __str__(self):
        return f"{self.class_code} — {self.class_name}"


# ─────────────────────────────────────────────
@dataclass
class Student:
    # Thứ tự PHẢI khớp với SELECT trong repositories.py
    student_id:    int
    student_code:  str
    full_name:     str
    gender:        Optional[str]      = None   # gender TRƯỚC date_of_birth (đúng thứ tự DB)
    date_of_birth: Optional[date]     = None
    phone:         Optional[str]      = None
    email:         Optional[str]      = None
    class_id:      Optional[int]      = None
    class_name:    Optional[str]      = None   # Bây giờ là trường thật trong DB theo yêu cầu
    building:      Optional[str]      = None
    floor:         Optional[str]      = None
    room:          Optional[str]      = None
    face_enrolled: bool               = False
    created_at:    Optional[datetime] = None
    # Join field — không có trong bảng Students
    # (Đã di chuyển class_name lên trên)

    def __post_init__(self):
        # pyodbc trả về int(0/1) từ BIT → cast về bool
        self.face_enrolled = bool(self.face_enrolled)

    def __str__(self):
        return f"[{self.student_code}] {self.full_name}"


# ─────────────────────────────────────────────
@dataclass
class FaceEmbedding:
    embedding_id:  int
    student_id:    int
    embedding:     Optional[np.ndarray] = None
    model_version: str                  = "buffalo_l"
    created_at:    Optional[datetime]   = None
    is_active:     bool                 = True

    def __post_init__(self):
        self.is_active = bool(self.is_active)

    def to_bytes(self) -> bytes:
        if self.embedding is None:
            raise ValueError("Embedding is None")
        return self.embedding.astype(np.float32).tobytes()

    @staticmethod
    def from_bytes(data: bytes) -> np.ndarray:
        return np.frombuffer(data, dtype=np.float32).copy()


# ─────────────────────────────────────────────
@dataclass
class Camera:
    camera_id:     int
    camera_name:   str
    location_desc: Optional[str]      = None
    rtsp_url:      Optional[str]      = None
    ip_address:    Optional[str]      = None
    resolution:    str                = "1280x720"
    area_id:       Optional[str]      = None
    is_active:     bool               = True
    # Credentials — thêm từ migration 004
    username:      Optional[str]      = "admin"
    password:      Optional[str]      = None
    floor:         Optional[int]      = None
    rtsp_port:     int                = 554
    device_group:  Optional[str]      = None

    def __post_init__(self):
        self.is_active = bool(self.is_active)
        if self.rtsp_port is None:
            self.rtsp_port = 554

    @property
    def effective_rtsp_url(self) -> Optional[str]:
        """
        Trả về RTSP URL hiệu lực:
        - Nếu rtsp_url đã có sẵn trong DB → dùng trực tiếp.
        - Nếu chưa → tự build từ ip_address, username, password, rtsp_port.
        """
        if self.rtsp_url:
            return self.rtsp_url
        if self.ip_address and self.username and self.password:
            return (
                f"rtsp://{self.username}:{self.password}"
                f"@{self.ip_address}:{self.rtsp_port}"
                f"/cam/realmonitor?channel=1&subtype=0"
            )
        return None

    def to_edge_dict(self) -> dict:
        """Chuyển thành dict để gửi cho Mini PC qua API."""
        return {
            "id": f"CAM_{self.camera_id:02d}",
            "camera_id": self.camera_id,
            "name": self.camera_name,
            "source": self.effective_rtsp_url or "",
            "floor": self.floor,
            "device_group": self.device_group,
            "area_id": self.area_id,
            "is_active": self.is_active,
        }

    def __str__(self):
        return f"{self.camera_name}"


# ─────────────────────────────────────────────
@dataclass
class AttendanceSession:
    # 11 cols từ DB + 2 join fields
    session_id:    int
    session_code:  str
    class_id:      int
    subject_name:  str
    session_date:  Optional[date]     = None
    start_time:    Optional[datetime] = None   # NULL khi chưa bắt đầu
    end_time:      Optional[datetime] = None
    status:        str                = "PENDING"
    present_count: int                = 0
    absent_count:  int                = 0
    created_at:    Optional[datetime] = None
    # Join fields
    class_name:    Optional[str]      = None
    class_code:    Optional[str]      = None

    @property
    def is_active_session(self) -> bool:
        return self.status == "ACTIVE"

    def start_time_str(self) -> str:
        if self.start_time is None:
            return "—"
        return self.start_time.strftime("%H:%M") if hasattr(self.start_time, "strftime") else str(self.start_time)

    def __str__(self):
        return f"{self.subject_name} — {self.session_date}"


# ─────────────────────────────────────────────
@dataclass
class AttendanceRecord:
    record_id:         int
    session_id:        int
    student_id:        int
    check_in_time:     Optional[datetime] = None
    status:            str                = "ABSENT"
    recognition_score: Optional[float]   = None
    snapshot_path:     Optional[str]      = None
    camera_id:         Optional[int]      = None
    created_at:        Optional[datetime] = None
    # Join fields
    student_code:      Optional[str]      = None
    full_name:         Optional[str]      = None
    class_name:        Optional[str]      = None

    @property
    def is_present(self) -> bool:
        return self.status == "PRESENT"

    @property
    def score_percent(self) -> str:
        if self.recognition_score is None:
            return "—"
        return f"{self.recognition_score * 100:.1f}%"


# ─────────────────────────────────────────────
@dataclass
class EmbeddingCache:
    """
    Cache toàn bộ embeddings vào RAM.
    student_ids[i] ↔ embeddings[i] — index tương ứng.
    """
    student_ids:   list = field(default_factory=list)
    student_codes: list = field(default_factory=list)
    full_names:    list = field(default_factory=list)
    class_ids:     list = field(default_factory=list)
    class_names:   list = field(default_factory=list)
    class_codes:   list = field(default_factory=list)
    embeddings:    Optional[np.ndarray] = None   # shape (N, 512)

    @property
    def size(self) -> int:
        return len(self.student_ids)

    @property
    def is_empty(self) -> bool:
        return self.size == 0

    def clear(self):
        self.student_ids.clear()
        self.student_codes.clear()
        self.full_names.clear()
        self.class_ids.clear()
        self.class_names.clear()
        self.class_codes.clear()
        self.embeddings = None

    def __repr__(self):
        shape = self.embeddings.shape if self.embeddings is not None else None
        return f"EmbeddingCache(size={self.size}, shape={shape})"


# ─────────────────────────────────────────────
@dataclass
class Building:
    ma_toa: str
    ten_toa: str


# ─────────────────────────────────────────────
@dataclass
class Room:
    ma_phong: str
    tang: int
    ma_toa: str
    ten_phong: str