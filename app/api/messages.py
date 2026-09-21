"""SMS endpoints (API key auth)"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import schema
from ..auth import authenticate

router = APIRouter(prefix="/api/messages", tags=["messages"], dependencies=[Depends(authenticate)])


class SendIn(BaseModel):
    to: str = Field(..., description="Target phone number")
    content: str = Field(..., description="SMS content")


def _digits(n: str) -> str:
    return re.sub(r"\D", "", n or "")


def _like(v: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    return (v or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _group_key(raw: str) -> str:
    """Normalized peer key: digits only, Chinese country-code collapsed."""
    d = _digits(raw)
    if d.startswith("86") and len(d) == 13:
        return d[2:]
    return d


def _peer_variants(raw: str) -> list:
    """Raw + common formatting variants so the peer filter matches any of them."""
    d = _digits(raw)
    out = [raw]
    if d and d not in out:
        out.append(d)
    if len(d) == 11:
        for prefix in ("86", "+86", "0086"):
            v = prefix + d
            if v not in out:
                out.append(v)
    return out


@router.get("")
def list_messages(
    request: Request,
    direction: str | None = Query(None, pattern="^(in|out)$"),
    sender: str | None = None,
    q: str | None = None,
    peer: str | None = None,
    before_id: int | None = Query(None, ge=1, description="Fetch messages older than this id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    db = request.app.state.db
    where, params = ["1=1"], []
    if direction:
        where.append("direction=?")
        params.append(direction)
    if sender:
        where.append("sender LIKE ? ESCAPE '\\'")
        params.append(f"%{_like(sender)}%")
    if q:
        where.append("content LIKE ? ESCAPE '\\'")
        params.append(f"%{_like(q)}%")
    if peer:
        variants = _peer_variants(peer)
        marks = ",".join("?" for _ in variants)
        where.append(f"((direction='in' AND sender IN ({marks})) OR (direction='out' AND receiver IN ({marks})))")
        params += variants + variants
    if before_id:
        where.append("id < ?")
        params.append(before_id)
    cond = " AND ".join(where)
    total = db.row(f"SELECT COUNT(*) AS c FROM messages WHERE {cond}", params)["c"]
    if peer:
        # chat paging: latest window ordered oldest->newest (DESC then reverse)
        rows = db.rows(
            f"SELECT id,direction,sender,receiver,content,status,created_at "
            f"FROM messages WHERE {cond} ORDER BY id DESC LIMIT ?",
            params + [page_size],
        )
        items = list(reversed(rows))
    else:
        items = db.rows(
            f"SELECT id,direction,sender,receiver,content,status,created_at "
            f"FROM messages WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
    return schema.Page(items=items, total=total, page=page, page_size=page_size).model_dump()


@router.get("/conversations")
def list_conversations(
    request: Request,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """Group the latest messages into per-number conversations (chat sidebar)."""
    db = request.app.state.db
    where, params = ["1=1"], []
    if q:
        where.append("content LIKE ?")
        params.append(f"%{q}%")
    cond = " AND ".join(where)
    rows = db.rows(
        f"SELECT id,direction,sender,receiver,content,status,created_at "
        f"FROM messages WHERE {cond} ORDER BY id DESC LIMIT ?",
        params + [limit],
    )
    groups: dict[str, list] = {}
    for r in rows:
        peer = r["receiver"] if r["direction"] == "out" else (r["sender"] or "")
        if not peer:
            continue
        groups.setdefault(_group_key(peer), []).append(r)

    names = {_group_key(c["phone"]): c["name"] for c in db.rows("SELECT name, phone FROM contacts")}
    convs = []
    for key, msgs in groups.items():
        latest = msgs[0]
        convs.append({
            "peer": latest["receiver"] if latest["direction"] == "out" else latest["sender"],
            "contact_name": names.get(key, ""),
            "count": len(msgs),
            "last_content": latest["content"],
            "last_status": latest["status"],
            "last_direction": latest["direction"],
            "last_at": latest["created_at"],
        })
    seen = set(groups)
    for c in db.rows("SELECT name, phone FROM contacts ORDER BY name"):
        key = _group_key(c["phone"])
        if key in seen:
            continue
        seen.add(key)
        convs.append({
            "peer": c["phone"],
            "contact_name": c["name"],
            "count": 0,
            "last_content": "",
            "last_status": "",
            "last_direction": "",
            "last_at": "",
        })
    convs.sort(key=lambda c: c["last_at"], reverse=True)
    return convs


@router.get("/{message_id}")
def get_message(message_id: int, request: Request):
    row = request.app.state.db.row("SELECT * FROM messages WHERE id=?", (message_id,))
    if not row:
        raise HTTPException(404, "Message not found")
    return row


@router.post("/send", status_code=202)
def send_sms(body: SendIn, request: Request):
    if not body.to.strip():
        raise HTTPException(422, "Number cannot be empty")
    if not body.content.strip():
        raise HTTPException(422, "Content cannot be empty")
    db = request.app.state.db
    mid = db.execute(
        "INSERT INTO messages (direction, receiver, content, status, raw) "
        "VALUES ('out', ?, ?, 'queued', '')",
        (body.to.strip(), body.content),
    )
    worker = request.app.state.worker
    if not worker:
        raise HTTPException(503, "Serial service not started")
    worker.send(mid, body.to.strip(), body.content)
    return {"id": mid, "status": "queued"}