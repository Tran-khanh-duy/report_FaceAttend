# Sử dụng base image Python 3.10 slim để tối ưu dung lượng
FROM python:3.10-slim

# Thiết lập thư mục làm việc trong container
WORKDIR /app

# Cài đặt các thư viện hệ thống cần thiết (C biên dịch cho numpy, mysql, faiss)
RUN apt-get update && apt-get install -y \
    build-essential \
    libmariadb-dev-compat \
    libmariadb-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Cập nhật pip
RUN pip install --upgrade pip

# Copy file requirements
COPY requirements.txt .

# LỌC BỎ CÁC THƯ VIỆN KHÔNG CẦN THIẾT CHO SERVER (GUI, Camera, ONNX)
# Server chỉ cần API, Database, và FAISS vector search
RUN grep -v -i "PyQt" requirements.txt \
    | grep -v -i "onnxruntime" \
    | grep -v -i "insightface" \
    | grep -v -i "opencv-python" \
    > req_server.txt

# Sửa lỗi typo nếu có trong requirements
RUN sed -i 's/pytest-qt==4.2.0paramiko>=4.0.0/paramiko>=4.0.0/' req_server.txt

# Cài đặt dependencies cho Server
RUN pip install --no-cache-dir -r req_server.txt

# Bổ sung redis nếu chưa có trong requirements
RUN pip install --no-cache-dir redis==5.0.1

# Sao chép toàn bộ mã nguồn vào container
COPY . .

# Tạo sẵn các thư mục cần thiết để map Volume
RUN mkdir -p logs models reports/output

# Mở cổng API
EXPOSE 8000

# Khởi chạy FastAPI
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
