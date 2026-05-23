import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load biến môi trường từ file .env
load_dotenv()

# =====================================================================
# TỐI ƯU HÓA HỆ THỐNG MẠNG & OPENCV (ZERO-LATENCY CHO RTSP)
# =====================================================================
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|analyzeduration;1000000|probesize;1000000"

# ─────────────────────────────────────────────
#  ĐƯỜNG DẪN CƠ BẢN
# ─────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
MODELS_DIR    = BASE_DIR / "models"
ASSETS_DIR    = BASE_DIR / "assets"
SNAPSHOTS_DIR = ASSETS_DIR / "snapshots"
LOGS_DIR      = BASE_DIR / "logs"
REPORTS_DIR   = BASE_DIR / "reports" / "output"

for _dir in [MODELS_DIR, SNAPSHOTS_DIR, LOGS_DIR, REPORTS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
#  SERVER CONFIG
# ─────────────────────────────────────────────
@dataclass
class ServerConfig:
    host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    port: int = int(os.getenv("SERVER_PORT", "9696"))
    allowed_ips: list = field(default_factory=lambda: [ip.strip() for ip in os.getenv("ALLOWED_IPS", "127.0.0.1").split(",")])
    device_token: str = os.getenv("DEVICE_TOKEN", "faceattend_secret_2026")
    workers: int = int(os.getenv("SERVER_WORKERS", "1"))
    reload: bool = os.getenv("SERVER_RELOAD", "false").lower() == "true"

# ─────────────────────────────────────────────
#  DATABASE — MYSQL
# ─────────────────────────────────────────────
@dataclass
class DatabaseConfig:
    host:     str = os.getenv("DB_HOST", "localhost")
    port:     int = int(os.getenv("DB_PORT", "3306"))
    database: str = os.getenv("DB_NAME", "qlsv")
    username: str = os.getenv("DB_USER", "root")
    password: str = os.getenv("DB_PASS", "")

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
#  REDIS CONFIG
# ─────────────────────────────────────────────
@dataclass
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", "6379"))
    db: int = int(os.getenv("REDIS_DB", "0"))
    password: str = os.getenv("REDIS_PASSWORD", "")

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"

# ─────────────────────────────────────────────
#  AI / NHẬN DẠNG KHUÔN MẶT
# ─────────────────────────────────────────────
@dataclass
class AIConfig:
    model_name:       str   = "buffalo_s"
    model_pack_dir:   Path  = MODELS_DIR
    gpu_ctx_id:       int   = -1
    onnx_providers: list = field(
        default_factory=lambda: [
            "OpenVINOExecutionProvider",
            "CPUExecutionProvider"
        ]
    )
    provider_options: list = field(
        default_factory=lambda: [
            {}, 
            {"intra_op_num_threads": 4, "inter_op_num_threads": 1}
        ]
    )
    det_size:         tuple = (480, 480) 
    recognition_threshold: float = float(os.getenv("AI_THRESHOLD", "0.65")) 
    min_enroll_photos:  int = 12
    max_enroll_photos:  int = 15
    embedding_size:   int   = 512
    attendance_cooldown_sec: int = int(os.getenv("ATTENDANCE_COOLDOWN", "60"))
    min_face_det_score: float = float(os.getenv("MIN_FACE_SCORE", "0.75"))

# ─────────────────────────────────────────────
#  CAMERA CONFIG
# ─────────────────────────────────────────────
@dataclass
class CameraConfig:
    source: str = os.getenv("REGISTRATION_CAM_RTSP", "0")
    fps: int = 30
    width:  int = 1280
    height: int = 720
    reconnect_delay_sec: int = 2
    max_reconnect_tries: int = 10
    process_every_n_frames: int = 3

    @property
    def is_ip_camera(self) -> bool:
        return str(self.source).startswith("rtsp://") or \
               str(self.source).startswith("http://")

# ─────────────────────────────────────────────
#  ANTI-SPOOFING
# ─────────────────────────────────────────────
@dataclass
class AntiSpoofConfig:
    enabled:   bool = False 
    model_dir: Path = BASE_DIR / "Silent-Face-Anti-Spoofing-master" / "resources" / "anti_spoof_models"
    device_id: int = -1
    threshold: float = 0.80 

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
#  MAPPING
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

WOL_MINI_PCS = [
    {"name": "Mini PC KTX E4", "mac_address": "54-BF-64-9C-79-AC", "location": "KTX E4 - Tầng 4", "device_name": "Edge Box 01", "broadcast": "192.168.1.255"},
    {"name": "Mini PC KTX E5", "mac_address": "AA:BB:CC:DD:EE:02", "location": "KTX E5 - Tầng 5", "device_name": "Edge Box 02", "broadcast": "192.168.1.255"},
]

@dataclass
class EdgeConfig:
    server_url:           str  = os.getenv("EDGE_SERVER_URL", "http://127.0.0.1:9696")
    api_key:              str  = os.getenv("EDGE_API_KEY", "faceattend_secret_2026")
    camera_id:            str  = os.getenv("EDGE_CAMERA_ID", "CAM_01")
    camera_source:        str  = os.getenv("EDGE_CAMERA_SOURCE", "0") 
    device_name:          str  = os.getenv("EDGE_DEVICE_NAME", "Edge Box 01")
    sync_interval_sec:    int  = int(os.getenv("EDGE_SYNC_INTERVAL", "30"))
    embedding_refresh_min: int = int(os.getenv("EDGE_EMBED_REFRESH", "10")) 
    attendance_cooldown:  int  = int(os.getenv("EDGE_COOLDOWN", "60"))
    process_every_n:      int  = int(os.getenv("EDGE_PROCESS_N", "1"))
    fullscreen:           bool = os.getenv("EDGE_FULLSCREEN", "true").lower() == "true"
    show_fps:             bool = os.getenv("EDGE_SHOW_FPS", "true").lower() == "true"
    auto_start:           bool = os.getenv("EDGE_AUTO_START", "true").lower() == "true"

server_config     = ServerConfig()
db_config         = DatabaseConfig()
redis_config      = RedisConfig()
ai_config         = AIConfig()
anti_spoof_config = AntiSpoofConfig()
camera_config     = CameraConfig()
report_config     = ReportConfig()
app_config        = AppConfig()
edge_config       = EdgeConfig()
