import os
import sys
from pathlib import Path

# Add script directory to sys.path
sys.path.insert(0, os.getcwd())

from database.connection import get_db

def main():
    try:
        db = get_db()
        # Let's find student with name like 'TÚ'
        query = """
            SELECT id, MaHV, HoTen, GioiTinh, IDLop, building, floor, room 
            FROM hocvien 
            WHERE HoTen LIKE '%TÚ%' OR HoTen LIKE '%Tu%'
        """
        rows = db.execute(query)
        print(f"Found {len(rows)} matches:")
        for r in rows:
            print(f"ID: {r[0]}, Code: {r[1]}, Name: {r[2]}, Gender: {r[3]}, Class: {r[4]}, Building: '{r[5]}', Floor: '{r[6]}', Room: '{r[7]}'")
            
        # Also query the Camera list from DB directly to verify match
        print("\nCameras Configuration:")
        cam_rows = db.execute("SELECT camera_id, camera_name, device_group, floor FROM Cameras")
        for cr in cam_rows:
             print(f"Cam ID: {cr[0]}, Name: {cr[1]}, Building: '{cr[2]}', Floor: '{cr[3]}'")

    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    main()
