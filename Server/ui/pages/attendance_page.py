"""
ui/pages/attendance_page.py
"""
import numpy as np
import threading
from datetime import datetime, date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QFrame,
    QScrollArea, QSizePolicy, QMessageBox,
    QSplitter, QDateEdit, QStackedWidget,
    QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QDate, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont, QColor, QPixmap, QImage, QAction
from loguru import logger
import sys
import os
import requests
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ui.styles.theme import Colors, card_style, combo_style
from ui.widgets.camera_preview import CameraPreviewWidget
from database.repositories import camera_repo, class_repo, record_repo, building_repo
from config import app_config

# ─────────────────────────────────────────────
#  [FIX #3] DBPollThread — DB query trong QThread riêng, không block GUI
# ─────────────────────────────────────────────
class DBPollThread(QThread):
    """
    Thực hiện 2 DB query (present_list + total_count) trong background thread.
    Emit signal khi xong để GUI thread cập nhật UI an toàn.
    """
    results_ready  = pyqtSignal(list, int)   # (present_list, total_students)
    error_occurred = pyqtSignal(str)

    def __init__(self, session_id: int, parent=None):
        super().__init__(parent)
        self._session_id = session_id

    def run(self):
        try:
            present_list   = record_repo.get_present_list(self._session_id)
            total_students = record_repo.get_total_count(self._session_id)
            self.results_ready.emit(present_list, total_students)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ─────────────────────────────────────────────
#  Worker: chạy AI pipeline trong QThread
# ─────────────────────────────────────────────
class AttendanceWorker(QThread):
    """
    Thread đọc Camera thuần tuý (không chạy AI).
    Giành nhiệm vụ AI cho Mini PC.
    """
    frame_ready = pyqtSignal(np.ndarray, float, list)
    error_occurred = pyqtSignal(str)

    def __init__(self, camera_source=0, parent=None):
        super().__init__(parent)
        self.camera_source = camera_source
        self._running = True
        self._paused = False

    def pause(self): self._paused = True
    def resume(self): self._paused = False

    def stop(self):
        self._running = False
        self._paused = False

    def run(self):
        import cv2, time
        import os
        from config import camera_config
        
        # TASK 3 (Từ trước): Ép OpenCV ngắt kết nối nhanh nếu mất luồng RTSP
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;1000000"

        source = int(self.camera_source) if str(self.camera_source).isdigit() else self.camera_source
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            self.error_occurred.emit(f"Không mở được camera (source={self.camera_source})")
            return

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

        last_time = time.time()
        fail_logged = False
        
        while self._running:
            try:
                ret, frame = self.cap.read()
                if not ret:
                    # TASK 3: Chặn Spam Log (Rate Limiting Logs)
                    if not fail_logged:
                        logger.error(f"Mất kết nối Camera (source={self.camera_source}). Đang thử lại...")
                        self.error_occurred.emit("Mất kết nối Camera. Đang đợi...")
                        fail_logged = True
                    
                    # TASK 1: Xử lý Vòng lặp bận (Nghỉ 3 giây nhả CPU)
                    time.sleep(3)
                    
                    # TASK 2: Giải phóng tài nguyên an toàn trước khi Reconnect
                    if self.cap is not None:
                        self.cap.release()
                        self.cap = None
                        
                    self.cap = cv2.VideoCapture(source)
                    if self.cap.isOpened():
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                        fail_logged = False # Reset cờ log nếu kết nối lại thành công
                    continue

                fail_logged = False
                now = time.time()
                elapsed_ms = (now - last_time) * 1000
                last_time = now

                if self._paused:
                    self.frame_ready.emit(frame, 0, [])
                    time.sleep(0.033)
                    continue

                self.frame_ready.emit(frame, elapsed_ms, [])
                
                # Giới hạn FPS cơ bản để không tốn CPU Server
                time.sleep(0.033)
                
            except Exception as e:
                if not fail_logged:
                    logger.error(f"Lỗi Camera (source={self.camera_source}): {e}")
                    self.error_occurred.emit("Lỗi đọc Camera. Đang thử lại...")
                    fail_logged = True
                    
                time.sleep(3)
                if getattr(self, 'cap', None) is not None:
                    self.cap.release()
                    self.cap = None
                self.cap = cv2.VideoCapture(source)

        if getattr(self, 'cap', None) is not None:
            self.cap.release()
        logger.info("Camera view stopped")


class RemoteStreamWorker(QThread):
    """
    Worker lấy khung hình từ API Server (do Mini PC upload lên).
    Dùng khi giám sát từ xa qua Edge Box.
    """
    frame_ready = pyqtSignal(np.ndarray, float, list)
    error_occurred = pyqtSignal(str)

    def __init__(self, camera_id=None, api_url="http://127.0.0.1:9696/api/system/frame", parent=None):
        super().__init__(parent)
        # [FIX] Giữ nguyên camera_id: chỉ strip() khoảng trắng
        # KHÔNG .upper() vì sẽ phá hỏng RTSP URL (rtsp://admin:pass@ip/...)
        safe_id = str(camera_id or "CAM_01").strip()
        self.camera_id = safe_id
        self.api_url = api_url
        self._running = True
        self._paused = False

        from PyQt6.QtCore import QMutex
        self._mutex = QMutex()

    def pause(self):
        self._mutex.lock()
        try:
            self._paused = True
        finally:
            self._mutex.unlock()

    def resume(self):
        self._mutex.lock()
        try:
            self._paused = False
        finally:
            self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        try:
            self._running = False
        finally:
            self._mutex.unlock()

    def run(self):
        import requests, cv2, time
        import numpy as np

        logger.info(f"RemoteStreamWorker started: {self.api_url}")
        last_time = time.time()
        
        # Dùng Session() để tái sử dụng TCP connection, giúp mượt hơn và giảm overhead.
        session = requests.Session()
        fail_count = 0
        
        while True:
            self._mutex.lock()
            try:
                running = self._running
                is_paused = self._paused
            finally:
                self._mutex.unlock()
                
            if not running:
                break
                
            if is_paused:
                time.sleep(0.1)
                continue
            try:
                # 1. Poll khung hình với camera_id đã được chuẩn hóa
                params = {"camera_id": self.camera_id}
                resp = session.get(self.api_url, params=params, timeout=3)
                
                if resp.status_code == 200:
                    image_bytes = resp.content
                    if not image_bytes:
                        time.sleep(0.05)
                        continue
                        
                    nparr = np.frombuffer(image_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if frame is not None:
                        # 2. Lấy tọa độ khuôn mặt từ Header
                        det_list = []
                        if "X-Face-Detections" in resp.headers:
                            try:
                                import base64, json
                                det_b64 = resp.headers["X-Face-Detections"]
                                det_list = json.loads(base64.b64decode(det_b64).decode())
                            except: pass

                        now = time.time()
                        elapsed_ms = (now - last_time) * 1000
                        last_time = now
                        self.frame_ready.emit(frame, elapsed_ms, det_list)
                    else:
                        self.error_occurred.emit("Lỗi Decode Ảnh")
                else:
                    # Log lỗi chi tiết ra UI terminal
                    msg = f"Lỗi Server ({resp.status_code})"
                    if resp.status_code == 404:
                        # Thử lấy danh sách camera có sẵn từ detail response
                        fail_count += 1
                        try:
                            detail = resp.json().get("detail", "")
                            available_info = ""
                            if "Available:" in detail:
                                available_info = f" (Sẵn có: {detail.split('Available:')[1]})"
                            msg = f"Server chưa có hình{available_info}"
                        except:
                            msg = "Server chưa có hình (Đang đợi Mini PC)"
                        
                        if fail_count % 20 == 0:
                            logger.warning(f"[WAIT] {self.camera_id}: {msg}")
                    else:
                        logger.error(f"[ERROR] UI Stream {self.camera_id} fail: {msg}")

                    
                    self.error_occurred.emit(msg)
                    time.sleep(0.5)

                # Giới hạn tốc độ poll để UI không bị đơ
                time.sleep(0.03)

            except requests.exceptions.ConnectionError:
                self.error_occurred.emit("Mất kết nối Server")
                time.sleep(2)
            except Exception as e:
                logger.error(f"RemoteStreamWorker Error ({self.camera_id}): {e}")
                self.error_occurred.emit("Lỗi luồng")
                time.sleep(1)

        logger.info("RemoteStreamWorker stopped")


# ─────────────────────────────────────────────
#  AttendanceListItem — 1 dòng học viên có mặt
# ─────────────────────────────────────────────
class AttendanceListItem(QWidget):
    def __init__(self, event: dict, index: int, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70) # Tăng chiều cao tránh mất chữ
        bg = Colors.BG_CARD if index % 2 == 0 else Colors.BG_PANEL
        self.setStyleSheet(f"""
            QWidget {{
                background: {bg};
                border-bottom: 1px solid {Colors.BORDER};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(16)

        # Avatar số thứ tự
        idx_lbl = QLabel(str(index))
        idx_lbl.setFixedSize(36, 36)
        idx_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idx_lbl.setStyleSheet(f"""
            QLabel {{
                background: {Colors.CYAN}18;
                color: {Colors.CYAN};
                border: 1px solid {Colors.CYAN}44;
                border-radius: 18px;
                font-size: 14px;
                font-weight: 800;
            }}
        """)
        layout.addWidget(idx_lbl)

        # Thông tin
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        display_name = f"{event['full_name']}"
        name_lbl = QLabel(display_name)
        name_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {Colors.TEXT}; border: none; background: transparent;")
        
        loc_info = f" — {event.get('building', '')} / {event.get('room', '')}" if event.get("building") else ""
        detail_lbl = QLabel(f"{event['student_code']}{loc_info}")
        detail_lbl.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_DIM}; border: none; background: transparent;")
        
        info_col.addWidget(name_lbl)
        info_col.addWidget(detail_lbl)
        layout.addLayout(info_col, 1)

        # Score + Time
        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        right_col.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        score_pct = event["similarity"] * 100
        score_color = Colors.GREEN if score_pct >= 80 else Colors.ORANGE
        score_lbl = QLabel(f"{score_pct:.1f}%")
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_lbl.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {score_color}; border: none; background: transparent;")
        
        time_lbl = QLabel(event["time_str"])
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_lbl.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_DIM}; border: none; background: transparent;")
        
        right_col.addWidget(score_lbl)
        right_col.addWidget(time_lbl)
        layout.addLayout(right_col)


# ─────────────────────────────────────────────
#  AttendancePage
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
#  CameraDataFetcher — thu thập data camera trong QThread
#  KHÔNG tạo bất kỳ QObject/QWidget nào bên trong thread này!
# ─────────────────────────────────────────────
class CameraDataFetcher(QThread):
    """
    Worker QThread chỉ thực hiện tác vụ IO (network request + DB query).
    Emit data thô về GUI thread để _build_camera_menu() xây dựng QMenu.
    KHÔNG được tạo QMenu hay bất kỳ QObject nào trong run().
    """
    data_ready = pyqtSignal(dict)   # {group_name: [(label, source, ip), ...]}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CameraDataFetcher")

    def run(self):
        import requests as _requests
        from database.repositories import camera_repo as _cam_repo
        from core.state_manager import state_manager as _sm
        from config import app_config as _cfg

        cam_groups: dict = {f"KTX E{i}": [] for i in range(1, 7)}

        try:
            cameras = _cam_repo.get_all(active_only=True)
            _db_rtsp_set = {cam.rtsp_url for cam in cameras if cam.rtsp_url}
        except Exception as e:
            logger.warning(f"[CameraDataFetcher] DB error: {e}")

        try:
            resp = _requests.get("http://127.0.0.1:9696/api/system/edge_status", timeout=2)
            edge_data = resp.json() if resp.status_code == 200 else {}

            if edge_data:
                for box_id, box_data in edge_data.items():
                    status = box_data.get("camera_status", {})
                    if not status:
                        continue
                    group_key = box_id
                    if group_key not in cam_groups:
                        cam_groups[group_key] = []

                    for k, v in status.items():
                        cam_name = k
                        cam_source = v
                        is_active = True
                        if isinstance(v, dict):
                            cam_name = v.get("name", k)
                            cam_source = v.get("source", "")
                            is_active = v.get("is_active", True)

                        cam_ip = ""
                        if isinstance(cam_source, str) and cam_source.startswith("rtsp://"):
                            try:
                                cam_ip = cam_source.split("@")[1].split("/")[0] if "@" in cam_source else cam_source.split("://")[1].split("/")[0]
                            except Exception:
                                cam_ip = ""

                        display_ip = cam_ip if cam_ip else "OFFLINE"
                        # [FIX TASK 2] Dùng hàm lấy trạng thái từ Redis, không dùng dict TCP block cũ
                        real_online = _sm.get_camera_is_active(k, cam_source)

                        if k.startswith("rtsp://"):
                            try:
                                ip_port = k.split("@")[1].split("/")[0] if "@" in k else k.split("://")[1].split("/")[0]
                            except Exception:
                                ip_port = "IP Cam"
                            label = (f"🟢 IP Cam ({ip_port}) [ONLINE]" if real_online
                                     else f"🔴 IP Cam ({ip_port}) [MẤT KẾT NỐI]")
                            cam_groups[group_key].append((label, k, ip_port))
                        else:
                            label = (f"🟢 {cam_name} ({display_ip}) [ONLINE]" if real_online
                                     else f"🔴 {cam_name} ({display_ip}) [MẤT KẾT NỐI]")
                            cam_groups[group_key].append((label, k, display_ip))

        except Exception as e:
            logger.warning(f"[CameraDataFetcher] Edge API error: {e}")

        self.data_ready.emit(cam_groups)


class AttendancePage(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: AttendanceWorker | None = None
        self._session_id: int | None = None
        self._selected_camera_source = None # Khởi tạo mặc định
        self._attendance_count = 0
        self._session_start: datetime | None = None
        self._rendered_student_codes = set()
        self._cam_groups: dict = {}  # Lưu data camera mới nhất từ fetcher

        # [FIX #3] DB poll thread — khởi tạo sẵn, start khi cần
        self._db_poll_thread: DBPollThread | None = None

        # [FIX threading] Camera data fetcher — chạy IO trong QThread riêng
        self._cam_fetcher: CameraDataFetcher | None = None

        # [FIX TASK 2] Timer kích hoạt fetcher mỗi 5s (Thay vì 3s để giảm load cho Server)
        self._status_refresh_timer = QTimer(self)
        self._status_refresh_timer.timeout.connect(self._refresh_cameras_async)
        self._status_refresh_timer.start(5000)

        self._setup_ui()

        # [FIX #3] DB polling timer — kích hoạt QThread, không gọi DB trực tiếp
        self._db_poll_timer = QTimer(self)
        self._db_poll_timer.timeout.connect(self._trigger_db_poll)

        # Đồng hồ cập nhật mỗi giây
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)

    # ─── UI Setup ─────────────────────────────

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; width: 8px; }")

        # LEFT: Camera
        left = self._build_camera_panel()
        splitter.addWidget(left)

        # RIGHT: Control + List
        right = self._build_control_panel()
        splitter.addWidget(right)

        splitter.setSizes([850, 420]) # Nới rộng panel bên phải để không bị cắt chữ
        splitter.setStretchFactor(0, 1) # Cho phép camera giãn nhiều nhất
        splitter.setStretchFactor(1, 0) # Panel phải giữ width cơ bản nhưng vẫn được co giãn nếu cần
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

    def _build_camera_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {Colors.CAM_BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 10, 15, 15)
        layout.setSpacing(10)

        # ── 1. Header Toolbar (Premium VS Code style) ──
        toolbar = QFrame()
        toolbar.setFixedHeight(50)
        toolbar.setStyleSheet(f"""
            QFrame {{
                background: {Colors.BG_DARK}; border: none;
                border-radius: 12px;
            }}
        """)
        t_layout = QHBoxLayout(toolbar)
        t_layout.setContentsMargins(15, 0, 15, 0)
        t_layout.setSpacing(15)

        # Trái: Icon + Title
        title_box = QHBoxLayout()
        icon_lbl = QLabel("🎥")
        icon_lbl.setStyleSheet("font-size: 16px;")
        self._lbl_cam_title = QLabel("Live Camera")
        self._lbl_cam_title.setStyleSheet(f"color: {Colors.TEXT}; font-size: 14px; font-weight: 800;")
        title_box.addWidget(icon_lbl)
        title_box.addWidget(self._lbl_cam_title)
        t_layout.addLayout(title_box)

        t_layout.addStretch()

        # Phải: FPS & Status
        self._lbl_fps = QLabel("— ms")
        self._lbl_fps.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_DIM}; padding: 3px 10px; border-radius: 6px; font-weight: 700; font-size: 11px;")
        
        self._lbl_cam_status = QLabel("⬤  Chờ")
        self._lbl_cam_status.setStyleSheet(f"color: {Colors.TEXT_DARK}; font-weight: 800; font-size: 12px; margin-left: 8px;")
        
        t_layout.addWidget(self._lbl_fps)
        t_layout.addWidget(self._lbl_cam_status)
        layout.addWidget(toolbar)

        # ── 2. Camera Selection Toolbar (Sub-menu style) ──
        sel_bar = QWidget()
        sel_bar.setFixedHeight(50)
        sb_layout = QHBoxLayout(sel_bar)
        sb_layout.setContentsMargins(5, 0, 5, 0)
        sb_layout.setSpacing(12)

        sel_label = QLabel("📍 Chọn Camera:")
        sel_label.setStyleSheet(f"color: {Colors.CYAN}; font-size: 13px; font-weight: 800;")
        sb_layout.addWidget(sel_label)

        # Nút bấm tổng hợp (Merge into one)
        self._btn_camera_select = QPushButton("🔍 CHỌN CAMERA HỆ THỐNG...  ▾")
        self._btn_camera_select.setFixedHeight(40)
        self._btn_camera_select.setMinimumWidth(250)
        self._btn_camera_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_camera_select.setStyleSheet(f"""
            QPushButton {{
                background: white; color: black;
                border: 1px solid #E0E0E0; border-radius: 8px;
                padding: 0 20px; font-size: 13px; font-weight: 800;
            }}
            QPushButton:hover {{ background: #F5F5F5; border-color: {Colors.CYAN}; }}
            QPushButton::menu-indicator {{ image: none; }}
        """)
        sb_layout.addWidget(self._btn_camera_select)

        sb_layout.addStretch()

        # Hộp trạng thái cam đang chọn
        self._cam_status_box = QFrame()
        self._cam_status_box.setFixedHeight(40)
        self._cam_status_box.setStyleSheet(f"background: {Colors.GREEN}14; border: 1.5px solid {Colors.GREEN}33; border-radius: 8px;")
        cs_layout = QHBoxLayout(self._cam_status_box)
        cs_layout.setContentsMargins(12, 0, 12, 0)
        self._lbl_current_cam = QLabel("Chưa chọn")
        self._lbl_current_cam.setStyleSheet(f"color: {Colors.GREEN}; font-weight: 800; font-size: 13px;")
        cs_layout.addWidget(self._lbl_current_cam)
        sb_layout.addWidget(self._cam_status_box)
        
        layout.addWidget(sel_bar)

        # ── 3. Camera Display area ──
        self._cam_stack = QStackedWidget()
        layout.addWidget(self._cam_stack, 1)

        # Setup page (Placeholder)
        self._cam_setup_page = QFrame()
        self._cam_setup_page.setStyleSheet(f"background: {Colors.BG_DARK}; border: none; border-radius: 15px;")
        setup_layout = QVBoxLayout(self._cam_setup_page)
        
        hint_icon = QLabel("📹")
        hint_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_icon.setStyleSheet("font-size: 48px; opacity: 0.5;")
        setup_layout.addStretch()
        setup_layout.addWidget(hint_icon)
        
        hint_lbl = QLabel("Vui lòng chọn Camera từ Menu bên trên")
        hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_lbl.setStyleSheet(f"color: {Colors.TEXT_DARK}; font-size: 15px; font-weight: 600; margin-top: 10px;")
        setup_layout.addWidget(hint_lbl)
        setup_layout.addStretch()
        self._cam_stack.addWidget(self._cam_setup_page)

        # Preview page
        self._camera_view = CameraPreviewWidget(placeholder_text="Đang kết nối Cam...")
        self._cam_stack.addWidget(self._camera_view)
        
        self._cam_stack.setCurrentIndex(0)

        # Notification toast
        self._toast = QLabel("")
        self._toast.setFixedHeight(50)
        self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast.setStyleSheet(f"background: {Colors.BG_DARK}; color: {Colors.CYAN}; border: 1px solid {Colors.CYAN}; border-radius: 10px; font-weight: 700;")
        self._toast.hide()
        layout.addWidget(self._toast)

        return panel

    def select_camera_by_id(self, cam_source: str):
        """Được gọi từ MainWindow khi user click thẻ Camera ở Dashboard."""
        # Tìm lại label dựa trên source
        label = "Camera"
        if hasattr(self, "_cam_groups"):
            for group, cams in self._cam_groups.items():
                for name, src, _ in cams:
                    if src == cam_source:
                        label = name
                        break
        self._on_camera_selected(cam_source, label)

    def _on_camera_selected(self, source, label):
        """Xử lý khi người dùng chọn camera."""
        self._selected_camera_source = source
        self._lbl_current_cam.setText(f"🎥 {label}")
        
        # Gửi target_camera lên Server ngay lập tức để Mini PC bắt đầu stream
        def update_target():
            try:
                # Nếu đang trong phiên thì START, nếu chưa thì STOP (Mini PC vẫn stream ảnh nhưng AI tắt)
                cmd = "START" if (hasattr(self, "_session_id") and self._session_id) else "STOP"
                payload = {"command": cmd, "target_camera": source}
                if cmd == "START":
                    payload["session_id"] = self._session_id
                    
                requests.post("http://127.0.0.1:9696/api/system/command", json=payload, headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"}, timeout=2)
            except: pass
        import threading
        threading.Thread(target=update_target, daemon=True).start()

        # Luôn chuyển/bật luồng stream mới trên giao diện
        if hasattr(self, "_worker") and self._worker:
            self._worker.stop()
            self._worker.wait()
            
        # Khởi tạo worker mới cho camera vừa chọn
        is_remote = (source.upper().startswith("CAM_") or source.lower().startswith("rtsp://"))
        if is_remote:
            self._worker = RemoteStreamWorker(camera_id=source)
        else:
            self._worker = AttendanceWorker(camera_source=source)
            
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.error_occurred.connect(self._on_camera_error)
        self._worker.start()
        
        self._camera_view.set_placeholder("🖥️  ĐANG KẾT NỐI CAMERA...")
        self._cam_stack.setCurrentIndex(1)
            
        self._btn_start.setEnabled(True)
        self._btn_start.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.GREEN}; color: white;
                border: none; border-radius: 12px;
                font-size: 18px; font-weight: 900;
            }}
            QPushButton:hover {{ background: {Colors.GREEN_DIM}; }}
        """)

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {Colors.BG_APP};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        panel.setMinimumWidth(380)

        # ── Tiêu đề ──
        title = QLabel("Điểm Danh")
        title.setStyleSheet(f"font-size: 22px; font-weight: 900; color: {Colors.TEXT};")
        layout.addWidget(title)

        # ── Chọn buổi học ──
        session_card = QWidget()
        session_card.setStyleSheet(f"background: {Colors.BG_CARD}; border: none; border-radius: 12px;")
        sc_layout = QVBoxLayout(session_card)
        sc_layout.setSpacing(6)
        sc_layout.setContentsMargins(16, 16, 16, 16)

        sc_title = QLabel("THIẾT LẬP BUỔI HỌC")
        sc_title.setStyleSheet(
            f"font-size: 11px; font-weight: 800; color: {Colors.TEXT_DIM}; letter-spacing: 1.5px; border: none; background: transparent;"
        )
        sc_layout.addWidget(sc_title)

        self._refresh_cameras()
        self._cmb_session_class = QComboBox() # Khởi tạo ẩn tránh lỗi reference

        subj_lbl = QLabel("Buổi học / Ca học")
        subj_lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 12px; font-weight: 600; border: none; background: transparent; padding-top: 6px;")
        sc_layout.addWidget(subj_lbl)

        self._inp_subject = QComboBox()
        self._inp_subject.addItems([
            "🌅 Sáng (7h15)",
            "🌤 Chiều (13h15)",
            "🌙 Tối (19h00)",
            "🚨 Đột xuất"
        ])
        self._inp_subject.setStyleSheet(combo_style())
        self._auto_select_session()

        # Ngày
        self._date_picker = QDateEdit()
        self._date_picker.setDate(QDate.currentDate())
        self._date_picker.setCalendarPopup(True)
        self._date_picker.setDisplayFormat("dd/MM/yyyy")
        self._date_picker.setStyleSheet(f"""
            QDateEdit {{
                background: {Colors.BG_CARD};
                color: {Colors.TEXT};
                border: 1.5px solid {Colors.BORDER_LT};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
                min-height: 36px;
            }}
            QDateEdit:focus {{ border-color: {Colors.CYAN}; }}
            QDateEdit::drop-down {{ border: none; width: 30px; }}
            QDateEdit::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {Colors.TEXT_DIM};
                margin-right: 10px;
            }}
        """)

        row_dt = QHBoxLayout()
        row_dt.addWidget(self._inp_subject, 3)
        row_dt.addWidget(self._date_picker, 2)

        sc_layout.addWidget(subj_lbl)
        sc_layout.addLayout(row_dt)
        sc_layout.addStretch()
        layout.addWidget(session_card)

        # ── Nút Bắt đầu / Kết thúc (Lớn, Phong cách Premium) ──
        self._btn_start = QPushButton("▶  BẮT ĐẦU ĐIỂM DANH")
        self._btn_start.setFixedHeight(65)
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.setEnabled(False) # Chờ chọn camera
        self._btn_start.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.BORDER}; color: {Colors.TEXT_DARK};
                border: none; border-radius: 12px;
                font-size: 18px; font-weight: 900;
            }}
        """)
        self._btn_start.clicked.connect(self._start_session)
        layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton("⏹  KẾT THÚC")
        self._btn_stop.setFixedHeight(65)
        self._btn_stop.hide() # Khi chưa bắt đầu thì ẩn
        self._btn_stop.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.RED}; color: #ffffff;
                border: none; border-radius: 12px;
                font-size: 18px; font-weight: 900;
            }}
            QPushButton:hover {{ background: {Colors.RED_DIM}; }}
        """)
        self._btn_stop.clicked.connect(self._stop_session)
        layout.addWidget(self._btn_stop)

        # ── Thống kê realtime ──
        stats_card = QWidget()
        stats_card.setStyleSheet(f"background: {Colors.BG_CARD}; border: none; border-radius: 12px;")
        stats_layout = QGridLayout(stats_card)
        stats_layout.setSpacing(12)
        stats_layout.setContentsMargins(12, 12, 12, 12)

        def stat_cell(label: str, color: str):
            col = QVBoxLayout()
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(f"font-size: 26px; font-weight: 900; color: {color}; border: none; background: transparent;")
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_DIM}; font-weight: 600; border: none; background: transparent;")
            col.addWidget(val)
            col.addWidget(lbl)
            return col, val

        c1, self._stat_present = stat_cell("Có mặt",  Colors.GREEN)
        c2, self._stat_absent  = stat_cell("Vắng",     Colors.RED)
        c3, self._stat_total   = stat_cell("Tổng HV",  Colors.CYAN)
        c4, self._stat_elapsed = stat_cell("Thời gian",Colors.ORANGE)

        stats_layout.addLayout(c1, 0, 0)
        stats_layout.addLayout(c2, 0, 1)
        stats_layout.addLayout(c3, 0, 2)
        stats_layout.addLayout(c4, 0, 3)
        layout.addWidget(stats_card)

        # ── Danh sách có mặt (scroll) ──
        list_header = QHBoxLayout()
        list_title = QLabel("DANH SÁCH CÓ MẶT")
        list_title.setStyleSheet(f"font-size: 12px; font-weight: 800; color: {Colors.TEXT_DIM}; letter-spacing: 1.5px;")
        
        self._lbl_list_count = QLabel("0")
        self._lbl_list_count.setStyleSheet(f"""
            font-size: 14px; font-weight: 800; color: {Colors.CYAN};
            background: {Colors.CYAN}18; border-radius: 12px; padding: 2px 10px;
        """)
        list_header.addWidget(list_title)
        list_header.addStretch()
        list_header.addWidget(self._lbl_list_count)
        layout.addLayout(list_header)

        # Scroll area
        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                border-radius: 12px;
                background: {Colors.BG_CARD};
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: {Colors.BORDER_LT}; border-radius: 3px; }}
        """)

        self._list_container = QWidget()
        self._list_container.setStyleSheet(f"background: {Colors.BG_CARD};")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()

        self._list_scroll.setWidget(self._list_container)
        layout.addWidget(self._list_scroll, 1)

        # Đồng hồ phiên
        self._lbl_session_clock = QLabel("00:00:00")
        self._lbl_session_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_session_clock.setStyleSheet(
            f"font-size: 14px; color: {Colors.TEXT_DARK}; font-weight: 800; letter-spacing: 2px;"
        )
        layout.addWidget(self._lbl_session_clock)

        return panel

    # ─── Logic ────────────────────────────────

    def _auto_select_session(self):
        # TASK 1: Tự động chọn ca học dựa trên giờ hệ thống
        from datetime import datetime
        hour = datetime.now().hour
        if 5 <= hour < 12:
            idx = 0  # 🌅 Sáng
        elif 12 <= hour < 18:
            idx = 1  # 🌤 Chiều
        elif 18 <= hour <= 23:
            idx = 2  # 🌙 Tối
        else:
            idx = 3  # 🚨 Đột xuất (0h - 4h59)
            
        self._inp_subject.setCurrentIndex(idx)

    def _refresh_cameras_async(self):
        """
        [FIX threading] Khởi chạy CameraDataFetcher (QThread) để thu thập
        data camera mà KHÔNG block GUI thread và KHÔNG tạo QObject ở thread ngoài.
        Kết quả được emit về GUI thread qua signal data_ready → _build_camera_menu().
        """
        # Nếu fetcher cũ đang chạy thì bỏ qua lần này (tránh chồng chất)
        if self._cam_fetcher and self._cam_fetcher.isRunning():
            return
        self._cam_fetcher = CameraDataFetcher(self)
        self._cam_fetcher.data_ready.connect(self._build_camera_menu)
        self._cam_fetcher.start()

    def _check_camera_port(self, ip_port: str, timeout=0.5) -> bool:
        """
        TASK 1: Hàm kiểm tra trạng thái thực bằng Socket (RTSP port).
        Thay vì ping ICMP dễ bị chặn, thử connect trực tiếp TCP.
        """
        if not ip_port or ip_port == "OFFLINE" or ip_port.startswith("127."):
            return False # Bỏ qua localhost hoặc OFFLINE
            
        import socket
        try:
            ip = ip_port.split(":")[0]
            port = int(ip_port.split(":")[1]) if ":" in ip_port else 554
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((ip, port))
            return True
        except Exception:
            return False

    def _refresh_cameras(self):
        """
        Phiên bản đồng bộ — chỉ gọi từ GUI thread trong lần khởi tạo UI.
        Các lần gọi định kỳ sau đó phải đi qua _refresh_cameras_async().
        """
        try:
            cameras = camera_repo.get_all(active_only=True)
            cam_groups: dict = {f"KTX E{i}": [] for i in range(1, 7)}

            try:
                resp = requests.get("http://127.0.0.1:9696/api/system/edge_status", timeout=2)
                edge_data = resp.json() if resp.status_code == 200 else {}
                if edge_data:
                    for box_id, box_data in edge_data.items():
                        status = box_data.get("camera_status", {})
                        if not status:
                            continue
                        group_key = box_id
                        if group_key not in cam_groups:
                            cam_groups[group_key] = []
                        for k, v in status.items():
                            cam_name = k
                            cam_source = v
                            if isinstance(v, dict):
                                cam_name = v.get("name", k)
                                cam_source = v.get("source", "")
                            cam_ip = ""
                            if isinstance(cam_source, str) and cam_source.startswith("rtsp://"):
                                try:
                                    cam_ip = cam_source.split("@")[1].split("/")[0] if "@" in cam_source else cam_source.split("://")[1].split("/")[0]
                                except Exception:
                                    cam_ip = ""
                            display_ip = cam_ip if cam_ip else "OFFLINE"
                            from core.state_manager import state_manager
                            # [FIX TASK 2] Đọc trạng thái nhẹ nhàng qua is_active từ Redis
                            real_online = state_manager.get_camera_is_active(k, cam_source)
                            if k.startswith("rtsp://"):
                                try:
                                    ip_port = k.split("@")[1].split("/")[0] if "@" in k else k.split("://")[1].split("/")[0]
                                except Exception:
                                    ip_port = "IP Cam"
                                label = (f"🟢 IP Cam ({ip_port}) [ONLINE]" if real_online
                                         else f"🔴 IP Cam ({ip_port}) [MẤT KẾT NỐI]")
                                cam_groups[group_key].append((label, k, ip_port))
                            else:
                                label = (f"🟢 {cam_name} ({display_ip}) [ONLINE]" if real_online
                                         else f"🔴 {cam_name} ({display_ip}) [MẤT KẾT NỐI]")
                                cam_groups[group_key].append((label, k, display_ip))
            except Exception as e:
                logger.warning(f"Không lấy được trạng thái live: {e}")

            # Build menu trên GUI thread (hàm này luôn chạy trên GUI thread)
            self._build_camera_menu(cam_groups)

        except Exception as e:
            logger.error(f"Error refreshing cameras: {e}")

    def _build_camera_menu(self, cam_groups: dict):
        """
        [FIX threading] Xây dựng QMenu và cập nhật UI.
        PHẢI được gọi trên GUI (main) thread — dù từ _refresh_cameras (đồng bộ)
        hay từ signal data_ready của CameraDataFetcher (auto-marshal qua Qt signal).
        """
        try:
            # Lưu lại data mới nhất
            self._cam_groups = cam_groups

            menu_style = f"""
                QMenu {{ background: {Colors.BG_DARK}; border: 1px solid {Colors.BORDER}; color: {Colors.TEXT}; padding: 5px; }}
                QMenu::item {{ padding: 10px 40px; border-radius: 6px; font-weight: 600; }}
                QMenu::item:selected {{ background: {Colors.CYAN}22; color: {Colors.CYAN}; }}
                QMenu::separator {{ height: 1px; background: {Colors.BORDER}; margin: 5px 10px; }}
            """

            # Tạo QMenu hoàn toàn trên GUI thread
            main_menu = QMenu(self)
            main_menu.setStyleSheet(menu_style)

            groups_to_render = list(app_config.camera_groups)
            for k in cam_groups.keys():
                if k not in groups_to_render:
                    groups_to_render.append(k)

            for g_name in groups_to_render:
                cams = list(cam_groups.get(g_name, []))  # copy để tránh race condition

                is_configured_ktx = any(g_name == g for g in app_config.camera_groups)
                if not cams and is_configured_ktx:
                    try:
                        from config import FLOOR_CLASS_MAPPING
                        floor_map = FLOOR_CLASS_MAPPING.get(g_name, {}).get("floors", {})
                        for floor_num in sorted(floor_map.keys()):
                            cams.append((f"⚪ Tầng {floor_num} [OFFLINE]", f"OFFLINE_{g_name}_{floor_num}", "OFFLINE"))
                    except Exception:
                        pass

                if not cams:
                    continue

                sub_menu = main_menu.addMenu(f"🏢  {g_name}")
                sub_menu.setStyleSheet(menu_style)

                for c_name, source, _ip in cams:
                    # c_name đã chứa 🟢 hoặc 🔴 từ CameraDataFetcher, không cần thêm 📷
                    display_text = c_name if ("🟢" in c_name or "🔴" in c_name or "⚪" in c_name) else f"📷 {c_name}"
                    act = sub_menu.addAction(display_text)
                    act.setData(source)
                    act.triggered.connect(lambda chk, s=source, n=c_name: self._on_camera_selected(s, n))

            # [FIX TASK 2] Just-In-Time Menu Update: cập nhật khi user click mở menu
            main_menu.aboutToShow.connect(self._update_local_menu_status)
            self._btn_camera_select.setMenu(main_menu)

            # Cập nhật label mặc định
            if self._cam_groups.get("KTX E4"):
                c_name, source, _ip = self._cam_groups["KTX E4"][0]
                self._lbl_current_cam.setText(f"🎥 KTX E4 - {c_name.split(' ')[1]}")
            elif self._cam_groups.get("KTX E1"):
                c_name, source, _ip = self._cam_groups["KTX E1"][0]
                self._lbl_current_cam.setText(f"🎥 KTX E1 - {c_name.split(' ')[1]}")

        except Exception as e:
            logger.error(f"[_build_camera_menu] Error: {e}")

    def _update_local_menu_status(self):
        """
        [FIX TASK 1 & 2] Single Source of Truth cho Local Edge App.
        Cập nhật trạng thái QMenu dựa vào _workers đang chạy trên MiniPC,
        bỏ qua cache trễ từ Server. Chạy ngay khi bấm mở menu (Just-in-Time).
        """
        try:
            import sys
            # Nếu module headless_processor chưa được nạp, tức là không chạy ở chế độ Edge
            if "MINI_PC.headless_processor" not in sys.modules:
                return
                
            from MINI_PC.headless_processor import headless_processor
            if not getattr(headless_processor, '_workers', None):
                return

            menu = self._btn_camera_select.menu()
            if not menu: return

            def _update_actions(m):
                for act in m.actions():
                    if act.menu():
                        _update_actions(act.menu())
                    else:
                        source = act.data()
                        if not source: continue

                        # Logic: Tìm worker, nếu có worker và đang active thì ONLINE
                        is_online = False
                        for cid, worker in headless_processor._workers.items():
                            if worker.source == source or worker.camera_id == source:
                                if getattr(worker, "_active", False):
                                    is_online = True
                                break
                        
                        txt = act.text()
                        # Làm sạch string cũ
                        txt = txt.replace("🟢 ", "").replace("🔴 ", "").replace(" [ONLINE]", "").replace(" [MẤT KẾT NỐI]", "")
                        
                        if is_online:
                            act.setText(f"🟢 {txt} [ONLINE]")
                        else:
                            act.setText(f"🔴 {txt} [MẤT KẾT NỐI]")
                            
            _update_actions(menu)
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Lỗi khi update Just-In-Time menu: {e}")


    def _load_buildings(self):
        """Load danh sách tòa nhà từ bảng ToaNha vào ComboBox."""
        self._cmb_session_class.clear()
        self._cmb_session_class.addItem("-- Chọn tòa nhà --", None)
        try:
            buildings = building_repo.get_all()
            for bld in buildings:
                self._cmb_session_class.addItem(f"Tòa {bld.ma_toa} ({bld.ten_toa})", bld.ma_toa)
        except Exception as e:
            logger.warning(f"Không load được danh sách tòa nhà: {e}")

    def _load_session_classes(self):
        """Giữ lại để tương thích hoặc debug, nhưng không dùng chính nữa."""
        pass
    def _start_session(self):
        # ── Lấy tòa nhà đã chọn (MaToa), mặc định "ALL" nếu chưa chọn ──
        class_id = self._cmb_session_class.currentData()
        if not class_id:
            class_id = "ALL"

        camera_source = self._selected_camera_source
        qdate = self._date_picker.date()
        session_date = date(qdate.year(), qdate.month(), qdate.day())

        try:
            from services.attendance_service import attendance_service

            # Xoá danh sách hiển thị cũ trên UI
            while self._list_layout.count() > 1:
                child = self._list_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            
            self._rendered_student_codes.clear()

            # --- SỬA LỖI KHÓA NGOẠI (1452) ---
            # class_id bây giờ đã chắc chắn tồn tại trong bảng Classes
            sid = attendance_service.create_session(
                class_id=class_id, 
                subject_name=self._inp_subject.currentText(), 
                session_date=session_date
            )
            attendance_service.start_session(sid)
            self._session_id = sid
            self._session_start = datetime.now()
            self._attendance_count = 0

            # Bật timer đồng hồ ngay, nhưng delay 1.5 giây trước khi poll DB
            # để tránh query ngay lập tức lấy record cũ chưa được flush hết
            self._clock_timer.start(1000)
            QTimer.singleShot(1500, lambda: self._db_poll_timer.start(1000))

            # TASK 2: Mapping Session Type dựa trên lựa chọn UI
            raw_subject = self._inp_subject.currentText()
            if "Sáng" in raw_subject:
                session_type = "MORNING"
            elif "Chiều" in raw_subject:
                session_type = "AFTERNOON"
            elif "Tối" in raw_subject:
                session_type = "EVENING"
            elif "Đột xuất" in raw_subject:
                session_type = "EXTRA"
            else:
                session_type = "UNKNOWN"

            # Gửi lệnh START tới API Server
            def send_start():
                try:
                    requests.post("http://127.0.0.1:9696/api/system/command", json={
                        "command": "START",
                        "session_id": sid,
                        "class_id": class_id,
                        "target_camera": camera_source,
                        "session_type": session_type  # Bổ sung session_type gửi lên API Server
                    }, headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"}, timeout=5)
                except Exception as ex:
                    logger.warning(f"Không thể gửi lệnh START tới API: {ex}")
            
            import threading
            threading.Thread(target=send_start, daemon=True).start()

            if not hasattr(self, "_worker") or not self._worker or not self._worker.isRunning():
                QMessageBox.warning(self, "Lỗi", "Vui lòng chọn lại Camera để kết nối!")
                return

            self._btn_start.hide()
            self._btn_stop.show()
            self._btn_camera_select.setEnabled(True)
            self._inp_subject.setEnabled(False)
            self._date_picker.setEnabled(False)
            self._cmb_session_class.setEnabled(False)
            
            self._set_cam_status("● Đang điểm danh", Colors.GREEN)
            self._stat_present.setText("0")
            self._stat_absent.setText("—")
            self._stat_total.setText("—")
            self._lbl_list_count.setText("0")

        except Exception as e:
            logger.error(f"Start session error: {e}")
            QMessageBox.critical(self, "Lỗi", f"Không thể bắt đầu: {e}")
    def _stop_session(self):
        reply = QMessageBox.question(
            self, "Kết thúc",
            "Bạn có chắc muốn kết thúc?\nHọc viên chưa điểm danh sẽ bị đánh vắng.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes: return

        # Xoá logic dừng worker ở đây để camera vẫn tiếp tục stream live
        pass

        try:
            from services.attendance_service import attendance_service
            session = attendance_service.end_session()

            # Dừng các timer
            self._db_poll_timer.stop()
            self._clock_timer.stop()
            self._session_id = None  # Reset để _poll_live_records không query session cũ

            # Ra lệnh STOP qua API
            def send_stop():
                try:
                    requests.post("http://127.0.0.1:9696/api/system/command", json={
                        "command": "STOP"
                    }, headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"}, timeout=5)
                except: pass

            import threading
            threading.Thread(target=send_stop, daemon=True).start()

            if session:
                QMessageBox.information(
                    self, "Tổng kết",
                    f"✅ Có mặt:  {session.present_count}\n"
                    f"❌ Vắng:    {session.absent_count}\n"
                    f"⏱ Thời gian: {self._get_elapsed()}"
                )
        except Exception as e:
            logger.error(f"Stop session error: {e}")
            # Đảm bảo timer luôn được dừng dù có exception
            self._db_poll_timer.stop()
            self._clock_timer.stop()
            self._session_id = None

        # Reset UI
        self._btn_start.show()
        self._btn_stop.hide()
        self._btn_camera_select.setEnabled(True)
        self._inp_subject.setEnabled(True)
        self._date_picker.setEnabled(True)
        self._cmb_session_class.setEnabled(True)
        self._load_buildings()
        self._camera_view.clear()
        
        self._cam_stack.setCurrentIndex(0)
        self._set_cam_status("⬤  Chờ", Colors.TEXT_DARK)
        self._session_start = None

    def _on_frame(self, frame: np.ndarray, elapsed_ms: float, detections: list = None):
        self._camera_view.update_frame_with_detections(frame, detections)
        
        # Nếu đang hiện thông báo lỗi "Chưa có hình", xóa đi vì đã có hình rồi
        if "chưa có hình" in self._lbl_cam_status.text().lower():
             self._set_cam_status("● Đang điểm danh", Colors.GREEN)

        if elapsed_ms > 0:
            color = Colors.GREEN if elapsed_ms < 150 else Colors.ORANGE if elapsed_ms < 300 else Colors.RED
            self._lbl_fps.setText(f"{elapsed_ms:.0f}ms")
            self._lbl_fps.setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {color}; "
                f"background: {Colors.BG_CARD}; border-radius: 6px; padding: 4px 12px;"
            )

    def _on_attendance_done(self, event: dict):
        self._attendance_count += 1
        item = AttendanceListItem(event, self._attendance_count)
        count = self._list_layout.count()
        self._list_layout.insertWidget(count - 1, item)

        self._lbl_list_count.setText(str(self._attendance_count))
        self._stat_present.setText(str(self._attendance_count))

        QTimer.singleShot(50, lambda: self._list_scroll.verticalScrollBar().setValue(
            self._list_scroll.verticalScrollBar().maximum()
        ))
        
        self._show_toast(f"✅  {event['full_name']} - {event['class_code']}  ({event['similarity']*100:.1f}%)")

    def _update_stats_ui(self, stats: dict):
        if "total_recorded" in stats:
            self._stat_present.setText(str(stats["total_recorded"]))

    def _on_camera_error(self, msg: str):
        # Hiển thị thông báo lỗi chi tiết ra thanh trạng thái
        self._set_cam_status(f"⬤  {msg}", Colors.RED)

    def _show_toast(self, msg: str):
        self._toast.setText(msg)
        self._toast.show()
        if not hasattr(self, '_toast_timer'):
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._toast.hide)
        self._toast_timer.start(3000)

    def _set_cam_status(self, text: str, color: str):
        self._lbl_cam_status.setText(text)
        self._lbl_cam_status.setStyleSheet(f"font-size: 13px; color: {color}; font-weight: 700;")
        
        # Đồng bộ trạng thái HUD trên Camera Preview
        if "điểm danh" in text.lower():
            self._camera_view.set_status("ONLINE", color)
        else:
            self._camera_view.set_status("OFFLINE", color)

    def _trigger_db_poll(self):
        """
        [FIX #3] Được gọi từ QTimer mỗi 1s.
        Khởi động DBPollThread mới nếu thread cũ đã xong.
        Tránh chồng chất nhiều thread nếu DB chậm.
        """
        if not self._session_id:
            return
        # Nếu thread cũ còn chạy → bỏ qua lần poll này
        if self._db_poll_thread and self._db_poll_thread.isRunning():
            return
        self._db_poll_thread = DBPollThread(self._session_id)
        self._db_poll_thread.results_ready.connect(self._on_db_poll_results)
        self._db_poll_thread.error_occurred.connect(
            lambda msg: logger.error(f"_trigger_db_poll error: {msg}")
        )
        self._db_poll_thread.start()

    def _on_db_poll_results(self, present_list: list, total_students: int):
        """
        [FIX #3] Được gọi từ GUI thread qua signal khi DBPollThread hoàn thành.
        Cập nhật UI hoàn toàn an toàn — không bao giờ block.
        """
        for p in present_list:
            code = p["code"]
            if code not in self._rendered_student_codes:
                self._rendered_student_codes.add(code)
                event = {
                    "full_name":    p["name"],
                    "class_code":   p.get("class_code", ""),
                    "student_code": code,
                    "similarity":   p["score"],
                    "building":     p.get("building", ""),
                    "room":         p.get("room", ""),
                    "time_str": (
                        p["time"].strftime("%H:%M:%S")
                        if hasattr(p["time"], "strftime")
                        else str(p["time"])
                    ),
                }
                self._on_attendance_done(event)

        self._stat_total.setText(str(total_students))
        absent_count = total_students - len(present_list)
        self._stat_absent.setText(str(max(0, absent_count)))

    def _poll_live_records(self):
        """Giữ lại để tương thích — routing qua _trigger_db_poll."""
        self._trigger_db_poll()

    def _update_clock(self):
        if self._session_start:
            delta = datetime.now() - self._session_start
            total_s = int(delta.total_seconds())
            h, m, s = total_s // 3600, (total_s % 3600) // 60, total_s % 60
            self._lbl_session_clock.setText(f"{h:02d}:{m:02d}:{s:02d}")
            self._stat_elapsed.setText(f"{m:02d}:{s:02d}" if h == 0 else f"{h}h{m:02d}m")
            self._lbl_session_clock.setStyleSheet(
                f"font-size: 14px; color: {Colors.CYAN}; font-weight: 800; letter-spacing: 2px;"
            )

    def _get_elapsed(self) -> str:
        if not self._session_start: return "—"
        total_s = int((datetime.now() - self._session_start).total_seconds())
        m, s = divmod(total_s, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def closeEvent(self, event):
        if self._worker:
            self._worker.stop()
            self._worker.wait(2000)
        # [FIX #3] Dọn dẹp DBPollThread
        if self._db_poll_thread and self._db_poll_thread.isRunning():
            self._db_poll_thread.quit()
            self._db_poll_thread.wait(1000)
        super().closeEvent(event)

    def hideEvent(self, event):
        if self._worker and not self._worker._paused: self._worker.pause()
        super().hideEvent(event)

    # [FIX #4] Xóa định nghĩa showEvent trùng lặp — chỉ giữ 1 bản duy nhất
    def showEvent(self, event):
        if self._worker and self._worker._paused: self._worker.resume()
        self._refresh_cameras_async()   # [FIX #8] gọi async, không block GUI
        self._load_buildings()
        super().showEvent(event)