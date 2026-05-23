"""
fix_null_location_students.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Script sửa chữa học viên bị NULL building/floor/room trong DB.
Chạy 1 lần sau khi deploy fix EnrollPage.

Logic:
  - Tìm tất cả hocvien có building IS NULL hoặc floor IS NULL
  - Nếu hv.room có giá trị và tồn tại trong Phong table:
      → Lấy MaToa và Tang từ Phong → backfill vào hv.building, hv.floor
  - In báo cáo trước khi commit để admin xác nhận

Chạy:
    cd Server
    python fix_null_location_students.py [--dry-run]
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database.connection import get_db
from loguru import logger

def main():
    parser = argparse.ArgumentParser(description="Sửa NULL building/floor cho học viên cũ")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chỉ báo cáo, không thay đổi DB")
    args = parser.parse_args()

    db = get_db()

    # 1. Tìm học viên bị NULL nhưng có room
    rows = db.execute(
        """
        SELECT hv.id, hv.MaHV, hv.HoTen, hv.room,
               p.MaToa, p.Tang
        FROM hocvien hv
        INNER JOIN Phong p ON p.MaPhong = hv.room
        WHERE (hv.building IS NULL OR hv.floor IS NULL)
          AND hv.room IS NOT NULL
        ORDER BY hv.id
        """
    )

    if not rows:
        logger.info("✅ Không có học viên nào cần sửa (hoặc chưa có room data để suy ra).")
        return

    print(f"\n{'='*65}")
    print(f"  Tìm thấy {len(rows)} học viên cần backfill building/floor:")
    print(f"{'='*65}")
    for r in rows:
        sid, ma_hv, ho_ten, room, ma_toa, tang = r
        print(f"  [{sid}] {ma_hv} — {ho_ten}: room={room!r} → building={ma_toa!r}, floor={tang}")
    print(f"{'='*65}\n")

    if args.dry_run:
        print("  [DRY RUN] Không thay đổi DB. Bỏ --dry-run để áp dụng.")
        return

    confirm = input("  Xác nhận cập nhật? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Hủy.")
        return

    fixed = 0
    for r in rows:
        sid, ma_hv, ho_ten, room, ma_toa, tang = r
        try:
            db.execute(
                "UPDATE hocvien SET building = ?, floor = ? WHERE id = ? AND (building IS NULL OR floor IS NULL)",
                (ma_toa, tang, sid),
                commit=True
            )
            logger.info(f"  ✅ [{sid}] {ma_hv}: building='{ma_toa}', floor={tang}")
            fixed += 1
        except Exception as e:
            logger.error(f"  ❌ [{sid}] {ma_hv}: Lỗi — {e}")

    print(f"\n  Hoàn tất: {fixed}/{len(rows)} học viên đã được sửa.\n")
    print("  ⚠️  Nhớ reload embedding cache: POST /api/reload-cache\n")


if __name__ == "__main__":
    main()
