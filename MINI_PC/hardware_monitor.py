import psutil
import requests
import time
import threading
import logging
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("HardwareMonitor")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(ch)

def get_cpu_temp():
    """Hỗ trợ lấy nhiệt độ (Tùy thuộc HĐH và phần cứng)"""
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                return temps['coretemp'][0].current
            elif temps:
                # Trả về cảm biến đầu tiên tìm được
                return list(temps.values())[0][0].current
    except Exception:
        pass
    return 45.0  # Mặc định / giả định cho môi trường không đọc được (như Windows không chạy quyền Admin)


def _build_no_retry_session() -> requests.Session:
    """Tạo Session KHÔNG retry — tránh spam log khi server offline."""
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(total=0, raise_on_status=False),
        pool_connections=1,
        pool_maxsize=2,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def hardware_monitor_loop(server_url: str, edge_id: str):
    """
    Vòng lặp thu thập và POST lên Server mỗi 60 giây.

    [FIX OFFLINE] Sử dụng Circuit Breaker để tránh log spam khi Server tắt:
    - Nếu 3 lần liên tiếp thất bại → im lặng (không log ERROR), chỉ thử lại sau 60s
    - Khi server trở lại → báo cáo và resume bình thường
    """
    logger.info(f"Khởi động Hardware Monitor. Gửi tới: {server_url} | Edge ID: {edge_id}")
    session = _build_no_retry_session()
    consecutive_failures = 0
    _MAX_SILENT_FAILURES = 3  # Sau đây im lặng, không log ERROR nữa

    while True:
        try:
            mem = psutil.virtual_memory()
            payload = {
                "edge_id": edge_id,
                "cpu_percent": psutil.cpu_percent(interval=1),
                "ram_percent": mem.percent,
                "ram_used_gb": round(mem.used / (1024 ** 3), 1),
                "ram_total_gb": round(mem.total / (1024 ** 3), 1),
                "temperature": get_cpu_temp()
            }

            resp = session.post(
                f"{server_url}/api/hardware/metrics",
                json=payload,
                timeout=2
            )

            if resp.status_code == 200:
                if consecutive_failures > 0:
                    logger.info(f"✅ HardwareMonitor: Server trở lại online — tiếp tục gửi telemetry.")
                consecutive_failures = 0
                logger.debug(
                    f"Đã gửi telemetry: CPU {payload['cpu_percent']}% "
                    f"| RAM {payload['ram_percent']}% | Temp {payload['temperature']}°C"
                )
            else:
                raise Exception(f"HTTP {resp.status_code}")

        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures <= _MAX_SILENT_FAILURES:
                # Chỉ log error trong vài lần đầu
                logger.error(f"Gửi telemetry thất bại (Sẽ thử lại sau 1 phút): {e}")
            elif consecutive_failures == _MAX_SILENT_FAILURES + 1:
                # Thông báo chuyển sang chế độ im lặng
                logger.warning(
                    f"⚠️ HardwareMonitor: Server không phản hồi sau {consecutive_failures} lần "
                    f"— chuyển sang chế độ im lặng. Sẽ thử lại mỗi 60s."
                )
            # else: im lặng hoàn toàn để tránh spam log

        # BẮT BUỘC: Sleep 60 giây theo yêu cầu để giới hạn tần suất gửi 1 phút/lần
        time.sleep(60)


if __name__ == "__main__":
    # Lấy thông số từ biến môi trường hoặc chạy mặc định
    SERVER_URL = os.getenv("EDGE_SERVER_URL", "http://127.0.0.1:9696")
    EDGE_ID = os.getenv("EDGE_CAMERA_ID", "MINI_PC_01")
    hardware_monitor_loop(SERVER_URL, EDGE_ID)
