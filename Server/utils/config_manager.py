"""
utils/config_manager.py
Tiện ích lưu trữ cấu hình vào file .env.
"""
import os
from pathlib import Path
from loguru import logger

def save_to_env(updates: dict):
    """
    Cập nhật hoặc thêm mới các biến môi trường vào file .env.
    Args:
        updates: Dictionary chứa {KEY: VALUE}.
    """
    env_path = Path(__file__).parent.parent / ".env"
    
    # Đọc nội dung hiện tại
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # Chuyển thành dict để dễ thao tác
    env_data = {}
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            env_data[key.strip()] = val.strip()

    # Ghi đè các giá trị mới
    for k, v in updates.items():
        env_data[k] = str(v)

    # Ghi lại vào file
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in sorted(env_data.items()):
                f.write(f"{k}={v}\n")
        logger.info(f"Đã lưu các cấu hình mới vào .env: {list(updates.keys())}")
        return True
    except Exception as e:
        logger.error(f"Lỗi khi lưu file .env: {e}")
        return False
