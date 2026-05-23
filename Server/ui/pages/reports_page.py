"""
ui/pages/reports_page.py
Giao diện quản lý báo cáo và chi tiết buổi học
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem, 
    QHeaderView, QSplitter, QFrame, QMessageBox, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from loguru import logger

import sys
# Đảm bảo import được các module từ thư mục gốc
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.styles.theme import Colors, card_style


# ─────────────────────────────────────────────
#  Export Worker (Luồng xuất báo cáo ngầm)
# ─────────────────────────────────────────────
class ExportWorker(QThread):
    done = pyqtSignal(dict)
    progress = pyqtSignal(str)

    def __init__(self, session_id: int, fmt: str, parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self.fmt = fmt

    def run(self):
        try:
            self.progress.emit("Đang thu thập dữ liệu...")
            from services.report_service import generate_report
            
            self.progress.emit(f"Đang tạo file {self.fmt.upper()}...")
            result = generate_report(self.session_id, self.fmt)
            self.done.emit(result)
        except Exception as e:
            logger.error(f"Export thread error: {e}")
            self.done.emit({"success": False, "error": str(e)})


# ─────────────────────────────────────────────
#  StatCard cho phần chi tiết
# ─────────────────────────────────────────────
class MiniStat(QWidget):
    def __init__(self, label: str, value: str, color: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{
                background: {color}12;
                border: none;
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)

        self._val = QLabel(value)
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {color};"
            f" border: none; background: transparent;"
        )

        lbl = QLabel(label.upper())
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 0.5px;"
            f" color: {Colors.TEXT_DIM}; border: none; background: transparent;"
        )

        lay.addWidget(self._val)
        lay.addWidget(lbl)

    def set_value(self, v: str):
        self._val.setText(v)


# ─────────────────────────────────────────────
#  Main ReportsPage Class
# ─────────────────────────────────────────────
class ReportsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_sessions = []
        self._selected_session_id = None
        self._export_worker = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(15)

        # --- 1. Header Area ---
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Quản Lý Báo Cáo")
        title.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {Colors.TEXT};")
        subtitle = QLabel("Tra cứu lịch sử điểm danh và xuất tệp tin")
        subtitle.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_DIM};")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self._btn_refresh = QPushButton("🔄  Làm mới")
        self._btn_refresh.setMinimumWidth(130)
        self._btn_refresh.setFixedHeight(36)
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refresh.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.BG_CARD}; color: {Colors.TEXT};
                border: 1px solid {Colors.BORDER}; border-radius: 6px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {Colors.BG_HOVER}; }}
        """)
        self._btn_refresh.clicked.connect(self._load_sessions)
        header.addWidget(self._btn_refresh)
        root.addLayout(header)

        # --- 2. Filter Bar ---
        filter_box = QFrame()
        filter_box.setStyleSheet(
            f"background: {Colors.BG_PANEL}; border-radius: 10px; border: none;"
        )
        filter_lay = QHBoxLayout(filter_box)
        filter_lay.setContentsMargins(12, 6, 12, 6)
        
        self._inp_search = QLineEdit()
        self._inp_search.setPlaceholderText("🔍 Tìm kiếm môn học, mã lớp...")
        self._inp_search.setStyleSheet(self._input_style())
        self._inp_search.textChanged.connect(self._filter_sessions)

        self._cmb_class = QComboBox()
        self._cmb_class.setMinimumWidth(220)
        self._cmb_class.setStyleSheet(self._combo_style())
        self._cmb_class.currentIndexChanged.connect(self._filter_sessions)

        self._cmb_status = QComboBox()
        self._cmb_status.setMinimumWidth(180)
        self._cmb_status.addItems(["Tất cả trạng thái", "COMPLETED", "ACTIVE", "PENDING"])
        self._cmb_status.setStyleSheet(self._combo_style())
        self._cmb_status.currentIndexChanged.connect(self._filter_sessions)

        filter_lay.addWidget(self._inp_search, 1)
        filter_lay.addWidget(QLabel("Lớp:"))
        filter_lay.addWidget(self._cmb_class)
        filter_lay.addWidget(QLabel("Trạng thái:"))
        filter_lay.addWidget(self._cmb_status)
        root.addWidget(filter_box)

        # --- 3. Content Area (Splitter) ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStyleSheet("QSplitter::handle { background: transparent; width: 4px; }")

        # Left: Session List
        self.left_panel = self._build_left_panel()
        # Right: Detail View
        self.right_panel = self._build_right_panel()

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([450, 650])
        root.addWidget(self.splitter, 1)

        # Initial load
        QTimer.singleShot(100, self._load_sessions)

    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        
        self._table_sessions = QTableWidget()
        self._table_sessions.setColumnCount(4)
        self._table_sessions.setHorizontalHeaderLabels(["Ngày", "Lớp", "Môn học", "Trạng thái"])
        self._table_sessions.verticalHeader().setVisible(False)
        self._table_sessions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table_sessions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table_sessions.setShowGrid(False)
        self._table_sessions.setAlternatingRowColors(True)
        
        hh = self._table_sessions.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        self._table_sessions.setStyleSheet(self._table_style())
        self._table_sessions.itemSelectionChanged.connect(self._on_selection_changed)
        
        lay.addWidget(self._table_sessions)
        
        self._lbl_count = QLabel("Đang tải dữ liệu...")
        self._lbl_count.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 11px; margin-top: 5px;")
        lay.addWidget(self._lbl_count)
        
        return container

    def _build_right_panel(self) -> QWidget:
        self.right_container = QFrame()
        self.right_container.setStyleSheet(
            f"background: {Colors.BG_CARD}; border: none; border-radius: 12px;"
        )
        self.detail_lay = QVBoxLayout(self.right_container)
        self.detail_lay.setContentsMargins(20, 20, 20, 20)
        self.detail_lay.setSpacing(15)

        # Placeholder khi chưa chọn
        self.empty_view = QWidget()
        ev_lay = QVBoxLayout(self.empty_view)
        ev_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl = QLabel("📂")
        icon_lbl.setStyleSheet("font-size: 50px; margin-bottom: 10px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt_lbl = QLabel("Vui lòng chọn một buổi học\ntừ danh sách bên trái để xem chi tiết")
        txt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt_lbl.setStyleSheet(f"color: {Colors.TEXT_DIM}; font-size: 14px; font-weight: 500;")
        ev_lay.addWidget(icon_lbl)
        ev_lay.addWidget(txt_lbl)
        self.detail_lay.addWidget(self.empty_view, 1)

        # Detail Content (Ẩn ban đầu)
        self.content_view = QWidget()
        cv_lay = QVBoxLayout(self.content_view)
        cv_lay.setContentsMargins(0, 0, 0, 0)
        cv_lay.setSpacing(8)  

        # Info Header
        self._lbl_detail_title = QLabel("Tên môn học")
        self._lbl_detail_title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {Colors.CYAN};")
        self._lbl_detail_subtitle = QLabel("Thông tin chi tiết lớp học")
        self._lbl_detail_subtitle.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_DIM};")
        cv_lay.addWidget(self._lbl_detail_title)
        cv_lay.addWidget(self._lbl_detail_subtitle)

        # Stats Row
        stats_row = QHBoxLayout()
        self._stat_total = MiniStat("Sĩ số", "0", Colors.TEXT)
        self._stat_present = MiniStat("Có mặt", "0", Colors.GREEN)
        self._stat_absent = MiniStat("Vắng", "0", Colors.RED)
        self._stat_ratio = MiniStat("Tỉ lệ %", "0%", Colors.ORANGE)
        for s in [self._stat_total, self._stat_present, self._stat_absent, self._stat_ratio]:
            stats_row.addWidget(s)
        cv_lay.addLayout(stats_row)


        lbl_preview = QLabel("DANH SÁCH HỌC VIÊN")
        lbl_preview.setStyleSheet(
            f"font-size: 10px; font-weight: 700; letter-spacing: 1px;"
            f" color: {Colors.TEXT_DIM}; border: none; background: transparent;"
            f" padding: 4px 0px 2px 0px;"
        )
        cv_lay.addWidget(lbl_preview)
        self._table_preview = QTableWidget()
        self._table_preview.setColumnCount(4)
        self._table_preview.setHorizontalHeaderLabels(["Mã HV", "Họ Tên", "Trạng thái", "Thời gian"])
        self._table_preview.verticalHeader().setVisible(False)
        self._table_preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table_preview.setStyleSheet(self._table_style())
        self._table_preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        cv_lay.addWidget(self._table_preview, 1)

        # Export Buttons 
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.setContentsMargins(0, 4, 0, 0)
        self._btn_excel = self._create_action_btn("📊 Xuất Excel", Colors.GREEN)
        self._btn_pdf = self._create_action_btn("📄 Xuất PDF", Colors.RED)
        self._btn_excel.clicked.connect(lambda: self._export("excel"))
        self._btn_pdf.clicked.connect(lambda: self._export("pdf"))

        btn_row.addWidget(self._btn_excel)
        btn_row.addWidget(self._btn_pdf)
        btn_row.addStretch()

        self._btn_folder = QPushButton("📂 Mở thư mục")
        self._btn_folder.setFixedSize(120, 38)
        self._btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_folder.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Colors.TEXT_DIM};
                border: none; border-radius: 6px; font-size: 12px;
            }}
            QPushButton:hover {{
                background: {Colors.BG_HOVER}; color: {Colors.TEXT};
            }}
        """)
        self._btn_folder.clicked.connect(self._open_reports_folder)
        btn_row.addWidget(self._btn_folder)

        cv_lay.addLayout(btn_row)
        
        self.detail_lay.addWidget(self.content_view, 1)
        self.content_view.hide()
        
        return self.right_container

    # --- Logic Methods ---

    def _load_sessions(self):
        try:
            from database.repositories import session_repo, class_repo
            self._all_sessions = session_repo.get_all()
            
            # Cập nhật filter lớp
            classes = class_repo.get_all()
            self._cmb_class.blockSignals(True)
            self._cmb_class.clear()
            self._cmb_class.addItem("Tất cả lớp học", None)
            for c in classes:
                self._cmb_class.addItem(f"{c.class_code} - {c.class_name}", c.class_id)
            self._cmb_class.blockSignals(False)
            
            self._render_table(self._all_sessions)
            logger.info(f"Đã tải {len(self._all_sessions)} buổi học.")
        except Exception as e:
            logger.error(f"Lỗi tải danh sách buổi học: {e}")

    def _render_table(self, data):
        self._table_sessions.setRowCount(0)
        for i, s in enumerate(data):
            self._table_sessions.insertRow(i)
            self._table_sessions.setRowHeight(i, 40)
            
            date_str = s.session_date.strftime("%d/%m/%Y") if s.session_date else "N/A"
            c_code = getattr(s, "class_code", "N/A")
            if c_code == "GLOBAL": c_code = "Toàn trường"
            
            items = [
                QTableWidgetItem(date_str),
                QTableWidgetItem(c_code),
                QTableWidgetItem(s.subject_name),
                QTableWidgetItem(s.status)
            ]
            
            # Căn giữa cột 0, 1, 3
            for col in [0, 1, 3]:
                items[col].setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            # Màu sắc trạng thái
            if s.status == "COMPLETED": items[3].setForeground(QColor(Colors.GREEN))
            elif s.status == "ACTIVE": items[3].setForeground(QColor(Colors.CYAN))
            
            for col, item in enumerate(items):
                # Lưu ID vào data của cell đầu tiên
                if col == 0: item.setData(Qt.ItemDataRole.UserRole, s.session_id)
                self._table_sessions.setItem(i, col, item)
                
        self._lbl_count.setText(f"Hiển thị {len(data)} / {len(self._all_sessions)} buổi học")

    def _filter_sessions(self):
        query = self._inp_search.text().lower()
        class_id = self._cmb_class.currentData()
        status = self._cmb_status.currentText()
        
        filtered = []
        for s in self._all_sessions:
            if class_id and s.class_id != class_id: continue
            if status != "Tất cả trạng thái" and s.status != status: continue
            if query and query not in f"{s.subject_name} {getattr(s, 'class_code', '')}".lower(): continue
            filtered.append(s)
        self._render_table(filtered)

    def _on_selection_changed(self):
        selected = self._table_sessions.selectedItems()
        if not selected:
            self.content_view.hide()
            self.empty_view.show()
            return
            
        # Lấy session_id từ cột đầu tiên của dòng đang chọn
        row = self._table_sessions.currentRow()
        sid = self._table_sessions.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._selected_session_id = sid
        self._load_detail(sid)

    def _load_detail(self, sid):
        try:
            from services.report_service import load_report_data
            data = load_report_data(sid)
            if not data: return
            
            self.empty_view.hide()
            self.content_view.show()
            
            self._lbl_detail_title.setText(f"📚 {data.subject_name}")
            c_code = "Toàn trường" if data.class_code == "GLOBAL" else data.class_code
            self._lbl_detail_subtitle.setText(f"Lớp: {c_code} | Ngày: {data.session_date} | Thời gian: {data.start_time} - {data.end_time}")
            
            self._stat_total.set_value(str(data.total_students))
            self._stat_present.set_value(str(data.present_count))
            self._stat_absent.set_value(str(data.absent_count))
            self._stat_ratio.set_value(f"{data.attendance_rate:.1f}%")
            
            # Preview Table
            self._table_preview.setRowCount(0)
            for i, r in enumerate(data.records):
                self._table_preview.insertRow(i)
                self._table_preview.setRowHeight(i, 35)
                
                status_txt = "✓ Có mặt" if r['status'] == 'PRESENT' else "✗ Vắng"
                status_item = QTableWidgetItem(status_txt)
                status_item.setForeground(QColor(Colors.GREEN if r['status'] == 'PRESENT' else Colors.RED))
                
                self._table_preview.setItem(i, 0, QTableWidgetItem(r['student_code']))
                self._table_preview.setItem(i, 1, QTableWidgetItem(r['full_name']))
                self._table_preview.setItem(i, 2, status_item)
                self._table_preview.setItem(i, 3, QTableWidgetItem(r['check_in_time']))

        except Exception as e:
            logger.error(f"Lỗi tải chi tiết: {e}")

    def _export(self, fmt):
        if not self._selected_session_id: return
        
        self._export_worker = ExportWorker(self._selected_session_id, fmt)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()
        
        # Disable nút khi đang chạy
        self._btn_excel.setEnabled(False)
        self._btn_pdf.setEnabled(False)

    def _on_export_done(self, result):
        self._btn_excel.setEnabled(True)
        self._btn_pdf.setEnabled(True)
        
        if result.get("success"):
            file_path = result.get('excel') or result.get('pdf') or result.get('path')
            QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo tại:\n{file_path}")
        else:
            QMessageBox.warning(self, "Thất bại", f"Lỗi: {result.get('error')}")

    def _open_reports_folder(self):
        try:
            from config import app_config
            path = str(app_config.reports_dir)
            if not os.path.exists(path): os.makedirs(path)
            
            if sys.platform == "win32": os.startfile(path)
            else: subprocess.Popen(["xdg-open", path])
        except Exception as e:
            logger.error(f"Không thể mở thư mục: {e}")

    # --- Style Helpers ---

    def _create_action_btn(self, text, color):
        btn = QPushButton(text)
        btn.setFixedSize(140, 40)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color}; color: white; border-radius: 6px; font-weight: bold; font-size: 13px;
            }}
            QPushButton:hover {{ background: {color}dd; }}
            QPushButton:disabled {{ background: {Colors.BORDER}; }}
        """)
        return btn

    def _input_style(self):
        return f"background: {Colors.BG_INPUT}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 8px 12px;"

    def _combo_style(self):
        return f"background: {Colors.BG_INPUT}; color: {Colors.TEXT}; border: 1px solid {Colors.BORDER}; border-radius: 6px; padding: 5px;"

    def _table_style(self):
        return f"""
            QTableWidget {{
                background: {Colors.BG_PANEL}; border: none; gridline-color: transparent; color: {Colors.TEXT};
            }}
            QTableWidget::item {{ padding: 5px; border-bottom: 1px solid {Colors.BORDER}40; }}
            QTableWidget::item:selected {{ background: {Colors.BG_SELECTED}; color: {Colors.CYAN}; font-weight: bold; }}
            QHeaderView::section {{
                background: {Colors.BG_CARD}; color: {Colors.TEXT_DIM}; font-weight: bold; font-size: 11px;
                border: none; border-bottom: 2px solid {Colors.BORDER}; padding: 10px;
            }}
        """

    def showEvent(self, event):
        """Mỗi khi màn hình hiển thị, tự động làm mới dữ liệu"""
        self._load_sessions()
        super().showEvent(event)