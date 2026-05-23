"""
MINI_PC/local_cache/embedding_sync.py

Kiến trúc mới: Edge Cache Partitioning
───────────────────────────────────────
Server xuất các file .pkl nhỏ đã phân mảnh theo khu vực:
    embeddings_KTXE4_Tang2.pkl   ← chỉ chứa SV tầng 2, toà KTXE4
    embeddings_KTXD1_Tang3.pkl   ← chỉ chứa SV tầng 3, toà KTXD1
    ...

Mini PC tại khu vực nào chỉ load đúng file .pkl đó lên RAM.
FaceEngine khi nhận diện không cần quan tâm building/floor nữa.
"""
import os
import pickle
import threading
from typing import Optional
from loguru import logger
from pathlib import Path

import sys
# MINI_PC/ — để tìm thấy config, edge_client, ...
_MINI_PC_DIR  = Path(__file__).parent.parent
# Server/ — EmbeddingCache sống ở Server/database/models.py
_SERVER_DIR   = _MINI_PC_DIR.parent / "Server"
for _p in [str(_MINI_PC_DIR), str(_SERVER_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from database.models import EmbeddingCache   # type: ignore[import]
except ImportError:
    # Fallback: tự định nghĩa dataclass tối giản nếu Server/ không mount được
    # (dùng khi chạy unit-test hoặc môi trường chỉ có MINI_PC/)
    from dataclasses import dataclass, field
    import numpy as np
    @dataclass
    class EmbeddingCache:          # type: ignore[no-redef]
        student_ids:   list = field(default_factory=list)
        student_codes: list = field(default_factory=list)
        full_names:    list = field(default_factory=list)
        class_ids:     list = field(default_factory=list)
        class_names:   list = field(default_factory=list)
        class_codes:   list = field(default_factory=list)
        embeddings:    object = None
        @property
        def size(self) -> int: return len(self.student_ids)
        @property
        def is_empty(self) -> bool: return self.size == 0


CACHE_DIR = Path(__file__).parent
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 3 — Hàm Load cache cục bộ từ file .pkl (Edge Cache Partitioning)
# ─────────────────────────────────────────────────────────────────────────────

def load_cache(filepath: str) -> Optional[EmbeddingCache]:
    """
    Load một EmbeddingCache đã được phân mảnh (shard) từ file ``.pkl``.

    File này được Server xuất bằng ``export_local_cache()`` và chỉ chứa
    đúng các sinh viên của khu vực Mini PC này (toà nhà / tầng).
    FaceEngine sẽ dùng trực tiếp cache này mà không cần bất kỳ bước lọc
    vị trí nào thêm.

    Args:
        filepath: Đường dẫn tuyệt đối hoặc tương đối tới file ``.pkl``,
                  ví dụ: ``"local_cache/embeddings_KTXE4_Tang2.pkl"``.

    Returns:
        :class:`EmbeddingCache` nếu load thành công, ``None`` nếu file
        không tồn tại hoặc dữ liệu bị lỗi / sai kiểu.
    """
    path = Path(filepath)

    if not path.exists():
        logger.error(
            f"load_cache: File không tồn tại → '{filepath}'. "
            f"Hãy chắc chắn Server đã xuất shard cho khu vực này."
        )
        return None

    try:
        with open(path, "rb") as fh:
            obj = pickle.load(fh)

        # Đảm bảo đúng kiểu dữ liệu
        if not isinstance(obj, EmbeddingCache):
            logger.error(
                f"load_cache: Object trong '{filepath}' không phải EmbeddingCache "
                f"(nhận được: {type(obj).__name__}). File có thể bị lỗi."
            )
            return None

        logger.success(
            f"✅ load_cache: Đã load {obj.size} sinh viên từ '{path.name}' "
            f"(embeddings shape: {obj.embeddings.shape if obj.embeddings is not None else 'None'})"
        )
        return obj

    except (pickle.UnpicklingError, EOFError, ValueError) as exc:
        logger.error(f"load_cache: File .pkl bị hỏng hoặc không đọc được '{filepath}': {exc}")
        return None
    except Exception as exc:
        logger.error(f"load_cache: Lỗi không xác định khi đọc '{filepath}': {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Lớp EmbeddingSyncManager (giữ lại để tương thích ngược với code cũ)
# ─────────────────────────────────────────────────────────────────────────────

class EmbeddingSyncManager:
    """
    Quản lý Đồng bộ và Lưu trữ Offline cho Embeddings (.pkl).

    .. deprecated::
        Trong kiến trúc Edge Cache Partitioning, hãy ưu tiên dùng hàm
        ``load_cache(filepath)`` thay vì ``load_cache(camera_id)``.
        Class này được giữ lại để không làm vỡ code cũ.
    """
    def __init__(self):
        self._lock = threading.Lock()

    def _get_cache_path(self, camera_id: str) -> Path:
        safe_name = "".join([c for c in str(camera_id) if c.isalnum() or c in ('_', '-')]).rstrip()
        if not safe_name:
            safe_name = "default"
        return CACHE_DIR / f"embeddings_{safe_name}.pkl"

    def save_cache(self, camera_id: str, cache_obj: EmbeddingCache, version: int) -> None:
        """Lưu EmbeddingCache xuống đĩa cứng (.pkl) theo camera_id."""
        path = self._get_cache_path(camera_id)
        with self._lock:
            try:
                data = {"version": version, "cache": cache_obj}
                with open(path, "wb") as fh:
                    pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
                logger.info(
                    f"💾 Đã lưu Offline Embeddings (Version {version}) cho Camera {camera_id}"
                )
            except Exception as exc:
                logger.error(f"Lỗi lưu pkl cache {camera_id}: {exc}")

    def load_cache_by_camera(self, camera_id: str) -> tuple[Optional[EmbeddingCache], int]:
        """
        Tải EmbeddingCache từ đĩa cứng theo camera_id.

        Returns:
            ``(CacheObject, Version)`` — ``(None, 0)`` nếu không tìm thấy.
        """
        path = self._get_cache_path(camera_id)
        with self._lock:
            if not path.exists():
                return None, 0
            try:
                with open(path, "rb") as fh:
                    data = pickle.load(fh)
                version: int              = data.get("version", 0)
                cache_obj: Optional[EmbeddingCache] = data.get("cache")
                logger.success(
                    f"📂 Đã tải Offline Embeddings (Version {version}) "
                    f"cho Camera {camera_id} từ đĩa."
                )
                return cache_obj, version
            except Exception as exc:
                logger.error(f"Lỗi đọc pkl cache {camera_id}: {exc}")
                return None, 0


embedding_sync = EmbeddingSyncManager()

