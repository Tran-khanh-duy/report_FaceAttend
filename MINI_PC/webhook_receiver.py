"""
MINI_PC/webhook_receiver.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HTTP server nho (FastAPI/uvicorn) chay ngam tren MiniPC.
Nhan Webhook push tu Server khi co thay doi DB,
kich hoat pull_embeddings() ngay lap tuc -- khong can restart.

Port mac dinh: 8765  (cau hinh qua EDGE_WEBHOOK_PORT)

Firewall (chay 1 lan voi quyen Admin tren MiniPC):
    netsh advfirewall firewall add rule name="MiniPC Webhook" ^
          dir=in action=allow protocol=TCP localport=8765
"""
import threading
import time
from loguru import logger

# ── Kiem tra dependency ──────────────────────────────────────────────────────
try:
    from fastapi import FastAPI, Request
    import uvicorn
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False
    logger.warning(
        "webhook_receiver: FastAPI/uvicorn chua cai -- Webhook bi TAT.\n"
        "   Chay: pip install fastapi uvicorn  de bat tinh nang nay."
    )

# ── Trang thai noi bo ────────────────────────────────────────────────────────
_server_thread = None
_last_refresh_time: float = 0.0
_DEBOUNCE_SEC: float = 2.0   # tranh pull qua nhieu lan neu Server gui don dap


def _build_app(edge_client_ref):
    """Tao FastAPI app va dang ky cac route webhook."""
    app = FastAPI(
        title="MiniPC Webhook Receiver",
        description="Nhan push notification tu Server de cap nhat embedding cache.",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.post("/webhook/refresh")
    async def cache_refresh(request: Request):
        """
        Server goi endpoint nay ngay sau khi DB thay doi
        (them/sua sinh vien, reload cache thu cong).
        """
        global _last_refresh_time

        try:
            body = await request.json()
        except Exception:
            body = {}

        event  = body.get("event", "CACHE_REFRESH")
        source = body.get("source", "unknown")

        # ── Debounce: tranh pull nhieu lan lien tiep trong 2 giay ──
        now = time.time()
        if now - _last_refresh_time < _DEBOUNCE_SEC:
            logger.debug(f"Webhook debounce -- bo qua request {event} tu {source}")
            return {"status": "debounced", "message": "Dang xu ly yeu cau truoc do."}
        _last_refresh_time = now

        logger.info(f"Webhook nhan: event='{event}' tu source='{source}'")

        # ── Chay pull trong thread rieng -- khong block HTTP response ──
        def _do_pull():
            try:
                logger.info("Webhook: Dang keo cache moi tu Server...")

                # [FIX] force=True: bat buoc pull du version so co trung.
                # Snapshot cam_ids TRUOC khi pull de tranh race condition.
                cam_ids = list(edge_client_ref._multi_caches.keys())
                edge_client_ref.pull_embeddings(force=True)       # Global cache
                for cam_id in cam_ids:
                    edge_client_ref.pull_embeddings(cam_id, force=True)  # Per-camera

                logger.success(
                    f"Webhook: Cache cap nhat xong! "
                    f"({len(cam_ids) + 1} cache(s) da refresh)"
                )
            except Exception as exc:
                logger.error(f"Webhook _do_pull loi: {exc}")

        t = threading.Thread(target=_do_pull, daemon=True, name="Webhook-Pull")
        t.start()

        return {"status": "ok", "message": "Dang cap nhat cache..."}

    @app.get("/webhook/health")
    async def health():
        """Health check -- Server co the goi truoc khi gui webhook that."""
        return {
            "status":  "ok",
            "service": "MiniPC Webhook Receiver",
            "last_refresh": _last_refresh_time,
        }

    return app


def start_webhook_server(edge_client_ref, port=None):
    """
    Khoi dong Webhook HTTP server trong background daemon thread.

    Goi ham nay tu ``main_edge.py`` SAU KHI EdgeClient da san sang.

    Args:
        edge_client_ref: Instance cua ``EdgeClient`` (singleton ``edge_client``).
        port:            Port lang nghe. Mac dinh lay tu ``edge_config.webhook_port``
                         hoac fallback 8765.

    Returns:
        Thread dang chay server, hoac None neu FastAPI chua cai.
    """
    global _server_thread

    if not _HAS_FASTAPI:
        logger.warning("Webhook server bi bo qua -- thieu FastAPI/uvicorn.")
        return None

    # Xac dinh port
    if port is None:
        try:
            from config import edge_config
            port = getattr(edge_config, "webhook_port", 8765)
        except Exception:
            port = 8765

    app = _build_app(edge_client_ref)

    def _run():
        logger.info(f"MiniPC Webhook server dang khoi dong tai 0.0.0.0:{port} ...")
        try:
            # [FIX] Dung uvicorn.Server thay vi uvicorn.run() khi chay trong thread.
            # uvicorn.run() mac dinh cai dat signal handlers (SIGINT/SIGTERM) tu thread con
            # → gay ra race condition voi main thread → block stdin → phai nhan Space moi tiep.
            # install_signal_handlers=False: tat hoan toan, main thread quan ly signal.
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=port,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)
            server.install_signal_handlers = lambda: None   # Khong de uvicorn chiem stdin
            server.run()
        except Exception as exc:
            logger.error(f"Webhook server crash: {exc}")

    _server_thread = threading.Thread(target=_run, daemon=True, name="Webhook-Server")
    _server_thread.start()

    # Cho ngan de server bind port truoc khi tiep tuc
    time.sleep(0.5)
    logger.success(f"Webhook receiver dang lang nghe tai port {port}")
    return _server_thread

