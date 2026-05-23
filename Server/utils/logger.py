import sys
from pathlib import Path
from loguru import logger

def setup_logger(log_dir: Path = None, level: str = "INFO"):
    logger.remove()
    logger.add(
        sys.stdout,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level:<8}</level> | <cyan>{name}:{function}:{line}</cyan> - {message}",
        colorize=True,
    )
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "app_{time:YYYY-MM-DD}.log",
            level=level,
            rotation="1 day",
            retention="30 days",
            encoding="utf-8",
        )
    return logger