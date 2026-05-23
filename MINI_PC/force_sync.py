"""
force_sync.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK 2: Ep dong bo lai Cache tu Server, xoa .pkl cu neu rong.

Chay tu thu muc MINI_PC/:
    python force_sync.py

Hoac chi xoa cache + re-pull cho 1 camera:
    python force_sync.py --camera CAM_01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import argparse
import pickle
from pathlib import Path

# Setup sys.path de import duoc cac module cua MINI_PC/
MINI_PC_DIR = Path(__file__).parent
SERVER_DIR  = MINI_PC_DIR.parent / "Server"
for _p in [str(MINI_PC_DIR), str(SERVER_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(MINI_PC_DIR / ".env.edge")

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="DEBUG", format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")

from config import edge_config
from local_cache.embedding_sync import CACHE_DIR, EmbeddingCache, embedding_sync
from edge_client import edge_client


SEP = "─" * 65


def inspect_pkl_files():
    """In trang thai tat ca file .pkl hien co trong local_cache/."""
    print(f"\n{SEP}")
    print(f"  KIEM TRA FILE CACHE HIEN CO: {CACHE_DIR}")
    print(f"{SEP}")

    pkl_files = list(CACHE_DIR.glob("*.pkl"))
    if not pkl_files:
        print("  [!] Khong co file .pkl nao trong local_cache/")
        return

    for pkl in pkl_files:
        size_kb = pkl.stat().st_size / 1024
        try:
            with open(pkl, "rb") as fh:
                data = pickle.load(fh)

            # Format moi: {"version": X, "cache": EmbeddingCache}
            if isinstance(data, dict):
                version  = data.get("version", "?")
                cache    = data.get("cache")
                sv_count = cache.size if isinstance(cache, EmbeddingCache) else "?"
            # Format cu: EmbeddingCache truc tiep
            elif isinstance(data, EmbeddingCache):
                version  = "legacy"
                sv_count = data.size
            else:
                version  = "unknown"
                sv_count = "?"

            status = "RONG ⚠" if sv_count == 0 else f"{sv_count} SV ✓"
            print(f"  [{status:>15}] {pkl.name:<45} (ver={version}, {size_kb:.1f}KB)")

        except Exception as e:
            print(f"  [     LOI     ] {pkl.name:<45} ({e})")

    print(f"{SEP}\n")


def delete_empty_pkls():
    """Xoa cac file .pkl rong (0 SV) de force re-download."""
    deleted = []
    for pkl in CACHE_DIR.glob("*.pkl"):
        try:
            with open(pkl, "rb") as fh:
                data = pickle.load(fh)
            if isinstance(data, dict):
                cache    = data.get("cache")
                sv_count = cache.size if isinstance(cache, EmbeddingCache) else 0
            elif isinstance(data, EmbeddingCache):
                sv_count = data.size
            else:
                sv_count = 0

            if sv_count == 0:
                pkl.unlink()
                deleted.append(pkl.name)
                logger.warning(f"Da xoa file cache rong: {pkl.name}")
        except Exception as e:
            logger.error(f"Loi khi kiem tra {pkl.name}: {e} — Bo qua.")

    if deleted:
        logger.info(f"Da xoa {len(deleted)} file cache rong: {deleted}")
    else:
        logger.info("Khong co file cache rong nao can xoa.")
    return deleted


def force_pull(camera_ids: list[str] = None):
    """
    Ket noi Server va tai lai toan bo cache cho cac camera chi dinh.
    Neu khong truyen camera_ids, lay tu edge_config.camera_list.
    """
    print(f"\n{SEP}")
    print(f"  FORCE SYNC: Dang ket noi Server tai {edge_config.server_url}")
    print(f"{SEP}\n")

    # Kiem tra ket noi Server — method dung la check_server() khong phai check_server_health()
    if not edge_client.check_server():
        logger.error(
            f"Khong the ket noi Server tai {edge_config.server_url}!\n"
            f"  1. Kiem tra EDGE_SERVER_URL trong .env.edge.\n"
            f"  2. Dam bao Server FastAPI dang chay.\n"
            f"  3. Thu ping IP may Server."
        )
        return False

    # Lay camera list: co the la list[dict] (tu API) hoac da co san
    cam_list_raw = getattr(edge_config, "camera_list", []) or []
    if not cam_list_raw:
        logger.info("camera_list trong edge_config rong, dang pull tu Server...")
        edge_client.pull_camera_list()
        cam_list_raw = getattr(edge_config, "camera_list", []) or []

    if not cam_list_raw:
        logger.error("Van khong lay duoc danh sach camera. Kiem tra DB Cameras tren Server.")
        return False

    # Loc theo camera_ids neu co tham so --camera
    if camera_ids:
        cam_list_raw = [
            c for c in cam_list_raw
            if (c.get("camera_id") if isinstance(c, dict) else str(c)) in camera_ids
        ]
        if not cam_list_raw:
            logger.error(f"Khong tim thay camera {camera_ids} trong danh sach.")
            return False

    logger.info(f"Se dong bo cache cho {len(cam_list_raw)} camera...")

    all_ok = True
    for cam in cam_list_raw:
        # Camera co the la dict (tu API) hoac string
        if isinstance(cam, dict):
            cam_id    = cam.get("camera_id") or cam.get("name", "?")
            db_cam_id = cam.get("id") or cam.get("db_id") or cam.get("camera_id")
        else:
            cam_id    = str(cam)
            db_cam_id = None

        logger.info(f"  [{cam_id}] Dang tai embedding tu Server (db_id={db_cam_id})...")
        ok = edge_client.pull_embeddings(
            target_camera_id=cam_id,
            db_camera_id=int(db_cam_id) if db_cam_id and str(db_cam_id).isdigit() else None,
        )
        if ok:
            cache = edge_client.get_cache(cam_id)
            sv_count = cache.size if cache else 0
            if sv_count > 0:
                logger.success(
                    f"  [{cam_id}] Da tai thanh cong {sv_count} SV vao RAM + luu .pkl."
                )
            else:
                logger.warning(
                    f"  [{cam_id}] Server tra ve 0 SV — filter building/floor co the sai.\n"
                    f"  Chay: python ../Server/check_db_data.py de kiem tra du lieu."
                )
                all_ok = False
        else:
            logger.error(f"  [{cam_id}] Pull that bai — kiem tra ket noi mang.")
            all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Force Sync: Xoa cache cu va tai lai tu Server")
    parser.add_argument("--camera", nargs="+", default=None,
                        help="Camera ID cu the, VD: --camera CAM_01 CAM_02")
    parser.add_argument("--inspect-only", action="store_true",
                        help="Chi in trang thai file .pkl, khong download")
    parser.add_argument("--skip-delete", action="store_true",
                        help="Khong xoa file rong, chi pull lai")
    args = parser.parse_args()

    # 1. In trang thai hien tai
    inspect_pkl_files()

    if args.inspect_only:
        logger.info("--inspect-only: Ket thuc sau khi in trang thai.")
        return

    # 2. Xoa file rong
    if not args.skip_delete:
        delete_empty_pkls()

    # 3. Pull lai tu Server
    ok = force_pull(camera_ids=args.camera)

    # 4. In trang thai sau khi sync
    print()
    inspect_pkl_files()

    if ok:
        logger.success("Force Sync hoan tat. Khoi dong lai Main Edge de ap dung cache moi.")
    else:
        logger.error("Force Sync co loi. Kiem tra log o tren de biet nguyen nhan.")


if __name__ == "__main__":
    main()
