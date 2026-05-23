"""
ui/pages/enroll_page.py
"""
import cv2
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QComboBox,
    QGroupBox, QScrollArea, QFrame, QSizePolicy,
    QMessageBox, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter,
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize,
)
from PyQt6.QtGui import QFont, QPixmap, QImage, QColor
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ui.styles.theme import Colors, card_style, badge_style, combo_style, input_style
from ui.widgets.camera_preview import CameraPreviewWidget
import requests


# ─────────────────────────────────────────────
#  Worker: chụp ảnh từ camera trong thread riêng
# ─────────────────────────────────────────────
class CaptureWorker(QThread):
    """Thread chụp ảnh liên tục từ webcam/IP camera."""
    frame_ready   = pyqtSignal(np.ndarray)   # Frame mới để hiển thị
    photo_taken   = pyqtSignal(int, int)     # (current, total)
    capture_done  = pyqtSignal(list)         # Danh sách frames đã chụp
    face_detected = pyqtSignal(bool)         # Có phát hiện khuôn mặt không

    def __init__(self, source=0, target_count=15, parent=None):
        super().__init__(parent)
        self.source       = source
        self.target_count = target_count
        self._capturing   = False
        self._running     = True
        self._frames      = []
        self._last_capture_time = 0

    def start_capture(self, existing_frames=None):
        self._capturing = True
        self._frames    = list(existing_frames) if existing_frames else []

    def stop_capture(self):
        self._capturing = False

    def stop(self):
        self._running   = False
        self._capturing = False

    def run(self):
        import time
        from config import camera_config
        
        # Đổi str thành int nếu là webcam USB
        source = int(self.source) if str(self.source).isdigit() else self.source
        cap = cv2.VideoCapture(source)
        
        if not cap.isOpened():
            logger.error(f"Không mở được camera source={self.source}")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  camera_config.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config.height)
        cap.set(cv2.CAP_PROP_FPS, camera_config.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Thay thế hoàn toàn model Anti-Spoofing / Buffalo bằng OpenCV siêu nhẹ
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        
        frame_skip = 0

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            frame_skip += 1
            has_face = False
            faces = []
            if frame_skip >= camera_config.process_every_n_frames:
                frame_skip = 0
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(100, 100))
                has_face = len(faces) > 0
                self.face_detected.emit(has_face)

            # Lưu frame đã RESIZE cho embedding (Tiết kiệm RAM cực lớn)
            if self._capturing and has_face:
                now = time.time()
                if now - self._last_capture_time >= 0.5:
                    # Resize xuống 640p trước khi lưu vào RAM
                    h_orig, w_orig = frame.shape[:2]
                    capture_scale = 640 / max(h_orig, w_orig)
                    small_frame = cv2.resize(frame, (int(w_orig * capture_scale), int(h_orig * capture_scale)))
                    
                    self._frames.append(small_frame)
                    self._last_capture_time = now
                    count = len(self._frames)
                    self.photo_taken.emit(count, self.target_count)
                    
                    if count >= self.target_count:
                        self._capturing = False
                        self.capture_done.emit(self._frames) # Không dùng copy() ở đây để tránh nhân đôi RAM
                    
                    # Giải phóng frame trung gian
                    del small_frame
                    import gc
                    gc.collect()

            # Flip ngang để hiển thị như gương — chỉ dùng cho UI
            display = cv2.flip(frame, 1)
            w = display.shape[1]

            # Vẽ bounding box trên frame đã flip
            if has_face:
                try:
                    for (x, y, fw, fh) in faces:
                        x1, y1, x2, y2 = x, y, x + fw, y + fh
                        x1m, x2m = w - x2, w - x1
                        color = (0, 220, 80)
                        cv2.rectangle(display, (int(x1m), int(y1)), (int(x2m), int(y2)), color, 2)
                except Exception:
                    pass

            # Vẽ khung guide căn giữa
            h, w2 = display.shape[:2]
            cx, cy = w2 // 2, h // 2
            gw, gh = 260, 320
            cv2.rectangle(display,
                          (cx - gw // 2, cy - gh // 2),
                          (cx + gw // 2, cy + gh // 2),
                          (80, 80, 80), 1)

            self.frame_ready.emit(display)

        cap.release()


# ─────────────────────────────────────────────
#  EnrollWorker — chạy finish_enrollment() trên thread riêng
# ─────────────────────────────────────────────
class EnrollWorker(QThread):
    """Thread xử lý trích xuất embedding + lưu DB để không block UI."""
    done = pyqtSignal(object)  # kết quả (EnrollResult)

    def __init__(self, frames: list, student_id: int, parent=None):
        super().__init__(parent)
        self._frames = frames
        self._student_id = student_id

    def run(self):
        try:
            from services.enrollment_service import enrollment_service
            enrollment_service.start_capture(
                student_id=self._student_id,
                photo_count=len(self._frames)
            )
            enrollment_service._capture.frames = self._frames
            result = enrollment_service.finish_enrollment()
            # Xóa list frame ngay sau khi xong để giải phóng RAM
            self._frames = []
            import gc
            gc.collect()
            self.done.emit(result)
        except Exception as e:
            logger.error(f"EnrollWorker error: {e}")
            # Phát một kết quả lỗi giả
            class _Err:
                success = False
                error_msg = str(e)
            self.done.emit(_Err())


# ─────────────────────────────────────────────
#  EnrollPage
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  EnrollPage
# ─────────────────────────────────────────────
class EnrollPage(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture_worker: CaptureWorker | None = None
        self._enroll_worker: EnrollWorker | None = None
        self._captured_frames: list = []
        self._current_student_id: int | None = None
        self._camera_active = False
        self._mode = "create"   # "create" hoặc "update"

        self._setup_ui()
        self._reset_form()

    # ─── UI ───────────────────────────────────

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(30, 25, 30, 25) # Đồng bộ margin với StudentsPage
        main.setSpacing(25)

        # ── Header ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 10)
        
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        
        self._title_lbl = QLabel("ĐĂNG KÝ HỌC VIÊN")
        self._title_lbl.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {Colors.TEXT};")
        
        self._subtitle_lbl = QLabel("Hệ thống nhận diện khuôn mặt — Chụp 15 ảnh mẫu để đảm bảo độ chính xác")
        self._subtitle_lbl.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_DIM};")
        
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._subtitle_lbl)
        header.addLayout(title_col)
        header.addStretch()
        
        main.addLayout(header)

        # ── Global Buttons Initialization ──
        def create_action_btn(text: str, bg: str, hover: str, text_col: str = "white", height=45):
            btn = QPushButton(text)
            btn.setFixedHeight(height)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ 
                    background: {bg}; color: {text_col}; border-radius: 8px; 
                    font-weight: 800; font-size: 13px; letter-spacing: 0.5px; 
                    padding: 0 20px; border: none;
                }}
                QPushButton:hover {{ background: {hover}; }}
                QPushButton:disabled {{ background: {Colors.BG_DARK}; color: {Colors.TEXT_DARK}; border: none {Colors.BORDER_LT}; }}
            """)
            return btn

        self._btn_reset = create_action_btn("🔄  LÀM MỚI", Colors.BG_CARD, Colors.BG_HOVER, Colors.TEXT)
        self._btn_reset.setStyleSheet(self._btn_reset.styleSheet().replace("border: none;", f"border: 1.5px solid {Colors.BORDER_LT};"))
        self._btn_reset.clicked.connect(self._reset_form)

        self._btn_create = create_action_btn("✅  XÁC NHẬN THÔNG TIN", Colors.CYAN, Colors.CYAN_DIM)
        self._btn_create.clicked.connect(self._on_create_student)

        self._btn_camera = create_action_btn("📷  MỞ CAMERA", Colors.BG_CARD, Colors.BG_HOVER, Colors.TEXT)
        self._btn_camera.setStyleSheet(self._btn_camera.styleSheet().replace("border: none;", f"border: 1.5px solid {Colors.BORDER_LT};"))
        self._btn_camera.clicked.connect(self._toggle_camera)

        self._btn_capture = create_action_btn("📸  BẮT ĐẦU CHỤP", Colors.GREEN, Colors.GREEN_DIM)
        self._btn_capture.setEnabled(False)
        self._btn_capture.clicked.connect(self._start_capture)

        self._btn_enroll = create_action_btn("🎯  HOÀN TẤT ĐĂNG KÝ", Colors.ORANGE, "#D97706")
        self._btn_enroll.setEnabled(False)
        self._btn_enroll.clicked.connect(self._finish_enrollment)

        # ── Content Splitter ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: transparent; width: 12px; }}")

        left = self._build_form_panel()
        right = self._build_camera_panel()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([550, 550])
        main.addWidget(splitter, 1)



    def _build_form_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet(f"background: {Colors.BG_CARD}; border: none; border-radius: 12px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        title = QLabel("THÔNG TIN CƠ BẢN")
        title.setStyleSheet(f"font-size: 12px; font-weight: 800; color: {Colors.CYAN}; letter-spacing: 1.5px;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(15)
        
        def create_label(text: str):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 11px; font-weight: 800; text-transform: uppercase;")
            return lbl

        grid.addWidget(create_label("Mã học viên *"), 0, 0)
        grid.addWidget(create_label("Họ và tên *"), 0, 1)
        self._inp_code = QLineEdit(); self._inp_code.setStyleSheet(input_style()); self._inp_code.setFixedHeight(42); self._inp_code.setPlaceholderText("VD: HV001")
        self._inp_name = QLineEdit(); self._inp_name.setStyleSheet(input_style()); self._inp_name.setFixedHeight(42); self._inp_name.setPlaceholderText("Nguyễn Văn A")
        self._inp_code.editingFinished.connect(self._check_existing_student)
        grid.addWidget(self._inp_code, 1, 0)
        grid.addWidget(self._inp_name, 1, 1)

        grid.addWidget(create_label("Lớp học *"), 2, 0)
        grid.addWidget(create_label("Giới tính *"), 2, 1)
        self._cmb_class = QComboBox(); self._cmb_class.setStyleSheet(combo_style()); self._cmb_class.setFixedHeight(42)
        self._cmb_gender = QComboBox(); self._cmb_gender.addItems(["-- Chọn --", "Nam", "Nữ"]); self._cmb_gender.setStyleSheet(combo_style()); self._cmb_gender.setFixedHeight(42)
        grid.addWidget(self._cmb_class, 3, 0)
        grid.addWidget(self._cmb_gender, 3, 1)

        grid.addWidget(create_label("Tòa nhà"), 4, 0)
        grid.addWidget(create_label("Tầng"), 4, 1)
        self._cmb_building = QComboBox(); self._cmb_building.setStyleSheet(combo_style()); self._cmb_building.setFixedHeight(42)
        self._cmb_floor = QComboBox(); self._cmb_floor.addItem("-- Tầng --"); self._cmb_floor.setStyleSheet(combo_style()); self._cmb_floor.setFixedHeight(42)
        grid.addWidget(self._cmb_building, 5, 0)
        grid.addWidget(self._cmb_floor, 5, 1)

        grid.addWidget(create_label("Phòng"), 6, 0)
        grid.addWidget(create_label("Số điện thoại"), 6, 1)
        self._cmb_room = QComboBox(); self._cmb_room.addItem("-- Phòng --"); self._cmb_room.setStyleSheet(combo_style()); self._cmb_room.setFixedHeight(42)
        self._inp_phone = QLineEdit(); self._inp_phone.setStyleSheet(input_style()); self._inp_phone.setFixedHeight(42); self._inp_phone.setPlaceholderText("090xxxxxxx")
        grid.addWidget(self._cmb_room, 7, 0)
        grid.addWidget(self._inp_phone, 7, 1)

        grid.addWidget(create_label("Email"), 8, 0)
        grid.addWidget(create_label("Nguồn Camera chụp ảnh"), 8, 1)
        self._inp_email = QLineEdit(); self._inp_email.setStyleSheet(input_style()); self._inp_email.setFixedHeight(42); self._inp_email.setPlaceholderText("example@mail.com")
        self._cmb_camera = QComboBox(); self._cmb_camera.setStyleSheet(combo_style()); self._cmb_camera.setFixedHeight(42)
        grid.addWidget(self._inp_email, 9, 0)
        grid.addWidget(self._cmb_camera, 9, 1)

        # Reverse-lookup cascade: Class + Gender → auto-fill Building + Floor
        self._cmb_class.currentIndexChanged.connect(self._on_class_gender_changed)
        self._cmb_gender.currentIndexChanged.connect(self._on_class_gender_changed)
        # Building/Floor still drive Room loading
        self._cmb_building.currentIndexChanged.connect(self._on_building_changed)
        self._cmb_floor.currentIndexChanged.connect(self._on_floor_changed)

        layout.addLayout(grid)
        layout.addStretch()

        self._lbl_create_status = QLabel("")
        self._lbl_create_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_create_status.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_DIM}; min-height: 25px;")
        layout.addWidget(self._lbl_create_status)

        # Bottom Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addWidget(self._btn_reset, 1)
        btn_row.addWidget(self._btn_create, 2)
        layout.addLayout(btn_row)

        return panel


    def _build_camera_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(20)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main Camera View
        self._camera_view = CameraPreviewWidget(placeholder_text="📷 Chờ mở Camera...")
        self._camera_view.setMinimumHeight(400)
        self._camera_view.setObjectName("CamView")
        self._camera_view.setStyleSheet(f"QWidget#CamView {{ background: {Colors.CAM_BG}; border: none; border-radius: 12px; }}")
        layout.addWidget(self._camera_view, 1)

        # Progress Section Card
        prog_card = QFrame()
        prog_card.setStyleSheet(f"background: {Colors.BG_CARD}; border: none; border-radius: 12px;")
        prog_layout = QVBoxLayout(prog_card)
        prog_layout.setContentsMargins(20, 20, 20, 20)
        prog_layout.setSpacing(15)

        p_header = QHBoxLayout()
        p_title = QLabel("TIẾN TRÌNH CHỤP MẪU")
        p_title.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {Colors.TEXT_DIM}; letter-spacing: 1px;")
        self._lbl_count = QLabel("0 / 15")
        self._lbl_count.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {Colors.CYAN};")
        p_header.addWidget(p_title); p_header.addStretch(); p_header.addWidget(self._lbl_count)
        prog_layout.addLayout(p_header)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 15); self._progress_bar.setValue(0); self._progress_bar.setFixedHeight(8); self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(f"QProgressBar {{ background: {Colors.BG_DARK}; border-radius: 4px; border: none; }} QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {Colors.CYAN_DIM}, stop:1 {Colors.CYAN}); border-radius: 4px; }}")
        prog_layout.addWidget(self._progress_bar)

        dot_container = QWidget()
        self._dots_layout = QHBoxLayout(dot_container); self._dots_layout.setContentsMargins(0, 0, 0, 0); self._dots_layout.setSpacing(6); self._dots: list[QLabel] = []
        for i in range(15):
            dot = QLabel("●"); dot.setFixedWidth(16); dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setStyleSheet(f"color: {Colors.BORDER_LT}; font-size: 16px;")
            self._dots.append(dot); self._dots_layout.addWidget(dot)
        self._dots_layout.addStretch()
        self._lbl_face_status = QLabel("⬤ NO FACE")
        self._lbl_face_status.setStyleSheet(f"color: {Colors.TEXT_DARK}; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        self._dots_layout.addWidget(self._lbl_face_status)
        prog_layout.addWidget(dot_container)
        layout.addWidget(prog_card)

        # Action Buttons
        layout.addStretch()
        self._lbl_guide = QLabel("💡 Vui lòng nhập thông tin học viên trước khi chụp ảnh")
        self._lbl_guide.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_guide.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 12px; font-weight: 600; font-style: italic;")
        layout.addWidget(self._lbl_guide)
        
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addWidget(self._btn_camera, 1)
        btn_row.addWidget(self._btn_capture, 1)
        btn_row.addWidget(self._btn_enroll, 1)
        layout.addLayout(btn_row)
        
        # Result Card
        self._result_card = QFrame(); self._result_card.setStyleSheet(f"border-radius: 12px;"); self._result_card.hide()
        res_layout = QVBoxLayout(self._result_card)
        self._lbl_result = QLabel(); self._lbl_result.setWordWrap(True); self._lbl_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_layout.addWidget(self._lbl_result)
        layout.addWidget(self._result_card)

        return panel


    # ─── Public: Load học viên từ trang Danh sách ──

    def load_student(self, student_id: int):
        try:
            from database.repositories import student_repo
            student = student_repo.get_by_id(student_id)
            if student is None:
                logger.error(f"Không tìm thấy học viên id={student_id}")
                return
        except Exception as e:
            logger.error(f"load_student error: {e}")
            return

        self._reset_form()
        self._mode = "update" if student.face_enrolled else "create"
        self._current_student_id = student_id

        self._inp_code.setText(student.student_code or "")
        self._inp_name.setText(student.full_name or "")
        self._inp_phone.setText(student.phone or "")
        self._inp_email.setText(student.email or "")
        
        # 1. Set Building & Floor trước (để trigger load_rooms và load_classes)
        b_idx = self._cmb_building.findData(student.building)
        if b_idx >= 0: self._cmb_building.setCurrentIndex(b_idx)
        
        # Vì floor trong DB đang là VARCHAR (Tầng 1...), cần convert hoặc findText nếu không khớp
        # Tuy nhiên room_repo lưu Tang là INT. 
        # Giả sử student.floor lưu giá trị INT (như Room.tang)
        f_idx = self._cmb_floor.findData(student.floor)
        if f_idx >= 0: self._cmb_floor.setCurrentIndex(f_idx)

        # 2. Sau khi Floor đã set -> Room đã được load -> Set Room
        r_idx = self._cmb_room.findData(student.room)
        if r_idx >= 0: self._cmb_room.setCurrentIndex(r_idx)

        # 3. Set Gender (Dùng findText cho an toàn)
        g_idx = self._cmb_gender.findText(student.gender or "Nam")
        if g_idx >= 0: self._cmb_gender.setCurrentIndex(g_idx)

        # 4. Set Class (class_id = IDLop dạng VARCHAR)
        for i in range(self._cmb_class.count()):
            if str(self._cmb_class.itemData(i)) == str(student.class_id or ""):
                self._cmb_class.setCurrentIndex(i)
                break

        if self._mode == "update":
            self._setup_update_mode(student)
        else:
            self._setup_create_mode_prefilled(student)

    def _setup_update_mode(self, student):
        self._title_lbl.setText("CẬP NHẬT THÔNG TIN")
        self._subtitle_lbl.setText(f"Chỉnh sửa thông tin và chụp lại ảnh mẫu — [{student.student_code}]")
        
        for w in [self._inp_code, self._inp_name, self._cmb_class, self._cmb_gender, self._inp_phone, self._inp_email, self._cmb_building, self._cmb_floor, self._cmb_room]:
            w.setEnabled(True)

        self._btn_create.setText("💾  LƯU CẬP NHẬT")
        self._btn_create.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ORANGE}; color: white; border-radius: 10px;
                font-size: 14px; font-weight: 800;
            }}
            QPushButton:hover {{ background: #D97706; }}
            QPushButton:disabled {{ background: {Colors.BORDER}; color: {Colors.TEXT_DARK}; }}
        """)
        self._btn_create.setEnabled(True)
        try: self._btn_create.clicked.disconnect()
        except: pass
        self._btn_create.clicked.connect(self._on_update_student)
        self._btn_camera.setEnabled(True)
        self._set_create_status(f"✏️  Sẵn sàng chỉnh sửa: [{student.student_code}] {student.full_name}", Colors.ORANGE)

    def _setup_create_mode_prefilled(self, student):
        self._title_lbl.setText("ĐĂNG KÝ HỌC VIÊN")
        self._subtitle_lbl.setText(f"Thông tin đã điền — [{student.student_code}] chỉ cần mở camera và chụp")
        # Không lock form để người dùng có thể sửa nếu cần
        self._btn_create.setEnabled(True)
        self._btn_create.setText("➡️  TIẾP TỤC")
        self._btn_camera.setEnabled(True)
        self._set_create_status(f"✅ Sẵn sàng: [{student.student_code}] {student.full_name} — Nhấn Tiếp tục hoặc Mở Camera", Colors.GREEN)

    def _on_update_student(self):
        code = self._inp_code.text().strip()
        name = self._inp_name.text().strip()
        if not code or not name:
            self._set_create_status("⚠️ Vui lòng nhập đầy đủ mã và tên!", Colors.ORANGE)
            return

        class_id = self._cmb_class.currentData()
        if class_id is None:
            self._set_create_status("⚠️ Vui lòng chọn lớp học!", Colors.ORANGE)
            return

        gender   = self._cmb_gender.currentText()
        gender   = None if gender == "-- Chọn --" else gender
        building = self._cmb_building.currentData()
        floor    = self._cmb_floor.currentData()
        room     = self._cmb_room.currentData()
        class_name = self._cmb_class.currentText()
        phone    = self._inp_phone.text().strip() or None
        email    = self._inp_email.text().strip() or None

        try:
            from database.repositories import student_repo
            updated = student_repo.update(
                student_id=self._current_student_id, student_code=code, full_name=name,
                class_id=class_id, class_name=class_name, gender=gender, phone=phone, email=email,
                building=building, floor=floor, room=room
            )
            if not updated:
                self._set_create_status("❌ Lỗi cập nhật thông tin!", Colors.RED)
                return

            if self._captured_frames:
                self._btn_create.setEnabled(False)
                self._btn_create.setText("⏳  Đang xử lý ảnh...")
                
                self._enroll_worker = EnrollWorker(
                    frames=self._captured_frames,
                    student_id=self._current_student_id
                )
                self._enroll_worker.done.connect(self._on_update_enroll_done)
                self._enroll_worker.start()
            else:
                self._set_create_status(f"✅ Đã cập nhật thông tin [{code}] {name} (không có ảnh mới)", Colors.GREEN)
                self._btn_create.setText("✅  Đã cập nhật")
                self._btn_create.setEnabled(False)
                # [FIX] Cập nhật cache ngay cả khi chỉ đổi text
                self._reload_and_notify()
        except Exception as e:
            logger.error(f"_on_update_student error: {e}")
            self._set_create_status(f"❌ Lỗi: {e}", Colors.RED)
            self._btn_create.setEnabled(True)

    def _on_update_enroll_done(self, result):
        if result.success:
            self._set_create_status(
                f"✅ Cập nhật thành công! Ảnh hợp lệ: {result.photos_valid}/{result.photos_taken}",
                Colors.GREEN
            )
            self._lbl_result.setText(
                f"✅ Đã cập nhật!\n[{result.student_code}] {result.full_name}\nẢnh hợp lệ: {result.photos_valid}/{result.photos_taken}"
            )
            self._lbl_result.setStyleSheet(
                f"font-size: 15px; font-weight: 800; color: {Colors.GREEN}; border: none; background: transparent;"
            )
            self._result_card.setStyleSheet(card_style(Colors.GREEN + "40", radius=12))
            self._result_card.show()
            self._btn_create.setText("✅  Đã cập nhật")
            # [FIX] Reload cache cục bộ + Thông báo API Server để Mini PC biết có embedding mới
            self._reload_and_notify()
        else:
            self._set_create_status(f"❌ Lỗi xử lý ảnh: {result.error_msg}", Colors.RED)
            self._btn_create.setEnabled(True)
            self._btn_create.setText("💾  LƯU CẬP NHẬT")

    # ─── Logic ────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # [FIX] Luôn load buildings để _cmb_building không bao giờ rỗng
        self._load_buildings()
        self._load_classes()
        self._load_cameras()

    def reset_to_new_form(self):
        """Công khai: Reset form về trạng thái tạo mới. Gọi từ ngoài khi cần."""
        self._reset_form()
        self._load_buildings()  # [FIX] Bắt buộc load buildings khi reset
        self._load_classes()
        self._load_cameras()


    def _load_buildings(self):
        """Load danh sách tòa nhà từ bảng ToaNha."""
        current_ma_toa = self._cmb_building.currentData()
        self._cmb_building.blockSignals(True)
        self._cmb_building.clear()
        self._cmb_building.addItem("-- Chọn Tòa --", None)
        try:
            from database.repositories import building_repo
            for b in building_repo.get_all():
                self._cmb_building.addItem(b.ten_toa, b.ma_toa)
            
            if current_ma_toa:
                idx = self._cmb_building.findData(current_ma_toa)
                if idx >= 0: self._cmb_building.setCurrentIndex(idx)
        except Exception as e:
            logger.warning(f"Không load được danh mục tòa nhà: {e}")
        finally:
            self._cmb_building.blockSignals(False)

    @staticmethod
    def _build_reverse_lookup() -> dict:
        """
        TASK 1 — Flatten FLOOR_CLASS_MAPPING thành bảng tra cứu ngược.

        Key  : "{class_name}_{gender}"  VD: "B5D13_Nam", "B5D13_Nữ"
        Value: {"ten_toa": "KTX E4", "ma_toa_hint": "E4", "floor": 4}

        Lưu ý: một class có thể xuất hiện ở nhiều (tòa, tầng) KHÁC giới tính.
        Khi có conflict (cùng class + gender, nhiều tòa), entry sau ghi đè entry trước.
        Trong thực tế mapping hiện tại điều này không xảy ra.
        """
        from core.config import FLOOR_CLASS_MAPPING
        table: dict = {}
        for ten_toa, bld_data in FLOOR_CLASS_MAPPING.items():
            gender   = bld_data.get("gender", "")
            floors   = bld_data.get("floors", {})
            # Gợi ý ma_toa: lấy phần sau cùng của ten_toa ("KTX E4" → "E4")
            ma_toa_hint = ten_toa.split()[-1] if " " in ten_toa else ten_toa
            for floor_int, class_list in floors.items():
                for class_name in class_list:
                    key = f"{class_name}_{gender}"
                    table[key] = {
                        "ten_toa":     ten_toa,
                        "ma_toa_hint": ma_toa_hint,
                        "floor":       int(floor_int),
                    }
        return table

    def _on_class_gender_changed(self):
        """
        TASK 3 — Reverse-lookup: Class + Gender → auto-fill Building + Floor.

        Khi cả hai trường đã được chọn:
          key = "{class_name}_{gender}"  →  tra bảng ngược
          → setCurrentIndex cho building và floor combos
          → setEnabled(False) để read-only (tránh admin bấm nhầm)
          → trigger _load_rooms() để load phòng phù hợp
        """
        class_name = self._cmb_class.currentText().strip()
        gender     = self._cmb_gender.currentText().strip()

        # Chưa chọn đủ cả hai → bỏ qua, để building/floor như cũ
        if not class_name or class_name.startswith("--") or \
           not gender or gender.startswith("--"):
            # Re-enable combos nếu user reset một trong hai
            self._cmb_building.setEnabled(True)
            self._cmb_floor.setEnabled(True)
            return

        key = f"{class_name}_{gender}"
        table = self._build_reverse_lookup()
        match = table.get(key)

        if match is None:
            # Không tìm thấy trong mapping (lớp không thuộc KTX nào theo giới này)
            self._set_create_status(
                f"⚠️ Lớp '{class_name}' + Giới tính '{gender}' không có trong cấu hình KTX. "
                f"Vui lòng chọn Tòa/Tầng thủ công.",
                Colors.ORANGE
            )
            self._cmb_building.setEnabled(True)
            self._cmb_floor.setEnabled(True)
            logger.warning(
                f"[REVERSE LOOKUP] Không tìm thấy key='{key}' trong FLOOR_CLASS_MAPPING. "
                f"Các key có sẵn: {sorted(table.keys())[:10]}..."
            )
            return

        ten_toa     = match["ten_toa"]    # "KTX E4"
        ma_toa_hint = match["ma_toa_hint"]  # "E4"
        floor_val   = match["floor"]      # 4

        logger.info(
            f"[REVERSE LOOKUP] key='{key}' → tòa='{ten_toa}' tầng={floor_val}"
        )

        # ── Auto-fill Building ──────────────────────────────────────────────
        # Tìm theo ma_toa (data) trước, fallback theo ten_toa (text)
        self._cmb_building.blockSignals(True)
        b_idx = self._cmb_building.findData(ma_toa_hint)
        if b_idx < 0:
            b_idx = self._cmb_building.findText(ten_toa)
        if b_idx >= 0:
            self._cmb_building.setCurrentIndex(b_idx)
        else:
            # Building chưa có trong combo (chưa load) → thêm tạm
            self._cmb_building.addItem(ten_toa, ma_toa_hint)
            self._cmb_building.setCurrentIndex(self._cmb_building.count() - 1)
        self._cmb_building.blockSignals(False)

        # ── Auto-fill Floor (load từ DB trước, sau đó set) ──────────────────
        self._cmb_floor.blockSignals(True)
        self._cmb_floor.clear()
        self._cmb_floor.addItem("-- Tầng --", None)
        try:
            from database.repositories import room_repo
            db_floors = room_repo.get_floors_by_building(ma_toa_hint) or []
            for f in sorted(set(db_floors + [floor_val])):
                self._cmb_floor.addItem(f"Tầng {f}", f)
        except Exception:
            self._cmb_floor.addItem(f"Tầng {floor_val}", floor_val)
        f_idx = self._cmb_floor.findData(floor_val)
        if f_idx >= 0:
            self._cmb_floor.setCurrentIndex(f_idx)
        self._cmb_floor.blockSignals(False)

        # ── Khoá Building + Floor (read-only) ───────────────────────────────
        locked_style = combo_style().replace(
            "background:", "background: #1a1a2e;"  # Darker to signal locked
        )
        self._cmb_building.setEnabled(False)
        self._cmb_building.setStyleSheet(
            combo_style() + "QComboBox { color: #7EB8D4; border-color: #7EB8D4; }"
        )
        self._cmb_floor.setEnabled(False)
        self._cmb_floor.setStyleSheet(
            combo_style() + "QComboBox { color: #7EB8D4; border-color: #7EB8D4; }"
        )

        self._set_create_status(
            f"✅ Tự động xác định: {ten_toa} — Tầng {floor_val}  🔒",
            Colors.CYAN
        )

        # Trigger room load sau khi đã set building + floor
        self._load_rooms()

    def _on_building_changed(self):
        """Còn dùng để load floors khi admin chỉnh tay (sau khi unlock)."""
        ma_toa = self._cmb_building.currentData()
        self._cmb_floor.blockSignals(True)
        self._cmb_floor.clear()
        self._cmb_floor.addItem("-- Tầng --", None)
        if ma_toa:
            try:
                from database.repositories import room_repo
                floors = room_repo.get_floors_by_building(ma_toa)
                for f in floors:
                    self._cmb_floor.addItem(f"Tầng {f}", f)
            except Exception as e:
                logger.warning(f"Lỗi load tầng: {e}")
        self._cmb_floor.blockSignals(False)
        self._on_floor_changed()

    def _on_floor_changed(self):
        self._load_rooms()

    def _load_rooms(self):
        ma_toa = self._cmb_building.currentData()
        tang = self._cmb_floor.currentData()
        
        current_room = self._cmb_room.currentText()
        self._cmb_room.blockSignals(True)
        self._cmb_room.clear()
        self._cmb_room.addItem("-- Phòng --")
        
        if ma_toa and tang is not None:
            try:
                from database.repositories import room_repo
                rooms = room_repo.get_by_building_and_floor(ma_toa, tang)
                for r in rooms:
                    self._cmb_room.addItem(r.ten_phong, r.ma_phong)
            except Exception as e:
                logger.warning(f"Lỗi load phòng: {e}")
                
        if current_room:
            idx = self._cmb_room.findText(current_room)
            if idx >= 0: self._cmb_room.setCurrentIndex(idx)
        self._cmb_room.blockSignals(False)

    def _load_classes(self):
        """Load tất cả lớp từ bảng lop (IDLop → class_id, TenLop → class_name)."""
        current_class_id = self._cmb_class.currentData()
        self._cmb_class.blockSignals(True)
        self._cmb_class.clear()
        self._cmb_class.addItem("-- Lớp --", None)

        try:
            from database.repositories import class_repo
            for cls in class_repo.get_all():
                # cls.class_id = IDLop (VARCHAR), cls.class_name = TenLop
                self._cmb_class.addItem(cls.class_name, cls.class_id)

            # Khôi phục lựa chọn cũ nếu có
            if current_class_id is not None:
                for i in range(self._cmb_class.count()):
                    if str(self._cmb_class.itemData(i)) == str(current_class_id):
                        self._cmb_class.setCurrentIndex(i)
                        break
        except Exception as e:
            logger.warning(f"Không load được danh sách lớp: {e}")
        finally:
            self._cmb_class.blockSignals(False)

    def _check_existing_student(self):
        code = self._inp_code.text().strip()
        if not code: return
        # Tránh load lại liên tục nếu vẫn đang ở chế độ update cùng mã
        if self._mode == "update" and self._current_student_id is not None:
            try:
                from database.repositories import student_repo
                stu = student_repo.get_by_id(self._current_student_id)
                if stu and stu.student_code == code:
                    return
            except: pass

        try:
            from database.repositories import student_repo
            student = student_repo.get_by_code(code)
            if student:
                # Nếu đã có, load để cập nhật
                self.load_student(student.student_id)
                self._set_create_status(f"🔍 Đã tự động tải học viên cũ: {student.full_name}", Colors.CYAN)
            else:
                # Trở về trạng thái tạo mới nếu trước đó đang update người khác
                if self._mode == "update":
                    self._reset_form()
                    self._inp_code.setText(code)
                    self._set_create_status(f"📝 Tạo mới học viên: {code}", Colors.CYAN)
        except Exception as e:
            logger.error(f"Error checking student code: {e}")

    def _load_cameras(self):
        """Load danh sách camera từ DB qua Server API."""
        self._cmb_camera.clear()
        try:
            resp = requests.get(
                "http://127.0.0.1:9696/api/system/cameras/edge-list",
                headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"},
                timeout=3
            )
            if resp.status_code == 200:
                cameras = resp.json().get("cameras", [])
                for cam in cameras:
                    label = f"{cam.get('name', '?')} ({cam.get('source','').split('@')[-1].split('/')[0]})"
                    self._cmb_camera.addItem(label, cam.get("source"))
        except Exception:
            pass
        # Fallback: cứ thêm camera USB nếu danh sách rỗng hoặc tất cả
        self._cmb_camera.addItem("Camera tích hợp / USB mặc định", 0)
        self._btn_camera.setEnabled(True)

    def _on_create_student(self):
        logger.info("Button Xác nhận clicked")
        code = self._inp_code.text().strip()
        name = self._inp_name.text().strip()
        if not code or not name:
            self._set_create_status("⚠️ Vui lòng nhập Mã và Tên học viên!", Colors.ORANGE)
            return

        class_id = self._cmb_class.currentData()
        if class_id is None:
            self._set_create_status("⚠️ Vui lòng chọn lớp học!", Colors.ORANGE)
            return

        class_name = self._cmb_class.currentText()
        gender   = self._cmb_gender.currentText()
        gender   = None if gender == "-- Chọn --" else gender
        building = self._cmb_building.currentData()   # ma_toa, e.g. "E4"
        floor    = self._cmb_floor.currentData()      # int tang, e.g. 4
        room     = self._cmb_room.currentData()       # ma_phong, e.g. "406E4"
        phone    = self._inp_phone.text().strip() or None
        email    = self._inp_email.text().strip() or None

        # [FIX] Cảnh báo sớm nếu trường vị trí bị NULL — ngăn lỗi nhận diện sau này
        if building is None or floor is None:
            logger.warning(
                f"[ENROLL WARNING] Tòa nhà/Tầng chưa được chọn cho [{code}]! "
                f"building={building!r}, floor={floor!r}, room={room!r}. "
                f"Học viên sẽ không được lọc đúng tại camera Edge!"
            )
            # Không block — chỉ cảnh báo, để admin quyết định
        else:
            logger.info(
                f"[ENROLL] Tạo học viên [{code}] {name}: "
                f"building={building!r}, floor={floor!r}, room={room!r}, class_id={class_id!r}"
            )

        try:
            from services.enrollment_service import enrollment_service
            sid = enrollment_service.create_student(
                student_code=code, full_name=name, class_id=class_id, gender=gender, phone=phone, email=email,
                class_name=class_name, building=building, floor=floor, room=room
            )
            
            # Nếu sid là None (học viên đã tồn tại), nhưng chúng ta đã có _current_student_id
            if sid is None and self._current_student_id:
                sid = self._current_student_id
            
            if sid and sid > 0:
                self._current_student_id = sid
                self._set_create_status(f"✅ Xác nhận: [{code}] {name}", Colors.GREEN)
                self._btn_create.setEnabled(False)
                self._btn_camera.setEnabled(True)
                self._lock_form()
            else:
                self._set_create_status(f"❌ Mã [{code}] đã tồn tại hoặc lỗi DB!", Colors.RED)
        except Exception as e:
            self._set_create_status(f"❌ Lỗi: {e}", Colors.RED)

    def _toggle_camera(self):
        if not self._camera_active: self._open_camera()
        else: self._close_camera()

    def _open_camera(self):
        if self._capture_worker: return
        camera_source = self._cmb_camera.currentData()
        if camera_source is None:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn Camera hợp lệ!")
            return
            
        self._capture_worker = CaptureWorker(source=camera_source, target_count=15)
        self._capture_worker.frame_ready.connect(self._on_frame)
        self._capture_worker.photo_taken.connect(self._on_photo_taken)
        self._capture_worker.capture_done.connect(self._on_capture_done)
        self._capture_worker.face_detected.connect(self._on_face_detected)
        self._capture_worker.start()

        self._camera_active = True
        self._btn_camera.setText("⏹  ĐÓNG CAMERA")
        self._btn_camera.setStyleSheet(self._btn_camera.styleSheet().replace(Colors.BG_CARD, Colors.RED_LT).replace(Colors.TEXT, Colors.RED))
        if self._current_student_id: self._btn_capture.setEnabled(True)

    def _close_camera(self):
        if self._capture_worker:
            self._capture_worker.stop()
            self._capture_worker.wait(3000)
            self._capture_worker = None
        self._camera_active = False
        self._camera_view.clear()
        self._camera_view.set_status("")
        self._btn_camera.setText("📷  MỞ CAMERA")
        self._btn_camera.setStyleSheet(self._btn_camera.styleSheet().replace(Colors.RED_LT, Colors.BG_CARD).replace(Colors.RED, Colors.TEXT))
        self._btn_capture.setEnabled(False)
        self._btn_capture.setText("📸  BẮT ĐẦU CHỤP")

    def _start_capture(self):
        if not self._current_student_id:
            QMessageBox.warning(self, "Chưa tạo học viên", "Vui lòng tạo học viên trước khi chụp ảnh!")
            return
        if not self._capture_worker: return
        
        valid_count = len(self._captured_frames)
        if valid_count == 0:
            self._progress_bar.setValue(0)
            self._lbl_count.setText("0 / 15")
            for dot in self._dots:
                dot.setText("○")
                dot.setStyleSheet(f"color: {Colors.BORDER_LT}; font-size: 18px;")
            self._result_card.hide()
        else:
            self._progress_bar.setValue(valid_count)
            self._lbl_count.setText(f"{valid_count} / 15")
            for idx, dot in enumerate(self._dots):
                if idx < valid_count:
                    dot.setText("●")
                    dot.setStyleSheet(f"color: {Colors.GREEN}; font-size: 18px;")
                else:
                    dot.setText("○")
                    dot.setStyleSheet(f"color: {Colors.BORDER_LT}; font-size: 18px;")
            
        self._btn_enroll.setEnabled(False)
        self._capture_worker.start_capture(existing_frames=self._captured_frames)
        self._btn_capture.setEnabled(False)
        self._btn_capture.setText("📸  ĐANG CHỤP...")
        self._lbl_guide.setText("✅  Hệ thống đang chụp tự động — Vui lòng nhìn thẳng")

    def _finish_enrollment(self):
        if self._btn_enroll.text() == "🎯  THỬ LẠI":
            valid_count = len(self._captured_frames)
            self._progress_bar.setValue(valid_count)
            self._lbl_count.setText(f"{valid_count} / 15")
            for idx, dot in enumerate(self._dots):
                if idx < valid_count:
                    dot.setText("●")
                    dot.setStyleSheet(f"color: {Colors.GREEN}; font-size: 18px;")
                else:
                    dot.setText("○")
                    dot.setStyleSheet(f"color: {Colors.BORDER_LT}; font-size: 18px;")
            
            if not self._camera_active:
                self._open_camera()
            
            self._btn_capture.setEnabled(True)
            self._btn_capture.setText("📸  BẮT ĐẦU CHỤP")
            self._btn_enroll.setEnabled(False)
            self._btn_enroll.setText("🎯  HOÀN TẤT ĐĂNG KÝ")
            self._lbl_guide.setText(f"💡 Đã giữ {valid_count} ảnh đạt chuẩn. Nhấn 'Bắt đầu chụp' để chụp bù {15 - valid_count} ảnh còn lại.")
            self._result_card.hide()
            return

        if not self._captured_frames:
            QMessageBox.warning(self, "Chưa có ảnh", "Vui lòng chụp ảnh trước!")
            return
        if not self._current_student_id: return
        if self._enroll_worker and self._enroll_worker.isRunning(): return

        self._btn_enroll.setEnabled(False)
        self._btn_enroll.setText("⏳  Đang xử lý...")
        self._result_card.hide()

        self._enroll_worker = EnrollWorker(
            frames=self._captured_frames,
            student_id=self._current_student_id
        )
        self._captured_frames = [] # Giải phóng reference ở UI ngay khi đẩy vào worker
        self._enroll_worker.done.connect(self._on_enroll_done)
        self._enroll_worker.start()

    def _on_enroll_done(self, result):
        self._result_card.show()
        if result.success:
            self._lbl_result.setText(
                f"🎉 ĐĂNG KÝ THÀNH CÔNG!\n"
                f"Học viên: {result.full_name}\n"
                f"Ảnh mẫu: {result.photos_valid}/{result.photos_taken}"
            )
            self._lbl_result.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {Colors.GREEN};")
            self._result_card.setStyleSheet(f"background: {Colors.GREEN}20; border : none {Colors.GREEN}44; border-radius: 12px;")
            self._btn_enroll.setText("✅  ĐÃ HOÀN TẤT")
            # [FIX] Reload cache cục bộ + Thông báo API Server để Mini PC biết có embedding mới
            self._reload_and_notify()
        else:
            self._lbl_result.setText(f"❌ {result.error_msg}")
            self._lbl_result.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {Colors.RED};")
            self._result_card.setStyleSheet(f"background: {Colors.RED}20; border : none {Colors.RED}44; border-radius: 12px;")
            self._btn_enroll.setText("🎯  THỬ LẠI")
            self._btn_enroll.setEnabled(True)
            self._captured_frames = getattr(result, "valid_frames", [])

    def _reload_and_notify(self):
        """
        [FIX] Bước khóa: Reload cache cục bộ (xài trên Server)
        rồi gọi HTTP /api/reload-cache để API Server tăng embedding_version.
        Mini PC polling endpoint này mỗi 10 giây → tự pull lại embeddings khi thấy version thay đổi.
        """
        import threading
        # 1. Reload RAM cache tren process Server
        try:
            from services.embedding_cache_manager import cache_manager
            cache_manager.load()
            logger.info(f"✅ Reload cache cục bộ: {cache_manager.size} học viên")
        except Exception as e:
            logger.error(f"Error reloading local cache: {e}")

        # 2. Gọi HTTP POST để api_server tăng embedding_version
        def _call_reload_api():
            try:
                import requests
                resp = requests.post(
                    "http://127.0.0.1:9696/api/reload-cache",
                    headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"},
                    timeout=3
                )
                if resp.ok:
                    data = resp.json()
                    logger.success(f"📢 API Server embedding_version = {data.get('embedding_version')} — Mini PC sẽ tự động cập nhật")
                else:
                    logger.warning(f"API /reload-cache trả về {resp.status_code}")
            except Exception as ex:
                logger.error(f"Có thể api_server chưa khởi động: {ex}")
        threading.Thread(target=_call_reload_api, daemon=True).start()

    def _on_frame(self, frame: np.ndarray):
        self._camera_view.update_frame(frame)
        # Hạn chế rò rỉ RAM bằng cách dọn rác định kỳ (mỗi 100 frame)
        if not hasattr(self, "_frame_counter"): self._frame_counter = 0
        self._frame_counter += 1
        if self._frame_counter % 100 == 0:
            import gc
            gc.collect()

    def _on_photo_taken(self, current: int, total: int):
        self._progress_bar.setValue(current)
        self._lbl_count.setText(f"{current} / {total}")
        if current <= len(self._dots):
            self._dots[current - 1].setText("●")
            self._dots[current - 1].setStyleSheet(f"color: {Colors.CYAN}; font-size: 18px;")

    def _on_capture_done(self, frames: list):
        self._captured_frames = frames
        self._btn_capture.setText("✅  Chụp xong")
        self._btn_capture.setEnabled(False)
        self._btn_enroll.setEnabled(True)
        self._lbl_guide.setText("✅ Chụp xong. Nhấn 'Hoàn Tất Đăng Ký' để lưu dữ liệu")
        for dot in self._dots:
            dot.setText("●")
            dot.setStyleSheet(f"color: {Colors.GREEN}; font-size: 18px;")

    def _on_face_detected(self, detected: bool):
        if detected:
            self._lbl_face_status.setText("⬤ FACE OK")
            self._lbl_face_status.setStyleSheet(f"color: {Colors.GREEN}; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            self._camera_view.set_status("Khuôn mặt hơp lệ", Colors.GREEN)
        else:
            self._lbl_face_status.setText("⬤ NO FACE")
            self._lbl_face_status.setStyleSheet(f"color: {Colors.TEXT_DARK}; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            self._camera_view.set_status("Đưa mặt vào khung", Colors.ORANGE)

    def _set_create_status(self, msg: str, color: str):
        self._lbl_create_status.setText(msg)
        self._lbl_create_status.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {color};")

    def _lock_form(self):
        # Khóa tất cả trừ Camera để người dùng vẫn có thể đổi trạm chụp
        for w in [self._inp_code, self._inp_name, self._cmb_class, self._cmb_gender, self._inp_phone, self._inp_email]:
            w.setEnabled(False)
        self._cmb_camera.setEnabled(True)

    def _reset_form(self):
        self._close_camera()
        self._current_student_id = None
        self._captured_frames    = []
        self._mode               = "create"

        for w in [self._inp_code, self._inp_name, self._cmb_class, self._cmb_gender, self._inp_phone, self._inp_email, self._cmb_camera, self._cmb_room, self._cmb_building, self._cmb_floor]:
            w.setEnabled(True)
            if isinstance(w, QLineEdit): w.clear()
            
        # Reset custom styling set by reverse-lookup lock
        self._cmb_building.setStyleSheet(combo_style())
        self._cmb_floor.setStyleSheet(combo_style())

        self._cmb_class.setCurrentIndex(0)
        self._cmb_gender.setCurrentIndex(0)
        self._cmb_room.setCurrentIndex(0)
        self._cmb_floor.setCurrentIndex(0)
        self._cmb_building.setCurrentIndex(0)
        
        self._title_lbl.setText("Đăng Ký Học Viên")
        self._subtitle_lbl.setText("Hệ thống nhận diện khuôn mặt — Chụp 10 ảnh mẫu")
        self._btn_create.setText("✅  XÁC NHẬN THÔNG TIN")
        self._btn_create.setEnabled(True)
        self._btn_create.setStyleSheet(self._btn_create.styleSheet()) # Giữ nguyên style cũ
        try: self._btn_create.clicked.disconnect()
        except Exception: pass
        self._btn_create.clicked.connect(self._on_create_student)

        self._btn_camera.setEnabled(False)
        self._btn_capture.setEnabled(False)
        self._btn_capture.setText("📸  BẮT ĐẦU CHỤP")
        self._btn_enroll.setEnabled(False)
        self._btn_enroll.setText("🎯  HOÀN TẤT ĐĂNG KÝ")
        self._progress_bar.setValue(0)
        self._result_card.hide()
        self._lbl_create_status.setText("")
        self._load_classes()
        self._load_buildings()
        self._load_cameras()
        self._lbl_guide.setText("💡 Vui lòng nhập thông tin học viên trước")
        for dot in self._dots:
            dot.setText("○")
            dot.setStyleSheet(f"color: {Colors.BORDER_LT}; font-size: 18px;")

    def closeEvent(self, event):
        self._close_camera()
        super().closeEvent(event)

    def hideEvent(self, event):
        if self._camera_active: self._close_camera()
        super().hideEvent(event)