"""
anti_spoof_service.py
─────────────────────────────────────────────────────────────
Dịch vụ chống giả mạo khuôn mặt (Anti-Spoofing).
Tự động tìm thư mục mã nguồn Silent-Face-Anti-Spoofing-master
và load model theo kiểu lazy (chỉ load khi cần).
─────────────────────────────────────────────────────────────
"""
import os
import sys
import cv2
import numpy as np
from loguru import logger
from pathlib import Path

# ─── Import cấu hình ──────────────────────────────────────
from config import anti_spoof_config

# ─── Kiểm tra PyTorch ─────────────────────────────────────
TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    logger.warning("Thư viện 'torch' không khả dụng. Tính năng Anti-Spoofing sẽ bị TẮT.")

# ─── Tự động tìm thư mục Silent-Face-Anti-Spoofing-master ──
def _find_anti_spoof_root() -> Path | None:
    """Tìm thư mục gốc của Silent-Face-Anti-Spoofing-master."""
    # Thư mục của file hiện tại (Server/services)
    current = Path(__file__).resolve()
    candidates = [
        # Nếu đặt trong cùng thư mục Server
        current.parent.parent / "Silent-Face-Anti-Spoofing-master",
        # Đặt trong MINI_PC (cùng root dự án)
        current.parent.parent.parent / "MINI_PC" / "Silent-Face-Anti-Spoofing-master",
        # Lùi thêm 1 cấp
        current.parent.parent.parent.parent / "MINI_PC" / "Silent-Face-Anti-Spoofing-master",
    ]
    for path in candidates:
        if path.exists() and (path / "src").exists():
            return path
    return None

ANTI_SPOOF_ROOT = _find_anti_spoof_root()

# ─── Thêm vào sys.path và import các module cần thiết ──────
AntiSpoofPredict = None
CropImage        = None
parse_model_name = None
get_kernel       = None
MODEL_MAPPING    = None

if ANTI_SPOOF_ROOT is not None:
    logger.info(f"✅ Tìm thấy Anti-Spoofing tại: {ANTI_SPOOF_ROOT}")
    root_str = str(ANTI_SPOOF_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    if TORCH_AVAILABLE and anti_spoof_config.enabled:
        try:
            from src.anti_spoof_predict import AntiSpoofPredict, MODEL_MAPPING
            from src.generate_patches import CropImage
            from src.utility import parse_model_name, get_kernel
            logger.success("🚀 Đã import module Anti-Spoofing thành công!")
        except Exception as e:
            logger.error(f"❌ Import Anti-Spoofing thất bại: {e}")
else:
    logger.warning("⚠️ Không tìm thấy thư mục Silent-Face-Anti-Spoofing-master!")


# ─── AntiSpoofService ─────────────────────────────────────
class AntiSpoofService:
    """
    Service chống giả mạo khuôn mặt.
    - Lazy loading: model chỉ được nạp vào RAM khi gọi is_real() lần đầu.
    - Singleton: dùng chung 1 instance trong toàn ứng dụng.
    """
    _instance = None

    @classmethod
    def get_instance(cls) -> "AntiSpoofService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._models_cache: dict = {}
        self._models_loaded: bool = False
        self.image_cropper = None
        self._available = False

        if not TORCH_AVAILABLE:
            logger.warning("AntiSpoofService: Torch không khả dụng.")
            return
        if not anti_spoof_config.enabled:
            logger.info("AntiSpoofService: Đã tắt theo cấu hình.")
            return
        if AntiSpoofPredict is None or CropImage is None:
            logger.error("AntiSpoofService: Module src chưa được import, không thể hoạt động.")
            return

        # Xác định thư mục chứa model
        self._model_dir = self._resolve_model_dir()
        if not self._model_dir:
            logger.error("AntiSpoofService: Không tìm được thư mục chứa file .pth model!")
            return

        # Khởi tạo image_cropper
        try:
            self.image_cropper = CropImage()
        except Exception as e:
            logger.error(f"AntiSpoofService: Lỗi khởi tạo CropImage: {e}")
            return

        # Xác định device
        try:
            self._device = torch.device(
                f"cuda:{anti_spoof_config.device_id}"
                if torch.cuda.is_available() and anti_spoof_config.device_id >= 0
                else "cpu"
            )
        except Exception:
            self._device = torch.device("cpu")

        self._available = True
        logger.success(f"AntiSpoofService: Khởi tạo thành công | Model dir: {self._model_dir} | Device: {self._device}")

    def _resolve_model_dir(self) -> Path | None:
        """Tìm thư mục chứa file model .pth."""
        # Đường dẫn từ config
        p = Path(anti_spoof_config.model_dir)
        if p.exists():
            return p

        # Fallback: tìm trong Silent-Face-Anti-Spoofing-master
        if ANTI_SPOOF_ROOT:
            fallback = ANTI_SPOOF_ROOT / "resources" / "anti_spoof_models"
            if fallback.exists():
                logger.info(f"📂 Model dir fallback: {fallback}")
                return fallback

        return None

    def _load_all_models(self):
        """Nạp tất cả file .pth vào memory (gọi 1 lần duy nhất)."""
        if not self._available:
            return

        model_files = [f for f in os.listdir(self._model_dir) if f.endswith(".pth")]
        logger.info(f"🔍 Quét model Anti-Spoofing: {self._model_dir} ({len(model_files)} file)")

        for model_name in model_files:
            try:
                model_path = os.path.join(self._model_dir, model_name)
                h_input, w_input, model_type, scale = parse_model_name(model_name)
                kernel_size = get_kernel(h_input, w_input)

                model = MODEL_MAPPING[model_type](conv6_kernel=kernel_size).to(self._device)
                state_dict = torch.load(model_path, map_location=self._device, weights_only=False)

                # Xử lý model được train bằng DataParallel
                first_key = next(iter(state_dict))
                if first_key.startswith("module."):
                    from collections import OrderedDict
                    state_dict = OrderedDict((k[7:], v) for k, v in state_dict.items())

                model.load_state_dict(state_dict)
                model.eval()

                self._models_cache[model_name] = {
                    "model": model,
                    "h_input": h_input,
                    "w_input": w_input,
                    "scale": scale,
                }
                logger.info(f"✅ Đã nạp: {model_name}")
            except Exception as e:
                logger.error(f"❌ Lỗi nạp {model_name}: {e}")

        if self._models_cache:
            logger.success(f"🎉 Anti-Spoofing sẵn sàng với {len(self._models_cache)} model!")
        else:
            logger.error("❌ Không nạp được model nào!")

    def is_real(self, frame: np.ndarray, bbox_xyxy) -> tuple[bool, float]:
        """
        Kiểm tra khuôn mặt có phải người thật hay không.
        Trả về (is_real: bool, real_score: float).
        Mặc định True nếu Anti-Spoofing không khả dụng.
        """
        if not self._available:
            return True, 1.0

        # Lazy load
        if not self._models_loaded:
            self._load_all_models()
            self._models_loaded = True

        if not self._models_cache:
            return True, 1.0

        try:
            x1, y1, x2, y2 = [int(v) for v in bbox_xyxy]
            bbox_xywh = [x1, y1, max(1, x2 - x1), max(1, y2 - y1)]

            prediction = np.zeros((1, 3))

            for m_info in self._models_cache.values():
                param = {
                    "org_img": frame,
                    "bbox": bbox_xywh,
                    "scale": m_info["scale"],
                    "out_w": m_info["w_input"],
                    "out_h": m_info["h_input"],
                    "crop": m_info["scale"] is not None,
                }
                img = self.image_cropper.crop(**param)

                # Normalize: chuyển sang float [0, 1]
                img_tensor = torch.from_numpy(img.transpose((2, 0, 1))).float()
                img_tensor = img_tensor.unsqueeze(0).to(self._device)

                with torch.no_grad():
                    result = m_info["model"](img_tensor)
                    result = F.softmax(result, dim=1).cpu().numpy()
                    prediction += result

            # label 1 = Real Face
            n = len(self._models_cache)
            real_score = float(prediction[0][1] / n)
            is_real = real_score >= anti_spoof_config.threshold

            return is_real, real_score

        except Exception as e:
            logger.error(f"❌ Lỗi runtime Anti-Spoofing: {e}")
            return True, 1.0

    @property
    def available(self) -> bool:
        return self._available

    @property
    def models_cache(self) -> dict:
        return self._models_cache


# ─── Singleton instance ────────────────────────────────────
anti_spoof_service = AntiSpoofService.get_instance()
