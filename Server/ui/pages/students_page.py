"""
ui/pages/students_page.py
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QMessageBox, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ui.styles.theme import Colors, card_style, badge_style, combo_style, input_style


class StudentsPage(QWidget):
    # Signal chuyển sang tab Đăng ký — mang student_id (int) hoặc -1 nếu đăng ký mới
    go_to_enroll = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_students = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(20)

        # ── Header ──
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        
        title = QLabel("DANH SÁCH HỌC VIÊN")
        title.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {Colors.TEXT};")
        
        self._subtitle = QLabel("Đang tải dữ liệu học viên...")
        self._subtitle.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_DIM};")
        
        title_col.addWidget(title)
        title_col.addWidget(self._subtitle)
        header.addLayout(title_col)
        header.addStretch()

        # ── Action Buttons ──
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)

        btn_add = QPushButton("➕ THÊM HỌC VIÊN MỚI")
        btn_add.setFixedHeight(42)
        btn_add.setMinimumWidth(180)
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.CYAN}; color: white;
                border: none; border-radius: 6px;
                font-weight: bold; font-size: 13px; letter-spacing: 0.5px;
                padding: 0px 16px;
            }}
            QPushButton:hover {{ background-color: {Colors.CYAN_DIM}; }}
        """)
        btn_add.clicked.connect(lambda: self.go_to_enroll.emit(-1))
        action_layout.addWidget(btn_add)

        btn_refresh = QPushButton("↻ LÀM MỚI") 
        btn_refresh.setFixedHeight(42)
        btn_refresh.setMinimumWidth(120)
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setToolTip("Làm mới danh sách dữ liệu")
        btn_refresh.setStyleSheet(f"""
            QPushButton {{
                background-color: white; color: {Colors.TEXT_DARK};
                border: 1px solid {Colors.BORDER_LT}; border-radius: 6px;
                font-size: 13px; font-weight: bold; letter-spacing: 0.5px;
                padding: 0px 16px;
            }}
            QPushButton:hover {{ 
                background-color: {Colors.BG_HOVER}; 
                border-color: {Colors.CYAN}; 
                color: {Colors.CYAN};
            }}
        """)
        btn_refresh.clicked.connect(self.load_students)
        action_layout.addWidget(btn_refresh)
        
        header.addLayout(action_layout)
        
        layout.addLayout(header)

        # ── Search + Filter Bar ──
        search_card = QFrame()
        # Không dùng card_style (thêm border) — chỉ dùng nền + bo góc
        search_card.setStyleSheet(
            f"background: {Colors.BG_CARD}; border: none; border-radius: 10px;"
        )
        search_lay = QHBoxLayout(search_card)
        search_lay.setContentsMargins(15, 8, 15, 8)
        search_lay.setSpacing(15)

        self._inp_search = QLineEdit()
        self._inp_search.setPlaceholderText("🔍  Tìm kiếm mã học viên, tên hoặc lớp...")
        self._inp_search.setStyleSheet(input_style() + "QLineEdit { font-size: 14px; min-height: 42px; }")
        self._inp_search.textChanged.connect(self._filter_table)
        search_lay.addWidget(self._inp_search, 3)

        self._cmb_filter = QComboBox()
        self._cmb_filter.addItems(["💎 Tất cả học viên", "✅ Đã đăng ký khuôn mặt", "⚠️ Chưa đăng ký mặt"])
        self._cmb_filter.setStyleSheet(combo_style() + "QComboBox { font-size: 14px; min-height: 42px; }")
        self._cmb_filter.currentIndexChanged.connect(self._filter_table)
        search_lay.addWidget(self._cmb_filter, 1)
        
        layout.addWidget(search_card)

        # ── Bảng dữ liệu ──
        # Tạo QTableWidget và cấu hình cột
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "MÃ HV", "HỌ VÀ TÊN", "LỚP HỌC", "GIỚI TÍNH", "PHÒNG Ở",
            "TRẠNG THÁI", "NGÀY TẠO", "THAO TÁC"
        ])
        # Thêm trực tiếp bảng vào layout chính
        layout.addWidget(self._table, 1)
        
        # Thanh cuộn ngang (Bắt buộc để không bị ép giao diện)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)

        # Cho phép bảng mở rộng
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setMinimumWidth(800) 

        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Colors.BG_PANEL};
                border: none;
                border-radius: 10px; color: {Colors.TEXT}; font-size: 14px;
            }}
            QTableWidget::item {{ padding: 0px 15px; border-bottom: 1px solid {Colors.BG_DARK}; }}
            QTableWidget::item:selected {{ background-color: {Colors.BG_SELECTED}; color: {Colors.CYAN}; font-weight: 600; }}
            QHeaderView::section {{
                background-color: {Colors.BG_CARD}; color: {Colors.TEXT_DIM};
                font-weight: 800; font-size: 11px; padding: 12px 8px; border: none;
                border-bottom: 2px solid {Colors.BORDER_LT}; text-transform: uppercase;
            }}
        """)
        self._table.itemDoubleClicked.connect(self._on_item_double_clicked)


        # ── Footer ──
        footer_lay = QHBoxLayout()
        self._lbl_footer = QLabel("Hiển thị 0 học viên")
        self._lbl_footer.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {Colors.TEXT_DIM};")
        footer_lay.addWidget(self._lbl_footer)
        footer_lay.addStretch()
        
        lbl_hint = QLabel("💡 Mẹo: Nhấn đúp vào hàng để xem chi tiết")
        lbl_hint.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_DARK}; font-style: italic; font-family: 'Segoe UI';")
        footer_lay.addWidget(lbl_hint)
        
        layout.addLayout(footer_lay)

        QTimer.singleShot(100, self.load_students)

    def load_students(self):
        """Tải dữ liệu từ Repository."""
        try:
            from database.repositories import student_repo
            self._all_students = student_repo.get_all()
            self._render_table(self._all_students)

            total = len(self._all_students)
            enrolled = sum(1 for s in self._all_students if s.face_enrolled)
            self._subtitle.setText(
                f"Tổng cộng: {total} học viên  |  Đã đăng ký: {enrolled}  |  Chưa đăng ký: {total-enrolled}"
            )
        except Exception as e:
            logger.error(f"Lỗi tải danh sách học viên: {e}")
            self._subtitle.setText(f"❌ Không thể kết nối cơ sở dữ liệu")

    def _filter_table(self):
        """Bộ lọc tìm kiếm và trạng thái."""
        keyword = self._inp_search.text().strip().lower()
        filter_idx = self._cmb_filter.currentIndex()

        filtered = []
        for s in self._all_students:
            # 1. Lọc theo trạng thái mặt
            if filter_idx == 1 and not s.face_enrolled: continue
            if filter_idx == 2 and s.face_enrolled: continue
            
            # 2. Lọc theo từ khóa
            if keyword:
                search_pool = f"{s.student_code} {s.full_name} {s.class_name or ''}".lower()
                if keyword not in search_pool: continue
            
            filtered.append(s)

        self._render_table(filtered)

    def _render_table(self, students: list):
        self._table.setRowCount(0)
        self._displayed_students = students
        self._lbl_footer.setText(f"Hiển thị {len(students)} / {len(self._all_students)} học viên")

        for row, s in enumerate(students):
            self._table.insertRow(row)
            self._table.setRowHeight(row, 55)

            # Mã HV 
            item_code = QTableWidgetItem(s.student_code)
            item_code.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_code.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            self._table.setItem(row, 0, item_code)

            # Họ Tên
            self._table.setItem(row, 1, QTableWidgetItem(s.full_name))

            # Lớp
            item_class = QTableWidgetItem(s.class_name or "—")
            item_class.setForeground(QColor(Colors.TEXT_DIM if not s.class_name else Colors.TEXT))
            item_class.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, item_class)

            # Giới tính
            item_gender = QTableWidgetItem(s.gender or "—")
            item_gender.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, item_gender)

            # Phòng ở
            room_val = getattr(s, 'room', getattr(s, 'room_name', "—"))
            item_room = QTableWidgetItem(str(room_val) if room_val else "—")
            item_room.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 4, item_room)

            # ── TRẠNG THÁI KHUÔN MẶT ──
            status_widget = QWidget()
            status_widget.setStyleSheet("background: transparent;")
            status_lay = QHBoxLayout(status_widget)
            status_lay.setContentsMargins(0, 0, 0, 0)
            status_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            enrolled = s.face_enrolled
            badge = QLabel("ĐÃ ĐĂNG KÝ" if enrolled else "CHƯA CÓ MẶT")
            badge.setFixedSize(125, 28) # Mở rộng bề ngang cho chữ thoải mái
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            if enrolled:
                badge.setStyleSheet("background-color: #E8F5E9; color: #2E7D32; border-radius: 4px; font-weight: bold; font-size: 11px;")
            else:
                badge.setStyleSheet("background-color: #FFF3E0; color: #E65100; border-radius: 4px; font-weight: bold; font-size: 11px;")
            
            status_lay.addWidget(badge)
            self._table.setCellWidget(row, 5, status_widget)

            # Ngày tạo
            date_val = s.created_at.strftime("%d/%m/%Y") if hasattr(s, 'created_at') and s.created_at else "—"
            item_date = QTableWidgetItem(date_val)
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_date.setForeground(QColor(Colors.TEXT_DIM))
            self._table.setItem(row, 6, item_date)

            # ── NÚT THAO TÁC ──
            action_widget = QWidget()
            action_widget.setStyleSheet("background: transparent;")
            action_lay = QHBoxLayout(action_widget)
            action_lay.setContentsMargins(0, 0, 0, 8) # Thêm lề dưới 8px để đẩy nút lên trên
            action_lay.setSpacing(10)
            action_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn_edit = QPushButton("CẬP NHẬT" if enrolled else "ĐĂNG KÝ")
            btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_edit.setFixedSize(100, 32) # Độ cao 32px để cân đối
            btn_edit.setStyleSheet(f"""
                QPushButton {{
                    background: {Colors.CYAN if not enrolled else "transparent"};
                    color: {"white" if not enrolled else Colors.CYAN};
                    border: 1px solid {Colors.CYAN}; border-radius: 6px;
                    font-size: 11px; font-weight: 800;
                    padding: 0px; margin: 0px;
                }}
                QPushButton:hover {{ background: {Colors.CYAN}; color: white; }}
            """)
            btn_edit.clicked.connect(lambda _, sid=s.student_id: self.go_to_enroll.emit(sid))
            
            btn_del = QPushButton("🗑️")
            btn_del.setToolTip("Xóa học viên")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setFixedSize(32, 32)
            btn_del.setStyleSheet(f"""
                QPushButton {{
                    background: #FFF5F5; color: {Colors.RED};
                    border: none; border-radius: 6px; font-size: 15px;
                    padding: 0px; margin: 0px;
                }}
                QPushButton:hover {{ background: #FFEBEE; }}
            """)
            btn_del.clicked.connect(lambda _, sid=s.student_id, name=s.full_name: self._on_delete_student(sid, name))

            action_lay.addWidget(btn_edit)
            action_lay.addWidget(btn_del)
            self._table.setCellWidget(row, 7, action_widget)


        # ── Cấu hình Header và Độ rộng (Thiết lập sau khi đổ dữ liệu để đảm bảo hiệu lực) ──
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(80)
        
        # Độ rộng mong muốn cho từng cột
        widths = [100, 250, 120, 100, 120, 180, 140, 220]
        for i, w in enumerate(widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(i, w)

    def _on_item_double_clicked(self, item):
        """Xử lý double click vào hàng để mở trang đăng ký/cập nhật."""
        row = item.row()
        if hasattr(self, '_displayed_students') and row < len(self._displayed_students):
            student = self._displayed_students[row]
            self.go_to_enroll.emit(student.student_id)

    def _on_delete_student(self, student_id: int, name: str):
        """Xử lý xóa học viên — cập nhật toàn hệ thống (DB → FAISS → Redis → MiniPC webhook)."""
        confirm = QMessageBox.question(
            self, "Xác nhận xóa",
            f"Bạn có chắc chắn muốn xóa học viên <b>{name}</b>?<br>"
            "Dữ liệu khuôn mặt và lịch sử điểm danh liên quan cũng sẽ bị xóa.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            from database.repositories import student_repo
            from services.embedding_cache_manager import cache_manager

            # ── Bước 1: Xóa DB (hocvien + FaceEmbeddings + AttendanceRecords) ──
            if not student_repo.delete(student_id):
                QMessageBox.warning(self, "Lỗi", "Không thể xóa học viên này!")
                return

            logger.success(f"[DELETE] Đã xóa học viên ID={student_id} ({name}) khỏi DB")

            # ── Bước 2: Xóa khỏi RAM cache (EmbeddingCacheManager - Server local) ──
            cache_manager.remove_student_from_cache(student_id)

            # ── Bước 3: Rebuild FAISS index trên Server ──
            # Không rebuild → Server vẫn nhận diện người đã xóa (embedding vẫn trong FAISS)
            import threading
            def _rebuild_and_notify():
                try:
                    from services.embedding_service import embedding_service
                    embedding_service.reload()
                    logger.info(
                        f"[DELETE] Rebuild FAISS xong: {embedding_service.size} học viên | "
                        f"version={embedding_service.version}"
                    )
                except Exception as e:
                    logger.error(f"[DELETE] Rebuild FAISS lỗi: {e}")

                # ── Bước 4: Invalidate Redis filter cache ──
                # Không invalidate → /api/attendance vẫn dùng danh sách cũ (60s TTL)
                try:
                    from api.attendance_routes import invalidate_student_filter_cache
                    invalidate_student_filter_cache()
                    logger.info("[DELETE] Đã xóa Redis filter cache (valid_ids + vip_ids)")
                except Exception as e:
                    logger.warning(f"[DELETE] Invalidate Redis cache lỗi: {e}")

                # ── Bước 5: Push webhook tới tất cả MiniPC ──
                # Không push → MiniPC vẫn nhận diện người đã xóa, vẫn hiện tên trên khung hình
                try:
                    from services.webhook_service import webhook_service
                    webhook_service.notify_all(event="CACHE_REFRESH")
                    logger.info("[DELETE] Đã push webhook CACHE_REFRESH tới tất cả MiniPC")
                except Exception as e:
                    logger.warning(f"[DELETE] Push webhook lỗi (MiniPC sẽ tự cập nhật trong ~10s): {e}")

            threading.Thread(target=_rebuild_and_notify, daemon=True, name="Delete-Sync").start()

            # Tải lại bảng UI ngay lập tức (không cần chờ thread trên)
            self.load_students()

        except Exception as e:
            logger.error(f"Delete student error: {e}")
            QMessageBox.critical(self, "Lỗi hệ thống", str(e))

    def showEvent(self, event):
        """Mỗi khi tab được hiển thị, tự động làm mới dữ liệu."""
        self.load_students()
        super().showEvent(event)