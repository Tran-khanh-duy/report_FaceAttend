"""
circuit_breaker_ui_snippet.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK 3: PyQt6 UI snippet — nút Retry cho Camera SUSPENDED.

Chiến lược UI:
  - Dùng QComboBox cho dropdown chọn camera (không thể nhúng widget vào item)
  - Thêm QPushButton "🔄 Thử Lại" nằm cạnh QComboBox
  - Button chỉ hiển thị và active khi camera được chọn đang SUSPENDED
  - QTimer mỗi 1 giây quét trạng thái để cập nhật button visibility

Cách tích hợp vào attendance_page.py hiện tại:
  1. Tìm chỗ khai báo QComboBox chọn camera
  2. Bọc QComboBox + QPushButton trong một QHBoxLayout
  3. Truyền `camera_manager` vào AttendancePage constructor
  4. Gọi _update_retry_button() mỗi lần user đổi camera hoặc timer tick
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QPushButton, QWidget,
)
from loguru import logger

# Import camera_manager singleton của dự án
# from core.camera_subsystem import CameraManager  (thực tế)
from core.camera_subsystem.camera_process import STATUS_SUSPENDED


class CameraDropdownWithRetry(QWidget):
    """
    Composite widget gồm QComboBox + QPushButton Retry.
    Tích hợp hoàn toàn với CameraManager.

    Cách dùng trong attendance_page.py:
        self._cam_selector = CameraDropdownWithRetry(camera_manager, self)
        layout.addWidget(self._cam_selector)
    """

    def __init__(self, camera_manager, parent=None):
        super().__init__(parent)
        self._cam_manager = camera_manager

        # ── Layout ngang: [QComboBox cam] [QPushButton Retry] ─────────────
        h_layout = QHBoxLayout(self)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(8)

        # QComboBox danh sách camera
        self._combo = QComboBox()
        self._combo.setMinimumWidth(260)
        self._combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1e1e2e;
                color: #cdd6f4;
                selection-background-color: #313244;
            }
        """)
        # Kết nối signal: mỗi khi user đổi camera → cập nhật nút Retry
        self._combo.currentTextChanged.connect(self._update_retry_button)
        h_layout.addWidget(self._combo)

        # QPushButton Retry — ẩn mặc định, chỉ hiện khi SUSPENDED
        self._btn_retry = QPushButton("🔄 Thử Lại")
        self._btn_retry.setFixedWidth(110)
        self._btn_retry.setFixedHeight(36)
        self._btn_retry.setStyleSheet("""
            QPushButton {
                background: #f38ba8;
                color: #1e1e2e;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #ff6b6b; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self._btn_retry.setVisible(False)   # Ẩn ban đầu
        self._btn_retry.clicked.connect(self._on_retry_clicked)
        h_layout.addWidget(self._btn_retry)

        # ── QTimer quét trạng thái mỗi 1 giây ────────────────────────────
        # 1000ms: đủ nhanh để phát hiện SUSPENDED → hiện nút, không tốn CPU
        self._state_timer = QTimer(self)
        self._state_timer.timeout.connect(self._update_retry_button)
        self._state_timer.start(1000)

    # ── Populate danh sách camera ──────────────────────────────────────────
    def populate_cameras(self, camera_entries: list[tuple[str, str]]) -> None:
        """
        Điền danh sách camera vào QComboBox.

        Args:
            camera_entries: list of (display_label, cam_id)
              VD: [("KTX E4 - Tầng 1", "CAM_01"), ...]
        """
        self._combo.blockSignals(True)
        self._combo.clear()
        for label, cam_id in camera_entries:
            self._combo.addItem(label, userData=cam_id)
        self._combo.blockSignals(False)
        self._update_retry_button()

    def current_cam_id(self) -> str | None:
        """Trả về cam_id của camera đang được chọn."""
        return self._combo.currentData()

    # ── TASK 3: Cập nhật label và nút Retry ───────────────────────────────
    def _update_retry_button(self) -> None:
        """
        Slot gọi mỗi 1 giây (hoặc khi đổi camera).
        Kiểm tra trạng thái camera đang chọn và hiện/ẩn nút Retry.
        Đồng thời cập nhật text của item trong combo để thêm icon 🔴/🟢/🔄.
        """
        cam_id = self.current_cam_id()
        if not cam_id or not self._cam_manager:
            return

        state = self._cam_manager.get_camera_state(cam_id)
        is_suspended = (state == STATUS_SUSPENDED)

        # Hiện/ẩn nút Retry
        self._btn_retry.setVisible(is_suspended)
        self._btn_retry.setEnabled(is_suspended)

        # Cập nhật icon trong combo item hiện tại để user thấy trạng thái
        idx = self._combo.currentIndex()
        if idx >= 0:
            base_text = self._combo.itemText(idx)
            # Xóa icon cũ trước (nếu có)
            for icon in ("🟢 ", "🔴 ", "🔄 "):
                base_text = base_text.replace(icon, "")

            if state == 1:    # ONLINE
                icon = "🟢 "
            elif state == 2:  # SUSPENDED
                icon = "🔄 "
            else:             # OFFLINE
                icon = "🔴 "

            self._combo.setItemText(idx, icon + base_text)

    # ── TASK 3: Slot xử lý khi user bấm Retry ─────────────────────────────
    def _on_retry_clicked(self) -> None:
        """
        Gọi camera_manager.trigger_retry() để set() manual_retry_event
        trong CameraProcess đang chờ. Process sẽ thức dậy và thử kết nối
        lại tối đa 3 lần.
        """
        cam_id = self.current_cam_id()
        if not cam_id:
            return

        logger.info(f"[UI] User bấm Retry cho camera: {cam_id}")

        # Disable nút ngay để tránh bấm nhiều lần liên tiếp
        self._btn_retry.setEnabled(False)
        self._btn_retry.setText("⏳ Đang thử...")

        # TASK 1 + TASK 2: Gửi tín hiệu xuống CameraProcess
        self._cam_manager.trigger_retry(cam_id)

        # Sau 10 giây, re-enable nếu vẫn SUSPENDED (tránh user bấm spam)
        # 10 giây = 3 lần thử × 2 giây delay + buffer
        QTimer.singleShot(10_000, self._reset_retry_button)

    def _reset_retry_button(self) -> None:
        """Khôi phục nút Retry sau timeout, cập nhật trạng thái từ process."""
        self._btn_retry.setText("🔄 Thử Lại")
        self._update_retry_button()  # Ẩn nếu đã ONLINE, hiện nếu vẫn SUSPENDED


# ── Snippet tích hợp vào attendance_page.py ──────────────────────────────────
"""
CÁCH TÍCH HỢP VÀO attendance_page.py HIỆN TẠI:

1. Import widget mới:
   from core.camera_subsystem.circuit_breaker_ui_snippet import CameraDropdownWithRetry
   from core.camera_subsystem import CameraManager, CameraConfig

2. Trong __init__ của AttendancePage:
   self._cam_dropdown = CameraDropdownWithRetry(camera_manager, self)
   # Thay thế QComboBox cũ bằng widget này trong layout

3. Populate danh sách:
   entries = [
       (f"KTX E4 - Tầng {i}", f"CAM_0{i}")
       for i in range(1, 6)
   ]
   self._cam_dropdown.populate_cameras(entries)

4. Lấy camera đang chọn:
   selected_cam_id = self._cam_dropdown.current_cam_id()

5. Trong _refresh_cameras() hiện tại — thay dòng gọi _check_camera_port()
   bằng:
   state = camera_manager.get_camera_state(cam_id)
   # state: 0=OFFLINE, 1=ONLINE, 2=SUSPENDED
   if state == 1:
       label = f"🟢 {cam_name}"
   elif state == 2:
       label = f"🔄 {cam_name}"
   else:
       label = f"🔴 {cam_name}"
"""
