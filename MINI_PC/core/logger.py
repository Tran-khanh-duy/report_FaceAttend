"""
core/logger.py — Cấu hình Loguru chuẩn Production cho MINI_PC (Edge Box)

Sao chép kiến trúc từ Server/core/logger.py nhưng điều chỉnh:
- Bỏ FastAPI/Uvicorn intercept (Edge không dùng)
- Thêm file edge.log riêng để dễ theo dõi trên Mini PC
- Gọi setup_edge_logging() trong main_edge.py TRƯỚC mọi import khác
"""
import sys
import logging
from pathlib import Path
from loguru import logger

# ── Đường dẫn logs nằm ngay trong thư mục MINI_PC/logs/ ──────────────────────
_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Format chung — dùng cho cả console lẫn file
_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# Format đơn giản hơn cho console (không cần tên module dài)
_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<level>{message}</level>"
)


def setup_edge_logging(log_level: str = "INFO", debug: bool = False) -> None:
    """
    Cài đặt Loguru cho MINI_PC Edge Box.

    Ghi vào 3 file:
    - ``logs/edge.log``   — mọi hoạt động hệ thống (INFO+)
    - ``logs/error.log``  — chỉ ERROR+ (để debug nhanh)
    - ``logs/attend.log`` — điểm danh thành công / spoof (bind với extra)

    Args:
        log_level: Mức tối thiểu cho console. Mặc định ``"INFO"``.
        debug:     Nếu ``True``, console sẽ hiện cả ``DEBUG``.
    """
    # 1. Xoá handler mặc định (stderr không format)
    logger.remove()

    console_level = "DEBUG" if debug else log_level

    # 2. Console — ra stdout để bat/systemd bắt được
    logger.add(
        sys.stdout,
        format=_CONSOLE_FORMAT,
        level=console_level,
        colorize=True,
        enqueue=False,          # Đồng bộ để banner in ngay lập tức
    )

    # 3. File: edge.log — ghi mọi thứ từ INFO trở lên
    logger.add(
        str(_LOGS_DIR / "edge.log"),
        format=_LOG_FORMAT,
        level="INFO",
        rotation="10 MB",       # Cắt file khi đạt 10 MB
        retention="14 days",    # Giữ 14 ngày (Edge storage nhỏ hơn Server)
        compression="zip",      # Nén file cũ để tiết kiệm ổ đĩa
        encoding="utf-8",
        enqueue=True,           # Async — không block pipeline AI
    )

    # 4. File: error.log — chỉ ERROR và CRITICAL
    logger.add(
        str(_LOGS_DIR / "error.log"),
        format=_LOG_FORMAT,
        level="ERROR",
        rotation="5 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # 5. File: attend.log — chỉ các record có extra["attendance"] = True
    logger.add(
        str(_LOGS_DIR / "attend.log"),
        format=_LOG_FORMAT,
        level="INFO",
        rotation="10 MB",
        retention="60 days",    # Giữ lâu hơn để audit điểm danh
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        filter=lambda record: record["extra"].get("attendance", False),
    )

    # 6. Bắt log từ thư viện chuẩn (requests, urllib3, ...) về Loguru
    class _InterceptHandler(logging.Handler):
        """Chuyển hướng stdlib logging sang Loguru."""
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame, depth = logging.currentframe(), 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Tắt bớt log ồn ào từ thư viện mạng
    for _noisy in ("urllib3", "requests", "httpcore", "httpx"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    logger.info(
        f"📋 Edge Logger đã khởi động | logs → {_LOGS_DIR.resolve()} | level={console_level}"
    )


# ── Logger chuyên biệt cho điểm danh ─────────────────────────────────────────
# Dùng: attendance_logger.info("Điểm danh thành công: ...")
# → sẽ ghi vào attend.log VÀ edge.log
attendance_logger = logger.bind(attendance=True)
