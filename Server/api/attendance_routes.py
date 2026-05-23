import base64
import json
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Security, HTTPException, Query
from pydantic import BaseModel
from core.logger import logger, attendance_logger

from core.security import verify_device_access
from core.state_manager import state_manager
from core.config import ai_config
from database.repositories import student_repo, session_repo, camera_repo
from services.embedding_service import embedding_service

router = APIRouter(tags=["Attendance & Embeddings"])

# ─────────────────────────────────────────────────────────────────────────────
# [FIX #9 & #12] Redis TTL Cache cho student-id filter
# Tránh 2+ DB queries mỗi POST /api/attendance khi load cao
# ─────────────────────────────────────────────────────────────────────────────
_VALID_IDS_TTL   = 60   # giây — cache danh sách SV theo (building, floor)
_VIP_IDS_TTL     = 300  # giây — VIP list ít thay đổi, cache 5 phút
_REDIS_VALID_PFX = "cache:valid_ids"
_REDIS_VIP_KEY   = "cache:vip_ids"

class AttendancePayload(BaseModel):
    camera_id: str
    timestamp: str
    embedding: list[float]
    liveness_score: Optional[float] = 1.0
    liveness_checked: Optional[bool] = False

class AttendanceResponse(BaseModel):
    status: str
    message: str
    student_code: Optional[str] = None
    full_name: Optional[str] = None
    class_name: Optional[str] = None
    similarity: Optional[float] = None
    session_id: Optional[int] = None

def _get_valid_student_ids(building: str, floor: str) -> Optional[list[int]]:
    """
    Lay danh sach student_id theo toa nha (building) va tang (floor).

    [FIX #9] Ket qua duoc cache trong Redis (TTL = 60 giay).
    Cache key = "cache:valid_ids:{building}:{floor}" — invalidate tu dong
    khi embedding version tang (xem invalidate_student_filter_cache).

    [TASK 1] Schema MySQL (xac nhan truc tiep tu DB):
      hocvien.building  VARCHAR  -- ma toa nha, VD: 'E4'
      hocvien.floor     INT      -- tang, VD: 4
      hocvien.room      VARCHAR  -- ma phong, VD: '406E4'
    Cac cot nay da duoc SELECT chinh xac trong StudentRepository._SQL.
    Khong co 'MaToa', 'Tang', 'MaPhong' trong bang hocvien.

    Return semantics:
    - None       : KHONG co filter -> caller nhan dien TOAN BO.
    - []         : Co filter nhung KHONG co SV thoa man -> Edge tra UNKNOWN.
    - [id1, ...] : Danh sach SV hop le.

    PATH A -- Bidirectional Fuzzy Match tren hv.building / hv.floor:
      (A) Exact  : UPPER(TRIM(hv.building)) = UPPER(param)
                   VD: hv.building='E4',     param='E4'     -> TRUE
      (B) DB c Cam: param LIKE '%hv.building%'
                   VD: hv.building='E4',     param='KTX E4' -> TRUE (E4 in KTX E4)
      (C) Cam c DB: hv.building LIKE '%param%'
                   VD: hv.building='KTX E4', param='E4'     -> TRUE
      Floor [TASK 2]: CAST INT-safe -- so sanh ca CHAR lan INT native

    PATH B -- Phong JOIN fallback (khi hv.building NULL nhung hv.room OK):
      JOIN hocvien.room -> Phong.MaPhong -> Phong.MaToa / Phong.Tang
    """
    building_clean = (building or "").strip()
    floor_clean    = str(floor or "").strip()

    # [FIX #9] Kiem tra Redis cache truoc — tranh DB query neu da co ket qua
    _redis_key = f"{_REDIS_VALID_PFX}:{building_clean}:{floor_clean}"
    try:
        cached = state_manager.redis.get(_redis_key)
        if cached is not None:
            cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else cached
            cached_val = json.loads(cached_str)
            # None duoc serialize thanh JSON "null"
            return cached_val  # co the la None, [], hoac [id1, ...]
    except Exception as cache_err:
        logger.debug(f"[FILTER CACHE] Miss/error: {cache_err}")

    # [TASK 3] Entry-log: in chinh xac tham so truoc moi lan query
    logger.info(
        f"[FILTER] Fetching students for "
        f"Building: '{building_clean}', Floor: '{floor_clean}' "
        f"(raw input: building={building!r}, floor={floor!r})"
    )

    if not building_clean and not floor_clean:
        logger.info("[FILTER] No location params -> returning None (no filter applied)")
        return None

    try:
        from database.connection import get_db
        db = get_db()

        # ── PATH A: Bidirectional Fuzzy Match tren hv.building / hv.floor ─────
        building_params: list = []
        building_clause: str = ""

        if building_clean:
            building_clause = """(
                UPPER(TRIM(hv.building)) = UPPER(%s)
                OR UPPER(%s) LIKE CONCAT('%%', UPPER(TRIM(hv.building)), '%%')
                OR UPPER(TRIM(hv.building)) LIKE CONCAT('%%', UPPER(%s), '%%')
            )"""
            building_params = [building_clean, building_clean, building_clean]

        floor_params: list = []
        floor_clause: str = ""

        if floor_clean:
            # [TASK 2] INT-safe floor: hocvien.floor la kieu INT (VD: 4).
            # So sanh kep: (1) CAST AS CHAR cho string '4', (2) native INT 4.
            # Dam bao khong bao gio sai lech kieu du lieu giua camera va DB.
            try:
                floor_int = int(floor_clean)
                floor_clause = "(TRIM(CAST(hv.floor AS CHAR)) = %s OR hv.floor = %s)"
                floor_params = [floor_clean, floor_int]
            except ValueError:
                # floor khong phai so nguyen -> chi dung CAST
                floor_clause = "TRIM(CAST(hv.floor AS CHAR)) = %s"
                floor_params = [floor_clean]

        clauses = [c for c in [building_clause, floor_clause] if c]
        if not clauses:
            logger.info("[FILTER] No clauses built -> returning None")
            return None

        sql_a = (
            "SELECT hv.id FROM hocvien hv WHERE "
            + " AND ".join(clauses)
        )
        # Debug SQL: hien thi chinh xac query de de kiem tra tren log
        logger.debug(
            f"[FILTER PATH-A SQL] {sql_a} "
            f"| params={tuple(building_params + floor_params)}"
        )
        rows_a = db.execute(sql_a, tuple(building_params + floor_params))

        if rows_a:
            ids = [int(r[0]) for r in rows_a]
            logger.info(
                f"[FILTER PATH-A OK] Building='{building_clean}' Floor='{floor_clean}': "
                f"{len(ids)} SV hop le "
                f"(hocvien.building VARCHAR + hocvien.floor INT -- columns confirmed)"
            )
            # [FIX #9] Luu vao Redis cache (TTL = 60 giay)
            try:
                state_manager.redis.setex(_redis_key, _VALID_IDS_TTL, json.dumps(ids))
            except Exception:
                pass
            return ids

        # ── PATH B: Phong JOIN fallback ─────────────────────────────────────
        # Dung khi hv.building bi NULL nhung hv.room duoc luu dung.
        # Camera.device_group="KTX E4" → match Phong.MaToa fuzzy; Camera.floor=4 → Phong.Tang=4
        phong_where_parts: list = []
        phong_params: list = []

        if building_clean:
            phong_where_parts.append("""(
                UPPER(TRIM(p.MaToa)) = UPPER(%s)
                OR UPPER(%s) LIKE CONCAT('%%', UPPER(TRIM(p.MaToa)), '%%')
                OR UPPER(TRIM(p.MaToa)) LIKE CONCAT('%%', UPPER(%s), '%%')
            )""")
            phong_params += [building_clean, building_clean, building_clean]

        if floor_clean:
            phong_where_parts.append("TRIM(CAST(p.Tang AS CHAR)) = %s")
            phong_params.append(floor_clean)

        if phong_where_parts:
            sql_b = (
                "SELECT DISTINCT hv.id FROM hocvien hv "
                "INNER JOIN Phong p ON p.MaPhong = hv.room "
                "WHERE " + " AND ".join(phong_where_parts)
            )
            rows_b = db.execute(sql_b, tuple(phong_params))
            if rows_b:
                ids_b = [int(r[0]) for r in rows_b]
                logger.info(
                    f"[FILTER PATH-B OK via Phong JOIN] [{building_clean} T{floor_clean}]: "
                    f"{len(ids_b)} SV hop le (MaToa/Tang match)"
                )
                # [FIX #9] Luu PATH-B result vao cache
                try:
                    state_manager.redis.setex(_redis_key, _VALID_IDS_TTL, json.dumps(ids_b))
                except Exception:
                    pass
                return ids_b

        # ── Khong co path nao match — log diagnostic ─────────────────────────
        try:
            raw_blds = db.execute(
                "SELECT TRIM(building) AS b, COUNT(*) AS c "
                "FROM hocvien WHERE building IS NOT NULL "
                "GROUP BY TRIM(building)"
            )
            raw_blds = sorted(raw_blds, key=lambda r: (r[0] or "").lower())
            bld_list = [f'"{r[0]}" ({r[1]} SV)' for r in raw_blds] or ["(trong)"]

            raw_flrs = db.execute(
                "SELECT TRIM(CAST(floor AS CHAR)) AS f, COUNT(*) AS c "
                "FROM hocvien "
                "WHERE floor IS NOT NULL "
                "AND (UPPER(TRIM(building)) = UPPER(%s) "
                "     OR UPPER(%s) LIKE CONCAT('%%', UPPER(TRIM(building)), '%%') "
                "     OR UPPER(TRIM(building)) LIKE CONCAT('%%', UPPER(%s), '%%')) "
                "GROUP BY TRIM(CAST(floor AS CHAR))",
                (building_clean, building_clean, building_clean)
            )
            def _floor_key(r):
                try:
                    return (0, int(r[0]))
                except (TypeError, ValueError):
                    return (1, str(r[0] or ""))
            raw_flrs = sorted(raw_flrs, key=_floor_key)
            flr_list = [f'"{r[0]}"' for r in raw_flrs] or ["(khong co tang nao match building nay)"]

            # Kiem tra so SV co NULL de phat hien loi Enrollment
            null_row = db.execute(
                "SELECT COUNT(*) FROM hocvien WHERE building IS NULL OR floor IS NULL"
            )
            null_count = null_row[0][0] if null_row else "?"

            # Kiem tra Phong table co MaToa tuong ung khong
            phong_check = db.execute(
                "SELECT DISTINCT MaToa FROM Phong WHERE "
                "UPPER(TRIM(MaToa)) = UPPER(%s) "
                "OR UPPER(%s) LIKE CONCAT('%%', UPPER(TRIM(MaToa)), '%%') "
                "OR UPPER(TRIM(MaToa)) LIKE CONCAT('%%', UPPER(%s), '%%')",
                (building_clean, building_clean, building_clean)
            )
            phong_toa_list = [r[0] for r in phong_check] if phong_check else []

        except Exception as diag_err:
            logger.debug(f"Khong lay duoc DISTINCT de diagnostic: {diag_err}")
            bld_list = ["(loi truy van diagnostic)"]
            flr_list = ["(loi truy van diagnostic)"]
            null_count = "?"
            phong_toa_list = []

        logger.warning(
            f"[STRICT FILTER] Khong tim thay SV nao sau PATH-A (fuzzy) + PATH-B (Phong JOIN):\n"
            f"  Tham so camera : building='{building_clean}' floor='{floor_clean}'\n"
            f"  DB hv.building : {', '.join(bld_list)}\n"
            f"  DB hv.floor    : {', '.join(flr_list)}\n"
            f"  SV NULL bld/flr: {null_count} (day la nguyen nhan chinh gay ra UNKNOWN)\n"
            f"  Phong.MaToa OK : {phong_toa_list} (PATH-B phu thuoc hv.room khong NULL)\n"
            f"  => NGUYEN NHAN: EnrollPage khong load Buildings combo → building=None → NULL DB.\n"
            f"  => FIX DA AP DUNG: showEvent() gio goi _load_buildings() truoc.\n"
            f"  => Chay: cd Server && python check_db_data.py "
            f"--building \"{building_clean}\" --floor \"{floor_clean}\""
        )
        result = []
        # [FIX #9] Luu ket qua rong vao cache (TTL ngan hon: 10s)
        # Tranh re-query lien tuc khi khong co SV nao match
        try:
            state_manager.redis.setex(_redis_key, 10, json.dumps(result))
        except Exception:
            pass
        return result

    except Exception as exc:
        logger.exception(
            f"Loi _get_valid_student_ids("
            f"building={building_clean!r}, floor={floor_clean!r}): {exc}"
        )
        return []


def invalidate_student_filter_cache():
    """
    [FIX #9] Xoa toan bo cache filter khi co hoc vien moi dang ky.
    Goi tu admin_routes khi embedding version tang.
    """
    try:
        keys = state_manager.redis.keys(f"{_REDIS_VALID_PFX}:*")
        if keys:
            state_manager.redis.delete(*keys)
        state_manager.redis.delete(_REDIS_VIP_KEY)
        logger.info(f"[FILTER CACHE] Invalidated {len(keys)} filter cache keys + VIP cache.")
    except Exception as e:
        logger.warning(f"[FILTER CACHE] Invalidation error: {e}")

def _find_camera_by_id(camera_id_str: str):
    if not camera_id_str:
        return None
    cameras = camera_repo.get_all()
    # 1. Numeric ID (db_camera_id tu headless_processor)
    if camera_id_str.isdigit():
        cam = next((c for c in cameras if c.camera_id == int(camera_id_str)), None)
        if cam:
            return cam
    # 2. Resolve qua edge_status
    for dev_status in state_manager.get_all_edge_status().values():
        cam_status = dev_status.get("camera_status", {})
        if camera_id_str in cam_status and isinstance(cam_status[camera_id_str], dict):
            name = cam_status[camera_id_str].get("name", camera_id_str)
            cam = next((c for c in cameras if c.camera_name == name), None)
            if cam:
                return cam
    # 3. Match theo ten hoac RTSP URL
    return next(
        (c for c in cameras
         if c.camera_name == camera_id_str
         or c.rtsp_url == camera_id_str
         or (c.effective_rtsp_url and c.effective_rtsp_url == camera_id_str)),
        None
    )

def _get_vip_student_ids() -> list[int]:
    """
    Tra ve danh sach student_id co quyen 'VIP / Anywhere Pass'.
    Tieu chi VIP (thoa 1 trong 2):
      - IDLop = 'ALL'      : Hoc vien thuoc lop ao 'Tat ca toa nha'
      - building = 'ALL'   : Hoc vien duoc danh dau wildcard thu cong
    VIP students bo qua location filter va diem danh duoc tai bat ky camera nao.

    [FIX #12] Ket qua duoc cache trong Redis (TTL = 300 giay = 5 phut).
    VIP list hiem khi thay doi nen co the cache lau hon valid_ids.
    """
    # [FIX #12] Kiem tra Redis cache truoc
    try:
        cached = state_manager.redis.get(_REDIS_VIP_KEY)
        if cached is not None:
            cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else cached
            return json.loads(cached_str)
    except Exception as cache_err:
        logger.debug(f"[VIP CACHE] Miss/error: {cache_err}")

    try:
        from database.connection import get_db
        rows = get_db().execute(
            """
            SELECT DISTINCT hv.id
            FROM hocvien hv
            WHERE UPPER(TRIM(hv.IDLop))    = 'ALL'
               OR UPPER(TRIM(hv.building)) = 'ALL'
            """
        )
        vip_ids = [int(r[0]) for r in rows] if rows else []
        if vip_ids:
            logger.info(f"[VIP] Tim thay {len(vip_ids)} hoc vien VIP (Anywhere Pass): {vip_ids}")

        # [FIX #12] Luu vao Redis cache (TTL = 300 giay)
        try:
            state_manager.redis.setex(_REDIS_VIP_KEY, _VIP_IDS_TTL, json.dumps(vip_ids))
        except Exception:
            pass

        return vip_ids
    except Exception as e:
        logger.warning(f"[VIP] Khong lay duoc danh sach VIP: {e}")
        return []

@router.get("/api/students")
async def get_students(
    class_id: Optional[str] = Query(None),
    api_key: str = Security(verify_device_access),
):
    try:
        students = student_repo.get_by_class(class_id) if class_id else student_repo.get_all()
        return {
            "status": "success",
            "data": [
                {"student_id": s.student_id, "student_code": s.student_code,
                 "full_name": s.full_name, "class_name": s.class_name}
                for s in students
            ],
        }
    except Exception as e:
        logger.error(f"Loi /students: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/embeddings")
async def get_embeddings(
    camera_id: Optional[str] = Query(None),
    api_key: str = Security(verify_device_access)
):
    try:
        if embedding_service.size == 0:
            return {"status": "success", "count": 0, "students": []}

        valid_student_ids = None
        if camera_id:
            camera = _find_camera_by_id(camera_id)
            if camera:
                flr = getattr(camera, "floor", None)          # Cameras.floor INT, VD: 4
                bld = getattr(camera, "device_group", None)   # Cameras.device_group STR, VD: 'KTX E4'
                # Fallback: lay tu area_id neu device_group/floor chua duoc set
                if (bld is None or flr is None) and camera.area_id:
                    parts = camera.area_id.split("_", 1)
                    bld = parts[0].strip() if len(parts) > 0 else bld
                    flr = parts[1].strip() if len(parts) > 1 else flr
                # [TASK 3] Log chinh xac truoc khi goi filter
                logger.info(
                    f"[FILTER] /api/embeddings cam_id='{camera_id}' "
                    f"-> Cameras.device_group={bld!r} (match hv.building), "
                    f"Cameras.floor={flr!r} (match hv.floor INT)"
                )
                # Dong nhat voi receive_attendance: filter ca khi chi co bld
                if flr is not None and bld is not None:
                    valid_student_ids = _get_valid_student_ids(bld, str(flr))
                elif bld is not None:
                    valid_student_ids = _get_valid_student_ids(bld, "")
                else:
                    valid_student_ids = None

        # ── VIP / Anywhere Pass: merge VIP students vào valid list ────────────
        # VIP students (IDLop='ALL' hoặc building='ALL') luôn được gửi xuống Edge
        # dù camera đang ở tầng/tòa nào.
        if valid_student_ids is not None:
            vip_ids = _get_vip_student_ids()
            if vip_ids:
                # Hợp nhất, loại trùng
                valid_student_ids = list(set(valid_student_ids) | set(vip_ids))
                logger.info(
                    f"[VIP] Sau khi merge: {len(valid_student_ids)} SV hợp lệ "
                    f"(bao gồm {len(vip_ids)} VIP)"
                )

        result = []
        all_embs = embedding_service.get_all_embeddings()
        for i in range(embedding_service.size):
            if valid_student_ids is not None and all_embs["student_ids"][i] not in valid_student_ids:
                continue
            emb_b64 = base64.b64encode(all_embs["embeddings"][i].tobytes()).decode("utf-8")
            result.append({
                "student_id": all_embs["student_ids"][i],
                "student_code": all_embs["student_codes"][i],
                "full_name": all_embs["full_names"][i],
                "class_id": all_embs["class_ids"][i],
                "class_name": all_embs["class_names"][i],
                "class_code": all_embs.get("class_codes", [""] * embedding_service.size)[i],
                "embedding_b64": emb_b64,
            })

        logger.info(f"Gui {len(result)}/{embedding_service.size} embeddings cho camera_id={camera_id}")
        return {"status": "success", "count": len(result), "students": result}
    except Exception as e:
        logger.error(f"Loi /embeddings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/embeddings/version")
async def get_embedding_version(api_key: str = Security(verify_device_access)):
    ver, updated_at = state_manager.get_embedding_version()
    return {"embedding_version": ver, "updated_at": updated_at, "total": embedding_service.size}

@router.get("/api/sessions/active")
async def get_active_sessions(api_key: str = Security(verify_device_access)):
    try:
        all_sessions = session_repo.get_all(limit=50)
        active = [s for s in all_sessions if s.status == "ACTIVE"]
        return {
            "status": "success",
            "data": [
                {
                    "session_id": s.session_id, "class_name": s.class_name,
                    "subject_name": s.subject_name,
                    "session_date": str(s.session_date) if s.session_date else "",
                    "start_time": s.start_time.isoformat() if s.start_time else "",
                    "present_count": s.present_count,
                }
                for s in active
            ],
        }
    except Exception as e:
        logger.error(f"Loi /sessions/active: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/attendance", response_model=AttendanceResponse)
async def receive_attendance(
    payload: AttendancePayload,
    api_key: str = Security(verify_device_access),
):
    import numpy as np
    try:
        incoming_vector = np.array(payload.embedding, dtype=np.float32)
        if len(incoming_vector) != 512:
            raise HTTPException(status_code=400, detail="Vector khong hop le (can 512 chieu)")

        if payload.liveness_checked and payload.liveness_score < getattr(ai_config, "liveness_threshold", 0.80):
            attendance_logger.warning(
                f"[SPOOFING] Score: {payload.liveness_score:.3f} "
                f"(threshold={getattr(ai_config, 'liveness_threshold', 0.80)}) "
                f"| Cam: {payload.camera_id}"
            )
            return AttendanceResponse(
                status="rejected",
                message=f"Phat hien gia mao (Liveness: {payload.liveness_score:.2f})",
            )

        if embedding_service.size == 0:
            return AttendanceResponse(status="ignored", message="CSDL trong")

        # Tim camera va filter hoc vien theo tang
        camera = None
        valid_student_ids = None
        if payload.camera_id:
            camera = _find_camera_by_id(payload.camera_id)
            if camera:
                flr = getattr(camera, "floor", None)          # Cameras.floor INT, VD: 4
                bld = getattr(camera, "device_group", None)   # Cameras.device_group STR, VD: 'KTX E4'
                # Fallback: lay tu area_id neu device_group/floor chua duoc set
                if (bld is None or flr is None) and camera.area_id:
                    parts = camera.area_id.split("_", 1)
                    bld = parts[0].strip() if len(parts) > 0 else bld
                    flr = parts[1].strip() if len(parts) > 1 else flr

                # [TASK 3] Log chinh xac tung truong truoc khi goi filter.
                # bld ('KTX E4') se match hocvien.building ('E4') qua fuzzy substring.
                # flr (4 INT)   se match hocvien.floor    (4 INT) qua CAST INT-safe.
                logger.info(
                    f"[FILTER] /api/attendance cam_id='{payload.camera_id}' "
                    f"-> Cameras.device_group={bld!r} (-> hv.building fuzzy), "
                    f"Cameras.floor={flr!r} (-> hv.floor INT-safe)"
                )

                # Chi filter neu CO CA building lan floor:
                # - Neu chi co floor ma khong biet building -> nguy hiem (match SV sai toa).
                # - Neu chi co building ma khong biet floor -> filter theo toa (an toan).
                if flr is not None and bld is not None:
                    valid_student_ids = _get_valid_student_ids(bld, str(flr))
                elif bld is not None:
                    valid_student_ids = _get_valid_student_ids(bld, "")
                else:
                    valid_student_ids = None  # Khong du thong tin -> nhan dien toan bo

                count_str = (
                    str(len(valid_student_ids)) if valid_student_ids
                    else "0 (FILTER ACTIVE -- no match, will return UNKNOWN)"
                    if valid_student_ids is not None
                    else "ALL (no filter applied)"
                )
                logger.info(
                    f"[FILTER RESULT] Camera [{camera.camera_id}] "
                    f"Building='{bld}' Floor='{flr}' -> valid_student_ids={count_str}"
                )
            else:
                logger.warning(
                    f"Khong tim thay camera_id='{payload.camera_id}' trong DB "
                    f"— nhan dien TOAN BO (co the gay False Positive)."
                )

        # ── VIP / Anywhere Pass: merge VIP students vào valid list ────────────
        # Học viên có IDLop='ALL' hoặc building='ALL' điểm danh được ở mọi camera.
        # Chỉ cần merge khi đang có location filter (valid_student_ids != None).
        if valid_student_ids is not None:
            vip_ids = _get_vip_student_ids()
            if vip_ids:
                valid_student_ids = list(set(valid_student_ids) | set(vip_ids))
                logger.info(
                    f"[VIP] /api/attendance: Merge xong, tổng {len(valid_student_ids)} SV hợp lệ "
                    f"(bao gồm {len(vip_ids)} VIP Anywhere Pass)"
                )

        # Nhan dien
        best_score, best_idx = embedding_service.search(
            incoming_vector=incoming_vector,
            top_k=1,
            valid_ids=valid_student_ids
        )

        if best_idx != -1 and best_score >= ai_config.recognition_threshold:
            student_info = embedding_service.get_student_info(best_idx)
            student_id   = student_info["student_id"]
            student_code = student_info["student_code"]
            full_name    = student_info["full_name"]
            class_name   = student_info["class_name"]

            cmd_state = state_manager.get_command_state()
            session_id = cmd_state["session_id"]
            active_session = None

            if session_id:
                active_session = session_repo.get_by_id(session_id)
                if active_session and active_session.status != "ACTIVE":
                    active_session = None

            if not active_session:
                all_sessions = session_repo.get_all(limit=5)
                active_list = [s for s in all_sessions if s.status == "ACTIVE"]
                if active_list:
                    active_session = active_list[0]
                    state_manager.set_command(
                        "START", active_session.session_id,
                        active_session.class_id, cmd_state.get("target_camera")
                    )

            if not active_session:
                return AttendanceResponse(
                    status="no_session",
                    message="Khong co phien diem danh",
                    student_code=student_code,
                    full_name=full_name,
                    class_name=class_name,
                    similarity=best_score,
                )

            db_cam_id = camera.camera_id if camera else 1
            task_payload = {
                "session_id": active_session.session_id,
                "student_id": student_id,
                "student_code": student_code,
                "full_name": full_name,
                "class_name": class_name,
                "recognition_score": best_score,
                "camera_id": db_cam_id,
                "timestamp": datetime.now().isoformat(),
                "retry_count": 0
            }
            state_manager.redis.lpush("queue:attendance", json.dumps(task_payload))
            q_size = state_manager.redis.llen("queue:attendance")
            attendance_logger.info(
                f"Dua {full_name} vao Queue (Score: {best_score:.2f} | QSize: {q_size})"
            )
            return AttendanceResponse(
                status="queued",
                message="Da dua vao hang doi xu ly",
                student_code=student_code,
                full_name=full_name,
                class_name=class_name,
                similarity=best_score,
                session_id=active_session.session_id,
            )
        else:
            return AttendanceResponse(
                status="unknown",
                message=f"Khong nhan dien duoc (Score: {best_score:.2f})",
            )
    except Exception as e:
        logger.error(f"Loi /attendance: {e}")
        raise HTTPException(status_code=500, detail=str(e))
