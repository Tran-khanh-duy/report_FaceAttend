"""
pyqt_integration_example.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snippet tích hợp CameraManager vào PyQt6 MainWindow.

Đây là file ví dụ — copy các đoạn code cần thiết vào MainWindow thực tế.
KHÔNG import file này trực tiếp vào ứng dụng.

Lưu ý quan trọng về Windows + multiprocessing:
  Trên Windows, multiprocessing dùng "spawn" (không phải "fork").
  Mọi code khởi động process PHẢI nằm trong:
      if __name__ == "__main__":
  Hoặc trong hàm được bảo vệ tương đương. Nếu không, child process sẽ
  re-import toàn bộ module và tạo vòng lặp vô tận.
"""
import sys
import multiprocessing as mp

import cv2
import numpy as np
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QWidget,
    QGridLayout, QVBoxLayout,
)

from core.camera_subsystem import CameraManager, CameraConfig


class CameraDisplayWidget(QWidget):
    """Widget hiển thị một camera feed + chỉ báo trạng thái."""

    def __init__(self, cam_id: str, parent=None):
        super().__init__(parent)
        self.cam_id = cam_id

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Label hiển thị video frame
        self.lbl_frame = QLabel()
        self.lbl_frame.setFixedSize(320, 240)
        self.lbl_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_frame.setStyleSheet("background: #111; border-radius: 8px;")
        layout.addWidget(self.lbl_frame)

        # Label trạng thái Online/Offline
        self.lbl_status = QLabel(f"🟡 {cam_id} — Đang kết nối...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_status)

    def update_frame(self, frame: np.ndarray, is_online: bool) -> None:
        """Cập nhật frame và trạng thái. Gọi từ QTimer slot."""
        # Cập nhật trạng thái
        if is_online:
            self.lbl_status.setText(f"🟢 {self.cam_id} — ONLINE")
            self.lbl_status.setStyleSheet("color: #00c853; font-weight: bold;")
        else:
            self.lbl_status.setText(f"🔴 {self.cam_id} — OFFLINE")
            self.lbl_status.setStyleSheet("color: #ff1744; font-weight: bold;")

        # Convert BGR numpy → QImage không copy (dùng .data trực tiếp)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        q_img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img).scaled(
            320, 240,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_frame.setPixmap(pixmap)


class MainWindow(QMainWindow):
    """
    Main window tích hợp CameraManager.
    Thay thế lớp MainWindow hiện tại của dự án với các đoạn code dưới đây.
    """

    # ── Khởi tạo ──────────────────────────────────────────────────────────
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FaceAttend AI — Camera Monitor")

        # ── Cấu hình danh sách camera ────────────────────────────────────
        camera_configs = [
            CameraConfig("CAM_01", "rtsp://admin:pass@192.168.1.101:554/stream", "192.168.1.101"),
            CameraConfig("CAM_02", "rtsp://admin:pass@192.168.1.102:554/stream", "192.168.1.102"),
            CameraConfig("CAM_03", "rtsp://admin:pass@192.168.1.103:554/stream", "192.168.1.103"),
            CameraConfig("CAM_04", "rtsp://admin:pass@192.168.1.104:554/stream", "192.168.1.104"),
            CameraConfig("CAM_05", "rtsp://admin:pass@192.168.1.105:554/stream", "192.168.1.105"),
        ]

        # ── Khởi động Camera Manager ──────────────────────────────────────
        self._cam_manager = CameraManager(camera_configs)
        self._cam_manager.start_all()

        # ── Xây dựng UI ───────────────────────────────────────────────────
        self._cam_widgets: dict[str, CameraDisplayWidget] = {}
        central = QWidget()
        grid = QGridLayout(central)

        for i, cfg in enumerate(camera_configs):
            widget = CameraDisplayWidget(cfg.camera_id)
            self._cam_widgets[cfg.camera_id] = widget
            grid.addWidget(widget, i // 3, i % 3)

        self.setCentralWidget(central)

        # ── QTimer cập nhật frame mỗi 100ms (10 FPS cho monitor view) ────
        # 100ms: đủ mượt cho giám sát, không gây tải nặng cho UI thread
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._update_camera_displays)
        self._refresh_timer.start(100)

    # ── QTimer slot ───────────────────────────────────────────────────────
    def _update_camera_displays(self) -> None:
        """
        Đọc frame từ Shared Memory và cập nhật UI.
        Hàm này chạy trong UI thread — phải nhanh, không blocking.
        get_frame_and_status() chỉ là copy numpy array → thường < 1ms.
        """
        for cam_id, widget in self._cam_widgets.items():
            frame, is_online = self._cam_manager.get_frame_and_status(cam_id)
            if frame is not None:
                widget.update_frame(frame, is_online)

    # ── Cleanup khi đóng cửa sổ ───────────────────────────────────────────
    def closeEvent(self, event) -> None:
        """
        Thứ tự cleanup là BẮT BUỘC:
          1. Dừng timer trước — không gọi get_frame_and_status khi SHM đang cleanup
          2. stop_all() — signal + join processes
          3. SHM cleanup nằm BÊN TRONG stop_all() sau khi processes đã dừng
        """
        self._refresh_timer.stop()
        self._cam_manager.stop_all(join_timeout=5.0)
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # QUAN TRỌNG trên Windows: phải gọi freeze_support() và set start method
    mp.freeze_support()
    mp.set_start_method("spawn", force=True)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
