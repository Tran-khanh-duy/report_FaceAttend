import ipaddress
from typing import Optional
from fastapi import Request, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from loguru import logger

from core.config import server_config

# Định nghĩa Header key cho thiết bị Edge (MINI_PC)
X_DEVICE_TOKEN_HEADER = APIKeyHeader(name="X-DEVICE-TOKEN", auto_error=False)

def check_ip_allowed(client_ip: str, allowed_ips: list) -> bool:
    """
    Kiểm tra IP có nằm trong danh sách whitelist hay không.
    Hỗ trợ cả địa chỉ IP tĩnh (VD: 192.168.1.100) và dải mạng (VD: 192.168.1.0/24).
    """
    if "*" in allowed_ips:
        return True
        
    for allowed_ip in allowed_ips:
        try:
            if ipaddress.ip_address(client_ip) in ipaddress.ip_network(allowed_ip, strict=False):
                return True
        except ValueError:
            # Bỏ qua nếu cấu hình IP không hợp lệ
            continue
    return False

async def verify_device_access(
    request: Request,
    device_token: Optional[str] = Security(X_DEVICE_TOKEN_HEADER)
) -> str:
    """
    Dependency bảo mật cho FastAPI (LAN Network).
    - Trả về 401 nếu thiếu X-DEVICE-TOKEN
    - Trả về 403 nếu sai Token hoặc IP không nằm trong Whitelist
    """
    client_ip = request.client.host

    # 1. Kiểm tra IP Whitelist (Chống máy lạ trong mạng LAN)
    if not check_ip_allowed(client_ip, server_config.allowed_ips):
        logger.warning(f"🔒 [403] Từ chối truy cập từ IP lạ {client_ip} - Không thuộc Whitelist")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IP Address not whitelisted"
        )

    # 2. Kiểm tra Token (Chống fake attendance từ IP hợp lệ nhưng phần mềm fake)
    if not device_token:
        logger.warning(f"🔒 [401] Từ chối truy cập từ IP {client_ip} - Thiếu X-DEVICE-TOKEN")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-DEVICE-TOKEN header"
        )
        
    if device_token != server_config.device_token:
        logger.warning(f"🔒 [403] Từ chối truy cập từ IP {client_ip} - Sai X-DEVICE-TOKEN")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Device Token"
        )

    return device_token
