"""设备状态接口（API Key 认证）"""

from fastapi import APIRouter, Depends, Request

from ..auth import authenticate

router = APIRouter(prefix="/api/device", tags=["device"], dependencies=[Depends(authenticate)])


@router.get("/status")
def status_info(request: Request):
    worker = request.app.state.worker
    db = request.app.state.db
    st = worker.status() if worker else {}
    st["messages_total"] = db.row("SELECT COUNT(*) AS c FROM messages")["c"]
    st["messages_in"] = db.row(
        "SELECT COUNT(*) AS c FROM messages WHERE direction='in'"
    )["c"]
    st["messages_out_pending"] = db.row(
        "SELECT COUNT(*) AS c FROM messages WHERE direction='out' AND status IN ('queued','sending')"
    )["c"]
    return st


@router.post("/reconnect")
def reconnect(request: Request):
    worker = request.app.state.worker
    if not worker:
        return {"ok": False, "detail": "worker 未启动"}
    worker.dev and worker.dev.close()
    return {"ok": True, "detail": "已请求重连（worker 将自动拉起）"}


@router.get("/sms-capable")
def sms_capable(request: Request):
    """探测当前端口是否支持短信（文本模式）。"""
    worker = request.app.state.worker
    if not worker or not worker.dev or not worker.dev.connected:
        return {"capable": False, "detail": "未连接"}
    res = worker.dev.command("AT+CSMS?", 5)
    ok = res.ok and any("CSMS" in ln or ">" in ln for ln in res.lines)
    return {"capable": True, "detail": res.lines}