# BỐ CỤC CHI TIẾT BÁO CÁO SẢN PHẨM: FACEATTEND AI
*(Quy mô dự kiến: 30 - 50 trang)*

---

## CHƯƠNG 1: TỔNG QUAN VỀ DỰ ÁN VÀ PHÂN TÍCH YÊU CẦU (8 - 12 trang)

### 1.1. Giới thiệu bài toán (2 - 3 trang)
*   **1.1.1. Thực trạng:** Phân tích các phương pháp điểm danh truyền thống (giấy, thẻ từ) và các vấn đề: gian lận, quên thẻ, sai sót dữ liệu.
*   **1.1.2. Giải pháp:** Giới thiệu công nghệ nhận diện khuôn mặt và lý do tại sao nó là giải pháp tối ưu cho quản lý ký túc xá/trường học.
*   **1.1.3. Mục tiêu dự án:** Xây dựng hệ thống tự động hóa hoàn toàn quy trình điểm danh từ đăng ký đến báo cáo.

### 1.2. Đối tượng và phạm vi ứng dụng (1 - 2 trang)
*   **1.2.1. Đối tượng:** Học viên, sinh viên và cán bộ quản lý.
*   **1.2.2. Phạm vi:** Triển khai tại các lối ra vào, hành lang hoặc phòng học.

### 1.3. Công nghệ và môi trường phát triển (5 - 7 trang)
*   **1.3.1. Python:** Phân tích ưu điểm của Python trong xử lý dữ liệu và AI.
*   **1.3.2. PyQt6:** Giải thích về kiến trúc lập trình GUI hiện đại, cách quản lý Layout và Stylesheet (Vanilla CSS).
*   **1.3.3. SQL Server & SQLite:** Phân tích kiến trúc lưu trữ dữ liệu tập trung kết hợp bộ đệm cục bộ để tối ưu tốc độ.
*   **1.3.4. AI Core:** 
    *   Giới thiệu mô hình **Buffalo_L** (InsightFace).
    *   Giải thích vai trò của **ONNX Runtime** và việc tận dụng **CUDA GPU** để xử lý ảnh thời gian thực.

---

## CHƯƠNG 2: KIẾN TRÚC HỆ THỐNG VÀ CÁC TÍNH NĂNG CỐT LÕI (15 - 22 trang)

### 2.1. Kiến trúc Dual-Mode Server - Edge (4 - 5 trang)
*   **2.1.1. Server Mode:** Vai trò quản trị viên (Admin), cấu hình và quản lý dữ liệu lớn.
*   **2.1.2. Edge/Kiosk Mode:** Vai trò máy trạm điểm danh, ưu tiên tốc độ nhận diện và khả năng chạy độc lập.
*   **2.1.3. Cơ chế đồng bộ:** Giải thích quy trình cập nhật Embedding Version qua API giữa Server và Edge.

### 2.2. Quy trình quản lý và đăng ký học viên (5 - 6 trang)
*   **2.2.1. Giao diện Đăng ký:** Mô tả các thành phần Form nhập liệu và Camera Preview.
*   **2.2.2. Thuật toán Auto-capture:** Giải thích logic chụp 15 ảnh mẫu liên tục khi phát hiện khuôn mặt hợp lệ.
*   **2.2.3. Xử lý Embedding:** Quy trình trích xuất vector 512 chiều và lưu trữ vào SQL Server.
*   *Chèn Screenshots: Trang danh sách học viên, Form đăng ký mới.*

### 2.3. Cơ chế điểm danh và nhận diện thời gian thực (4 - 6 trang)
*   **2.3.1. Multi-threading:** Phân tích kỹ thuật tách luồng Capture hình ảnh và luồng xử lý AI để tránh giật lag UI.
*   **2.3.2. So khớp khuôn mặt:** Giải thích công thức tính toán Cosine Similarity để xác định danh tính với độ chính xác cao.
*   **2.3.3. Logic điểm danh:** Cách hệ thống ghi nhận trạng thái (Present/Absent) và ngăn chặn ghi đè dữ liệu trong cùng một phiên.

### 2.4. Quản lý thông tin và báo cáo thống kê (2 - 5 trang)
*   **2.4.1. Cấu trúc tổ chức:** Giải thích cách phân cấp Building -> Floor -> Room trong quản lý ký túc xá.
*   **2.4.2. Báo cáo Excel:** Quy trình truy vấn dữ liệu từ SQL Server và xuất ra file báo cáo tiêu chuẩn.

---

## CHƯƠNG 3: TRIỂN KHAI, ĐÁNH GIÁ VÀ HƯỚNG PHÁT TRIỂN (7 - 16 trang)

### 3.1. Thiết kế giao diện và trải nghiệm người dùng (2 - 3 trang)
*   **3.1.1. Thẩm mỹ hiện đại:** Phân tích cách sử dụng màu sắc (Colors system), các hiệu ứng Hover và Micro-animations.
*   **3.1.2. Tính đáp ứng (Responsiveness):** Cách giao diện tự điều chỉnh trên các kích thước màn hình khác nhau.

### 3.2. Kết quả thực nghiệm và đánh giá hiệu năng (3 - 6 trang)
*   **3.2.1. Độ chính xác:** Bảng thống kê tỷ lệ nhận diện đúng trong các điều kiện (đeo kính, thiếu sáng, góc nghiêng).
*   **3.2.2. Tốc độ xử lý:** So sánh FPS (Frames Per Second) giữa việc sử dụng CPU và GPU.

### 3.3. Quản trị hệ thống và bảo trì (2 - 4 trang)
*   **3.3.1. Embedding Cache:** Cơ chế tự động nạp lại dữ liệu RAM khi có học viên mới.
*   **3.3.2. Loguru:** Hệ thống ghi nhật ký (Logging) để theo dõi lỗi và hoạt động của hệ thống.

### 3.4. Kết luận và hướng phát triển tương lai (1 - 3 trang)
*   **3.4.1. Tổng kết:** Những giá trị thực tế mà sản phẩm mang lại.
*   **3.4.2. Mở rộng:** Tích hợp nhận diện sống (Anti-spoofing), thông báo qua các nền tảng khác, hoặc tích hợp thẻ từ NFC.

---

## TÀI LIỆU THAM KHẢO & PHỤ LỤC
*   Danh mục các thư viện Python sử dụng.
*   Hướng dẫn cài đặt môi trường (CUDA, SQL Server).
*   Các đoạn code quan trọng (Algorithm logic).
