"""
main_edge.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry point cho Mini PC (Edge Box)
Chạy giao diện Kiosk — chỉ có camera feed + nhận diện

Cách khởi động:
    python main_edge.py

Cấu hình:
    Sửa file .env.edge hoặc truyền biến môi trường
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import sys
import time
from pathlib import Path
from loguru import logger

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# Tối ưu kết nối siêu tốc cho Camera IP (bỏ qua bước phân tích stream rườm rà)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|analyzeduration;500000|probesize;5000000"
# Load .env.edge nếu tồn tại
ROOT = Path(__file__).parent
env_file = ROOT / ".env.edge"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)

sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT.parent / "Server"))

# Tạo các thư mục cần thiết
for folder in ["logs", "models", "database", "core"]:
    (ROOT / folder).mkdir(parents=True, exist_ok=True)

# ── PHẢI GỌI TRƯỚC MỌI IMPORT KHÁC ──────────────────────────────────────────
# Cài đặt Loguru: ghi ra logs/edge.log, logs/error.log, logs/attend.log
# với rotation 10MB, nén zip file cũ, giữ 14 ngày.
from core.logger import setup_edge_logging
_debug_mode = os.getenv("DEBUG", "false").lower() == "true"
setup_edge_logging(log_level="INFO", debug=_debug_mode)
# ─────────────────────────────────────────────────────────────────────────────

from config import edge_config
from headless_processor import headless_processor
from edge_client import edge_client

# Import thêm thư viện để chạy ngầm
import threading
from hardware_monitor import hardware_monitor_loop

def run_headless():
    """Chạy Edge AI ở chế độ Headless (Không giao diện)."""
    
    print("=" * 60)
    print(f"  FACEATTEND EDGE (HEADLESS) — {edge_config.device_name}")
    print(f"  Server: {edge_config.server_url}")
    cam_list = edge_config.camera_list
    if cam_list:
        print(f"  Camera ({len(cam_list)} cái từ DB):")
        for c in cam_list:
            print(f"    [{c.get('id','?')}] {c.get('name','?')} | {c.get('source','N/A')}")
    else:
        print("  Camera: Đang pull từ Server... (sẽ retry khi khởi động)")
    print(f"  AI Processing mode: ACTIVE")
    print("=" * 60)

    try:
        # 1. Kích hoạt luồng Hardware Monitor chạy ngầm (Gửi dữ liệu mỗi 60s)
        server_url = edge_config.server_url or "http://127.0.0.1:9696"
        edge_id = edge_config.device_name or "MINI_PC_01"
        threading.Thread(
            target=hardware_monitor_loop,
            args=(server_url, edge_id),
            daemon=True
        ).start()
        logger.info(f"Đã khởi động luồng Hardware Monitor (Gửi tới {server_url})")

        # [FIX] 2. Khởi động Webhook Receiver — lắng nghe push từ Server khi có
        #          học viên mới đăng ký, không phải chờ polling 10 giây nữa
        try:
            from webhook_receiver import start_webhook_server
            start_webhook_server(edge_client_ref=edge_client)
            logger.success("✅ Webhook receiver đã khởi động — sẵn sàng nhận push từ Server.")
        except Exception as wh_err:
            logger.warning(f"⚠️ Webhook receiver không khởi động được (hệ thống vẫn hoạt động bình thường): {wh_err}")

        # 3. Khởi động xử lý AI
        headless_processor.start()
    except KeyboardInterrupt:
        logger.info("Dừng hệ thống theo yêu cầu (KeyboardInterrupt)...")
    except Exception as e:
        logger.exception(f"Lỗi nghiêm trọng: {e}")
    finally:
        # Dọn dẹp
        headless_processor.stop()
        edge_client.stop()
        logger.info("Hệ thống đã tắt an toàn.")

if __name__ == "__main__":
    run_headless()
