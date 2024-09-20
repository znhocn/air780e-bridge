"""短信相关接口（API Key 认证）"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import schema
from ..auth import authenticate

router = APIRouter(prefix="/api/messages", tags=["messages"], dependencies=[Depends(authenticate)])


class SendIn(BaseModel):
    to: str = Field(..., description="目标号码")
    content: str = Field(..., description="短信内容")


@router.get("")
def list_messages(
    request: Request,
    direction: str | None = Query(None, pattern="^(in|out)$"),
    sender: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    db = request.app.state.db
    where, params = ["1=1"], []
    if direction:
        where.append("direction=?")
        params.append(direction)
    if sender:
        where.append("sender LIKE ?")
        params.append(f"%{sender}%")
    if q:
        where.append("content LIKE ?")
        params.append(f"%{q}%")
    cond = " AND ".join(where)
    total = db.row(f"SELECT COUNT(*) AS c FROM messages WHERE {cond}", params)["c"]
    items = db.rows(
        f"SELECT id,direction,sender,receiver,content,status,created_at "
        f"FROM messages WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    return schema.Page(items=items, total=total, page=page, page_size=page_size).model_dump()


@router.get("/{message_id}")
def get_message(message_id: int, request: Request):
    row = request.app.state.db.row("SELECT * FROM messages WHERE id=?", (message_id,))
    if not row:
        raise HTTPException(404, "短信不存在")
    return row


@router.post("/send", status_code=202)
def send_sms(body: SendIn, request: Request):
    if not body.to.strip():
        raise HTTPException(422, "号码不能为空")
    if not body.content.strip():
        raise HTTPException(422, "内容不能为空")
    db = request.app.state.db
    mid = db.execute(
        "INSERT INTO messages (direction, receiver, content, status, raw) "
        "VALUES ('out', ?, ?, 'queued', '')",
        (body.to.strip(), body.content),
    )
    worker = request.app.state.worker
    if not worker:
        raise HTTPException(503, "串口服务未启动")
    worker.send(mid, body.to.strip(), body.content)
    return {"id": mid, "status": "queued"}