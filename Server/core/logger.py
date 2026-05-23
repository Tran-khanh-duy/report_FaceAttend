import sys
import logging
from loguru import logger
import time
from fastapi import Request

def setup_logging():
    """Cấu hình Loguru chuẩn Production cho hệ thống Face Attendance."""
    # Xóa cấu hình mặc định
    logger.remove()
    
    # Định dạng chung
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # 1. Console Output (Dành cho Dev/Docker logs)
    logger.add(
        sys.stdout, 
        format=log_format, 
        level="INFO", 
        colorize=True
    )

    # 2. File: api.log (Ghi nhận hoạt động API, Latency, Queue)
    logger.add(
        "logs/api.log",
        rotation="10 MB",     # Cắt file khi đạt 10MB
        retention="30 days",  # Giữ file trong 30 ngày
        format=log_format,
        level="INFO",
        enqueue=True,         # Async logging (Không chặn thread chính)
        filter=lambda record: "attendance" not in record["extra"] and record["level"].name != "ERROR"
    )

    # 3. File: attendance.log (Dành riêng cho kết quả điểm danh, Spoofing)
    logger.add(
        "logs/attendance.log",
        rotation="10 MB",
        retention="60 days",  # Giữ lâu hơn để audit
        format=log_format,
        level="INFO",
        enqueue=True,
        filter=lambda record: "attendance" in record["extra"]
    )

    # 4. File: error.log (Lưu tất cả lỗi ngoại lệ)
    logger.add(
        "logs/error.log",
        rotation="10 MB",
        retention="30 days",
        format=log_format,
        level="ERROR",
        enqueue=True
    )
    
    # Kéo log từ thư viện chuẩn (logging) về Loguru
    class InterceptHandler(logging.Handler):
        def emit(self, record):
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame, depth = logging.currentframe(), 2
            while frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    # Ép Uvicorn và FastAPI dùng Loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for _log in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        _logger = logging.getLogger(_log)
        _logger.handlers = [InterceptHandler()]

# Gọi cấu hình ngay khi file được import
setup_logging()

# Tạo logger chuyên biệt cho điểm danh (Sẽ ghi vào attendance.log)
attendance_logger = logger.bind(attendance=True)

async def api_latency_middleware(request: Request, call_next):
    """FastAPI Middleware đo thời gian xử lý API."""
    start_time = time.time()
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info(f"API {request.method} {request.url.path} - {response.status_code} - {process_time:.2f}ms")
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(f"API ERROR {request.method} {request.url.path} - {process_time:.2f}ms | {e}")
        raise e
