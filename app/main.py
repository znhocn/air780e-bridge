"""FastAPI application entrypoint"""

import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import auth, contacts, device, keys, messages, notify, tasks
from .config import settings
from .database import Database
from .version import __version__
from .forwarder import Forwarder
from .scheduler import SchedulerService
from .worker import SerialWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("air780e")


def _prepare_jwt_secret(db):
    if settings.jwt_secret:
        return
    row = db.row("SELECT value FROM settings WHERE key='jwt_secret'")
    if row:
        settings.jwt_secret = row["value"]
        return
    settings.jwt_secret = secrets.token_urlsafe(48)
    db.execute("INSERT INTO settings (key, value) VALUES ('jwt_secret', ?)", (settings.jwt_secret,))


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(os.path.abspath(settings.db_path)), exist_ok=True)
    db = Database(settings.db_path)
    app.state.db = db
    _prepare_jwt_secret(db)
    admin_cnt = db.row("SELECT COUNT(*) AS c FROM admins")["c"]
    if admin_cnt == 0:
        log.info("First deployment: open the admin page in a browser to create the admin account")
    forwarder = Forwarder()
    app.state.forwarder = forwarder
    worker = SerialWorker(
        db,
        forwarder,
        on_message=lambda m: log.info("message callback #%s", m.get("id")),
    )
    app.state.worker = worker
    scheduler = SchedulerService(db, worker)
    worker.on_send_result = scheduler.on_send_result
    app.state.scheduler = scheduler
    worker.start()
    scheduler.start()
    log.info("Air780E SMS Bridge started (db=%s, port=%s)", settings.db_path, settings.device_port)
    yield
    scheduler.stop()
    worker.stop()
    worker.join(timeout=3)
    scheduler.join(timeout=3)
    log.info("Stopped")


app = FastAPI(title="Air780E SMS Bridge", version=__version__, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(messages.router)
app.include_router(device.router)
app.include_router(keys.router)
app.include_router(notify.router)
app.include_router(tasks.router)
app.include_router(contacts.router)


@app.get("/api/health")
def health():
    w = getattr(app.state, "worker", None)
    return {"ok": True, "worker": w.status() if w else None}


@app.get("/api/version")
def version():
    return {"version": __version__}


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