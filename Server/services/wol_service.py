import socket
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable
from loguru import logger

# ─── Mini PC Registry ─────────────────────────────────────────────────────────

@dataclass
class MiniPCDevice:
    """Thông tin một thiết bị Mini PC."""
    name:         str                    # Ví dụ: "Mini PC KTX E4"
    mac_address:  str                    # Ví dụ: "54-BF-64-9C-79-AC"
    ip_address:   str = ""               # IP tĩnh (nếu có), để kiểm tra online
    location:     str = ""               # Ví dụ: "KTX E4 - Tầng 4"
    broadcast:    str = "255.255.255.255" # Broadcast LAN
    wol_port:     int = 9
    is_online:    bool = False
    last_seen:    float = 0.0
    device_name:  str = ""               # Trùng với edge_config.device_name

@dataclass
class WOLConfig:
    devices: List[MiniPCDevice] = field(default_factory=list)
    online_timeout_sec: float = 60.0     # Sau 60s không thấy heartbeat = offline

class WOLService:
    """Dịch vụ Wake-on-LAN trung tâm."""
    
    def __init__(self, config: WOLConfig = None):
        self._config = config or WOLConfig()
        self._lock = threading.Lock()
        self._status_callbacks: List[Callable] = []

    def set_devices(self, devices: List[MiniPCDevice]):
        with self._lock:
            self._config.devices = devices
            logger.info(f"📋 WOL: Đã nạp {len(devices)} thiết bị vào danh sách.")

    def _send_packet(self, mac_address: str, broadcast: str, port: int) -> bool:
        """Gửi Magic Packet chuẩn WOL."""
        try:
            clean_mac = mac_address.upper().replace(":", "").replace("-", "").replace(".", "")
            if len(clean_mac) != 12:
                logger.error(f"❌ WOL: MAC address không hợp lệ: {mac_address}")
                return False
            
            # Tạo Magic Packet (102 bytes)
            mac_bytes = bytes.fromhex(clean_mac)
            magic_packet = b'\xff' * 6 + mac_bytes * 16
            
            # Gửi qua UDP Broadcast (Thử cả port 7 và 9 để tăng tỉ lệ thành công)
            ports = [port] if port != 9 else [9, 7]
            
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                # Trên Windows, đôi khi cần bind tới 0.0.0.0
                try:
                    s.bind(('', 0))
                except:
                    pass
                
                for p in ports:
                    s.sendto(magic_packet, (broadcast, p))
                    # Thử gửi tới địa chỉ 255.255.255.255 nếu broadcast đang dùng là cụ thể
                    if broadcast != "255.255.255.255":
                        s.sendto(magic_packet, ("255.255.255.255", p))
            
            logger.success(f"🔌 WOL: Đã gửi Magic Packet tới {mac_address} ({broadcast})")
            return True
        except Exception as e:
            logger.error(f"❌ WOL: Lỗi khi gửi tới {mac_address}: {e}")
            return False

    def wake_all(self, async_mode: bool = True, force: bool = False):
        """Đánh thức tất cả thiết bị."""
        def _do_wake():
            with self._lock:
                targets = [d for d in self._config.devices if force or not d.is_online]
            
            if not targets:
                logger.info("ℹ️ WOL: Không có thiết bị nào cần đánh thức (đều đang online).")
                return

            logger.info(f"🚀 WOL: Đang đánh thức {len(targets)} Mini PC...")
            for d in targets:
                self._send_packet(d.mac_address, d.broadcast, d.wol_port)
                time.sleep(0.1) # Tránh nghẽn mạng
        
        if async_mode:
            threading.Thread(target=_do_wake, daemon=True).start()
        else:
            _do_wake()

    def wake_device(self, identifier: str, force: bool = True) -> bool:
        """Đánh thức một thiết bị cụ thể."""
        with self._lock:
            target = None
            for d in self._config.devices:
                if identifier.upper() in [d.name.upper(), d.mac_address.upper(), d.device_name.upper()]:
                    target = d
                    break
        
        if target:
            return self._send_packet(target.mac_address, target.broadcast, target.wol_port)
        
        # Nếu không có trong danh sách, thử coi identifier là MAC
        if len(identifier.replace(":","").replace("-","")) == 12:
            return self._send_packet(identifier, "255.255.255.255", 9)
            
        return False

    def update_online_status(self, device_name: str, ip: str = ""):
        """Cập nhật trạng thái khi nhận heartbeat."""
        with self._lock:
            now = time.time()
            for d in self._config.devices:
                if d.device_name == device_name:
                    if not d.is_online:
                        logger.success(f"🟢 Mini PC ONLINE: {d.name} ({ip})")
                    d.is_online = True
                    d.last_seen = now
                    if ip: d.ip_address = ip
                    return

    def check_timeouts(self):
        """Đánh dấu offline nếu quá lâu không thấy heartbeat."""
        with self._lock:
            now = time.time()
            for d in self._config.devices:
                if d.is_online and (now - d.last_seen > self._config.online_timeout_sec):
                    d.is_online = False
                    logger.warning(f"🔴 Mini PC OFFLINE: {d.name}")

# Singleton
wol_service = WOLService()
