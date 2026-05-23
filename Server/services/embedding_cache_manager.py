"""
services/embedding_cache_manager.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quản lý cache embeddings trong RAM.

Tại sao cần cache?
  - 1000 học viên × 512 float32 = ~2MB → hoàn toàn vừa trong RAM
  - Tìm kiếm trên RAM (numpy matrix): ~0.2ms
  - Tìm kiếm trên DB mỗi lần: ~10-50ms
  → Cache nhanh hơn 50-250 lần!

Hot reload:
  - Khi thêm học viên mới → reload cache không cần restart app
  - Dùng RLock để thread-safe trong khi reload
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import threading
import time
from typing import Optional
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.models import EmbeddingCache
from database.repositories import embedding_repo


class EmbeddingCacheManager:
    """
    Singleton quản lý cache embeddings.
    Thread-safe: nhiều camera thread cùng đọc cùng lúc an toàn.
    """
    _instance: Optional["EmbeddingCacheManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        # RLock cho phép cùng thread acquire nhiều lần (tránh deadlock)
        self._rlock = threading.RLock()
        self._cache: EmbeddingCache = EmbeddingCache()
        self._last_reload: float = 0.0
        self._reload_count: int = 0
        self._initialized = True

    # ─── Load / Reload ────────────────────────

    def load(self) -> bool:
        """
        Load toàn bộ embeddings từ DB vào RAM.
        Gọi 1 lần khi khởi động, hoặc sau khi thêm học viên mới.
        """
        logger.info("Đang load embeddings từ DB vào RAM cache...")
        t0 = time.perf_counter()

        try:
            new_cache = embedding_repo.load_all_to_cache()
            elapsed = (time.perf_counter() - t0) * 1000

            with self._rlock:
                self._cache = new_cache
                self._last_reload = time.time()
                self._reload_count += 1

            if new_cache.is_empty:
                logger.warning(
                    "Cache rỗng! Chưa có học viên nào được đăng ký khuôn mặt.\n"
                    "→ Vào tab 'Đăng ký học viên' để thêm học viên."
                )
            else:
                logger.success(
                    f"✅ Cache loaded: {new_cache.size} học viên | "
                    f"Matrix shape: {new_cache.embeddings.shape} | "
                    f"Thời gian: {elapsed:.1f}ms"
                )
            return True

        except Exception as e:
            logger.error(f"Lỗi load cache: {e}")
            return False

    def reload_after_enrollment(self, student_id: int = None):
        """
        Hot reload sau khi đăng ký học viên mới.
        Không restart app, không dừng camera.
        """
        name = f"student_id={student_id}" if student_id else "all"
        logger.info(f"Hot reload cache sau khi thêm {name}...")
        self.load()

    # ─── Đọc cache (thread-safe) ──────────────

    def get_cache(self) -> EmbeddingCache:
        """Lấy cache hiện tại — safe để dùng trong nhiều thread."""
        with self._rlock:
            return self._cache

    @property
    def size(self) -> int:
        with self._rlock:
            return self._cache.size

    @property
    def is_empty(self) -> bool:
        with self._rlock:
            return self._cache.is_empty

    # ─── Thêm/Xóa học viên vào cache ngay lập tức ─

    def add_student_to_cache(self, student_id: int, student_code: str,
                              full_name: str, class_id, class_name: str,
                              embedding, class_code: str = "") -> bool:
        """
        Thêm 1 học viên vào cache mà không cần reload toàn bộ.
        """
        import numpy as np
        try:
            emb = embedding.astype(np.float32)
            norm = np.linalg.norm(emb)
            if norm > 1e-8:
                emb = emb / norm

            with self._rlock:
                self._cache.student_ids.append(student_id)
                self._cache.student_codes.append(student_code)
                self._cache.full_names.append(full_name)
                self._cache.class_ids.append(class_id)
                self._cache.class_names.append(class_name)
                # [BUG FIX] Thiếu class_codes → index lệch khi Mini PC pull xuống
                self._cache.class_codes.append(class_code)

                if self._cache.embeddings is None:
                    # Tạo array mới hoàn toàn
                    self._cache.embeddings = emb.reshape(1, -1).copy()
                else:
                    # Tạo array mới (KHÔNG mutate array cũ)
                    new_embeddings = np.empty(
                        (self._cache.embeddings.shape[0] + 1, emb.shape[0]),
                        dtype=np.float32
                    )
                    new_embeddings[:-1] = self._cache.embeddings
                    new_embeddings[-1]  = emb
                    self._cache.embeddings = new_embeddings  # atomic replace

            logger.info(f"Thêm vào cache: [{student_code}] {full_name} ({class_name}) | Cache size: {self.size}")
            return True
        except Exception as e:
            logger.error(f"Lỗi add_student_to_cache: {e}")
            return False

    def remove_student_from_cache(self, student_id: int) -> bool:
        """Xóa học viên khỏi RAM cache ngay lập tức (thread-safe)."""
        import numpy as np
        try:
            with self._rlock:
                if student_id not in self._cache.student_ids:
                    logger.debug(f"remove_student_from_cache: student_id={student_id} không có trong cache (đã bị xóa trước đó?)")
                    return False

                idx = self._cache.student_ids.index(student_id)

                # Xóa khỏi tất cả các list metadata — PHẢI đồng bộ để tránh lệch index
                self._cache.student_ids.pop(idx)
                self._cache.student_codes.pop(idx)
                self._cache.full_names.pop(idx)
                self._cache.class_ids.pop(idx)
                self._cache.class_names.pop(idx)
                # [BUG FIX] class_codes trước đây BỊ BỎ SÓT → index lệch giữa các list
                if self._cache.class_codes and idx < len(self._cache.class_codes):
                    self._cache.class_codes.pop(idx)

                # Xóa hàng tương ứng khỏi numpy matrix
                if self._cache.embeddings is not None:
                    self._cache.embeddings = np.delete(self._cache.embeddings, idx, axis=0)
                    if self._cache.embeddings.shape[0] == 0:
                        self._cache.embeddings = None

            logger.info(f"Đã xóa student_id={student_id} khỏi RAM cache. Size còn lại: {self.size}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi xóa học viên khỏi cache: {e}")
            return False

    # ─── Thông tin debug ──────────────────────

    def get_info(self) -> dict:
        import datetime
        with self._rlock:
            return {
                "size":         self._cache.size,
                "shape":        str(self._cache.embeddings.shape) if self._cache.embeddings is not None else "None",
                "last_reload":  datetime.datetime.fromtimestamp(self._last_reload).strftime("%H:%M:%S") if self._last_reload else "Chưa load",
                "reload_count": self._reload_count,
                "memory_mb":    round(self._cache.embeddings.nbytes / 1024 / 1024, 3) if self._cache.embeddings is not None else 0,
            }


# Singleton instance
cache_manager = EmbeddingCacheManager()
