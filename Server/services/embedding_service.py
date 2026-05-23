import pickle
import threading
import time
import numpy as np
try:
    import faiss
except ImportError:
    faiss = None
    
from loguru import logger
from typing import Optional, Tuple

import sys
from pathlib import Path
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.repositories import embedding_repo
from core.state_manager import state_manager
from database.models import EmbeddingCache

class EmbeddingManager:
    """
    Service chuyên trách xử lý Nhận diện khuôn mặt (Embeddings).
    - Quản lý FAISS index (Hoặc Fallback sang Numpy Matrix)
    - Lazy loading từ Database
    - Thread-safe cosine search
    """
    _instance: Optional["EmbeddingManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self._rlock = threading.RLock()
        
        self.faiss_index = None
        self.cache = EmbeddingCache()
        self._last_reload = 0.0
        self._embedding_version = 0
        self._initialized = True

    def load(self) -> bool:
        """Tải toàn bộ Embeddings từ DB và build FAISS Index."""
        logger.info("Đang build Embedding Index từ Database...")
        t0 = time.perf_counter()
        
        try:
            new_cache = embedding_repo.load_all_to_cache()
            
            with self._rlock:
                self.cache = new_cache
                
                if not new_cache.is_empty:
                    # Chẩn hóa vector (Cosine Similarity -> Inner Product)
                    norms = np.linalg.norm(new_cache.embeddings, axis=1, keepdims=True)
                    norms[norms == 0] = 1e-8
                    normalized_embeddings = new_cache.embeddings / norms
                    
                    if faiss is not None:
                        # Khởi tạo FAISS IndexFlatIP (Inner Product)
                        d = normalized_embeddings.shape[1]
                        self.faiss_index = faiss.IndexFlatIP(d)
                        self.faiss_index.add(normalized_embeddings.astype(np.float32))
                    else:
                        # Fallback sang Numpy Matrix lưu trực tiếp
                        self.faiss_index = normalized_embeddings.astype(np.float32)
                else:
                    self.faiss_index = None

                self._last_reload = time.time()
                # Tăng version trên Redis
                self._embedding_version, _ = state_manager.increment_embedding_version()

                # [FIX #9 HOOK] Xoa cache filter Redis khi co hoc vien moi
                # De _get_valid_student_ids() va _get_vip_student_ids() tra ket qua moi
                try:
                    from api.attendance_routes import invalidate_student_filter_cache
                    invalidate_student_filter_cache()
                except Exception:
                    pass  # Khong lam hong qua trinh load embedding neu hook bi loi

            elapsed = (time.perf_counter() - t0) * 1000
            if new_cache.is_empty:
                logger.warning("Embedding Index: Database trống!")
            else:
                engine = "FAISS" if faiss is not None else "NUMPY"
                logger.success(f"✅ {engine} Index build thành công: {new_cache.size} vectors | {elapsed:.1f}ms")
            
            return True
        except Exception as e:
            logger.error(f"Lỗi build Index: {e}")
            return False

    def reload(self):
        """Force reload."""
        self.load()

    def search(self, incoming_vector: np.ndarray, top_k: int = 1, valid_ids: list[int] = None) -> Tuple[float, int]:
        """
        Tim kiem khuon mat bang FAISS / Numpy.
        Tra ve (Best Score, Real Index). Trang thai rong -> return (-1.0, -1)

        valid_ids semantics:
        - None : khong filter, tim toan bo cache.
        - []   : filter active nhung KHONG co SV hop le -> tra (-1.0, -1) ngay lap tuc.
        - [id1, id2, ...]: chi tim trong danh sach nay.
        """
        with self._rlock:
            if self.faiss_index is None or self.cache.is_empty:
                return -1.0, -1

            # Guard: valid_ids duoc truyen vao nhung rong hoan toan
            # -> khong co ai hop le -> khong can tinh toan -> tra -1 ngay
            if valid_ids is not None and len(valid_ids) == 0:
                return -1.0, -1

            # Chuan hoa vector dau vao
            emb = incoming_vector.astype(np.float32).flatten()
            norm = np.linalg.norm(emb)
            if norm < 1e-8:
                return -1.0, -1
            emb = (emb / norm).reshape(1, -1)

            if faiss is not None:
                # FAISS Mach tim kiem
                k = min(self.cache.size, top_k if valid_ids is None else self.cache.size)
                distances, indices = self.faiss_index.search(emb, k)
                
                if valid_ids is not None:
                    # Loc thu cong FAISS results theo valid_ids
                    valid_set = set(valid_ids)  # O(1) lookup
                    for idx_rank in range(k):
                        real_idx = indices[0][idx_rank]
                        if self.cache.student_ids[real_idx] in valid_set:
                            return float(distances[0][idx_rank]), real_idx
                    return -1.0, -1

                return float(distances[0][0]), indices[0][0]
            else:
                # Numpy Mach tim kiem
                similarities = self.faiss_index @ emb.T
                similarities = similarities.flatten()
                
                if valid_ids is not None:
                    valid_set = set(valid_ids)  # O(1) lookup
                    for i in range(self.cache.size):
                        if self.cache.student_ids[i] not in valid_set:
                            similarities[i] = -1.0

                best_idx   = int(np.argmax(similarities))
                best_score = float(similarities[best_idx])

                # Guard: neu tat ca deu bi mask (-1.0) thi tra -1
                if best_score < 0:
                    return -1.0, -1

                return best_score, best_idx
            
    def get_student_info(self, idx: int) -> dict:
        """Lấy thông tin học viên dựa vào index."""
        with self._rlock:
            if idx < 0 or idx >= self.cache.size:
                return {}
            return {
                "student_id": self.cache.student_ids[idx],
                "student_code": self.cache.student_codes[idx],
                "full_name": self.cache.full_names[idx],
                "class_name": self.cache.class_names[idx],
                "class_id": self.cache.class_ids[idx],
            }

    def get_all_embeddings(self) -> dict:
        """Trả về toàn bộ embeddings (dùng cho API sync xuống Mini PC)."""
        with self._rlock:
            return {
                "student_ids":   self.cache.student_ids,
                "student_codes": self.cache.student_codes,
                "full_names":    self.cache.full_names,
                "class_names":   self.cache.class_names,
                "class_ids":     self.cache.class_ids,
                "class_codes":   self.cache.class_codes,   # ← fix: thêm class_codes
                "embeddings":    self.cache.embeddings,
            }

    @property
    def size(self):
        with self._rlock:
            return self.cache.size

    @property
    def version(self):
        return self._embedding_version

embedding_service = EmbeddingManager()


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 1 — Edge Cache Partitioning: Cắt mảnh Database theo Toà nhà / Tầng
# ─────────────────────────────────────────────────────────────────────────────

def export_local_cache(
    global_cache: EmbeddingCache,
    target_building: str,
    target_floor: str,
    save_path: str,
) -> bool:
    """
    Trích xuất (shard) một EmbeddingCache nhỏ từ global_cache,
    chỉ chứa sinh viên thuộc đúng ``target_building`` / ``target_floor``.

    File .pkl được lưu tại ``save_path`` và Mini PC ở khu vực đó sẽ load
    đúng file này — không cần truyền camera_building / camera_floor khi
    nhận diện nữa.

    Args:
        global_cache:     EmbeddingCache toàn bộ hệ thống (đã load từ DB).
        target_building:  Mã toà nhà cần lọc (ví dụ: ``"KTXE4"``).
        target_floor:     Tầng cần lọc (ví dụ: ``"2"``).
        save_path:        Đường dẫn file đầu ra, ví dụ:
                          ``"local_cache/embeddings_KTXE4_Tang2.pkl"``.

    Returns:
        ``True`` nếu xuất thành công, ``False`` nếu không có sinh viên nào
        khớp điều kiện hoặc xảy ra lỗi.
    """
    if global_cache is None or global_cache.is_empty:
        logger.warning(
            f"export_local_cache: global_cache rỗng — "
            f"không thể xuất shard [{target_building}/{target_floor}]."
        )
        return False

    if global_cache.embeddings is None:
        logger.error("export_local_cache: global_cache.embeddings là None.")
        return False

    tb = target_building.strip()
    tf = target_floor.strip()

    # ── Bước 1: Thu thập các index thoả điều kiện ──────────────────────────
    # NOTE: EmbeddingCache hiện tại không lưu buildings/floors trực tiếp.
    # Trường class_codes thường chứa mã lớp gắn với khu vực (ví dụ: "KTXE4-T2").
    # Nếu bạn bổ sung field buildings/floors vào EmbeddingCache, hãy thay
    # phần kiểm tra dưới đây bằng:
    #   str(global_cache.buildings[i]).strip() == tb
    #   str(global_cache.floors[i]).strip()    == tf
    valid_indices: list[int] = []

    has_buildings = hasattr(global_cache, "buildings") and global_cache.buildings
    has_floors    = hasattr(global_cache, "floors")    and global_cache.floors

    for i in range(global_cache.size):
        if has_buildings and has_floors:
            # Cách chính tắc — dùng field buildings / floors
            match_building = str(global_cache.buildings[i]).strip() == tb
            match_floor    = str(global_cache.floors[i]).strip()    == tf
        else:
            # Fallback — lọc qua class_codes chứa mã khu vực
            # (Ví dụ class_code = "KTXE4" hoặc "KTXE4-T2")
            code = str(global_cache.class_codes[i]).strip() if global_cache.class_codes else ""
            match_building = tb.lower() in code.lower()
            match_floor    = tf.lower() in code.lower()

        if match_building and match_floor:
            valid_indices.append(i)

    if not valid_indices:
        logger.warning(
            f"export_local_cache: Không tìm thấy sinh viên nào thuộc "
            f"[{target_building} / Tầng {target_floor}]. File sẽ không được tạo."
        )
        return False

    # ── Bước 2: Tạo local_cache theo valid_indices ─────────────────────────
    local_cache = EmbeddingCache()

    for idx in valid_indices:
        local_cache.student_ids.append(global_cache.student_ids[idx])
        local_cache.student_codes.append(global_cache.student_codes[idx])
        local_cache.full_names.append(global_cache.full_names[idx])
        local_cache.class_ids.append(global_cache.class_ids[idx]   if global_cache.class_ids   else None)
        local_cache.class_names.append(global_cache.class_names[idx] if global_cache.class_names else None)
        local_cache.class_codes.append(global_cache.class_codes[idx] if global_cache.class_codes else None)
        # Sao chép thêm fields buildings/floors nếu model đã được mở rộng
        if has_buildings:
            if not hasattr(local_cache, "buildings"):
                local_cache.buildings = []          # type: ignore[attr-defined]
            local_cache.buildings.append(global_cache.buildings[idx])   # type: ignore[attr-defined]
        if has_floors:
            if not hasattr(local_cache, "floors"):
                local_cache.floors = []             # type: ignore[attr-defined]
            local_cache.floors.append(global_cache.floors[idx])         # type: ignore[attr-defined]

    # ── Bước 3: Cắt ma trận Numpy bằng fancy indexing ──────────────────────
    # Quan trọng: .copy() để tách bộ nhớ hoàn toàn khỏi mảng gốc
    idx_array = np.array(valid_indices, dtype=np.int64)
    local_cache.embeddings = global_cache.embeddings[idx_array].copy()

    # ── Bước 4: Lưu local_cache ra đĩa ────────────────────────────────────
    try:
        import pathlib
        pathlib.Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, "wb") as fh:
            pickle.dump(local_cache, fh, protocol=pickle.HIGHEST_PROTOCOL)

        logger.success(
            f"✅ export_local_cache: Đã xuất {local_cache.size} sinh viên "
            f"[{target_building} / Tầng {target_floor}] → '{save_path}'"
        )
        return True

    except Exception as exc:
        logger.error(f"export_local_cache: Lỗi ghi file '{save_path}': {exc}")
        return False
