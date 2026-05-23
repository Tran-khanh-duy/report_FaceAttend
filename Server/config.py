"""
config.py — Cấu hình trung tâm (Đã tối ưu hóa cho Server CPU i5-12400)
"""
import os
import cv2
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

# =====================================================================
# TỐI ƯU HÓA HỆ THỐNG MẠNG & OPENCV (ZERO-LATENCY CHO RTSP)
# Loại bỏ buffer mặc định của FFMPEG, ép dùng TCP để tránh rớt gói tin
# =====================================================================
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|analyzeduration;1000000|probesize;1000000"

# ─────────────────────────────────────────────
#  ĐƯỜNG DẪN CƠ BẢN
# ─────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
MODELS_DIR    = BASE_DIR / "models"
ASSETS_DIR    = BASE_DIR / "assets"
SNAPSHOTS_DIR = ASSETS_DIR / "snapshots"
LOGS_DIR      = BASE_DIR / "logs"
REPORTS_DIR   = BASE_DIR / "reports" / "output"

for _dir in [MODELS_DIR, SNAPSHOTS_DIR, LOGS_DIR, REPORTS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
#  DATABASE — SQL SERVER / MYSQL
# ─────────────────────────────────────────────
@dataclass
class DatabaseConfig:
    host:     str = os.getenv("DB_HOST", "localhost")
    port:     int = int(os.getenv("DB_PORT", "3306"))
    database: str = os.getenv("DB_NAME", "qlsv")
    username: str = os.getenv("DB_USER", "root")
    password: str = os.getenv("DB_PASS", "Quockhai@24092003")

    @property
    def connection_args(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.username,
            "password": self.password,
            "consume_results": True,
        }

# ─────────────────────────────────────────────
#  AI / NHẬN DẠNG KHUÔN MẶT (TỐI ƯU CHO i5-12400)
# ─────────────────────────────────────────────
@dataclass
class AIConfig:
    model_name:       str   = "buffalo_s"  # Model nhỏ gọn, cực nhanh cho CPU
    model_pack_dir:   Path  = MODELS_DIR

    # Sử dụng CPU (-1) thay vì GPU
    gpu_ctx_id:       int   = -1   

    # Ưu tiên OpenVINO để tăng tốc x2-x3 trên chip Intel, fallback về CPU thường
    onnx_providers: list = field(
        default_factory=lambda: [
            "OpenVINOExecutionProvider",
            "CPUExecutionProvider"
        ]
    )

    # Giới hạn luồng (Threads) để không gây nghẽn CPU các tác vụ khác (i5-12400 có 6 nhân)
    provider_options: list = field(
        default_factory=lambda: [
            {}, # Trống cho OpenVINO (để thư viện tự quản lý)
            {"intra_op_num_threads": 4, "inter_op_num_threads": 1} # Dành 4 luồng cho CPUExecutionProvider
        ]
    )

    # Kích thước lưới quét: 480x480 là cân bằng hoàn hảo giữa tốc độ và chất lượng cho Đăng ký
    det_size:         tuple = (480, 480) 

    # ── Threshold & Margin ────────────────────────────────────────────────────
    # Ngưỡng nhận diện: 0.50 là "điểm vàng" thực chiến cho buffalo_l/s + cosine similarity.
    # Tăng lên 0.55 nếu vẫn còn False Positive; hạ về 0.45 nếu bỏ sót quá nhiều.
    # KHÔNG dùng 0.65+ vì cosine similarity sau L2-normalize thường nằm trong [0.3, 0.7].
    recognition_threshold: float = float(os.getenv("AI_THRESHOLD", "0.50"))

    # Margin kiểm tra chéo (Task 2): Nếu best_score - second_best_score < margin
    # → hệ thống "đang bối rối" → từ chối nhận diện (gán Unknown).
    # Giá trị 0.08 (~8%) là ngưỡng phân biệt đủ tin cậy trong thực tế camera IP.
    recognition_margin:    float = float(os.getenv("AI_MARGIN",    "0.08"))

    # ── Enrollment ────────────────────────────────────────────────────────────
    # Lấy 12 đến 15 ảnh chất lượng cao để tính Embedding trung bình
    min_enroll_photos:       int   = 12
    max_enroll_photos:       int   = 15
    embedding_size:          int   = 512
    attendance_cooldown_sec: int   = int(os.getenv("ATTENDANCE_COOLDOWN", "60"))

    # Yêu cầu mặt rõ nét khi đăng ký (0.75 trở lên)
    min_face_det_score: float = float(os.getenv("MIN_FACE_SCORE", "0.75"))




# ─────────────────────────────────────────────
#  CAMERA (CẤU HÌNH SERVER GỌI TRỰC TIẾP RTSP)
# ─────────────────────────────────────────────
@dataclass
class CameraConfig:
    # URL Camera dùng để đăng ký khuôn mặt
    source: str = os.getenv("REGISTRATION_CAM_RTSP", "rtsp://admin:a1234567@192.168.1.17:554/cam/realmonitor?channel=1&subtype=0")
    fps: int = 30
    width:  int = 1280
    height: int = 720
    reconnect_delay_sec: int = 2
    max_reconnect_tries: int = 10

    # Bỏ qua frame rác: Xử lý 1 frame, bỏ qua 2 frame tiếp theo (tiết kiệm CPU)
    process_every_n_frames: int = 3

    @property
    def is_ip_camera(self) -> bool:
        return str(self.source).startswith("rtsp://") or \
               str(self.source).startswith("http://")

# Danh sách Camera — ĐÃ XOÁ HARDCODE
# Giờ được lưu trong bảng `Cameras` của MySQL và
# được lấy qua camera_repo.get_all() / get_for_edge()
# Xem: database/repositories.py → CameraRepository

# ─────────────────────────────────────────────
#  BÁO CÁO & ỨNG DỤNG
# ─────────────────────────────────────────────
@dataclass
class ReportConfig:
    output_dir:        Path  = REPORTS_DIR
    institution_name:  str   = os.getenv("INSTITUTION_NAME", "Trung tâm Đào tạo XYZ")
    institution_logo:  str   = ""
    excel_template:    str   = "default"
    date_format:       str   = "%d/%m/%Y"
    datetime_format:   str   = "%d/%m/%Y %H:%M:%S"

@dataclass
class AppConfig:
    app_name:    str  = "Hệ thống Điểm danh Nhận dạng Khuôn mặt"
    app_version: str  = "1.0.0"
    debug_mode:  bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level:   str  = "DEBUG" if debug_mode else "INFO"
    log_dir:     Path = LOGS_DIR
    save_snapshots: bool = True
    snapshot_dir:   Path = SNAPSHOTS_DIR
    window_width:  int = 1280
    window_height: int = 800
    
    camera_groups: list = field(default_factory=lambda: ["KTX E1", "KTX E2", "KTX E3", "KTX E4", "KTX E5", "KTX E6"])

# ─────────────────────────────────────────────
#  ANTI-SPOOFING (TẮT HOÀN TOÀN NHƯ YÊU CẦU)
# ─────────────────────────────────────────────
@dataclass
class AntiSpoofConfig:
    enabled:   bool = False 
    model_dir: Path = BASE_DIR / "Silent-Face-Anti-Spoofing-master" / "resources" / "anti_spoof_models"
    device_id: int = -1  # CPU
    threshold: float = 0.80 

# ─────────────────────────────────────────────
#  WAKE-ON-LAN CONFIG (GIỮ NGUYÊN HOẶC XÓA NẾU KHÔNG CÒN MINIPC)
# ─────────────────────────────────────────────
WOL_MINI_PCS = [
    {"name": "Mini PC KTX E4", "mac_address": "54-BF-64-9C-79-AC", "location": "KTX E4 - Tầng 4", "device_name": "Edge Box 01", "broadcast": "192.168.1.255"},
    {"name": "Mini PC KTX E5", "mac_address": "AA:BB:CC:DD:EE:02", "location": "KTX E5 - Tầng 5", "device_name": "Edge Box 02", "broadcast": "192.168.1.255"},
]

# ─────────────────────────────────────────────
#  FLOOR-CLASS MAPPING
# ─────────────────────────────────────────────
FLOOR_CLASS_MAPPING: dict = {
    "KTX E4": {
        "gender": "Nam",
        "floors": {
            1: ["B4D14", "B5D14", "B6D14"],
            2: ["B1-LT8", "B2-LT8", "B3D14"],
            3: ["B2D12", "B3D12", "B4D12", "B5D12"],
            4: ["B3D13", "B4D13", "B5D13", "B6D13"],
            5: ["VB2K3"],
        }
    },
    "KTX E5": {
        "gender": "Nam",
        "floors": {
            1: ["B1-VB2K4", "B2-VB2K4", "B3-VB2K4"],
            2: ["VB2C1"],
            3: ["B3D15", "B4D15", "B5D15", "B6D15", "B7D15", "B8D15"],
            4: ["B9D15", "B10D15", "B11D15"],
        }
    },
    "KTX E3": {
        "gender": "Nữ",
        "floors": {
            1: ["B3D13", "B4D13", "B5D13", "B6D13", "B3D14", "B4D14", "B5D14", "B6D14"],
            2: ["VB2K3", "B1-VB2K4", "B2-VB2K4", "B3-VB2K4", "VB2C1", "B2D12", "B3D12", "B4D12", "B5D12", "B1-LT8", "B2-LT8"],
            3: ["B3D15", "B4D15", "B5D15", "B6D15", "B7D15", "B8D15", "B9D15", "B10D15", "B11D15"],
        }
    },
}

# ─────────────────────────────────────────────
#  EDGE CONFIG (CÓ THỂ BỎ QUA NẾU ĐÃ BỎ HẲN MINI PC)
# ─────────────────────────────────────────────
@dataclass
class EdgeConfig:
    server_url:           str  = os.getenv("EDGE_SERVER_URL", "http://127.0.0.1:9696")
    api_key:              str  = os.getenv("EDGE_API_KEY", "faceattend_secret_2026")
    device_name:          str  = os.getenv("EDGE_DEVICE_NAME", "Edge Box 01")
    # Lọc theo nhóm thiết bị (ví dụ: 'KTX E4'). Rỗng = lấy tất cả.
    device_group:         str  = os.getenv("EDGE_DEVICE_GROUP", "")

    sync_interval_sec:    int  = int(os.getenv("EDGE_SYNC_INTERVAL", "30"))
    embedding_refresh_min: int = int(os.getenv("EDGE_EMBED_REFRESH", "10"))
    attendance_cooldown:  int  = int(os.getenv("EDGE_COOLDOWN", "60"))
    process_every_n:      int  = int(os.getenv("EDGE_PROCESS_N", "1"))
    fullscreen:           bool = os.getenv("EDGE_FULLSCREEN", "true").lower() == "true"
    show_fps:             bool = os.getenv("EDGE_SHOW_FPS", "true").lower() == "true"
    auto_start:           bool = os.getenv("EDGE_AUTO_START", "true").lower() == "true"

    # camera_list ĐÃ XOÁ HARDCODE.
    # Mini PC sẽ gọi GET /api/system/cameras/edge-list khi khởi động
    # và lưu vào thuộc tính này thông qua EdgeClient.pull_camera_list().
    camera_list: list = field(default_factory=list)

db_config         = DatabaseConfig()
ai_config         = AIConfig()
anti_spoof_config = AntiSpoofConfig()
camera_config     = CameraConfig()
report_config     = ReportConfig()
app_config        = AppConfig()
edge_config       = EdgeConfig()