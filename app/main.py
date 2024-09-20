"""FastAPI 应用入口"""

import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import auth, device, keys, messages, notify
from .auth import hash_password
from .config import settings
from .database import Database
from .forwarder import Forwarder
from .worker import SerialWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("air780")


def _prepare_jwt_secret(db):
    if settings.jwt_secret:
        return
    row = db.row("SELECT value FROM settings WHERE key='jwt_secret'")
    if row:
        settings.jwt_secret = row["value"]
        return
    settings.jwt_secret = secrets.token_urlsafe(48)
    db.execute("INSERT INTO settings (key, value) VALUES ('jwt_secret', ?)", (settings.jwt_secret,))


def _seed_legacy_admin(db):
    """兼容旧部署：设置了 ADMIN_PASSWORD 且尚无任何管理员时，播种 admin 账号。"""
    if not settings.admin_password:
        return
    if db.row("SELECT id FROM admins LIMIT 1"):
        return
    salt, ph = hash_password(settings.admin_password)
    db.execute(
        "INSERT INTO admins (username, password_hash, salt) VALUES ('admin',?,?)",
        (ph, salt),
    )
    log.info("已通过 ADMIN_PASSWORD 播种管理员账号 admin（旧部署兼容）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(os.path.abspath(settings.db_path)), exist_ok=True)
    db = Database(settings.db_path)
    app.state.db = db
    _prepare_jwt_secret(db)
    _seed_legacy_admin(db)
    admin_cnt = db.row("SELECT COUNT(*) AS c FROM admins")["c"]
    if admin_cnt == 0:
        log.info("首次部署：请在浏览器打开管理页并创建管理员账户")
    forwarder = Forwarder()
    app.state.forwarder = forwarder
    worker = SerialWorker(
        db,
        forwarder,
        on_message=lambda m: log.info("消息回调 #%s", m.get("id")),
    )
    app.state.worker = worker
    worker.start()
    log.info("Air780 短信桥已启动 (db=%s, port=%s)", settings.db_path, settings.device_port)
    yield
    worker.stop()
    worker.join(timeout=3)
    log.info("已停止")


app = FastAPI(title="Air780 短信桥", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(messages.router)
app.include_router(device.router)
app.include_router(keys.router)
app.include_router(notify.router)


@app.get("/api/health")
def health():
    w = getattr(app.state, "worker", None)
    return {"ok": True, "worker": w.status() if w else None}


static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )