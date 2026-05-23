"""
main.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry point — chạy ứng dụng FaceAttend

Cách khởi động:
    cd \face_attendance
    py main.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import sys
from pathlib import Path

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Đảm bảo thư mục gốc trong PATH
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Tạo các thư mục cần thiết nếu chưa có
for folder in ["logs", "models", "assets/snapshots", "assets/profiles", "reports"]:
    (ROOT / folder).mkdir(parents=True, exist_ok=True)

from ui.main_window import run

if __name__ == "__main__":
    run()
