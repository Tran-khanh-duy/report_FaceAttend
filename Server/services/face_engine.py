"""
services/face_engine.py

Luồng xử lý 1 frame:
  Frame 720p/1080p
    → RetinaFace: Detect khuôn mặt + 5 landmarks (Thread-Safe)
    → Crop + Align: Chuẩn hoá về 112×112
    → ArcFace: Trích xuất vector 512 chiều
    → Batch Cosine Similarity: So sánh song song toàn bộ khuôn mặt bằng Ma Trận (Cực nhanh)
    → Kết quả: (student_id, name, score) trong vài milliseconds
"""
import time
import threading
from dataclasses import dataclass
from typing import Optional
import warnings

# Suppress InsightFace FutureWarnings to keep the console clean
warnings.filterwarnings("ignore", category=FutureWarning)

import cv2
import numpy as np
from loguru import logger

# InsightFace — bao gồm cả RetinaFace và ArcFace
try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except Exception as e:
    INSIGHTFACE_AVAILABLE = False
    logger.error(f"⚠️ Lỗi khởi động InsightFace (Có thể do Driver hoặc DLL): {e}")
    logger.warning("Vui lòng cắm sạc Laptop và để chế độ Best Performance.")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ai_config, app_config, anti_spoof_config
from database.models import EmbeddingCache
# Anti-Spoofing Service (Có cơ chế dự phòng nếu Torch lỗi DLL)
try:
    if anti_spoof_config.enabled:
        from services.anti_spoof_service import anti_spoof_service
        if anti_spoof_service and anti_spoof_service.available:
            ANTI_SPOOF_AVAILABLE = True
            logger.success("🚀 Anti-Spoofing đã sẵn sàng và đang hoạt động!")
        else:
            ANTI_SPOOF_AVAILABLE = False
            logger.warning("⚠️ Anti-Spoofing Service khởi tạo thất bại. Kiểm tra log phía trên.")
    else:
        ANTI_SPOOF_AVAILABLE = False
        logger.info("ℹ️ Anti-Spoofing đang bị tắt trong cấu hình.")
except Exception as e:
    if anti_spoof_config.enabled:
        logger.error(f"⚠️ Không thể khởi động Anti-Spoofing (Lỗi hệ thống): {e}")
        logger.warning("Hệ thống sẽ chạy ở chế độ NHẬN DIỆN THƯỜNG (Tắt chống giả mạo).")
    ANTI_SPOOF_AVAILABLE = False



# ─────────────────────────────────────────────
#  Data classes kết quả
# ─────────────────────────────────────────────

@dataclass
class DetectedFace:
    """1 khuôn mặt được phát hiện trong frame."""
    bbox:       np.ndarray      # [x1, y1, x2, y2]
    landmarks:  np.ndarray      # 5 điểm: mắt trái, mắt phải, mũi, miệng trái, miệng phải
    det_score:  float           # Độ tin cậy detection (0.0 - 1.0)
    embedding:  Optional[np.ndarray] = None   # Vector 512D sau khi qua ArcFace


@dataclass
class RecognitionResult:
    """Kết quả nhận diện cho 1 khuôn mặt."""
    # Thông tin khuôn mặt
    bbox:           np.ndarray
    det_score:      float

    # Kết quả nhận diện
    recognized:     bool            # True nếu nhận ra (score >= threshold)
    student_id:     Optional[int]   # None nếu không nhận ra
    student_code:   Optional[str]
    full_name:      Optional[str]
    class_id:       Optional[int]
    class_name:     Optional[str]
    class_code:     Optional[str]
    similarity:     float           # Cosine similarity (0.0 - 1.0)
    
    # Anti-Spoofing
    is_real:        bool = True     # Mặc định là thật nếu không check hoặc lỗi
    spoof_score:    float = 1.0

    # Meta
    process_time_ms: float = 0.0

    @property
    def display_name(self) -> str:
        if self.recognized:
            # Lấy code, ép về string và trim khoảng trắng
            c_code = str(self.class_code or "").strip()
            # Kiểm tra xem có thực sự có ký tự hay không (loại trừ chuỗi "None" nếu DB bị lỗi casting)
            if c_code and c_code.lower() != "none" and c_code.lower() != "null":
                cls = f" - {c_code}"
            else:
                cls = ""
            return f"{self.full_name}{cls} ({self.similarity*100:.1f}%)"
        return f"Khách / Lạ ({self.similarity*100:.1f}%)"

    @property
    def box_color(self) -> tuple:
        """Màu bounding box: xanh lá = nhận ra, đỏ = không nhận ra, cam = spoof."""
        if not self.is_real:
            return (0, 140, 255)  # Orange (BGR)
        return (0, 220, 100) if self.recognized else (60, 60, 220)


# ─────────────────────────────────────────────
#  FaceEngine — Singleton
# ─────────────────────────────────────────────

class FaceEngine:
    """
    Engine nhận dạng khuôn mặt — Singleton, thread-safe cho Multi-Camera.

    Sử dụng:
        engine = FaceEngine.get_instance()
        results, ms = engine.process_frame(frame, cache)
    """
    _instance: Optional["FaceEngine"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "FaceEngine":
        return cls()

    def __init__(self):
        if self._initialized:
            return

        self._app: Optional[FaceAnalysis] = None
        self._model_loaded = False
        
        # Locks
        self._load_lock = threading.Lock()
        
        # NÂNG CẤP 1: Inference Lock bảo vệ GPU khỏi xung đột khi hàng chục Camera gọi vào cùng 1 thời điểm
        self._inference_lock = threading.Lock() 
        
        self._initialized = True

        # Thống kê hiệu suất
        self._stats = {
            "total_frames":     0,
            "total_faces":      0,
            "recognized":       0,
            "avg_time_ms":      0.0,
            "last_time_ms":     0.0,
        }

    # ─── Load model ───────────────────────────

    def load_model(self, force_reload: bool = False) -> bool:
        """Load buffalo_l model lên GPU."""
        if self._model_loaded and not force_reload:
            return True

        if not INSIGHTFACE_AVAILABLE:
            logger.error("InsightFace chưa cài đặt!")
            return False

        with self._load_lock:
            if self._model_loaded and not force_reload:
                return True

            try:
                logger.info(f"Đang load model GPU '{ai_config.model_name}'...")
                t0 = time.perf_counter()

                try:
                    self._app = FaceAnalysis(
                        name=ai_config.model_name,
                        root=str(ai_config.model_pack_dir),
                        providers=ai_config.onnx_providers,
                    )
                    self._app.prepare(
                        ctx_id=ai_config.gpu_ctx_id,
                        det_size=ai_config.det_size,
                    )
                except Exception as e:
                    logger.warning(f"CUDA loading failed ({e}), falling back to CPU...")
                    self._app = FaceAnalysis(
                        name=ai_config.model_name,
                        root=str(ai_config.model_pack_dir),
                        providers=['CPUExecutionProvider'],
                    )
                    self._app.prepare(
                        ctx_id=-1, # Force CPU
                        det_size=ai_config.det_size,
                    )

                elapsed = (time.perf_counter() - t0) * 1000
                self._model_loaded = True

                # Xác định đang dùng GPU hay CPU
                import onnxruntime as ort
                device = ort.get_device()
                logger.success(
                    f"✅ Model '{ai_config.model_name}' đã load xong! "
                    f"Device: {device} | Thời gian: {elapsed:.0f}ms"
                )
                return True

            except Exception as e:
                logger.error(f"Lỗi load model: {e}")
                self._model_loaded = False
                return False

    @property
    def is_ready(self) -> bool:
        return self._model_loaded and self._app is not None

    def unload_model(self):
        """Giải phóng model khỏi GPU/RAM để tránh tràn bộ nhớ."""
        with self._load_lock:
            # Acquire inference lock trước để đảm bảo detect_faces đã xong
            with self._inference_lock:
                if self._app:
                    del self._app
                    self._app = None
            self._model_loaded = False
            
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            logger.info("🗑️ Đã giải phóng AI Model khỏi bộ nhớ (RAM/VRAM).")

    # ─── Detect khuôn mặt ─────────────────────

    def detect_faces(self, frame: np.ndarray) -> list[DetectedFace]:
        """Phát hiện tất cả khuôn mặt trong frame (Thread-Safe).

        [FIX #6] Giảm tối đa thời gian giữ _inference_lock:
        - Chỉ bao quanh lời gọi model GPU thực sự (self._app.get).
        - Bước lọc det_score và tạo DetectedFace chạy NGOÀI lock.
        - Với N camera: thời gian block nhau giảm từ ~50ms xuống ~35ms/frame.
        """
        if not self.is_ready:
            return []

        try:
            # ── Bước 1: Gọi GPU model — giữ lock ngắn nhất có thể ──────────
            with self._inference_lock:
                if self._app is None:   # Re-check sau khi có lock
                    return []
                raw_faces = self._app.get(frame)  # Chỉ GPU call ở đây
            # Lock được giải phóng ngay sau GPU call

            # ── Bước 2: Post-processing NGOÀI lock — không cần GPU ──────────
            result = []
            for f in raw_faces:
                if f.det_score < ai_config.min_face_det_score:
                    continue
                result.append(DetectedFace(
                    bbox=f.bbox.astype(int),
                    landmarks=f.kps,
                    det_score=float(f.det_score),
                    embedding=f.embedding,   # ArcFace embedding 512D
                ))
            return result

        except Exception as e:
            logger.error(f"Lỗi detect_faces: {e}")
            return []



    # ─── NÂNG CẤP 2: Nhận diện HÀNG LOẠT (Batch Processing) ───

    def recognize_batch(
        self,
        faces: list[DetectedFace],
        cache: EmbeddingCache,
        # ╔══════════════════════════════════════════════════════════════════╗
        # ║  KIẾN TRÚC MỚI: Edge Cache Partitioning                        ║
        # ║  cache đã là shard đúng khu vực của Mini PC này.               ║
        # ║  → TUYỆT ĐỐI KHÔNG truyền camera_building / camera_floor vào  ║
        # ║    hàm này. Không có vòng lặp lọc vị trí nào ở đây.           ║
        # ╚══════════════════════════════════════════════════════════════════╝
    ) -> list[RecognitionResult]:
        """
        Nhận diện hàng loạt khuôn mặt bằng phép nhân ma trận thuần NumPy.

        Thiết kế cho kiến trúc **Edge Cache Partitioning**:
        - ``cache`` là file ``.pkl`` nhỏ đã được Server xuất sẵn cho đúng
          khu vực (toà nhà / tầng) của Mini PC này.
        - Không có vòng lặp lọc vị trí, không truyền ``camera_building`` /
          ``camera_floor`` — toàn bộ cache đều là sinh viên hợp lệ.
        - Đảm bảo **one-to-one assignment**: mỗi student_id chỉ được gán cho
          đúng 1 khuôn mặt có cosine score cao nhất; khuôn mặt thua cuộc bị
          xếp vào "Người Lạ".

        Args:
            faces: Danh sách :class:`DetectedFace` đầu ra của ``detect_faces()``.
            cache: :class:`EmbeddingCache` cục bộ (shard) của khu vực này.

        Returns:
            Danh sách :class:`RecognitionResult` theo đúng thứ tự ``faces``.
        """
        if not faces:
            return []

        # ── Guard: cache rỗng → toàn bộ là "Người Lạ" ──────────────────────
        if cache is None or cache.is_empty:
            return [
                RecognitionResult(
                    bbox=face.bbox, det_score=face.det_score,
                    recognized=False, student_id=None, student_code=None,
                    full_name=None, class_id=None, class_name=None, class_code=None,
                    similarity=0.0,
                )
                for face in faces
            ]

        # ── Bước 1: Tách khuôn mặt có embedding hợp lệ, giữ index gốc ──────
        # result_map[i] = RecognitionResult cho faces[i]
        result_map: dict[int, RecognitionResult] = {}

        valid_pairs: list[tuple[int, DetectedFace]] = []  # (orig_idx, face)
        embeddings_list: list[np.ndarray] = []

        for i, face in enumerate(faces):
            if face.embedding is not None:
                valid_pairs.append((i, face))
                embeddings_list.append(face.embedding)
            else:
                # Không trích được embedding → "Người Lạ" ngay lập tức
                result_map[i] = RecognitionResult(
                    bbox=face.bbox, det_score=face.det_score,
                    recognized=False, student_id=None, student_code=None,
                    full_name=None, class_id=None, class_name=None, class_code=None,
                    similarity=0.0,
                )

        # Không có face nào có embedding hợp lệ
        if not embeddings_list:
            return [result_map[i] for i in range(len(faces))]

        # ── Bước 2: Chuẩn hoá L2 + nhân ma trận → cosine scores ─────────────
        # emb_matrix shape: (M, 512)  — M = số face hợp lệ
        emb_matrix: np.ndarray = np.array(embeddings_list, dtype=np.float32)

        norms: np.ndarray = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms[norms < 1e-8] = 1e-8          # tránh chia cho 0
        emb_matrix = emb_matrix / norms     # L2-normalized

        # (M, 512) @ (512, N) → scores_matrix shape (M, N)
        # N = số sinh viên trong cache (nhỏ vì đã shard theo khu vực)
        scores_matrix: np.ndarray = emb_matrix @ cache.embeddings.T

        best_indices: np.ndarray = np.argmax(scores_matrix, axis=1)  # shape (M,)
        best_scores:  np.ndarray = np.max(scores_matrix,  axis=1)    # shape (M,)

        # ── Bước 2b: Tính second-best score để kiểm tra Margin ───────────────
        # Mục đích: nếu best và second_best xấp xỉ nhau → hệ thống đang "bối rối"
        # → từ chối nhận diện dù đã vượt threshold (chặn False Positive).
        #
        # Dùng np.partition thay vì sort toàn bộ → O(N) thay vì O(N log N).
        # Nếu cache chỉ có 1 người → không có second → margin không áp dụng.
        N: int = cache.embeddings.shape[0]
        if N >= 2:
            # partition(-2) đưa phần tử lớn thứ 2 (0-indexed) lên vị trí -2
            second_best_scores: np.ndarray = np.partition(scores_matrix, -2, axis=1)[:, -2]
            # margin[i] = khoảng cách giữa best và runner-up cho face thứ i
            margins: np.ndarray = best_scores - second_best_scores   # shape (M,)
        else:
            # Chỉ 1 người trong cache → không có cơ sở so sánh → margin = ∞
            margins = np.full(len(embeddings_list), np.inf, dtype=np.float32)

        margin_threshold: float = getattr(ai_config, "recognition_margin", 0.08)

        # ── Bước 3: One-to-one assignment + Margin Gate ──────────────────────
        # Điều kiện để 1 face được xét nhận diện (PHẢI THỎA CẢ HAI):
        #   (a) best_score >= recognition_threshold
        #   (b) margin >= recognition_margin  ← kiểm tra chéo mới
        # Khuôn mặt không thỏa → "Người Lạ".
        M: int = len(valid_pairs)
        assigned_face_for_student: dict[int, int] = {}  # student_cache_idx → local_i

        for local_i in range(M):
            score: float  = float(best_scores[local_i])
            margin: float = float(margins[local_i])

            # Cổng kép: threshold + margin
            if score < ai_config.recognition_threshold:
                continue  # Dưới ngưỡng tuyệt đối → bỏ qua
            if margin < margin_threshold:
                # Hệ thống đang bối rối (2 ứng viên quá gần nhau) → từ chối
                logger.debug(
                    f"Margin quá nhỏ ({margin:.3f} < {margin_threshold}) "
                    f"— từ chối nhận diện (score={score:.3f}). Gán Unknown."
                )
                continue

            student_cache_idx: int = int(best_indices[local_i])
            if student_cache_idx not in assigned_face_for_student:
                assigned_face_for_student[student_cache_idx] = local_i
            else:
                # Đã có face tranh giành student này — giữ face có score cao hơn
                prev_local_i: int = assigned_face_for_student[student_cache_idx]
                if score > float(best_scores[prev_local_i]):
                    assigned_face_for_student[student_cache_idx] = local_i

        # ── Bước 4: Xây dựng RecognitionResult theo thứ tự gốc ──────────────
        winning_local_indices: set[int] = set(assigned_face_for_student.values())

        for local_i, (orig_idx, face) in enumerate(valid_pairs):
            student_cache_idx: int = int(best_indices[local_i])
            best_score: float      = float(best_scores[local_i])
            margin: float          = float(margins[local_i])

            is_above_threshold: bool = best_score >= ai_config.recognition_threshold
            is_margin_ok: bool       = margin >= margin_threshold
            is_winner: bool          = local_i in winning_local_indices

            if is_above_threshold and is_margin_ok and is_winner:
                result_map[orig_idx] = RecognitionResult(
                    bbox=face.bbox,
                    det_score=face.det_score,
                    recognized=True,
                    student_id=cache.student_ids[student_cache_idx],
                    student_code=cache.student_codes[student_cache_idx],
                    full_name=cache.full_names[student_cache_idx],
                    class_id=cache.class_ids[student_cache_idx]   if cache.class_ids   else None,
                    class_name=cache.class_names[student_cache_idx] if cache.class_names else None,
                    class_code=cache.class_codes[student_cache_idx] if cache.class_codes else None,
                    similarity=best_score,
                )
            else:
                # Dưới ngưỡng, margin quá nhỏ, hoặc thua one-to-one
                result_map[orig_idx] = RecognitionResult(
                    bbox=face.bbox,
                    det_score=face.det_score,
                    recognized=False,
                    student_id=None, student_code=None, full_name=None,
                    class_id=None, class_name=None, class_code=None,
                    similarity=best_score,
                )

        # Trả về theo đúng thứ tự gốc 0..len(faces)-1
        return [result_map[i] for i in range(len(faces))]

    # ─── Xử lý toàn bộ 1 frame ───────────────

    def process_frame(
        self,
        frame: np.ndarray,
        cache: EmbeddingCache,
    ) -> tuple[list[RecognitionResult], float]:
        t0 = time.perf_counter()

        # 1. Phát hiện tất cả khuôn mặt
        detected = self.detect_faces(frame)
        
        # TỐI ƯU: Sắp xếp theo diện tích từ lớn nhất và giới hạn 4 mặt
        if len(detected) > 1:
            detected.sort(key=lambda df: (df.bbox[2] - df.bbox[0]) * (df.bbox[3] - df.bbox[1]), reverse=True)
        
        max_f = getattr(ai_config, "max_faces_per_frame", 4)
        if len(detected) > max_f:
            detected = detected[:max_f]

        # 2. Nhận diện song song toàn bộ
        t_rec_start = time.perf_counter()
        results = self.recognize_batch(detected, cache)
        
        # Anti-Spoofing check has been moved to AttendanceService to run ONLY during check-in
        for r in results:
            r.is_real = True
            r.spoof_score = 1.0

        # Cập nhật thông số thời gian xử lý cho từng khuôn mặt
        t_rec_elapsed = (time.perf_counter() - t_rec_start) * 1000
        for r in results:
            r.process_time_ms = t_rec_elapsed / len(results) if results else 0.0

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Cập nhật thống kê
        self._stats["total_frames"] += 1
        self._stats["total_faces"]  += len(results)
        self._stats["recognized"]   += sum(1 for r in results if r.recognized)
        self._stats["last_time_ms"]  = elapsed_ms

        # [FIX #15] Exponential Moving Average (α=0.1) thay cumulative average.
        # Tránh float overflow sau hàng triệu frame và nhạy cảm hơn với spike gần đây.
        _alpha = 0.1
        prev_avg = self._stats["avg_time_ms"]
        self._stats["avg_time_ms"] = (
            elapsed_ms if prev_avg == 0.0          # khởi tạo lần đầu
            else _alpha * elapsed_ms + (1.0 - _alpha) * prev_avg
        )

        return results, elapsed_ms

    # ─── Mọi hàm Enroll, Vẽ Box, Thống kê bên dưới được giữ nguyên chuẩn mực ───

    def recognize(self, face: DetectedFace, cache: EmbeddingCache) -> RecognitionResult:
        """Hàm nhận diện đơn lẻ (Giữ lại để tương thích ngược)"""
        return self.recognize_batch([face], cache)[0] if cache else None

    def compute_enrollment_embedding(self, photos: list[np.ndarray]) -> tuple[Optional[np.ndarray], float, int, list]:
        import traceback
        from loguru import logger
        
        if not self.is_ready:
            logger.info("AI Model chưa được load vào GPU/RAM. Đang nạp model...")
            success = self.load_model()
            if not success:
                logger.error("❌ Không thể khởi động AI Model để trích xuất khuôn mặt!")
                return None, 0.0, 0, []

        embeddings = []
        det_scores = []
        valid_photos = []
        
        logger.info(f"👉 Bắt đầu vòng lặp duyệt {len(photos)} ảnh...")
        
        # Kích thước tối đa để detect (320px là kích thước lưới quét tối ưu của model buffalo_s/l)
        # Việc resize đúng về 320 giúp giảm thiểu việc padding/scaling nội bộ của InsightFace -> Tiết kiệm VRAM nhất.
        MAX_DETECT_SIZE = 320

        for i, photo in enumerate(photos):
            # Cố gắng xử lý mỗi ảnh tối đa 2 lần nếu gặp lỗi CUDA OOM
            for attempt in range(2):
                try:
                    # 0. Giải phóng bộ nhớ triệt để
                    import gc
                    gc.collect()
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.synchronize()
                            torch.cuda.empty_cache()
                    except: pass

                    # 1. TRẠM KIỂM TRA HÌNH DÁNG ẢNH
                    logger.info(f"📸 Ảnh {i+1} (Lần {attempt+1}): shape={photo.shape}")

                    # 2. RESIZE XUỐNG ĐỂ TRÁNH CUDA OOM
                    h, w = photo.shape[:2]
                    scale = 1.0
                    if max(h, w) > MAX_DETECT_SIZE:
                        scale = MAX_DETECT_SIZE / max(h, w)
                        detect_frame = cv2.resize(
                            photo,
                            (int(w * scale), int(h * scale)),
                            interpolation=cv2.INTER_AREA,
                        )
                    else:
                        detect_frame = photo

                    faces = self.detect_faces(detect_frame)
                    
                    if not faces:
                        # Nếu không thấy mặt, có thể do resize quá nhỏ hoặc lỗi logic
                        # Ta không retry ở đây mà chuyển sang ảnh tiếp theo
                        if attempt == 0:
                            logger.warning(f"⚠️ AI 'mù' - Không tìm thấy khuôn mặt trong ảnh {i+1}")
                        break

                    # ── TASK 3: Từ chối ảnh có nhiều hơn 1 khuôn mặt ─────────────
                    # Lý do: Nếu background có người khác, tự động chọn "mặt lớn nhất"
                    # có thể vô tình trích xuất embedding của người lạ → làm bẩn Database
                    # → gây False Positive khi nhận diện sau này.
                    # Giải pháp: Yêu cầu người dùng chụp lại ảnh chỉ có 1 mặt rõ ràng.
                    if len(faces) > 1:
                        face_count = len(faces)
                        logger.warning(
                            f"🚫 Ảnh {i+1}: Phát hiện {face_count} khuôn mặt — "
                            f"Từ chối để bảo vệ độ sạch Database. "
                            f"Yêu cầu chụp lại với CHỈ 1 khuôn mặt trong khung hình."
                        )
                        # Raise để vòng lặp ngoài bỏ qua ảnh này (không ghi embedding)
                        raise ValueError(
                            f"MULTI_FACE: Ảnh {i+1} chứa {face_count} khuôn mặt. "
                            f"Chỉ chấp nhận ảnh có đúng 1 khuôn mặt."
                        )
                    # ─────────────────────────────────────────────────────────────

                    # Scale bbox + landmarks trở về kích thước ảnh GỐC để crop chính xác
                    if scale < 1.0:
                        inv = 1.0 / scale
                        for f in faces:
                            f.bbox = (f.bbox * inv).astype(int)
                            if f.landmarks is not None:
                                f.landmarks = f.landmarks * inv

                    # Lấy khuôn mặt lớn nhất
                    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    
                    if face.embedding is not None:
                        embeddings.append(face.embedding.copy())
                        det_scores.append(face.det_score)
                        valid_photos.append(photo.copy())
                        logger.info(f"✅ Ảnh {i+1}: Trích xuất embedding thành công!")
                    else:
                        logger.warning(f"⚠️ Ảnh {i+1}: Thấy mặt nhưng KHÔNG trích xuất được embedding!")
                    
                    # Thành công thì break khỏi vòng lặp attempt
                    # Nghỉ 150ms để ổn định GPU
                    time.sleep(0.15)
                    
                    # Giải phóng các đối tượng tạm
                    del faces, face, detect_frame
                    break
                        
                except Exception as e:
                    err_msg = str(e)
                    if "out of memory" in err_msg.lower() and attempt == 0:
                        logger.error(f"🚨 Lỗi CUDA OOM tại ảnh {i+1}, đang thử lại sau 0.5s...")
                        time.sleep(0.5)
                        continue
                    elif err_msg.startswith("MULTI_FACE:"):
                        # Ảnh bị từ chối vì có nhiều khuôn mặt — không phải lỗi hệ thống
                        # Đã log warning ở trên, chỉ cần break (bỏ qua ảnh, không retry)
                        break
                    else:
                        logger.error(f"❌ VĂNG LỖI TẠI ẢNH {i+1}: {e}")
                        break  # Thoát khỏi retry nếu lỗi khác hoặc đã retry rồi

        if not embeddings: 
            logger.error("❌ KẾT LUẬN: Không có embedding nào được lấy ra từ 15 ảnh!")
            return None, 0.0, 0, []
            
        mean_emb = np.mean(embeddings, axis=0).astype(np.float32)
        norm = np.linalg.norm(mean_emb)
        if norm > 1e-8: mean_emb = mean_emb / norm
        return mean_emb, float(np.mean(det_scores)), len(embeddings), valid_photos

    def get_embedding(self, face_region: np.ndarray) -> "np.ndarray | None":
        if not self.is_ready: return None
        try:
            face_img = cv2.resize(face_region, (112, 112))
            if face_img.ndim == 2: face_img = cv2.cvtColor(face_img, cv2.COLOR_GRAY2BGR)
            faces = self.detect_faces(face_img)
            if faces and faces[0].embedding is not None: return faces[0].embedding
            
            if hasattr(self._app, "models") and self._app.models:
                for model in self._app.models:
                    if hasattr(model, "get_feat"):
                        with self._inference_lock:
                            feat = model.get_feat([face_img])
                        if feat is not None and len(feat) > 0:
                            emb = feat[0].astype(np.float32)
                            norm = np.linalg.norm(emb)
                            if norm > 1e-8: return emb / norm
            return None
        except Exception as e:
            logger.debug(f"get_embedding error: {e}")
            return None

    def find_match(self, embedding: np.ndarray, cache: "EmbeddingCache") -> "RecognitionResult | None":
        if cache is None or cache.is_empty: return None
        dummy_face = DetectedFace(
            bbox=np.array([0, 0, 112, 112]), det_score=1.0, embedding=embedding,
            landmarks=np.zeros((5, 2), dtype=np.float32),
        )
        return self.recognize(dummy_face, cache)

    def draw_results(self, frame: np.ndarray, results: list[RecognitionResult], elapsed_ms: float = 0.0) -> np.ndarray:
        output = frame.copy()
        
        # 1. Vẽ bounding box bằng OpenCV cho nhanh
        for res in results:
            x1, y1, x2, y2 = res.bbox
            color = res.box_color
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
            
        # 2. Vẽ Text tiếng Việt bằng PIL
        try:
            from PIL import Image, ImageDraw, ImageFont
            import os
            
            # Convert BGR to RGB for PIL
            img_pil = Image.fromarray(cv2.cvtColor(output, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)
            
            # Ưu tiên load font Arial để nhận Tiếng Việt (có sẵn trên Windows)
            try:
                # Kích thước font chữ
                font = ImageFont.truetype("arial.ttf", 22)
            except:
                font = ImageFont.load_default()

            for res in results:
                x1, y1, x2, y2 = res.bbox
                label = res.display_name
                
                # Convert BGR (OpenCV) -> RGB (PIL) cho màu nền chữ
                b, g, r = res.box_color
                rgb_color = (r, g, b)
                
                # Tính kích thước chuỗi text
                bbox_text = draw.textbbox((0, 0), label, font=font)
                tw = bbox_text[2] - bbox_text[0]
                th = bbox_text[3] - bbox_text[1]
                pad = 5
                
                # Vẽ nền màu trùng màu box, ở phía trên bounding box
                draw.rectangle(
                    [x1, y1 - th - pad * 2, x1 + tw + pad * 2, y1],
                    fill=rgb_color
                )
                # Vẽ chữ trắng
                draw.text((x1 + pad, y1 - th - pad - 2), label, font=font, fill=(255, 255, 255))

            # Thông số góc trái trên
            info = f"{elapsed_ms:.0f}ms | {len(results)} face(s)"
            draw.text((12, 12), info, font=font, fill=(255, 200, 0)) # Màu vàng cam (RGB)

            # Chuyển ngược lại về BGR
            output = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
            
        except ImportError:
            # Fallback nếu máy không install PIL (dùng OpenCV cũ sẽ bị lỗi dấu hỏi)
            for res in results:
                x1, y1, x2, y2 = res.bbox
                color = res.box_color
                label = res.display_name.encode('ascii', 'ignore').decode('utf-8')
                font, font_scale = cv2.FONT_HERSHEY_SIMPLEX, 0.6
                (lw, lh), _ = cv2.getTextSize(label, font, font_scale, 2)
                pad = 4
                cv2.rectangle(output, (x1, y1 - lh - pad * 2 - 2), (x1 + lw + pad * 2, y1), color, -1)
                cv2.putText(output, label, (x1 + pad, y1 - pad - 2), font, font_scale, (255, 255, 255), 2, cv2.LINE_AA)

            info = f"{elapsed_ms:.0f}ms | {len(results)} face(s)"
            cv2.putText(output, info, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2, cv2.LINE_AA)
            
        except Exception as e:
            logger.error(f"Lỗi vẽ label bằng PIL: {e}")

        return output

    def get_stats(self) -> dict:
        s = self._stats.copy()
        s["recognition_rate"] = (s["recognized"] / s["total_faces"] * 100) if s["total_faces"] > 0 else 0.0
        return s

    def reset_stats(self):
        for k in self._stats: self._stats[k] = 0 if isinstance(self._stats[k], int) else 0.0

# ─────────────────────────────────────────────
#  Singleton instance
# ─────────────────────────────────────────────
face_engine = FaceEngine.get_instance()