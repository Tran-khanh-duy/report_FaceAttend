"""
ui/pages/edge_kiosk_page.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Giao diện Kiosk cho Mini PC (Edge Box)
  - Camera feed toàn màn hình
  - Overlay kết quả nhận diện (tên, lớp, %)
  - Badge trạng thái kết nối Server
  - Tự động xử lý: Detect → Anti-Spoof → Recognize → Gửi Server
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import time
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap, QImage, QFont, QColor
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import edge_config, ai_config
from ui.styles.theme import Colors


# ─────────────────────────────────────────────
#  Worker Thread — AI Processing
# ─────────────────────────────────────────────
class EdgeProcessingThread(QThread):
    """Thread chạy pipeline: Camera → FaceEngine → Anti-Spoof → Send Server."""
    frame_ready = pyqtSignal(np.ndarray)           # Frame đã vẽ box
    attendance_ok = pyqtSignal(str, str, str, float)  # code, name, class, score
    attendance_fail = pyqtSignal(str)               # message
    spoof_detected = pyqtSignal()                   # Phát hiện giả mạo
    status_update = pyqtSignal(str, str)            # key, value
    fps_update = pyqtSignal(float)                  # fps

    def __init__(self):
        super().__init__()
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        # Import nặng ở đây để không block UI thread
        from services.face_engine import face_engine, ANTI_SPOOF_AVAILABLE
        try:
            from services.anti_spoof_service import anti_spoof_service
        except Exception:
            ANTI_SPOOF_AVAILABLE = False
            anti_spoof_service = None

        from edge_client import edge_client

        # 1. Load Model AI
        self.status_update.emit("model", "Đang load...")
        ai_config.onnx_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if not face_engine.load_model():
            self.status_update.emit("model", "❌ Lỗi load model")
            return
        self.status_update.emit("model", "✅ Sẵn sàng")

        # 2. Pull embeddings từ Server
        self.status_update.emit("sync", "Đang tải...")
        success = edge_client.pull_embeddings()
        cache = edge_client.get_cache()
        if success and not cache.is_empty:
            self.status_update.emit("sync", f"✅ {cache.size} học viên")
        else:
            self.status_update.emit("sync", "⚠️ Chưa có dữ liệu")

        # 3. Mở Camera
        source = edge_config.camera_source
        cam_source = int(source) if source.isdigit() else source
        cap = cv2.VideoCapture(cam_source)
        if not cap.isOpened():
            self.status_update.emit("camera", "❌ Không mở được camera")
            logger.error(f"Không thể mở camera: {source}")
            return
        
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.status_update.emit("camera", "✅ Hoạt động")

        # 4. Vòng lặp chính
        frame_count = 0
        last_fps_time = time.time()
        fps_frame_count = 0
        last_embed_check = time.time()

        while self._running:
            ret, frame = cap.read()
            if not ret:
                self.status_update.emit("camera", "⚠️ Mất tín hiệu")
                time.sleep(0.5)
                continue

            frame_count += 1
            fps_frame_count += 1

            # Tính FPS
            now = time.time()
            if now - last_fps_time >= 1.0:
                self.fps_update.emit(fps_frame_count / (now - last_fps_time))
                fps_frame_count = 0
                last_fps_time = now

            # Refresh embeddings định kỳ
            if now - last_embed_check >= 60:  # Kiểm tra mỗi phút
                last_embed_check = now
                if edge_client.should_refresh_embeddings():
                    self.status_update.emit("sync", "🔄 Đang refresh...")
                    edge_client.pull_embeddings()
                    cache = edge_client.get_cache()
                    self.status_update.emit("sync", f"✅ {cache.size} học viên")

            # Chỉ xử lý mỗi N frame
            if frame_count % edge_config.process_every_n != 0:
                self.frame_ready.emit(frame)
                continue

            # ── AI Pipeline ──
            cache = edge_client.get_cache()
            detected = face_engine.detect_faces(frame)

            if not detected:
                self.frame_ready.emit(frame)
                continue

            results = face_engine.recognize_batch(detected, cache)

            # Vẽ kết quả lên frame
            output = frame.copy()
            for i, res in enumerate(results):
                x1, y1, x2, y2 = res.bbox

                # Anti-Spoofing check
                is_real = True
                spoof_score = 1.0
                if ANTI_SPOOF_AVAILABLE and anti_spoof_service:
                    try:
                        is_real, spoof_score = anti_spoof_service.is_real(frame, res.bbox)
                    except Exception:
                        is_real = True

                color = (0, 220, 100) if res.recognized else (60, 60, 220)
                if not is_real:
                    color = (0, 140, 255)  # Orange = spoof

                cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)

                # Label
                if res.recognized:
                    label = f"{res.full_name} ({res.similarity*100:.0f}%)"
                else:
                    label = "Unknown"
                if not is_real:
                    label = "SPOOF!"

                # Vẽ label background
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(output, (x1, y1 - th - 12), (x1 + tw + 10, y1), color, -1)
                cv2.putText(output, label, (x1 + 5, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

                # ── Logic điểm danh ──
                if not is_real:
                    self.spoof_detected.emit()
                    continue

                if not res.recognized:
                    continue

                # Cooldown check
                remaining = edge_client.check_cooldown(res.student_id)
                if remaining > 0:
                    continue

                # Gửi về Server
                if res.embedding is not None:
                    embedding_to_send = detected[i].embedding
                else:
                    embedding_to_send = detected[i].embedding

                if embedding_to_send is not None:
                    result = edge_client.send_attendance(
                        embedding=embedding_to_send,
                        liveness_score=spoof_score,
                        liveness_checked=ANTI_SPOOF_AVAILABLE,
                    )

                    edge_client.set_cooldown(res.student_id)

                    status = result.get("status", "")
                    if status == "success":
                        self.attendance_ok.emit(
                            result.get("student_code", res.student_code or ""),
                            result.get("full_name", res.full_name or ""),
                            result.get("class_name", ""),
                            result.get("similarity", res.similarity),
                        )
                    elif status == "duplicate":
                        pass  # Đã điểm danh rồi, im lặng
                    elif status == "offline":
                        self.attendance_fail.emit("📴 Đã lưu offline — chờ kết nối")
                    elif status == "unknown":
                        pass
                    elif status == "no_session":
                        self.attendance_fail.emit("⚠️ Chưa có phiên điểm danh nào đang mở")

            self.frame_ready.emit(output)

        cap.release()
        logger.info("Edge processing thread đã dừng")


# ─────────────────────────────────────────────
#  Edge Kiosk Page — UI chính
# ─────────────────────────────────────────────
class EdgeKioskPage(QWidget):
    """Giao diện kiosk toàn màn hình cho Mini PC."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._worker = None

        # Timer ẩn overlay sau 4 giây
        self._overlay_timer = QTimer()
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.timeout.connect(self._hide_overlay)

        # Timer cập nhật đồng hồ
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

    def _build_ui(self):
        """Xây dựng giao diện Kiosk."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Container chính ──
        self._container = QWidget()
        self._container.setStyleSheet("background-color: #0F172A;")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Header Bar ──
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1E293B, stop:1 #0F172A);
                border-bottom: 1px solid #334155;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        # Logo + Tên
        title = QLabel("🎯 FaceAttend Edge")
        title.setStyleSheet("""
            color: #F8FAFC; font-size: 20px; font-weight: 800;
            letter-spacing: 1px; background: transparent;
        """)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Đồng hồ
        self._clock_label = QLabel()
        self._clock_label.setStyleSheet("""
            color: #94A3B8; font-size: 16px; font-weight: 600;
            background: transparent;
        """)
        header_layout.addWidget(self._clock_label)

        # Server status badge
        self._server_badge = QLabel("● Server")
        self._server_badge.setStyleSheet("""
            color: #F59E0B; font-size: 13px; font-weight: 700;
            background: #F59E0B18; border: 1px solid #F59E0B44;
            border-radius: 12px; padding: 4px 14px;
        """)
        header_layout.addWidget(self._server_badge)

        container_layout.addWidget(header)

        # ── Camera Feed ──
        self._camera_label = QLabel("Đang khởi động camera...")
        self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_label.setStyleSheet("""
            background-color: #0F172A; color: #475569;
            font-size: 22px; font-weight: 600;
        """)
        self._camera_label.setMinimumSize(640, 480)
        container_layout.addWidget(self._camera_label, 1)

        # ── Status Bar ──
        status_bar = QFrame()
        status_bar.setFixedHeight(48)
        status_bar.setStyleSheet("""
            QFrame {
                background: #1E293B;
                border-top: 1px solid #334155;
            }
        """)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(20, 0, 20, 0)

        self._status_labels = {}
        for key, icon, text in [
            ("model", "🧠", "AI: Đang load..."),
            ("camera", "📷", "Camera: Chờ..."),
            ("sync", "🔄", "Sync: Chờ..."),
            ("fps", "⚡", "FPS: --"),
            ("offline", "💾", "Offline: 0"),
        ]:
            lbl = QLabel(f"{icon} {text}")
            lbl.setStyleSheet("""
                color: #94A3B8; font-size: 12px; font-weight: 600;
                background: transparent; padding: 0 8px;
            """)
            status_layout.addWidget(lbl)
            self._status_labels[key] = lbl

        status_layout.addStretch()
        container_layout.addWidget(status_bar)

        layout.addWidget(self._container)

        # ── Overlay kết quả (ẩn mặc định) ──
        self._build_overlay()

    def _build_overlay(self):
        """Overlay hiện khi điểm danh thành công."""
        self._overlay = QFrame(self._container)
        self._overlay.setFixedSize(420, 180)
        self._overlay.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 185, 129, 230), stop:1 rgba(5, 150, 105, 230));
                border-radius: 20px;
                border: 2px solid rgba(255, 255, 255, 0.3);
            }
        """)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 8)
        self._overlay.setGraphicsEffect(shadow)

        ov_layout = QVBoxLayout(self._overlay)
        ov_layout.setContentsMargins(24, 20, 24, 20)
        ov_layout.setSpacing(6)

        # Tiêu đề overlay
        ov_title = QLabel("✅ ĐIỂM DANH THÀNH CÔNG")
        ov_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_title.setStyleSheet("""
            color: white; font-size: 15px; font-weight: 800;
            letter-spacing: 1.5px; background: transparent;
        """)
        ov_layout.addWidget(ov_title)

        # Tên học viên
        self._ov_name = QLabel("—")
        self._ov_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ov_name.setStyleSheet("""
            color: white; font-size: 28px; font-weight: 900;
            background: transparent;
        """)
        ov_layout.addWidget(self._ov_name)

        # Mã SV + Lớp
        self._ov_info = QLabel("—")
        self._ov_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ov_info.setStyleSheet("""
            color: rgba(255, 255, 255, 0.85); font-size: 16px;
            font-weight: 600; background: transparent;
        """)
        ov_layout.addWidget(self._ov_info)

        # Score
        self._ov_score = QLabel("")
        self._ov_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ov_score.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7); font-size: 14px;
            font-weight: 600; background: transparent;
        """)
        ov_layout.addWidget(self._ov_score)

        self._overlay.hide()

        # ── Overlay SPOOF (Cảnh báo giả mạo) ──
        self._spoof_overlay = QFrame(self._container)
        self._spoof_overlay.setFixedSize(400, 120)
        self._spoof_overlay.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(239, 68, 68, 230), stop:1 rgba(220, 38, 38, 230));
                border-radius: 20px;
                border: 2px solid rgba(255, 255, 255, 0.3);
            }
        """)
        spoof_layout = QVBoxLayout(self._spoof_overlay)
        spoof_layout.setContentsMargins(20, 16, 20, 16)
        spoof_title = QLabel("⚠️ PHÁT HIỆN GIẢ MẠO!")
        spoof_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spoof_title.setStyleSheet("""
            color: white; font-size: 22px; font-weight: 900;
            letter-spacing: 1px; background: transparent;
        """)
        spoof_layout.addWidget(spoof_title)
        spoof_msg = QLabel("Vui lòng sử dụng khuôn mặt thật")
        spoof_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spoof_msg.setStyleSheet("""
            color: rgba(255,255,255,0.8); font-size: 14px;
            font-weight: 600; background: transparent;
        """)
        spoof_layout.addWidget(spoof_msg)
        self._spoof_overlay.hide()

        # Timer ẩn spoof overlay
        self._spoof_timer = QTimer()
        self._spoof_timer.setSingleShot(True)
        self._spoof_timer.timeout.connect(self._spoof_overlay.hide)

    # ─── Update Clock ─────────────────────────

    def _update_clock(self):
        now = datetime.now()
        self._clock_label.setText(now.strftime("%H:%M:%S  |  %d/%m/%Y"))

    # ─── Start / Stop Processing ──────────────

    def start_processing(self):
        """Bắt đầu pipeline AI."""
        if self._worker and self._worker.isRunning():
            return

        self._worker = EdgeProcessingThread()
        self._worker.frame_ready.connect(self._update_frame)
        self._worker.attendance_ok.connect(self._show_success_overlay)
        self._worker.attendance_fail.connect(self._show_fail_message)
        self._worker.spoof_detected.connect(self._show_spoof_overlay)
        self._worker.status_update.connect(self._update_status)
        self._worker.fps_update.connect(self._update_fps)
        self._worker.start()

    def stop_processing(self):
        """Dừng pipeline."""
        if self._worker:
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None

    # ─── Signal Handlers ──────────────────────

    def _update_frame(self, frame: np.ndarray):
        """Hiển thị frame lên camera label."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        q_img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)

        # Scale to fit label
        label_w = self._camera_label.width()
        label_h = self._camera_label.height()
        pixmap = QPixmap.fromImage(q_img).scaled(
            label_w, label_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._camera_label.setPixmap(pixmap)

    def _show_success_overlay(self, code: str, name: str, class_name: str, score: float):
        """Hiển thị overlay điểm danh thành công."""
        self._ov_name.setText(name)
        self._ov_info.setText(f"{code}  •  {class_name}")
        self._ov_score.setText(f"Độ tin cậy: {score*100:.1f}%  |  {datetime.now().strftime('%H:%M:%S')}")

        # Vị trí: góc phải trên camera
        self._overlay.move(
            self._container.width() - self._overlay.width() - 30,
            90,
        )
        self._overlay.show()
        self._overlay.raise_()

        # Update server badge
        self._server_badge.setText("● Online")
        self._server_badge.setStyleSheet("""
            color: #10B981; font-size: 13px; font-weight: 700;
            background: #10B98118; border: 1px solid #10B98144;
            border-radius: 12px; padding: 4px 14px;
        """)

        # Ẩn sau 4 giây
        self._overlay_timer.start(4000)
        logger.info(f"✅ Overlay: [{code}] {name} | {class_name} | {score:.2f}")

    def _hide_overlay(self):
        self._overlay.hide()

    def _show_fail_message(self, msg: str):
        """Hiển thị lỗi nhỏ trên status bar."""
        self._status_labels.get("sync", QLabel()).setText(f"⚠️ {msg}")

    def _show_spoof_overlay(self):
        """Hiển thị cảnh báo giả mạo."""
        self._spoof_overlay.move(
            (self._container.width() - self._spoof_overlay.width()) // 2,
            self._container.height() // 2 - self._spoof_overlay.height() // 2,
        )
        self._spoof_overlay.show()
        self._spoof_overlay.raise_()
        self._spoof_timer.start(3000)

    def _update_status(self, key: str, value: str):
        """Cập nhật status bar."""
        icons = {"model": "🧠", "camera": "📷", "sync": "🔄", "fps": "⚡", "offline": "💾"}
        icon = icons.get(key, "")
        lbl = self._status_labels.get(key)
        if lbl:
            lbl.setText(f"{icon} {value}")

        # Cập nhật server badge
        if key == "sync":
            if "✅" in value:
                self._server_badge.setText("● Online")
                self._server_badge.setStyleSheet("""
                    color: #10B981; font-size: 13px; font-weight: 700;
                    background: #10B98118; border: 1px solid #10B98144;
                    border-radius: 12px; padding: 4px 14px;
                """)
            elif "⚠️" in value or "❌" in value:
                self._server_badge.setText("● Offline")
                self._server_badge.setStyleSheet("""
                    color: #EF4444; font-size: 13px; font-weight: 700;
                    background: #EF444418; border: 1px solid #EF444444;
                    border-radius: 12px; padding: 4px 14px;
                """)

    def _update_fps(self, fps: float):
        lbl = self._status_labels.get("fps")
        if lbl:
            lbl.setText(f"⚡ {fps:.1f} FPS")

    # ─── Cleanup ──────────────────────────────

    def closeEvent(self, event):
        self.stop_processing()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Auto-start khi page được hiển thị
        QTimer.singleShot(500, self.start_processing)
