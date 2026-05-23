import cv2
import socket
import re
from loguru import logger

def detect_available_cameras(max_to_check=10):
    """
    Quét các chỉ số camera từ 0 đến max_to_check.
    Trả về danh sách các index camera đang hoạt động.
    """
    available_indices = []
    
    # Thử quét các index
    for i in range(max_to_check):
        try:
            # Dùng CAP_DSHOW trên Windows để nhanh hơn, 
            # hoặc mặc định nếu trên Linux (Mini PC thường chạy Linux hoặc Win)
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret:
                    available_indices.append(i)
                    logger.debug(f"📷 Phát hiện Camera tại index: {i}")
                cap.release()
        except Exception as e:
            logger.error(f"Lỗi khi kiểm tra camera index {i}: {e}")
            
    return available_indices

def discover_network_cameras(timeout=3.0):
    """
    Quét mạng LAN bằng Multicast (WS-Discovery) để tìm IP Camera (ONVIF).
    Trả về dict: { "ip_address": "Brand" }
    """
    UDP_IP = "239.255.255.250"
    UDP_PORT = 3702

    probe_msg = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">
  <s:Header>
    <a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
    <a:MessageID>urn:uuid:7362ba22-f63b-4892-95cd-658b4fac91cc</a:MessageID>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
    <a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <Probe xmlns="http://schemas.xmlsoap.org/ws/2005/04/discovery">
      <d:Types xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dp0="http://www.onvif.org/ver10/network/wsdl">dp0:NetworkVideoTransmitter</d:Types>
    </Probe>
  </s:Body>
</s:Envelope>"""

    cam_details = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', 0))
        sock.sendto(probe_msg.encode('utf-8'), (UDP_IP, UDP_PORT))
        
        while True:
            try:
                data, _ = sock.recvfrom(65536)
                response = data.decode('utf-8', errors='ignore')
                match = re.search(r'XAddrs>(http://([0-9\.]+)[^<]+)', response)
                if match:
                    ip = match.group(2)
                    brand = "Unknown"
                    if "hikvision" in response.lower(): brand = "Hikvision"
                    elif "dahua" in response.lower(): brand = "Dahua/KBVision"
                    elif "tp-link" in response.lower() or "tapo" in response.lower(): brand = "Tapo"
                    elif "xm" in response.lower() or "xiongmai" in response.lower(): brand = "XMeye"
                    elif "onvif" in response.lower(): brand = "ONVIF"
                    cam_details[ip] = brand
            except socket.timeout:
                break
    except Exception as e:
        logger.debug(f"Lỗi khi quét ONVIF: {e}")
    finally:
        try:
            sock.close()
        except:
            pass
            
    return cam_details

def generate_rtsp_links(cam_details, auth="admin:a1234567@"):
    """
    Từ details (IP, Brand), sinh ra các RTSP URLs tiềm năng.
    """
    links = []
    for ip, brand in cam_details.items():
        if brand == "Hikvision":
            links.append(f"rtsp://{auth}{ip}:554/Streaming/Channels/101")
            links.append(f"rtsp://{auth}{ip}:554/Streaming/Channels/102")
        elif brand == "Dahua/KBVision":
            links.append(f"rtsp://{auth}{ip}:554/cam/realmonitor?channel=1&subtype=0")
        elif brand == "Tapo":
            links.append(f"rtsp://{auth}{ip}:554/stream1")
            links.append(f"rtsp://{auth}{ip}:554/stream2")
        elif brand == "XMeye":
            links.append(f"rtsp://{ip}:554/user=admin&password=123456&channel=1&stream=0.sdp?real_stream")
        else:
            links.append(f"rtsp://{auth}{ip}:554/Streaming/Channels/101") # Default try Hikvision
            links.append(f"rtsp://{auth}{ip}:554/cam/realmonitor?channel=1&subtype=0") # Default try Dahua
            links.append(f"rtsp://{auth}{ip}:554/stream1") # Tapo/Onvif
            links.append(f"rtsp://{auth}{ip}:554/onvif1") # Yoosee
    return list(set(links))

if __name__ == "__main__":
    # Test nhanh
    print("Đang quét camera...")
    cams = detect_available_cameras()
    print(f"Các camera tìm thấy: {cams}")
