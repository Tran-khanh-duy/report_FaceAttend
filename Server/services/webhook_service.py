"""
Server/services/webhook_service.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quản lý việc gửi Webhook notification tới các MiniPC.

Khi nào Server gọi:
  - enrollment_service.finish_enrollment()   → thêm SV mới
  - /api/reload-cache                        → Admin force reload

MiniPC nhận webhook → pull_embeddings() ngay → cache cập nhật <0.5s
"""
import threading
import requests
from loguru import logger

MINIPC_WEBHOOK_PORT = 8765
WEBHOOK_PATH        = "/webhook/refresh"
WEBHOOK_TIMEOUT_S   = 3   # giây — không để quá cao tránh block thread


class WebhookService:
    """
    Singleton lưu danh sách IP MiniPC đang online và gửi POST thông báo.

    MiniPC tự đăng ký IP mỗi khi gọi POST /api/system/edge_status.
    Khi Server DB thay đổi, gọi ``notify_all()`` để tất cả MiniPC
    biết và pull cache ngay lập tức.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # { device_name: ip_address }  — cập nhật mỗi khi edge_status đến
        self._minipc_ips: dict[str, str] = {}

    # ─── Đăng ký / Cập nhật IP ────────────────

    def register_device(self, device_name: str, ip_address: str):
        """
        Đăng ký IP của MiniPC.
        Gọi từ /api/system/edge_status mỗi khi MiniPC báo cáo trạng thái.
        """
        if not ip_address or ip_address in ("127.0.0.1", "::1", ""):
            return
        with self._lock:
            old_ip = self._minipc_ips.get(device_name)
            self._minipc_ips[device_name] = ip_address
            if old_ip != ip_address:
                logger.info(
                    f"📋 WebhookService: Đã đăng ký '{device_name}' → {ip_address}:{MINIPC_WEBHOOK_PORT}"
                )

    def unregister_device(self, device_name: str):
        """Xóa MiniPC khỏi danh sách (nếu cần)."""
        with self._lock:
            self._minipc_ips.pop(device_name, None)

    def get_registered_devices(self) -> dict:
        """Trả về bản sao danh sách device đang đăng ký (debug/monitoring)."""
        with self._lock:
            return dict(self._minipc_ips)

    # ─── Gửi Webhook ──────────────────────────

    def notify_all(self, event: str = "CACHE_REFRESH"):
        """
        Gửi webhook tới TẤT CẢ MiniPC đang đăng ký.
        Chạy hoàn toàn async trong background thread — KHÔNG block API response.

        Args:
            event: Tên sự kiện gửi kèm trong body JSON. Mặc định 'CACHE_REFRESH'.
        """
        with self._lock:
            targets = dict(self._minipc_ips)

        if not targets:
            logger.debug("WebhookService.notify_all: Chưa có MiniPC nào đăng ký IP.")
            return

        def _send_all():
            success = 0
            for device_name, ip in targets.items():
                ok = self._send_webhook(device_name, ip, event)
                if ok:
                    success += 1
            logger.info(f"📡 Webhook gửi xong: {success}/{len(targets)} MiniPC nhận được.")

        t = threading.Thread(target=_send_all, daemon=True, name="Webhook-Notify")
        t.start()
        logger.debug(f"Đã phát lệnh notify_all(event='{event}') tới {len(targets)} MiniPC (async).")

    def notify_device(self, device_name: str, event: str = "CACHE_REFRESH") -> bool:
        """
        Gửi webhook tới một MiniPC cụ thể (dùng cho debug/test).
        Chạy đồng bộ — có thể block nếu MiniPC offline.

        Returns:
            True nếu MiniPC xác nhận nhận được, False nếu lỗi.
        """
        with self._lock:
            ip = self._minipc_ips.get(device_name)
        if not ip:
            logger.warning(f"WebhookService.notify_device: '{device_name}' chưa đăng ký IP.")
            return False
        return self._send_webhook(device_name, ip, event)

    def _send_webhook(self, device_name: str, ip: str, event: str) -> bool:
        """Gửi HTTP POST tới một MiniPC. Trả về True nếu thành công."""
        url = f"http://{ip}:{MINIPC_WEBHOOK_PORT}{WEBHOOK_PATH}"
        try:
            resp = requests.post(
                url,
                json={"event": event, "source": "server"},
                timeout=WEBHOOK_TIMEOUT_S,
            )
            if resp.status_code == 200:
                logger.success(f"✅ Webhook OK → '{device_name}' ({ip}): {resp.json().get('message', '')}")
                return True
            else:
                logger.warning(
                    f"⚠️ Webhook '{device_name}' ({ip}): HTTP {resp.status_code} — {resp.text[:80]}"
                )
                return False
        except requests.ConnectionError:
            logger.warning(f"❌ Webhook '{device_name}' ({ip}): Không thể kết nối (MiniPC offline?)")
            return False
        except requests.Timeout:
            logger.warning(f"⏱️ Webhook '{device_name}' ({ip}): Timeout sau {WEBHOOK_TIMEOUT_S}s")
            return False
        except Exception as exc:
            logger.error(f"Webhook lỗi → '{device_name}' ({ip}): {exc}")
            return False


# ── Singleton ──────────────────────────────────────────
webhook_service = WebhookService()
