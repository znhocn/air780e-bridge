"""Scheduled SMS tasks (admin auth)"""

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import schema
from ..auth import require_admin_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_admin_user)])


def _row_out(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "enabled": r["enabled"],
        "interval_days": r["interval_days"],
        "phone": r["phone"],
        "content": r["content"],
        "last_run_at": r.get("last_run_at"),
        "last_status": r.get("last_status") or "never",
        "last_msg_id": r.get("last_msg_id"),
        "created_at": r.get("created_at"),
    }


@router.get("")
def list_tasks(request: Request):
    db = request.app.state.db
    return [_row_out(r) for r in db.rows("SELECT * FROM scheduled_tasks ORDER BY id")]


@router.post("", status_code=201)
def create_task(body: schema.TaskIn, request: Request):
    db = request.app.state.db
    if not str(body.phone).strip():
        raise HTTPException(422, "Phone number cannot be empty")
    if not str(body.content).strip():
        raise HTTPException(422, "Content cannot be empty")
    mid = db.execute(
        "INSERT INTO scheduled_tasks (name, enabled, interval_days, phone, content) "
        "VALUES (?,?,?,?,?)",
        (body.name.strip(), 1 if body.enabled else 0, body.interval_days, body.phone.strip(), body.content),
    )
    return _row_out(db.row("SELECT * FROM scheduled_tasks WHERE id=?", (mid,)))


@router.put("/{task_id}")
def update_task(task_id: int, body: schema.TaskIn, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM scheduled_tasks WHERE id=?", (task_id,)):
        raise HTTPException(404, "Scheduled task not found")
    if not str(body.phone).strip():
        raise HTTPException(422, "Phone number cannot be empty")
    if not str(body.content).strip():
        raise HTTPException(422, "Content cannot be empty")
    db.execute(
        "UPDATE scheduled_tasks SET name=?, enabled=?, interval_days=?, phone=?, content=? WHERE id=?",
        (body.name.strip(), 1 if body.enabled else 0, body.interval_days, body.phone.strip(), body.content, task_id),
    )
    return _row_out(db.row("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)))


@router.delete("/{task_id}")
def delete_task(task_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM scheduled_tasks WHERE id=?", (task_id,)):
        raise HTTPException(404, "Scheduled task not found")
    db.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
    return {"ok": True}


@router.post("/{task_id}/trigger")
def trigger_task(task_id: int, request: Request):
    scheduler = request.app.state.scheduler
    if not scheduler:
        raise HTTPException(503, "Scheduler not started")
    if not scheduler.trigger(task_id):
        raise HTTPException(404, "Scheduled task not found")
    return {"ok": True, "detail": "task triggered"}