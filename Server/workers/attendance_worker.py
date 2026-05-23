import json
import time
import threading
from datetime import datetime
from core.logger import logger, attendance_logger
import redis
from pydantic import BaseModel

import sys
from pathlib import Path
# Dam bao import duoc cac module cua Server
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import redis_config
from database.repositories import record_repo, session_repo

REDIS_QUEUE_KEY = "queue:attendance"
REDIS_DLQ_KEY   = "queue:attendance_dlq"
MAX_RETRIES     = 3

# [FIX #10] Debounce interval cho absent-count check
# Chi query DB moi _ABSENT_CHECK_EVERY lan ghi thanh cong cua cung lop
_ABSENT_CHECK_EVERY = 5

class AttendanceTask(BaseModel):
    session_id: int
    student_id: int
    student_code: str
    full_name: str
    class_name: str
    recognition_score: float
    camera_id: int
    timestamp: str
    retry_count: int = 0

class AttendanceWorker:
    """
    Worker xử lý điểm danh bất đồng bộ qua Redis Queue.
    Giảm tải cho FastAPI, chống nghẽn MySQL khi có hàng trăm camera.
    """
    def __init__(self):
        self.redis = redis.Redis.from_url(redis_config.url, decode_responses=True)
        self._stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="Attendance-Worker")

    def start(self):
        try:
            self.redis.ping()
            logger.info("🚀 Attendance Worker khởi động - Sẵn sàng xử lý Queue.")
            self.thread.start()
        except Exception as e:
            logger.error(f"❌ Worker không thể kết nối Redis: {e}")

    def stop(self):
        logger.info("🛑 Đang dừng Attendance Worker (Graceful Shutdown)...")
        self._stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("💤 Attendance Worker đã dừng an toàn.")

    def _run(self):
        while not self._stop_event.is_set():
            try:
                # Lấy task từ queue, block tối đa 2s để có cơ hội check _stop_event
                result = self.redis.brpop(REDIS_QUEUE_KEY, timeout=2)
                if result:
                    _, task_json = result
                    self._process_task(task_json)
                else:
                    # Rảnh rỗi, có thể log queue size nếu cần
                    pass
            except redis.ConnectionError:
                logger.warning("Worker mất kết nối Redis, đang thử lại...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Worker Error: {e}")
                time.sleep(1)

    def _process_task(self, task_json: str):
        try:
            task_dict = json.loads(task_json)
            task = AttendanceTask(**task_dict)
        except Exception as parse_err:
            logger.error(f"[WORKER] Invalid task format: {task_json[:200]} | {parse_err}")
            self._send_to_dlq(task_json, "Invalid Format")
            return

        logger.debug(
            f"[WORKER] Dang xu ly: '{task.full_name}' ({task.student_code}) "
            f"| session={task.session_id} | score={task.recognition_score:.3f}"
        )

        try:
            # ── Chot chan 1: Validate Session Active ──────────────────────────
            session = session_repo.get_by_id(task.session_id)
            if not session:
                logger.error(
                    f"[WORKER] [SKIP] session_id={task.session_id} KHONG TON TAI trong DB! "
                    f"student='{task.full_name}' — Kiem tra lai session da bi xoa chua?"
                )
                return
            if session.status != "ACTIVE":
                logger.warning(
                    f"[WORKER] [SKIP] Session {task.session_id} khong ACTIVE "
                    f"(status hien tai: '{session.status}') "
                    f"— student='{task.full_name}' bi tu choi.\n"
                    f"  Nguyen nhan: Session co the da COMPLETED, PENDING, "
                    f"hoac chua duoc start_session() goi."
                )
                return

            # ── Chot chan 2: Duplicate check ──────────────────────────────────
            already = record_repo.is_already_recorded(task.session_id, task.student_id)
            if already:
                logger.info(
                    f"[WORKER] [DUP] '{task.full_name}' da diem danh trong "
                    f"session {task.session_id} — bo qua."
                )
                return

            # ── Ghi DB ────────────────────────────────────────────────────────
            success = record_repo.record_attendance(
                session_id=task.session_id,
                student_id=task.student_id,
                recognition_score=task.recognition_score,
                camera_id=task.camera_id,
            )

            if success:
                attendance_logger.info(
                    f"[WORKER] [OK] Ghi DB thanh cong: '{task.full_name}' "
                    f"| session={task.session_id} "
                    f"| score={task.recognition_score:.2f}"
                )

                # Gui Telegram neu lop du — [FIX #10] debounce bang Redis counter
                try:
                    import sys
                    from pathlib import Path
                    root_dir = Path(__file__).parent.parent.parent
                    if str(root_dir) not in sys.path:
                        sys.path.insert(0, str(root_dir))
                    from telegram_notifier import send_telegram_msg

                    # [FIX #10] Tang counter moi khi ghi thanh cong cho lop nay
                    counter_key = f"absent_check_counter:{task.session_id}:{task.class_name}"
                    counter_val = self.redis.incr(counter_key)
                    self.redis.expire(counter_key, 86400)  # Auto-expire sau 1 ngay

                    # Chi chay get_class_absent_count moi 5 lan, hoac lan dau tien (counter=1)
                    if counter_val == 1 or (counter_val % _ABSENT_CHECK_EVERY == 0):
                        absent_count = record_repo.get_class_absent_count(
                            task.session_id, task.class_name
                        )
                        if absent_count == 0:
                            redis_key = f"notified_full_{task.session_id}_{task.class_name}"
                            if not self.redis.get(redis_key):
                                self.redis.set(redis_key, "1", ex=86400)
                                msg_full = f"{task.class_name} - Du"
                                threading.Thread(
                                    target=send_telegram_msg, args=(msg_full,), daemon=True
                                ).start()
                    else:
                        logger.debug(
                            f"[WORKER] [ABSENT-SKIP] '{task.class_name}' "
                            f"counter={counter_val} — skip absent check (moi {_ABSENT_CHECK_EVERY} lan moi check)"
                        )
                except Exception as tg_err:
                    logger.error(f"[WORKER] Loi gui Telegram (lop du): {tg_err}")

            else:
                # record_attendance tra False: phan biet ro nguyen nhan
                logger.error(
                    f"[WORKER] [FAIL] record_attendance tra ve False!\n"
                    f"  student_id  : {task.student_id} ('{task.full_name}')\n"
                    f"  session_id  : {task.session_id}\n"
                    f"  Kiem tra SQL: SELECT * FROM AttendanceRecords "
                    f"WHERE session_id={task.session_id} "
                    f"AND student_id={task.student_id}\n"
                    f"  -> Neu rong: student KHONG thuoc session nay "
                    f"(prefill ABSENT chua chay hoac class_id sai).\n"
                    f"  -> Neu co dong: kiem tra loi UPDATE/upsert trong record_repo."
                )
                # Chuyen vao DLQ thay vi retry vo tan (vi retry cung se fail)
                self._send_to_dlq(
                    task.model_dump_json(),
                    f"record_attendance=False | student={task.student_id} "
                    f"session={task.session_id}"
                )

        except Exception as proc_err:
            logger.exception(
                f"[WORKER] CRITICAL: Exception khi xu ly task | "
                f"student='{task.full_name}' ({task.student_id}) | {proc_err}"
            )
            self._handle_failure(task)


    def _handle_failure(self, task: AttendanceTask):
        task.retry_count += 1
        if task.retry_count <= MAX_RETRIES:
            logger.info(f"🔄 Re-queueing task {task.student_code} (Lần {task.retry_count}/{MAX_RETRIES})")
            # Đẩy lại vào queue (dùng lpush để xử lý sớm hoặc rpush để xử lý sau)
            self.redis.lpush(REDIS_QUEUE_KEY, task.model_dump_json())
        else:
            logger.error(f"💀 Dead-letter (DLQ) cho {task.student_code} sau {MAX_RETRIES} lần thử.")
            self._send_to_dlq(task.model_dump_json(), "Max Retries Exceeded")

    def _send_to_dlq(self, payload_str: str, reason: str):
        try:
            dlq_item = {
                "payload": payload_str,
                "reason": reason,
                "failed_at": datetime.now().isoformat()
            }
            self.redis.lpush(REDIS_DLQ_KEY, json.dumps(dlq_item))
        except Exception as e:
            logger.error(f"Không thể ghi vào DLQ: {e}")

# Khởi tạo instance toàn cục cho FastAPI
attendance_worker = AttendanceWorker()

if __name__ == "__main__":
    attendance_worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        attendance_worker.stop()
