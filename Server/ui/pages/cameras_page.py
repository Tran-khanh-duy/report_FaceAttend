"""
ui/pages/cameras_page.py
Trang quản lý danh sách Camera — CRUD.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFrame, QFormLayout, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from loguru import logger
from pathlib import Path
import sys

# Ensure import path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.styles.theme import Colors, card_style
from database.repositories import camera_repo

class CamerasPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_camera_id = None
        self._setup_ui()
        self.refresh_data()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(20)

        # --- Header ---
        header = QVBoxLayout()
        title = QLabel("Quản Lý Camera")
        title.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {Colors.TEXT};")
        subtitle = QLabel("Thêm, sửa hoặc xóa các thiết bị Camera trong hệ thống")
        subtitle.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_DIM};")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        # --- Main Content (Splitter-like layout) ---
        content = QHBoxLayout()
        content.setSpacing(25)

        # 1. Left: List Table
        list_container = QFrame()
        list_container.setStyleSheet(card_style(Colors.BORDER, radius=12))
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(15, 15, 15, 15)

        list_title = QLabel("DANH SÁCH CAMERA")
        list_title.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {Colors.CYAN}; border: none;")
        list_layout.addWidget(list_title)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["ID", "Tên Camera", "Nguồn / URL", "Vị trí", "Trạng thái"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{ border: none; background: transparent; gridline-color: {Colors.BORDER_LT}; }}
            QHeaderView::section {{ background-color: {Colors.BG_PANEL}; border: none; padding: 10px; font-weight: 700; color: {Colors.TEXT}; }}
        """)
        self._table.itemClicked.connect(self._on_row_clicked)
        list_layout.addWidget(self._table)
        
        content.addWidget(list_container, 3)

        # 2. Right: Form
        form_container = QFrame()
        form_container.setFixedWidth(350)
        form_container.setStyleSheet(card_style(Colors.BORDER, radius=12))
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(20, 20, 20, 20)

        form_title = QLabel("CHI TIẾT CAMERA")
        form_title.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {Colors.CYAN}; border: none;")
        form_layout.addWidget(form_title)

        form = QFormLayout()
        form.setSpacing(15)
        form.setContentsMargins(0, 10, 0, 10)

        self._inp_name = QLineEdit()
        self._inp_name.setPlaceholderText("Ví dụ: Camera Cổng Chính")
        self._inp_source = QLineEdit()
        self._inp_source.setPlaceholderText("0 hoặc rtsp://...")
        self._inp_location = QLineEdit()
        self._inp_location.setPlaceholderText("Ví dụ: Tầng 1, Khu A")
        self._inp_res = QLineEdit("1280x720")

        form.addRow("Tên hiển thị:", self._inp_name)
        form.addRow("Nguồn (Source):", self._inp_source)
        form.addRow("Vị trí mô tả:", self._inp_location)
        form.addRow("Độ phân giải:", self._inp_res)
        form_layout.addLayout(form)

        # Buttons
        btn_box = QVBoxLayout()
        btn_box.setSpacing(10)
        
        self._btn_save = QPushButton("💾  Lưu / Cập nhật")
        self._btn_save.setFixedHeight(40)
        self._btn_save.setStyleSheet(f"background: {Colors.CYAN}; color: white; border-radius: 8px; font-weight: 700;")
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._save_camera)
        
        self._btn_new = QPushButton("🆕  Thêm mới (Reset)")
        self._btn_new.setFixedHeight(40)
        self._btn_new.setStyleSheet(f"background: {Colors.BG_PANEL}; border: 1px solid {Colors.BORDER}; border-radius: 8px; font-weight: 600;")
        self._btn_new.clicked.connect(self._reset_form)
        
        self._btn_delete = QPushButton("🗑️  Xóa Camera")
        self._btn_delete.setFixedHeight(40)
        self._btn_delete.setStyleSheet(f"background: {Colors.RED}; color: white; border-radius: 8px; font-weight: bold;")
        self._btn_delete.clicked.connect(self._delete_camera)
        
        self._btn_retry = QPushButton("🔌 Thử kết nối lại (Retry)")
        self._btn_retry.setFixedHeight(40)
        self._btn_retry.setStyleSheet(f"background: #FFEDD5; color: {Colors.ORANGE}; border-radius: 8px; font-weight: 700;")
        self._btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_retry.clicked.connect(self._retry_camera)
        
        btn_box.addWidget(self._btn_save)
        btn_box.addWidget(self._btn_new)
        btn_box.addSpacing(10)
        btn_box.addWidget(self._btn_retry)
        btn_box.addWidget(self._btn_delete)
        form_layout.addLayout(btn_box)
        form_layout.addStretch()

        content.addWidget(form_container, 2)
        root.addLayout(content)

    def refresh_data(self):
        try:
            import requests
            active_cams = set()
            try:
                resp = requests.get("http://127.0.0.1:9696/api/system/edge_status", timeout=1)
                if resp.status_code == 200:
                    for dev in resp.json().values():
                        cams = dev.get("camera_status", {})
                        for cid, cinfo in cams.items():
                            if isinstance(cinfo, dict) and cinfo.get("is_active"):
                                active_cams.add(str(cinfo.get("source", cid)))
                            elif cinfo is True:
                                active_cams.add(str(cid))
            except:
                pass

            cameras = camera_repo.get_all(active_only=False)
            self._table.setRowCount(len(cameras))
            for i, cam in enumerate(cameras):
                self._table.setItem(i, 0, QTableWidgetItem(str(cam.camera_id)))
                self._table.setItem(i, 1, QTableWidgetItem(cam.camera_name))
                source = cam.rtsp_url or "Webcam"
                self._table.setItem(i, 2, QTableWidgetItem(source))
                self._table.setItem(i, 3, QTableWidgetItem(cam.location_desc or "---"))
                
                is_active = str(source) in active_cams
                status_item = QTableWidgetItem("🟢 Online" if is_active else "🔴 Offline")
                status_item.setForeground(QColor(Colors.GREEN) if is_active else QColor(Colors.RED))
                self._table.setItem(i, 4, status_item)
                
            self._reset_form()
        except Exception as e:
            logger.error(f"Error loading cameras: {e}")

    def _on_row_clicked(self, item):
        row = item.row()
        self._current_camera_id = int(self._table.item(row, 0).text())
        
        cam = camera_repo.get_by_id(self._current_camera_id)
        if cam:
            self._inp_name.setText(cam.camera_name)
            self._inp_source.setText(cam.rtsp_url or "")
            self._inp_location.setText(cam.location_desc or "")
            self._inp_res.setText(cam.resolution or "1280x720")
            self._btn_delete.setEnabled(True)

    def _reset_form(self):
        self._current_camera_id = None
        self._inp_name.clear()
        self._inp_source.clear()
        self._inp_location.clear()
        self._inp_res.setText("1280x720")
        self._btn_delete.setEnabled(False)
        self._table.clearSelection()

    def _save_camera(self):
        name = self._inp_name.text().strip()
        source = self._inp_source.text().strip()
        if not name:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập tên camera")
            return

        try:
            if self._current_camera_id:
                # Update
                camera_repo.update(
                    self._current_camera_id,
                    camera_name=name,
                    rtsp_url=source,
                    location_desc=self._inp_location.text(),
                    resolution=self._inp_res.text()
                )
                logger.info(f"Updated camera {self._current_camera_id}")
            else:
                # Create
                camera_repo.create(
                    camera_name=name,
                    rtsp_url=source,
                    location_desc=self._inp_location.text(),
                    resolution=self._inp_res.text()
                )
                logger.info("Created new camera")
            
            self.refresh_data()
            QMessageBox.information(self, "Thành công", "Đã lưu thông tin camera")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu: {str(e)}")

    def _delete_camera(self):
        if not self._current_camera_id: return
        
        ans = QMessageBox.question(self, "Xác nhận", "Bạn có chắc chắn muốn xóa camera này?", 
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            try:
                camera_repo.delete(self._current_camera_id)
                self.refresh_data()
                QMessageBox.information(self, "Thành công", "Đã xóa camera")
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Không thể xóa: {str(e)}")

    def _retry_camera(self):
        source = self._inp_source.text().strip()
        if not source:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn hoặc nhập nguồn camera cần thử lại!")
            return
            
        try:
            import requests
            payload = {
                "command": "RETRY_CAMERA",
                "target_camera": source
            }
            resp = requests.post("http://127.0.0.1:9696/api/system/command", json=payload, headers={"X-DEVICE-TOKEN": "faceattend_secret_2026"}, timeout=2)
            if resp.status_code == 200:
                QMessageBox.information(self, "Thành công", f"Đã gửi lệnh thử lại kết nối tới camera: {source}\nVui lòng chờ Mini PC xử lý!")
            else:
                QMessageBox.warning(self, "Lỗi", f"Server trả về mã lỗi: {resp.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi kết nối", f"Không thể gửi lệnh: {str(e)}")
