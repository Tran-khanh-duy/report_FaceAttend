"""
utils/pkl_export.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Xuất file .pkl cho MINI_PC local_cache sau khi có học viên mới.

Vì Server và MINI_PC chạy trên CÙNG một máy (dev/test setup),
Server có thể ghi thẳng vào MINI_PC/local_cache/*.pkl.

Luồng:
  1. POST /api/reload-cache → embedding_service.reload() (Server RAM)
  2. → export_all_camera_pkls()  (cập nhật file .pkl cho MINI_PC)
  3. MINI_PC đọc lại .pkl → nhận diện được học viên mới
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import pickle
import threading
import numpy as np
from pathlib import Path
from loguru import logger
from typing import Optional

import sys
_SERVER_DIR = Path(__file__).parent.parent
_MINI_PC_DIR = _SERVER_DIR.parent / "MINI_PC"
_LOCAL_CACHE_DIR = _MINI_PC_DIR / "local_cache"

# Đảm bảo thư mục tồn tại
_LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Thêm Server vào sys.path để import models
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from database.models import EmbeddingCache
from database.repositories import embedding_repo, camera_repo


def _filter_cache_by_building_floor(
    full_cache: EmbeddingCache,
    building: Optional[str],
    floor: Optional[str],
) -> EmbeddingCache:
    """
    Trả về EmbeddingCache chỉ chứa học viên khớp building + floor.
    Dùng Fuzzy Match (giống _get_valid_student_ids trên Server):
      - Exact   : DB.building == building
      - DB ⊂ param: building chứa DB.building
      - param ⊂ DB: DB.building chứa building
    """
    from database.connection import get_db
    db = get_db()

    building_clean = (building or "").strip().upper()
    floor_clean    = str(floor or "").strip()

    if not building_clean and not floor_clean:
        return full_cache  # Không filter → trả về tất cả

    # Query lấy student_id theo building/floor (fuzzy match)
    clauses, params = [], []
    if building_clean:
        clauses.append("""(
            UPPER(TRIM(hv.building)) = %s
            OR UPPER(%s) LIKE CONCAT('%%', UPPER(TRIM(hv.building)), '%%')
            OR UPPER(TRIM(hv.building)) LIKE CONCAT('%%', UPPER(%s), '%%')
        )""")
        params += [building_clean, building_clean, building_clean]
    if floor_clean:
        clauses.append("TRIM(CAST(hv.floor AS CHAR)) = %s")
        params.append(floor_clean)

    sql = "SELECT hv.id FROM hocvien hv WHERE " + " AND ".join(clauses)
    rows = db.execute(sql, tuple(params))
    valid_ids = {int(r[0]) for r in rows}

    if not valid_ids:
        logger.warning(f"[PKL-EXPORT] Không có học viên nào khớp building='{building}' floor='{floor}'")
        return EmbeddingCache()

    # Lọc full_cache theo valid_ids
    filtered = EmbeddingCache()
    vecs = []
    for i, sid in enumerate(full_cache.student_ids):
        if sid not in valid_ids:
            continue
        filtered.student_ids.append(full_cache.student_ids[i])
        filtered.student_codes.append(full_cache.student_codes[i])
        filtered.full_names.append(full_cache.full_names[i])
        filtered.class_ids.append(full_cache.class_ids[i])
        filtered.class_names.append(full_cache.class_names[i])
        if hasattr(full_cache, "class_codes") and full_cache.class_codes:
            filtered.class_codes.append(full_cache.class_codes[i])
        if full_cache.embeddings is not None:
            vecs.append(full_cache.embeddings[i])

    if vecs:
        filtered.embeddings = np.vstack(vecs).astype(np.float32)

    logger.info(
        f"[PKL-EXPORT] Filter building='{building}' floor='{floor}': "
        f"{len(filtered.student_ids)}/{len(full_cache.student_ids)} học viên"
    )
    return filtered


def _save_pkl(path: Path, cache: EmbeddingCache, version: int) -> bool:
    """Lưu EmbeddingCache xuống file .pkl theo format MINI_PC."""
    try:
        data = {"version": version, "cache": cache}
        with open(path, "wb") as fh:
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        logger.success(
            f"[PKL-EXPORT] ✅ Đã ghi '{path.name}': "
            f"{cache.size} học viên | version={version}"
        )
        return True
    except Exception as e:
        logger.error(f"[PKL-EXPORT] ❌ Lỗi ghi '{path.name}': {e}")
        return False


def export_all_camera_pkls(embedding_version: int) -> dict:
    """
    Xuất file .pkl cập nhật cho TẤT CẢ camera trong DB + file GLOBAL.
    Gọi sau khi reload embedding_service để MINI_PC nhận được học viên mới.

    Returns:
        dict {"exported": [...], "failed": [...], "total": N}
    """
    result = {"exported": [], "failed": [], "total": 0}

    # 1. Load full cache từ DB
    try:
        full_cache = embedding_repo.load_all_to_cache()
    except Exception as e:
        logger.error(f"[PKL-EXPORT] Không load được cache từ DB: {e}")
        return result

    if full_cache.is_empty:
        logger.warning("[PKL-EXPORT] Cache rỗng — không có học viên nào để export")
        return result

    # 2. Export GLOBAL (tất cả học viên — dự phòng)
    global_path = _LOCAL_CACHE_DIR / "embeddings_GLOBAL.pkl"
    if _save_pkl(global_path, full_cache, embedding_version):
        result["exported"].append("GLOBAL")
    else:
        result["failed"].append("GLOBAL")
    result["total"] += 1

    # 3. Export từng camera (filter theo building/floor)
    try:
        cameras = camera_repo.get_all(active_only=True)
    except Exception as e:
        logger.error(f"[PKL-EXPORT] Không lấy được danh sách camera: {e}")
        return result

    for cam in cameras:
        cam_key = f"CAM_{int(cam.camera_id):02d}"
        pkl_path = _LOCAL_CACHE_DIR / f"embeddings_{cam_key}.pkl"

        # Lấy building/floor từ camera record
        bld = getattr(cam, "device_group", None)
        flr = getattr(cam, "floor", None)

        # Fallback: parse từ area_id (format "KTX E4_4")
        if (not bld or not flr) and cam.area_id:
            parts = cam.area_id.split("_", 1)
            bld = parts[0].strip() if len(parts) > 0 else bld
            flr = parts[1].strip() if len(parts) > 1 else flr

        try:
            filtered = _filter_cache_by_building_floor(full_cache, bld, str(flr) if flr else None)
            if _save_pkl(pkl_path, filtered, embedding_version):
                result["exported"].append(cam_key)
            else:
                result["failed"].append(cam_key)
        except Exception as e:
            logger.error(f"[PKL-EXPORT] Lỗi export {cam_key}: {e}")
            result["failed"].append(cam_key)

        result["total"] += 1

    logger.info(
        f"[PKL-EXPORT] Tổng kết: {len(result['exported'])}/{result['total']} thành công | "
        f"Version={embedding_version}"
    )
    return result


def export_pkl_async(embedding_version: int):
    """Chạy export trong background thread để không block API response."""
    t = threading.Thread(
        target=export_all_camera_pkls,
        args=(embedding_version,),
        daemon=True,
        name="pkl-export"
    )
    t.start()
    return t


# ── Script chạy thủ công ────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Chạy: cd Server && python utils/pkl_export.py
    Dùng khi cần cập nhật .pkl ngay lập tức sau khi đăng ký học viên mới.
    """
    from services.embedding_service import embedding_service
    embedding_service.reload()
    version = embedding_service.version
    print(f"\n🔄 Embedding version hiện tại: {version}")
    result = export_all_camera_pkls(version)
    print(f"\n✅ Đã export: {result['exported']}")
    if result["failed"]:
        print(f"❌ Thất bại: {result['failed']}")
