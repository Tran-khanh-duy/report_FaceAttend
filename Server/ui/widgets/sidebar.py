"""
ui/widgets/sidebar.py
Thanh điều hướng bên trái — Windows 11 Fluent Light Style.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.styles.theme import Colors


class NavButton(QPushButton):
    """Nút điều hướng phong cách Fluent với thanh Accent Bar."""
    def __init__(self, icon_text: str, label: str, page_id: int):
        super().__init__()
        self.page_id = page_id
        self._active = False

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 0, 16, 0)
        self._layout.setSpacing(12)

        # Thanh chỉ báo trạng thái (Accent Bar) - Kiểu Windows 11
        self._accent_bar = QFrame()
        self._accent_bar.setFixedWidth(3)
        self._accent_bar.setFixedHeight(16)
        self._accent_bar.setStyleSheet("border-radius: 1.5px; background: transparent;")
        
        self._icon_lbl = QLabel(icon_text)
        self._icon_lbl.setFixedWidth(24)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet("font-size: 16px; background: transparent;")

        self._text_lbl = QLabel(label)
        self._text_lbl.setStyleSheet("font-size: 13px; background: transparent;")

        self._layout.addWidget(self._accent_bar)
        self._layout.addWidget(self._icon_lbl)
        self._layout.addWidget(self._text_lbl)
        self._layout.addStretch()

        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_style()

    def set_active(self, active: bool):
        self._active = active
        self._refresh_style()

    def _refresh_style(self):
        # Cấu hình màu sắc Fluent
        hover_bg = "#E8E8E8"  # Xám nhạt khi hover
        active_bg = "#F3F3F3" # Nền khi đang chọn
        
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {active_bg};
                    border: none;
                    border-radius: 6px;
                    margin: 2px 8px;
                }}
            """)
            self._accent_bar.setStyleSheet(f"border-radius: 1.5px; background: {Colors.CYAN};")
            self._text_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT}; background: transparent;")
            self._icon_lbl.setStyleSheet(f"color: {Colors.CYAN}; font-size: 16px;")
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    border-radius: 6px;
                    margin: 2px 8px;
                }}
                QPushButton:hover {{
                    background-color: {hover_bg};
                }}
            """)
            self._accent_bar.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_DIM}; background: transparent;")
            self._icon_lbl.setStyleSheet(f"color: {Colors.TEXT_DARK}; font-size: 16px;")


class Sidebar(QWidget):
    page_changed = pyqtSignal(int)

    PAGE_DASHBOARD  = 0
    PAGE_ATTENDANCE = 1
    PAGE_ENROLL     = 2
    PAGE_STUDENTS   = 3
    PAGE_REPORTS    = 4
    PAGE_CAMERAS    = 5
    PAGE_SETTINGS   = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(240) # Độ rộng tiêu chuẩn Windows 11
        self.setObjectName("leftSidebar")
        self.setStyleSheet(f"""
            QWidget#leftSidebar {{
                background-color: #F9F9F9;
                border-right: 1px solid {Colors.BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Logo Header ──
        header = QWidget()
        header.setFixedHeight(72)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(24, 0, 16, 0)
        h_lay.setSpacing(12)

        logo_box = QFrame()
        logo_box.setFixedSize(48, 48) # Tăng từ 36 lên 48
        logo_box.setStyleSheet("""
            background-color: transparent;
            border: none;
        """)
        lb_lay = QHBoxLayout(logo_box)
        lb_lay.setContentsMargins(0, 0, 0, 0)
        from PyQt6.QtGui import QPixmap
        import os
        icon_path = os.path.join(str(Path(__file__).parent.parent.parent), "assets", "app_icon.png")
        
        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet("background: transparent;")
        
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            # Tăng từ 24 lên 48 để logo to rõ và đẹp hơn
            logo_lbl.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo_lbl.setText("👁")
            logo_lbl.setStyleSheet("font-size: 32px; color: #1E293B; background: transparent;")
            
        lb_lay.addWidget(logo_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(2) # Tạo khoảng hở nhỏ giữa tên App và version
        title_box.setAlignment(Qt.AlignmentFlag.AlignVCenter) # Căn giữa text block với logo theo chiều dọc
        
        app_name = QLabel("FaceAttend")
        app_name.setStyleSheet(f"font-size: 16px; font-weight: 900; color: {Colors.TEXT};")
        version_lbl = QLabel("v1.0.0")
        version_lbl.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {Colors.TEXT_DARK};")
        title_box.addWidget(app_name)
        title_box.addWidget(version_lbl)

        h_lay.addWidget(logo_box)
        h_lay.addLayout(title_box)
        h_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter) # Đảm bảo Logo và Text đều được Căn giữa dọc
        h_lay.addStretch()
        layout.addWidget(header)

        # ── Section label (Đã comment out theo yêu cầu) ──
        # menu_label = QLabel("ĐIỀU HƯỚNG")
        # menu_label.setStyleSheet(f"color: {Colors.TEXT_DARK}; padding: 20px 24px 8px 24px;")
        # layout.addWidget(menu_label)
        layout.addSpacing(16) # Thêm khoảng trống nhỏ thay thế

        # ── Nav items ──
        self._nav_buttons: list[NavButton] = []
        nav_items = [
            ("🏠", "DASHBOARD",       self.PAGE_DASHBOARD),
            ("📸", "ĐIỂM DANH",       self.PAGE_ATTENDANCE),
            ("👤", "ĐĂNG KÝ MỚI",     self.PAGE_ENROLL),
            ("📚", "DANH SÁCH HV",    self.PAGE_STUDENTS),
            ("📊", "XUẤT BÁO CÁO",    self.PAGE_REPORTS),
            ("🎥", "QUẢN LÝ CAMERA",  self.PAGE_CAMERAS),
        ]
        
        for icon, label, page_id in nav_items:
            btn = NavButton(icon, label, page_id)
            btn.clicked.connect(lambda checked, pid=page_id: self._on_nav_click(pid))
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # ── Divider ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {Colors.BORDER}; border: none; max-height: 1px; margin: 0 16px;")
        layout.addWidget(sep)

        # ── Settings ──
        settings_btn = NavButton("⚙️", "Cấu hình hệ thống", self.PAGE_SETTINGS)
        settings_btn.clicked.connect(lambda: self._on_nav_click(self.PAGE_SETTINGS))
        self._nav_buttons.append(settings_btn)
        layout.addWidget(settings_btn)

        layout.addSpacing(10) # Tạo khoảng cách giữa nút Cấu hình và Trạng thái máy chủ

        # ── DB Status Chip ──
        self._status_frame = QFrame()
        # Bỏ setFixedHeight(48) để không bị ép không gian chật hẹp
        sf_lay = QHBoxLayout(self._status_frame)
        sf_lay.setContentsMargins(24, 8, 24, 16) # Thêm lề dọc để status frame có khoảng thở tự nhiên
        
        self._db_dot = QLabel("●")
        self._db_text = QLabel("Máy chủ: Ngoại tuyến")
        self._db_text.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {Colors.TEXT_DIM};")
        
        sf_lay.addWidget(self._db_dot)
        sf_lay.addWidget(self._db_text)
        sf_lay.addStretch()
        layout.addWidget(self._status_frame)

        self._set_active(self.PAGE_DASHBOARD)
        self.set_db_status(False)

    def _on_nav_click(self, page_id: int):
        self._set_active(page_id)
        self.page_changed.emit(page_id)

    def _set_active(self, page_id: int):
        for btn in self._nav_buttons:
            btn.set_active(btn.page_id == page_id)

    def set_db_status(self, connected: bool):
        color = Colors.GREEN if connected else Colors.RED
        self._db_dot.setStyleSheet(f"color: {color}; font-size: 14px; margin-right: 4px;")
        status_txt = "Máy chủ: Sẵn sàng" if connected else "Máy chủ: Ngoại tuyến"
        self._db_text.setText(status_txt)
        self._db_text.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {Colors.TEXT if connected else Colors.RED};")