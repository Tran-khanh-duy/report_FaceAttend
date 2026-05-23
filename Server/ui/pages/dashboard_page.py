import subprocess
import platform
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QTime, QDate
from PyQt6.QtGui import QFont, QColor, QPainter, QPen

import sys
import requests
import psutil
from loguru import logger
from pathlib import Path

# Đảm bảo import đúng từ cấu trúc thư mục
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.styles.theme import Colors, create_shadow


def get_gpu_name():
    """Lấy tên GPU từ Windows bằng wmic"""
    try:
        output = subprocess.check_output("wmic path win32_VideoController get name", shell=True, text=True)
        lines = [line.strip() for line in output.split('\n') if line.strip() and "Name" not in line]
        if lines:
            return lines[0]
    except Exception:
        pass
    return platform.processor()


class DonutChart(QWidget):
    """Biểu đồ tỷ lệ điểm danh hình Donut - Dành cho Light Theme"""
    def __init__(self, color_str, bg_color_str, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 220)
        self.value = 0
        self.max_value = 0
        self.color = QColor(color_str)
        self.bg_color = QColor(bg_color_str)

    def set_value(self, val, max_val):
        self.value = int(val)
        self.max_value = int(max_val)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRectF(30, 30, 160, 160)
        
        # Nền vòng tròn (Thường dành cho vắng mặt)
        pen_bg = QPen(self.bg_color)
        pen_bg.setWidth(16)
        pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_bg)
        painter.drawArc(rect, 0, 360 * 16)
        
        # Vòng tròn giá trị (Có mặt)
        if self.max_value > 0:
            pen_val = QPen(self.color)
            pen_val.setWidth(16)
            pen_val.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_val)
            
            span_angle = int((self.value / self.max_value) * 360 * 16)
            painter.drawArc(rect, 90 * 16, -span_angle)
        
        # Text ở giữa biểu đồ
        painter.setPen(QColor(Colors.TEXT_PRI))
        font_val = QFont("Segoe UI", 32, QFont.Weight.Bold)
        painter.setFont(font_val)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.max_value}")
        
        font_lbl = QFont("Segoe UI", 11)
        painter.setFont(font_lbl)
        rect_lbl = QRectF(30, 120, 160, 40)
        painter.setPen(QColor(Colors.TEXT_SEC))
        painter.drawText(rect_lbl, Qt.AlignmentFlag.AlignCenter, "Tổng cộng")


class DashboardPage(QWidget):
    go_to_live_view = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gpu_name = get_gpu_name()
        
        # Đặt nền tảng tổng thể là Very Light Slate Gray
        self.setStyleSheet(f"background-color: {Colors.BG_APP};")
        
        self._students_total = 0
        self._present_count = 0
        self._absent_count = 0

        self._setup_ui()

        # ── TASK 1: QTimer tự động quét dữ liệu thời gian thực (3 giây / lần) ──
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_dashboard_data)
        self._refresh_timer.start(3000)

        # Timer cho đồng hồ thời gian thực
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        # Kéo dữ liệu ban đầu ngay khi khởi tạo
        self.refresh_dashboard_data()

    def _setup_ui(self):
        # Master Layout: QVBoxLayout với Margins 20px, Spacing 16px
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # ==========================================
        # ROW 1: HEADER SECTION
        # ==========================================
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)
        
        # Cụm Tiêu đề (Left)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        lbl_title = QLabel("DASHBOARD")
        lbl_title.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {Colors.TEXT_PRI}; border: none;")
        lbl_subtitle = QLabel("Hệ thống điểm danh AI theo thời gian thực")
        lbl_subtitle.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_SEC}; border: none;")
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_subtitle)
        header_layout.addLayout(title_layout)
        
        header_layout.addStretch()

        # Cụm Thông tin (Right)
        self.lbl_clock = QLabel(QTime.currentTime().toString("HH:mm:ss"))
        self.lbl_clock.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {Colors.TEXT_PRI};")
        
        lbl_server_health = QLabel("🟢 Server Health: Healthy")
        lbl_server_health.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Colors.SUCCESS};")
        
        lbl_bell = QLabel("🔔")
        lbl_bell.setStyleSheet("font-size: 20px;")
        
        lbl_avatar = QLabel("👤 Admin User")
        lbl_avatar.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRI}; background-color: {Colors.BORDER}; padding: 8px 16px; border-radius: 18px;")

        header_layout.addWidget(lbl_server_health)
        header_layout.addSpacing(24)
        header_layout.addWidget(self.lbl_clock)
        header_layout.addSpacing(24)
        header_layout.addWidget(lbl_bell)
        header_layout.addSpacing(16)
        header_layout.addWidget(lbl_avatar)

        main_layout.addLayout(header_layout)

        # ==========================================
        # ROW 2: KPI CARDS (Lưới 2 Hàng 3 Cột)
        # ==========================================
        kpi_layout = QGridLayout()
        kpi_layout.setSpacing(16)
        
        # Thẻ 1
        self.lbl_total_present = self._create_kpi_card("Tổng điểm danh hôm nay", "0", Colors.TEXT_PRI, kpi_layout, 0, 0, trend="🟢 +12%")
        # Thẻ 2
        self.lbl_active_cameras = self._create_kpi_card("Số camera hoạt động", "0", Colors.TEXT_PRI, kpi_layout, 0, 1)
        # Thẻ 3
        self.lbl_accuracy = self._create_kpi_card("Độ chính xác nhận diện", "99.1%", Colors.TEXT_PRI, kpi_layout, 0, 2)
        # Thẻ 4
        self.lbl_sys_status = self._create_kpi_card("Trạng thái hệ thống", "ỔN ĐỊNH", Colors.SUCCESS, kpi_layout, 1, 0)
        # Thẻ 5
        self.lbl_queue = self._create_kpi_card("Queue processing", "0 ms", Colors.TEXT_PRI, kpi_layout, 1, 1)
        # Thẻ 6
        self.lbl_spoof = self._create_kpi_card("Cảnh báo spoof detection", "0", Colors.DANGER, kpi_layout, 1, 2, trend="🔴 Nguy cơ")

        main_layout.addLayout(kpi_layout)

        # ==========================================
        # ROW 3: CORE ANALYTICS (Stretch 3:7)
        # ==========================================
        core_layout = QHBoxLayout()
        core_layout.setSpacing(16)

        # --- LEFT (Stretch 3): TỶ LỆ ĐIỂM DANH ---
        chart_frame = QFrame()
        chart_frame.setGraphicsEffect(create_shadow())
        chart_layout = QVBoxLayout(chart_frame)
        chart_layout.setContentsMargins(20, 20, 20, 20)
        
        lbl_chart_title = QLabel("Tỷ lệ điểm danh")
        lbl_chart_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Colors.TEXT_PRI}; border: none;")
        chart_layout.addWidget(lbl_chart_title)
        
        # Donut Chart
        donut_container = QHBoxLayout()
        self.donut = DonutChart(Colors.SUCCESS, Colors.BORDER)
        donut_container.addStretch()
        donut_container.addWidget(self.donut)
        donut_container.addStretch()
        chart_layout.addLayout(donut_container)
        
        # Chú thích
        self.lbl_legend = QLabel("🟩 Có mặt: 0    ⬜ Vắng mặt: 0")
        self.lbl_legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_legend.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_SEC};")
        chart_layout.addWidget(self.lbl_legend)
        
        core_layout.addWidget(chart_frame, 3)

        # --- RIGHT (Stretch 7): LIVE ATTENDANCE LOG ---
        log_frame = QFrame()
        log_frame.setGraphicsEffect(create_shadow())
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(20, 20, 20, 20)
        
        lbl_log_title = QLabel("NỘI DUNG LOG ĐIỂM DANH TRỰC TIẾP")
        lbl_log_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Colors.TEXT_PRI}; border: none;")
        log_layout.addWidget(lbl_log_title)

        # Bảng Log
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Camera", "Mã SV", "Họ Tên", "Thời gian", "Trạng thái"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        
        # TASK 1: Thêm khoảng không gian thở dưới đáy bảng bằng QSS
        self.table.setStyleSheet("QTableWidget { padding-bottom: 15px; border: none; background: transparent; }")
        
        # (Dự phòng TASK 2): Nếu QSS không đủ mạnh, thêm lề cho Viewport
        self.table.viewport().setContentsMargins(0, 0, 0, 15)
        
        log_layout.addWidget(self.table)
        core_layout.addWidget(log_frame, 7)

        main_layout.addLayout(core_layout)

        # ==========================================
        # ROW 4: INFRASTRUCTURE MONITOR
        # ==========================================
        infra_layout = QVBoxLayout()
        infra_layout.setSpacing(10)
        
        # ROW 4A: Panels for Edge AI Devices & Camera
        infra_top = QHBoxLayout()
        infra_top.setSpacing(16)
        
        edge_frame = QFrame()
        edge_frame.setGraphicsEffect(create_shadow())
        edge_layout = QVBoxLayout(edge_frame)
        lbl_edge_title = QLabel("Trạng thái thiết bị (Edge AI Devices)")
        lbl_edge_title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRI};")
        edge_layout.addWidget(lbl_edge_title)
        edge_layout.addWidget(QLabel("Đang đồng bộ dữ liệu..."))
        infra_top.addWidget(edge_frame)
        
        cam_frame = QFrame()
        cam_frame.setGraphicsEffect(create_shadow())
        cam_layout = QVBoxLayout(cam_frame)
        lbl_cam_title = QLabel("Trạng thái Camera")
        lbl_cam_title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRI};")
        cam_layout.addWidget(lbl_cam_title)
        cam_layout.addWidget(QLabel("Đang truyền phát (Streaming)..."))
        infra_top.addWidget(cam_frame)
        
        infra_layout.addLayout(infra_top)

        # ROW 4B: Bottom Bar
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(f"background-color: {Colors.BG_CARD}; border-radius: 6px; border: none;")
        bottom_bar_layout = QHBoxLayout(bottom_bar)
        bottom_bar_layout.setContentsMargins(16, 6, 16, 6)
        
        self.lbl_redis = QLabel("Redis Latency: --")
        self.lbl_mysql = QLabel("MySQL Latency: --")
        self.lbl_worker = QLabel("Worker Status: Idle")
        self.lbl_api = QLabel("API Latency: --")
        self.lbl_queue_load = QLabel("Queue Load: 0")
        self.lbl_mem = QLabel("Memory Usage: --")
        
        for lbl in [self.lbl_redis, self.lbl_mysql, self.lbl_worker, self.lbl_api, self.lbl_queue_load, self.lbl_mem]:
            lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {Colors.TEXT_SEC};")
            bottom_bar_layout.addWidget(lbl)
            
        infra_layout.addWidget(bottom_bar)
        
        main_layout.addLayout(infra_layout)

    def _create_kpi_card(self, title, value, color, layout, row, col, trend=None):
        """Helper: Tạo thẻ KPI và nhét vào lưới (Grid)"""
        card = QFrame()
        card.setGraphicsEffect(create_shadow())
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(4)
        
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_SEC}; border: none;")
        
        val_layout = QHBoxLayout()
        lbl_val = QLabel(value)
        lbl_val.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {color}; border: none;")
        val_layout.addWidget(lbl_val)
        
        if trend:
            lbl_trend = QLabel(trend)
            lbl_trend.setStyleSheet("font-size: 12px; font-weight: bold; border: none;")
            val_layout.addWidget(lbl_trend)
            
        val_layout.addStretch()
        
        card_layout.addWidget(lbl_title)
        card_layout.addLayout(val_layout)
        
        layout.addWidget(card, row, col)
        return lbl_val

    def _update_clock(self):
        """Cập nhật đồng hồ thời gian thực"""
        self.lbl_clock.setText(QTime.currentTime().toString("HH:mm:ss"))

    def _create_avatar_label(self, name):
        """Tạo icon Avatar dạng hình tròn"""
        initials = "".join([word[0] for word in name.split()[:2]]).upper()
        lbl = QLabel(initials)
        lbl.setFixedSize(36, 36)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"""
            background-color: {Colors.BG_APP}; 
            color: {Colors.PRIMARY}; 
            border: 1px solid {Colors.BORDER_LT};
            border-radius: 18px; 
            font-weight: bold; 
            font-size: 14px;
        """)
        return lbl

    def _create_pill_badge(self, is_valid):
        """Tạo nhãn trạng thái Hợp lệ / Không hợp lệ dạng viên thuốc (Pill shape)"""
        lbl = QLabel("Hợp lệ" if is_valid else "Không hợp lệ")
        if is_valid:
            lbl.setStyleSheet(f"""
                background-color: #D1FAE5;
                color: #065F46;
                border-radius: 14px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            """)
        else:
            lbl.setStyleSheet(f"""
                background-color: #FEE2E2;
                color: #991B1B;
                border-radius: 14px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            """)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    # =========================================================================
    # PRESERVED METHODS (GIỮ NGUYÊN CHỮ KÝ HÀM ĐỂ KHÔNG BREAK APP)
    # Những phương thức này cần giữ nguyên để Backend gọi không bị lỗi
    # =========================================================================

    def add_attendance_log(self, name: str, student_id: str, time_str: str,
                           class_name: str = "", is_present: bool = True,
                           camera: str = "CAM"):
        """
        Thêm dữ liệu vào bảng Log điểm danh.
        Cột: [Camera | Mã SV | Họ Tên | Thời gian | Trạng thái]
        Giữ nguyên signature cũ (name, student_id, time_str, class_name, is_present)
        để không break các lời gọi từ Backend. 'camera' là tham số bổ sung tuỳ chọn.
        """
        self.table.insertRow(0)

        # Cột 0: Camera
        cam_item = QTableWidgetItem(f"  📸 {camera}")
        cam_item.setForeground(QColor(Colors.PRIMARY))
        self.table.setItem(0, 0, cam_item)

        # Cột 1: Mã SV
        self.table.setItem(0, 1, QTableWidgetItem(f"  {student_id}"))

        # Cột 2: Họ Tên
        self.table.setItem(0, 2, QTableWidgetItem(f"  {name}"))

        # Cột 3: Thời gian
        self.table.setItem(0, 3, QTableWidgetItem(f"  {time_str}"))

        # Cột 4: Trạng thái (Pill badge)
        badge_widget = QWidget()
        badge_layout = QHBoxLayout(badge_widget)
        badge_layout.setContentsMargins(4, 0, 4, 0)
        badge = self._create_pill_badge(is_present)
        badge_layout.addWidget(badge)
        badge_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setCellWidget(0, 4, badge_widget)

        self.table.setRowHeight(0, 50)

        # Giữ log tối đa 50 hàng
        if self.table.rowCount() > 50:
            self.table.removeRow(50)

    def update_stats(self, **kwargs):
        """Cập nhật các thống kê sĩ số"""
        if "students" in kwargs:
            self._students_total = kwargs["students"]
        if "present" in kwargs:
            self._present_count = kwargs["present"]
        if "absent" in kwargs:
            self._absent_count = kwargs["absent"]
            
        # Cập nhật biểu đồ Donut Chart (Cốt lõi)
        self.donut.set_value(self._present_count, self._students_total)
        self.lbl_legend.setText(f"🟩 Có mặt: {self._present_count}    ⬜ Vắng mặt: {self._absent_count}")
        
        # Cập nhật số trên KPI Cards
        self.lbl_total_present.setText(str(self._present_count))

    def update_latest_snapshot(self, frame_or_pixmap, student_name: str, student_id: str):
        """Giữ nguyên signature để tương thích với luồng quét mã cũ (dù thẻ UI đã bỏ)"""
        pass

    def update_system_status(self, key: str, ok: bool, text: str = "", custom_color: str = None):
        """Cập nhật trạng thái hệ thống từ các Worker ở Backend"""
        if key == "camera":
            status_text = text if text else ("Hoạt động" if ok else "Mất kết nối")
            self.lbl_active_cameras.setText(status_text)
            self.lbl_active_cameras.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {Colors.TEXT_PRI if ok else Colors.DANGER}; border: none;")
            
        elif key == "database":
            status_text = text if text else ("ỔN ĐỊNH" if ok else "LỖI KẾT NỐI")
            color = custom_color if custom_color else (Colors.SUCCESS if ok else Colors.DANGER)
            self.lbl_sys_status.setText(status_text)
            self.lbl_sys_status.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {color}; border: none;")

    # =========================================================================
    # REAL-TIME DATA BINDING
    # =========================================================================

    def refresh_dashboard_data(self):
        """
        TASK 2: Hàm tổng hợp cập nhật toàn bộ 11 thông số Dashboard thời gian thực.
        Gọi 2 API endpoints song song, bọ trong try...except an toàn.
        """
        HEADERS = {"X-DEVICE-TOKEN": "faceattend_secret_2026"}

        # ── Khối 1: /admin/system-status → Latency, Queue, Camera, Edge ──────
        try:
            resp_sys = requests.get(
                "http://127.0.0.1:9696/admin/system-status",
                headers=HEADERS, timeout=2
            )
            if resp_sys.status_code == 200:
                sys_data = resp_sys.json()

                # Redis Latency
                redis_ms = sys_data.get("latency_ms", {}).get("redis", -1)
                self.lbl_redis.setText(
                    f"Redis: {redis_ms:.1f} ms" if redis_ms >= 0 else "Redis: N/A"
                )

                # MySQL Latency
                db_ms = sys_data.get("latency_ms", {}).get("database", -1)
                self.lbl_mysql.setText(
                    f"MySQL: {db_ms:.1f} ms" if db_ms >= 0 else "MySQL: N/A"
                )

                # Queue size + Queue Load bar
                q_size = sys_data.get("queue_size", 0)
                self.lbl_queue_load.setText(f"Queue Load: {q_size}")
                self.lbl_queue.setText(f"{q_size}")

                # Số camera online / tổng
                cam_info = sys_data.get("cameras", {})
                online = cam_info.get("online", 0)
                total  = cam_info.get("total", 0)
                self.lbl_active_cameras.setText(f"{online}/{total}")

                # Trạng thái hệ thống tổng quát
                self.update_system_status("database", True, "ỔN ĐỊNH", Colors.SUCCESS)

                # RAM
                mem = psutil.virtual_memory()
                self.lbl_mem.setText(f"Memory: {mem.percent}%")

            else:
                self.update_system_status("database", False, f"LỖI {resp_sys.status_code}", Colors.DANGER)

        except Exception as e:
            logger.warning(f"[Dashboard] Không lấy được /admin/system-status: {e}")
            self.lbl_redis.setText("Redis: Mất kết nối")
            self.lbl_mysql.setText("MySQL: Mất kết nối")
            self.update_system_status("database", False, "MẤT KẾT NỐI", Colors.DANGER)

        # ── Khối 2: /api/dashboard/stats → Số HV, Camera, Log ─────────────
        try:
            date_str = QDate.currentDate().toString("yyyy-MM-dd")
            resp_stat = requests.get(
                f"http://127.0.0.1:9696/api/dashboard/stats?date={date_str}",
                headers=HEADERS, timeout=2
            )
            if resp_stat.status_code == 200:
                stat_data = resp_stat.json()

                # Cập nhật KPI Cards số học viên / điểm danh
                self.update_stats(
                    students=stat_data.get("student_count", 0),
                    present=stat_data.get("present_count", 0),
                    absent=stat_data.get("absent_count", 0)
                )

                # Cập nhật cảnh báo Spoof
                spoof_count = stat_data.get("spoof_warnings", 0)
                self.lbl_spoof.setText(str(spoof_count))
                self.lbl_spoof.setStyleSheet(
                    f"font-size: 28px; font-weight: 900; border: none; "
                    f"color: {Colors.DANGER if spoof_count > 0 else Colors.SUCCESS};"
                )

                # Cập nhật API Latency nếu API Server có trả về
                api_ms = stat_data.get("api_latency_ms", -1)
                self.lbl_api.setText(
                    f"API: {api_ms:.1f} ms" if api_ms >= 0 else "API: OK"
                )

                # ── Cập nhật bảng LOG ĐIỂM DANH TRỰC TIẼP ──────────────────
                latest_logs = stat_data.get("latest_logs", [])
                if latest_logs:
                    # Chỉ thêm dòng mới; giới hạn 50 dòng để không nặng UI
                    current_ids = set()
                    for r in range(self.table.rowCount()):
                        item = self.table.item(r, 1)  # Cột Mã SV
                        if item:
                            current_ids.add(item.text().strip())

                    for log in latest_logs:
                        sv_code = str(log.get("id", "")).strip()
                        time_str = log.get("time", "")
                        # Kiểm tra có tốn tại cặp (mã + giờ) chưa — tránh push rác
                        row_key = f"{sv_code}_{time_str}"
                        if row_key in current_ids:
                            continue

                        self.table.insertRow(0)

                        # Cột 0: Tên Camera đang gửi kết quả
                        cam_item = QTableWidgetItem(f"  📸 {log.get('camera', 'CAM')}")
                        cam_item.setForeground(QColor(Colors.PRIMARY))
                        self.table.setItem(0, 0, cam_item)

                        # Cột 1: Mã SV
                        self.table.setItem(0, 1, QTableWidgetItem(f"  {sv_code}"))

                        # Cột 2: Họ Tên
                        self.table.setItem(0, 2, QTableWidgetItem(f"  {log.get('name', '---')}"))

                        # Cột 3: Thời gian
                        self.table.setItem(0, 3, QTableWidgetItem(f"  {time_str}"))

                        # Cột 4: Trạng thái (Pill Badge)
                        is_ok = log.get("is_present", True)
                        badge_widget = QWidget()
                        badge_layout = QHBoxLayout(badge_widget)
                        badge_layout.setContentsMargins(0, 0, 0, 0)
                        badge = self._create_pill_badge(is_ok)
                        badge_layout.addWidget(badge)
                        badge_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                        self.table.setCellWidget(0, 4, badge_widget)

                        self.table.setRowHeight(0, 50)

                    # Giới hạn 50 hàng
                    while self.table.rowCount() > 50:
                        self.table.removeRow(self.table.rowCount() - 1)

        except Exception as e:
            logger.warning(f"[Dashboard] Không lấy được /api/dashboard/stats: {e}")

    def update_dashboard_metrics(self):
        """Alias giữ tương thích ngược với code cũ."""
        self.refresh_dashboard_data()

    def _fetch_stats_by_date(self, qdate):
        """Alias giữ tương thích ngược với code cũ."""
        self.refresh_dashboard_data()