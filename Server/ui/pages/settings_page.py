"""
ui/pages/settings_page.py
Trang cấu hình hệ thống — Cải tiến: Bỏ mục thừa, thêm mục thực tế, lưu .env.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QLineEdit, QCheckBox, QComboBox, QGroupBox, QFormLayout,
    QScrollArea, QFrame, QMessageBox, QDoubleSpinBox, QSpinBox
)
from PyQt6.QtCore import Qt
from loguru import logger
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.styles.theme import Colors, card_style
from config import db_config, ai_config, camera_config, app_config, report_config
from utils.config_manager import save_to_env

class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_current_settings()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(20)

        # --- Header ---
        header = QVBoxLayout()
        title = QLabel("Cấu Hình Hệ Thống")
        title.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {Colors.TEXT};")
        subtitle = QLabel("Tinh chỉnh thông số vận hành AI và Kết nối dữ liệu")
        subtitle.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_DIM};")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        # --- Scroll Area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        container = QWidget()
        self.container_layout = QVBoxLayout(container)
        self.container_layout.setSpacing(25)

        # 1. Nhóm AI Engine
        self._group_ai = self._create_group("🧠  CẤU HÌNH NHẬN DẠNG (AI)")
        ai_form = QFormLayout(self._group_ai)
        self._set_form_style(ai_form)

        # Helper to create advice label
        def help_lbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 11px; color: {Colors.TEXT_DARK}; font-style: italic; margin-bottom: 5px;")
            return lbl

        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(0.1, 0.95)
        self.spin_threshold.setSingleStep(0.05)
        ai_form.addRow("Ngưỡng nhận dạng:", self.spin_threshold)
        ai_form.addRow("", help_lbl("Độ tin cậy tối thiểu (0.60 là chuẩn). Cao hơn = chính xác hơn nhưng khó nhận diện hơn."))
        
        self.spin_min_face = QDoubleSpinBox()
        self.spin_min_face.setRange(0.3, 0.9)
        self.spin_min_face.setSingleStep(0.05)
        ai_form.addRow("Độ nét khuôn mặt:", self.spin_min_face)
        ai_form.addRow("", help_lbl("Chỉ nhận diện khi mặt rõ nét. Giúp tránh báo danh nhầm do ảnh mờ."))

        self.cmb_det_size = QComboBox()
        self.cmb_det_size.addItems(["320x320 (Nhanh)", "640x640 (Chính xác)"])
        ai_form.addRow("Kích thước quét:", self.cmb_det_size)
        ai_form.addRow("", help_lbl("640x640 giúp nhận diện tốt hơn ở khoảng cách xa."))
        
        self.spin_frame_skip = QSpinBox()
        self.spin_frame_skip.setRange(1, 10)
        ai_form.addRow("Tần suất xử lý (N):", self.spin_frame_skip)
        ai_form.addRow("", help_lbl("Xử lý 1 hình sau mỗi N khung hình. Tăng N để giảm tải cho máy (đỡ nóng)."))
        
        self.check_gpu = QCheckBox("Sử dụng NVIDIA GPU (CUDA)")
        ai_form.addRow("Tăng tốc phần cứng:", self.check_gpu)

        # 2. Nhóm Database
        self._group_db = self._create_group("🗄️  CƠ SỞ DỮ LIỆU (SQL SERVER)")
        db_form = QFormLayout(self._group_db)
        self._set_form_style(db_form)

        self.edit_db_server = QLineEdit()
        self.edit_db_name = QLineEdit()
        self.check_win_auth = QCheckBox("Sử dụng Windows Authentication")

        db_form.addRow("Địa chỉ Server:", self.edit_db_server)
        db_form.addRow("", help_lbl("Sử dụng '.' cho máy cục bộ hoặc địa chỉ IP của server."))
        db_form.addRow("Tên Database:", self.edit_db_name)
        db_form.addRow("Xác thực:", self.check_win_auth)

        # Add groups
        self.container_layout.addWidget(self._group_ai)
        self.container_layout.addWidget(self._group_db)
        self.container_layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll)

        # --- Footer Actions ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_save = QPushButton("💾  Lưu cấu hình")
        self.btn_save.setFixedSize(180, 45)
        self.btn_save.setStyleSheet(f"background: {Colors.CYAN}; color: white; border-radius: 8px; font-weight: 800;")
        self.btn_save.clicked.connect(self._save_settings)
        
        btn_layout.addWidget(self.btn_save)
        root.addLayout(btn_layout)

    def _create_group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setStyleSheet(f"""
            QGroupBox {{
                font-weight: 800; font-size: 11px; color: {Colors.CYAN};
                border: 1px solid {Colors.BORDER}; border-radius: 12px;
                margin-top: 20px; background: {Colors.BG_CARD}; padding-top: 15px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 15px; padding: 0 5px; }}
        """)
        return group

    def _set_form_style(self, form: QFormLayout):
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(10)

    def _load_current_settings(self):
        # AI
        self.spin_threshold.setValue(ai_config.recognition_threshold)
        self.spin_min_face.setValue(ai_config.min_face_det_score)
        self.cmb_det_size.setCurrentIndex(1 if ai_config.det_size[0] == 640 else 0)
        self.spin_frame_skip.setValue(camera_config.process_every_n_frames)
        self.check_gpu.setChecked(ai_config.gpu_ctx_id >= 0)

        # DB
        self.edit_db_server.setText(db_config.host)
        self.edit_db_name.setText(db_config.database)
        self.check_win_auth.setChecked(False)

    def _save_settings(self):
        try:
            updates = {
                "AI_THRESHOLD": f"{self.spin_threshold.value():.2f}",
                "MIN_FACE_SCORE": f"{self.spin_min_face.value():.2f}",
                "CAM_PROCESS_N": self.spin_frame_skip.value(),
                "DB_SERVER": self.edit_db_server.text(),
                "DB_NAME": self.edit_db_name.text(),
            }
            
            # Cập nhật nóng vào bộ nhớ
            ai_config.recognition_threshold = self.spin_threshold.value()
            ai_config.min_face_det_score = self.spin_min_face.value()
            camera_config.process_every_n_frames = self.spin_frame_skip.value()
            
            # Lưu vào .env
            if save_to_env(updates):
                QMessageBox.information(self, "Thành công", 
                    "Đã lưu cấu hình.\n\n"
                    "Lưu ý: Thay đổi về GPU hoặc Database sẽ có hiệu lực sau khi khởi động lại.")
            else:
                QMessageBox.warning(self, "Lỗi", "Không thể ghi file .env.")

        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu: {str(e)}")
            logger.error(f"Settings Save Error: {e}")


        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu: {str(e)}")
            logger.error(f"Settings Save Error: {e}")