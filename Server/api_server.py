import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from loguru import logger
from fastapi.middleware.cors import CORSMiddleware

# Core & Config
from core.config import server_config, WOL_MINI_PCS
from core.state_manager import state_manager

# Services & Workers
from core.logger import setup_logging, api_latency_middleware
from services.embedding_service import embedding_service
from services.wol_service import wol_service, MiniPCDevice
from database.repositories import session_repo
from workers.attendance_worker import attendance_worker

# API Routers
from api.camera_routes import router as camera_router
from api.admin_routes import router as admin_router
from api.attendance_routes import router as attendance_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 API Server đang khởi động (Clean Architecture)...")
    
    # 1. Load Embeddings Cache (Bằng FAISS hoặc Numpy)
    embedding_service.load()
    if embedding_service.size > 0:
        logger.info(f"✅ Đã tải thành công {embedding_service.size} khuôn mặt vào RAM.")
    else:
        logger.warning("⚠️ Database hiện chưa có học viên nào!")
    
    # 2. Phục hồi Session ACTIVE (Auto-Recovery)
    try:
        all_sessions = session_repo.get_all(limit=10)
        active_list = [s for s in all_sessions if s.status == "ACTIVE"]
        if active_list:
            latest = max(active_list, key=lambda s: s.session_id)
            state_manager.set_command("START", latest.session_id, latest.class_id, None)
            logger.info(f"🔄 Phục hồi session ACTIVE: id={latest.session_id}")
    except Exception as e:
        logger.warning(f"Không thể phục hồi session: {e}")
        
    # 3. Wake-on-LAN khởi động Mini PC tự động
    if WOL_MINI_PCS:
        devices = []
        for d in WOL_MINI_PCS:
            if 'mac' in d and 'mac_address' not in d:
                d['mac_address'] = d.pop('mac')
            devices.append(MiniPCDevice(**d))
        wol_service.set_devices(devices)
        wol_service.wake_all(async_mode=True)
    
    # 4. Khởi chạy Attendance Redis Worker
    attendance_worker.start()
    
    yield
    
    logger.info("🛑 API Server đang tắt...")
    attendance_worker.stop()

app = FastAPI(
    title="FaceAttend API Server",
    description="API trung tâm cho hệ thống điểm danh khuôn mặt (Clean Architecture)",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký middleware đo latency
app.middleware("http")(api_latency_middleware)

# Đăng ký các router
app.include_router(camera_router)
app.include_router(admin_router)
app.include_router(attendance_router)

def send_telegram_msg(msg: str):
    """Giữ nguyên utils function cho UI/Worker import"""
    # (Có thể chuyển sang utils/telegram.py sau)
    pass

if __name__ == "__main__":
    logger.info(f"Khởi động Uvicorn Server tại {server_config.host}:{server_config.port}")
    uvicorn.run(
        "api_server:app",
        host=server_config.host,
        port=server_config.port,
        workers=server_config.workers,
        reload=server_config.reload
    )