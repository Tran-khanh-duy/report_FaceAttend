import insightface
import onnxruntime as ort
import numpy as np

print("\n" + "="*30)
print("KIỂM TRA HỆ THỐNG ĐIỂM DANH")
print("="*30)

# 1. Kiểm tra Version
print(f"✅ InsightFace: {insightface.__version__}")
print(f"✅ Numpy: {np.__version__}")

# 2. Kiểm tra GPU
providers = ort.get_available_providers()
print(f"✅ Thiết bị khả dụng: {providers}")

if 'CUDAExecutionProvider' in providers:
    print("🚀 TRẠNG THÁI: Sẵn sàng chạy bằng GPU (Tốc độ cao)")
else:
    print("⚠️ TRẠNG THÁI: Đang chạy bằng CPU (Có thể sẽ chậm)")

# 3. Chạy thử Engine
try:
    model = insightface.app.FaceAnalysis(providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    model.prepare(ctx_id=0, det_size=(640, 640))
    print("✅ Khởi tạo AI Engine: THÀNH CÔNG")
except Exception as e:
    print(f"❌ Lỗi khởi tạo: {e}")