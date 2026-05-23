"""
check_db_data.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Diagnostic script: "Soi" gia tri building va floor thuc te trong MySQL.

Chay:
    cd Server/
    python check_db_data.py

Hoac voi IP cu the:
    python check_db_data.py --building "KTX E4" --floor 4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import argparse
from pathlib import Path

# Them Server/ vao path de import duoc config va database
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")  # Load bien moi truong

from database.connection import get_db

SEP  = "─" * 65
SEP2 = "═" * 65


def _safe_int(val) -> tuple:
    """Key ham sort: so nguyen truoc, chuoi sau. Tranh crash khi mix kieu."""
    try:
        return (0, int(val))
    except (TypeError, ValueError):
        return (1, str(val or ""))


def soi_distinct_values():
    """In tat ca gia tri DISTINCT building va floor trong bang hocvien."""
    db = get_db()

    print(f"\n{SEP2}")
    print("  DIAGNOSTIC: Gia tri thuc te trong bang `hocvien`")
    print(f"{SEP2}\n")

    # ── 1. DISTINCT building ──────────────────────────────────────────
    print(f"{SEP}")
    print("  COT: hocvien.building (hien thi chinh xac ky tu, ke ca khoang trang)")
    print(f"{SEP}")

    # FIX only_full_group_by: KHONG dung ORDER BY trong SQL.
    # Fetch het, sort bang Python.
    rows_bld = db.execute(
        "SELECT building, COUNT(*) AS so_sv "
        "FROM hocvien "
        "GROUP BY building"
        # ORDER BY da bi xoa de tranh loi MySQL only_full_group_by
    )
    # Sort Python: None cuoi, con lai theo ten
    rows_bld = sorted(rows_bld, key=lambda r: (r[0] is None, (r[0] or "").lower()))

    if not rows_bld:
        print("  [!] Khong co du lieu hoac cot 'building' trong bang hocvien la NULL het.")
    else:
        print(f"  {'#':<4} {'Gia tri building (boc ngoac de thay khoang trang)':<50} {'So SV':>6}")
        print(f"  {'─'*4} {'─'*50} {'─'*6}")
        for i, (bld, cnt) in enumerate(rows_bld, 1):
            display = f'"{bld}"' if bld is not None else "<NULL>"
            flag = " ⚠ KHOANG_TRANG!" if bld and (bld != bld.strip()) else ""
            print(f"  {i:<4} {display:<50} {cnt:>6}{flag}")

    # ── 2. DISTINCT floor ─────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  COT: hocvien.floor (kieu du lieu thuc te va gia tri)")
    print(f"{SEP}")

    # FIX: Tranh CASE expression phuc tap + ORDER BY trong cung 1 query strict mode.
    # Lay raw data, tinh kieu + sort o Python.
    rows_flr_raw = db.execute(
        "SELECT floor, COUNT(*) AS so_sv "
        "FROM hocvien "
        "GROUP BY floor"
        # ORDER BY da bi xoa
    )

    def _floor_type(val):
        if val is None:
            return "NULL"
        s = str(val).strip()
        if s.isdigit():
            return "NUMERIC_STRING"
        return "STRING"

    # Sort: NULL cuoi, so truoc, chuoi sau
    rows_flr = sorted(
        [(r[0], r[1], _floor_type(r[0])) for r in rows_flr_raw],
        key=lambda r: (r[0] is None, *_safe_int(r[0]))
    )

    if not rows_flr:
        print("  [!] Khong co du lieu hoac cot 'floor' trong bang hocvien la NULL het.")
    else:
        print(f"  {'#':<4} {'Gia tri floor':<20} {'Kieu':<18} {'So SV':>6}")
        print(f"  {'─'*4} {'─'*20} {'─'*18} {'─'*6}")
        for i, (flr, cnt, kieu) in enumerate(rows_flr, 1):
            display = f'"{flr}"' if flr is not None else "<NULL>"
            flag = " ⚠ KHOANG_TRANG!" if flr and str(flr) != str(flr).strip() else ""
            print(f"  {i:<4} {display:<20} {kieu:<18} {cnt:>6}{flag}")

    # ── 3. Ket hop building + floor ───────────────────────────────────
    print(f"\n{SEP}")
    print("  KET HOP: building + floor (so sanh voi tham so ban dang dung)")
    print(f"{SEP}")

    # FIX: GROUP BY expression phai khop CHINH XAC voi SELECT expression.
    # Dung alias trong subquery hoac GROUP BY trung lap SELECT.
    # Cach don gian nhat: lay raw, sort Python.
    rows_combo_raw = db.execute(
        "SELECT TRIM(building), TRIM(CAST(floor AS CHAR)), COUNT(*) "
        "FROM hocvien "
        "WHERE building IS NOT NULL "
        "GROUP BY TRIM(building), TRIM(CAST(floor AS CHAR))"
        # ORDER BY da bi xoa — sort Python phia duoi
    )
    # Sort: theo building (A-Z), sau do floor (so tang tang dan)
    rows_combo = sorted(
        rows_combo_raw,
        key=lambda r: ((r[0] or "").lower(), *_safe_int(r[1]))
    )

    if not rows_combo:
        print("  [!] Khong co du lieu.")
    else:
        print(f"  {'building (sau TRIM)':<25} {'floor (sau TRIM)':<20} {'So SV':>6}")
        print(f"  {'─'*25} {'─'*20} {'─'*6}")
        for bld, flr, cnt in rows_combo:
            print(f"  {f'[{bld}]':<25} {f'[{flr}]':<20} {cnt:>6}")

    print(f"\n{SEP2}")
    print("  GHI CHU:")
    print("  - Gia tri bi boc trong [ngoac vuong] la gia tri sau TRIM().")
    print("  - Neu thay khac voi tham so ban truyen vao -> do chinh la root cause.")
    print("  - 'KTX E4' vs 'KTX E4 ' -> khoang trang thua -> mismatch!")
    print("  - floor='4' (str) vs floor=4 (int) -> deu duoc xu ly boi CAST().")
    print(f"{SEP2}\n")


def test_query_with_params(building: str, floor: str):
    """Thu tuc query voi tham so cu the de xem co tra ve SV khong."""
    db = get_db()

    print(f"\n{SEP}")
    print(f"  TEST QUERY: building='{building}' floor='{floor}'")
    print(f"{SEP}")

    # Query cu (de so sanh)
    rows_old = db.execute(
        "SELECT hv.id, hv.MaHV, hv.HoTen, hv.building, hv.floor "
        "FROM hocvien hv "
        "WHERE hv.building = %s AND hv.floor = %s "
        "LIMIT 5",
        (building, floor)
    )
    print(f"\n  [Query cu - EXACT MATCH]: {len(rows_old)} ket qua")
    for r in rows_old:
        print(f"    id={r[0]} | MaHV={r[1]} | HoTen={r[2]} | building='{r[3]}' | floor='{r[4]}'")

    # Query moi (bullet-proof)
    rows_new = db.execute(
        "SELECT hv.id, hv.MaHV, hv.HoTen, hv.building, hv.floor "
        "FROM hocvien hv "
        "WHERE UPPER(TRIM(hv.building)) = UPPER(%s) "
        "  AND TRIM(CAST(hv.floor AS CHAR)) = %s "
        "LIMIT 5",
        (building.strip(), str(floor).strip())
    )
    print(f"\n  [Query moi - BULLET-PROOF TRIM+UPPER]: {len(rows_new)} ket qua")
    for r in rows_new:
        print(f"    id={r[0]} | MaHV={r[1]} | HoTen={r[2]} | building='{r[3]}' | floor='{r[4]}'")

    if len(rows_old) == 0 and len(rows_new) > 0:
        print("\n  *** ROOT CAUSE XAC NHAN: Query cu tra 0, query moi tra ket qua!")
        print("      -> Co khoang trang thua hoac khac hoa/thuong trong DB.")
    elif len(rows_new) == 0:
        print("\n  *** Ca 2 query deu tra 0. Kiem tra lai gia tri 'building' va 'floor'")
        print("      bang ket qua DISTINCT o tren.")
    else:
        print("\n  *** Ca 2 query deu tra ket qua. DB data dang match tot.")

    print(f"{SEP}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnostic: Soi du lieu building/floor trong DB")
    parser.add_argument("--building", type=str, default=None,
                        help="Thu query voi building cu the, VD: 'KTX E4'")
    parser.add_argument("--floor",    type=str, default=None,
                        help="Thu query voi floor cu the, VD: '4'")
    args = parser.parse_args()

    try:
        # Luon chay diagnostic tong quat
        soi_distinct_values()

        # Neu co tham so, thu query cu the
        if args.building or args.floor:
            test_query_with_params(
                building=args.building or "",
                floor=args.floor or "",
            )
    except ConnectionError as e:
        print(f"\n[LOI KET NOI DB]: {e}")
        print("-> Kiem tra file Server/.env: DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n[LOI KHONG MONG MUON]: {e}")
        traceback.print_exc()
        sys.exit(1)
