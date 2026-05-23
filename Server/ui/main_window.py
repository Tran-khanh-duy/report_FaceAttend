"""
ui/main_window.py
"""
import sys
import ctypes
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QApplication, QMessageBox,
    QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon, QColor, QPixmap
from loguru import logger

# Đảm bảo import đúng path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import app_config

# Icon app
ICON_PATH = str(Path(__file__).parent.parent / "assets" / "app_icon.png")

from ui.styles.theme import Colors, apply_theme
from ui.widgets.sidebar import Sidebar
from ui.pages.dashboard_page import DashboardPage
from ui.pages.enroll_page import EnrollPage
from ui.pages.students_page import StudentsPage
from ui.pages.attendance_page import AttendancePage
from ui.pages.reports_page import ReportsPage
from ui.pages.cameras_page import CamerasPage
from ui.pages.settings_page import SettingsPage

# ─────────────────────────────────────────────
#  Loading Overlay (Giao diện chờ khởi động)
# ─────────────────────────────────────────────
class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Lớp phủ màu tối bao trùm cửa sổ
        self.setStyleSheet(f"background-color: {Colors.BG_DARK};")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)

        from PyQt6.QtGui import QPixmap
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(ICON_PATH)
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("👁")
            logo.setStyleSheet("font-size: 70px; color: white;")
        layout.addWidget(logo)

        title = QLabel("FaceAttend AI")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {Colors.TEXT}; letter-spacing: 2px;")
        layout.addWidget(title)

        self._msg_lbl = QLabel("Đang khởi động hệ thống...")
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_lbl.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_DIM};")
        layout.addWidget(self._msg_lbl)

        # Thanh Progress Bar đơn giản
        self._bar_bg = QFrame()
        self._bar_bg.setFixedSize(400, 6)
        self._bar_bg.setStyleSheet(f"background: {Colors.BORDER}; border-radius: 3px;")
        self._bar_fill = QFrame(self._bar_bg)
        self._bar_fill.setFixedHeight(6)
        self._bar_fill.setStyleSheet(f"background: {Colors.CYAN}; border-radius: 3px;")
        self._bar_fill.setFixedWidth(0)
        layout.addWidget(self._bar_bg, 0, Qt.AlignmentFlag.AlignHCenter)

        self._pct_lbl = QLabel("0%")
        self._pct_lbl.setStyleSheet(f"font-size: 12px; color: {Colors.CYAN}; font-weight: 700;")
        layout.addWidget(self._pct_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_progress(self, message: str, percent: int):
        self._msg_lbl.setText(message)
        width = int(400 * percent / 100)
        self._bar_fill.setFixedWidth(width)
        self._pct_lbl.setText(f"{percent}%")

# ─────────────────────────────────────────────
#  InitWorker (Luồng xử lý khởi động)
# ─────────────────────────────────────────────
class InitWorker(QThread):
    progress = pyqtSignal(str, int)
    init_status = pyqtSignal(dict)
    init_done = pyqtSignal()

    def run(self):
        status = {"db": False, "model": False, "cache": 0}
        
        # 1. Kết nối Database
        self.progress.emit("Đang kết nối SQL Server...", 20)
        try:
            from database.connection import db # Import đối tượng db đã tạo sẵn
            conn = db.get_connection()         # Gọi hàm lấy connection
            if conn:
                status["db"] = True
                self.progress.emit("SQL Server: Kết nối thành công ✅", 40)
            else:
                self.progress.emit("SQL Server: Lỗi kết nối! ⚠️", 40)
        except Exception as e:
            logger.error(f"DB Error: {e}")
            self.progress.emit("Lỗi kết nối CSDL ❌", 40)

        # 2. Load Cache
        self.progress.emit("Nạp dữ liệu học viên lên RAM...", 60)
        try:
            from services.embedding_cache_manager import cache_manager
            success = cache_manager.load()
            if success:
                status["cache"] = cache_manager.size
                self.progress.emit(f"Đã nạp {status['cache']} học viên ✅", 70)
            else:
                self.progress.emit("Không thể nạp cache từ DB! ⚠️", 70)
        except Exception as e:
            logger.error(f"Cache Load Error: {e}")
            self.progress.emit("Lỗi nạp dữ liệu RAM ❌", 70)

        # 3. Skip Load Model AI at startup (load on demand)
        self.progress.emit("Hệ thống đã sẵn sàng!", 90)
        status["model"] = False # Sẽ được load khi cần

        self.init_status.emit(status)
        self.progress.emit("Hệ thống đã sẵn sàng!", 100)
        self.init_done.emit()

# ─────────────────────────────────────────────
#  MainWindow
# ─────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._setup_window()
        self._build_layout()
        self._start_init()
        self.showMaximized()

    def _setup_window(self):
        self.setWindowTitle("FaceAttend AI")
        self.resize(1280, 850)
        self.setMinimumSize(1100, 750)
        icon = QIcon(ICON_PATH)
        if not icon.isNull():
            self.setWindowIcon(icon)


    def _build_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Sidebar
        self._sidebar = Sidebar()
        self._sidebar.page_changed.connect(self._handle_navigation)
        root.addWidget(self._sidebar)

        # 2. Main Content
        self._stack = QStackedWidget()
        
        self._pages = {
            Sidebar.PAGE_DASHBOARD: DashboardPage(),
            Sidebar.PAGE_ATTENDANCE: AttendancePage(),
            Sidebar.PAGE_ENROLL: EnrollPage(),
            Sidebar.PAGE_STUDENTS: StudentsPage(),
            Sidebar.PAGE_REPORTS: ReportsPage(),
            Sidebar.PAGE_CAMERAS: CamerasPage(),
            Sidebar.PAGE_SETTINGS: SettingsPage()
        }

        for pid, page in self._pages.items():
            self._stack.addWidget(page)
            
        root.addWidget(self._stack, 1)

        # ── Signals ──
        self._pages[Sidebar.PAGE_STUDENTS].go_to_enroll.connect(self._navigate_to_enroll)
        self._pages[Sidebar.PAGE_DASHBOARD].go_to_live_view.connect(self._navigate_to_live_view)

        # 3. Loading Overlay (Không dùng import nữa, gọi trực tiếp class ở trên)
        self._loading = LoadingOverlay(central)
        self._loading.resize(1280, 850)
        self._loading.show()
        self._loading.raise_()

    def _handle_navigation(self, page_id: int):
        # Khi bấm "Đăng ký mới" trực tiếp từ sidebar → luôn reset về form trống
        if page_id == Sidebar.PAGE_ENROLL:
            self._pages[Sidebar.PAGE_ENROLL]._reset_form()
        self._stack.setCurrentIndex(page_id)


    def _navigate_to_enroll(self, student_id: int):
        # Chọn mục Đăng ký trên thanh điều hướng bên trái
        self._sidebar._on_nav_click(Sidebar.PAGE_ENROLL)
        
        # Tải học viên hoặc reset form nếu thêm mới
        enroll_page = self._pages[Sidebar.PAGE_ENROLL]
        if student_id > 0:
            enroll_page.load_student(student_id)
        else:
            enroll_page._reset_form()

    def _navigate_to_live_view(self, cam_id: str):
        # Chọn mục Điểm danh live
        self._sidebar._on_nav_click(Sidebar.PAGE_ATTENDANCE)
        # Báo cho trang Attendance chọn đúng camera (nếu trang đó có hàm này)
        attendance_page = self._pages[Sidebar.PAGE_ATTENDANCE]
        if hasattr(attendance_page, 'select_camera_by_id'):
            attendance_page.select_camera_by_id(cam_id)

    def _start_init(self):
        self._worker = InitWorker()
        self._worker.progress.connect(self._loading.set_progress)
        self._worker.init_status.connect(self._apply_init_results)
        self._worker.init_done.connect(lambda: self._loading.hide())
        self._worker.start()

    def _apply_init_results(self, status):
        self._sidebar.set_db_status(status["db"])
        self._pages[Sidebar.PAGE_DASHBOARD].update_system_status("database", status["db"], "Connected" if status["db"] else "Error")
        self._pages[Sidebar.PAGE_DASHBOARD].update_system_status("ai_model", status["model"], "GPU Active" if status["model"] else "CPU Mode")
        self._pages[Sidebar.PAGE_DASHBOARD].update_stats(enrolled=status["cache"])

    def closeEvent(self, event):
        try:
            from services.camera_manager import camera_manager
            camera_manager.stop_all()
        except: pass
        event.accept()

def run():
    # Bắt buộc để Windows dùng icon riêng thay vì icon python.exe trên taskbar
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FaceAttend.AI.1.0")
    except Exception:
        pass

    app = QApplication(sys.argv)
    apply_theme(app)
    icon = QIcon(ICON_PATH)
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()