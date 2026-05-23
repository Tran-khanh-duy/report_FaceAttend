import requests

# Khai báo thông tin Bot (Sẽ chuyển sang file .env ở Bước 4 để bảo mật)
BOT_TOKEN = "8249094281:AAHgXpAkv2hd3am3g5TFSlU0H48e9jvbq9M"
CHAT_ID = "8666460707"

def send_telegram_msg(message):
    """Gửi tin nhắn qua Telegram Bot"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }
    import json
    try:
        # TASK 2: Sử dụng json.dumps với ensure_ascii=False và mã hóa cứng sang utf-8
        # Điều này đảm bảo Requests không tự động can thiệp làm hỏng Unicode của tiếng Việt
        json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        
        # Gửi request với timeout để không làm treo hệ thống nếu rớt mạng
        response = requests.post(url, data=json_data, headers=headers, timeout=5)
        if response.status_code == 200:
            print("Đã gửi thông báo Telegram thành công.")
        else:
            print(f"Lỗi gửi tin: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Lỗi kết nối mạng khi gửi Telegram: {e}")