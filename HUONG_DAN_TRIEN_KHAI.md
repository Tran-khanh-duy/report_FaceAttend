# 📑 HƯỚNG DẪN TRIỂN KHAI SANG MÁY TÍNH (PC) MỚI

Tài liệu này hướng dẫn bạn cách chuyển toàn bộ dự án Face Attendance sang một máy tính khác (Mini PC hoặc Server) một cách nhanh chóng.

---

## 🏗️ 1. Chuẩn bị (Prerequisites)
Trước khi bắt đầu, hãy đảm bảo máy tính mới đã cài đặt:
- **Python 3.10 hoặc 3.11**: [Tải tại đây](https://www.python.org/downloads/).
  - **LƯU Ý QUAN TRỌNG**: Khi cài đặt, bắt buộc phải tích chọn ô **"Add Python to PATH"**.
- **Git** (Tùy chọn): Nếu bạn muốn quản lý mã nguồn.

---

## 📂 2. Sao chép mã nguồn
1. Nén thư mục `coding_t07-main` thành file `.zip`.
2. Copy sang máy tính mới và giải nén (Ví dụ vào ổ `D:\FACE_ATTEND`).

---

## 🖥️ 3. Thiết lập cho MINI PC (Edge Box)

### Bước 3.1: Cấu hình Camera và Kết nối
Mở file `MINI_PC/.env.edge` bằng Notepad và chỉnh sửa:
- `EDGE_CAMERA_SOURCE`: Đặt là `0` (webcam mặc định), hoặc `1`, `2`... 
- `EDGE_SERVER_URL`: Địa chỉ của máy Server (Ví dụ: `http://192.168.1.5:8000`).

### Bước 3.2: Cài đặt thư viện
1. Truy cập folder `MINI_PC/scripts/`.
2. Chạy file `install_dependencies.bat`. 
   - *Hệ thống sẽ tự tạo môi trường ảo `.venv` và tải các model AI cần thiết.*

### Bước 3.3: Khởi chạy
1. Trong folder `MINI_PC/scripts/`, chạy file `run_edge.bat`.
2. Nếu muốn chạy kiểm tra Camera trước, hãy vào `MINI_PC/utils/` chạy `camera_checker.py`.

---

## 🌐 4. Thiết lập cho SERVER (Máy chủ)

### Bước 4.1: Cấu hình Database
Mở file `Server/.env.server` và cấu hình các thông số SQL Server (nếu có dùng Database thực tế).

### Bước 4.2: Cài đặt thư viện
1. Truy cập folder `Server/scripts/`.
2. Chạy file `install_dependencies.bat`.

### Bước 4.3: Khởi chạy
1. Trong folder `Server/scripts/`, chạy file `run_server.bat`.

---

## 🛠️ 5. Xử lý sự cố thường gặp (Troubleshooting)

| Lỗi | Cách xử lý |
| :--- | :--- |
| **ModuleNotFoundError** | Chạy lại `install_dependencies.bat` trong folder `scripts`. |
| **Không mở được Camera** | Chạy `MINI_PC/utils/camera_checker.py` để tìm ID đúng, sau đó điền vào `.env.edge`. |
| **Cửa sổ CMD bị đóng nhanh** | Mở CMD thủ công, kéo file `.bat` vào và nhấn Enter để xem thông báo lỗi. |
| **Lỗi thư viện AI (.pth)** | Đảm bảo internet ổn định khi chạy installer để nó tải đủ model AI. |

---
*Chúc bạn triển khai thành công! Chế độ Edge hiện đã được tối ưu hóa cực kỳ ổn định.*
