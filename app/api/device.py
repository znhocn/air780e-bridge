"""Device status endpoints (API key auth)"""

from fastapi import APIRouter, Depends, Request

from ..auth import authenticate

router = APIRouter(prefix="/api/device", tags=["device"], dependencies=[Depends(authenticate)])


@router.get("/status")
def status_info(request: Request):
    worker = request.app.state.worker
    db = request.app.state.db
    st = worker.status() if worker else {}
    stats = db.row(
        "SELECT "
        "(SELECT COUNT(*) FROM messages) AS total, "
        "(SELECT COUNT(*) FROM messages WHERE direction='in') AS in_cnt, "
        "(SELECT COUNT(*) FROM messages WHERE direction='out' AND status IN ('queued','sending')) AS pending_cnt, "
        "(SELECT COUNT(*) FROM messages WHERE direction='in' AND date(created_at)=date('now','localtime')) AS today_in_cnt, "
        "(SELECT COUNT(*) FROM messages WHERE direction='out' AND date(created_at)=date('now','localtime')) AS today_out_cnt, "
        "(SELECT COUNT(*) FROM messages WHERE direction='out' AND status='failed' "
        "AND date(created_at)=date('now','localtime')) AS today_failed_cnt, "
        "(SELECT COUNT(*) FROM forward_logs WHERE channel='call') AS calls_cnt"
    )
    st["messages_total"] = stats["total"]
    st["messages_in"] = stats["in_cnt"]
    st["messages_out_pending"] = stats["pending_cnt"]
    st["messages_today_in"] = stats["today_in_cnt"]
    st["messages_today_out"] = stats["today_out_cnt"]
    st["messages_today_failed"] = stats["today_failed_cnt"]
    st["calls_total"] = stats["calls_cnt"]
    return st


@router.post("/reconnect")
def reconnect(request: Request):
    worker = request.app.state.worker
    if not worker:
        return {"ok": False, "detail": "worker not started"}
    worker.dev and worker.dev.close()
    return {"ok": True, "detail": "reconnect requested (worker will restart automatically)"}


@router.get("/sms-capable")
def sms_capable(request: Request):
    """Check whether the current port supports SMS (text mode)."""
    worker = request.app.state.worker
    if not worker or not worker.dev or not worker.dev.connected:
        return {"capable": False, "detail": "not connected"}
    res = worker.dev.command("AT+CSMS?", 5)
    ok = res.ok and any("CSMS" in ln or ">" in ln for ln in res.lines)
    return {"capable": True, "detail": res.lines}