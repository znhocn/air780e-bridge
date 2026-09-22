"""Notify configs and forward logs (admin auth)"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import schema
from ..auth import authenticate, format_ts, require_admin_user
from ..forwarder import CHANNEL_FIELDS, CHANNEL_LABELS, build_apprise_url, target_display

router = APIRouter(prefix="/api", tags=["notify"])

CHANNEL_TYPES = {"dingtalk", "wecom", "feishu", "telegram", "email", "webhook", "apprise"}


def _validate(type_: str, params: dict):
    if type_ not in CHANNEL_TYPES:
        raise HTTPException(422, f"Unsupported channel: {type_}")
    required = [f["key"] for f in CHANNEL_FIELDS[type_] if f.get("required")]
    for k in required:
        if not str(params.get(k) or "").strip():
            raise HTTPException(422, f"Missing required param: {k}")
    if type_ != "webhook":
        try:
            build_apprise_url(type_, params)  # structural validation; 422 if the Apprise URL cannot be built
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


def _row_out(cfg: dict) -> dict:
    try:
        params = json.loads(cfg.get("params") or "{}") or {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "type": cfg["type"],
        "type_label": CHANNEL_LABELS.get(cfg["type"], cfg["type"]),
        "enabled": cfg["enabled"],
        "match_from": cfg.get("match_from", ""),
        "match_contains": cfg.get("match_contains", ""),
        "params": params,
        "target": target_display(cfg, params),
        "created_at": format_ts(cfg.get("created_at")),
    }


@router.get("/notify-configs/types", dependencies=[Depends(authenticate)])
def notify_types():
    return {
        t: {"label": CHANNEL_LABELS[t], "fields": CHANNEL_FIELDS[t]}
        for t in CHANNEL_TYPES
    }


@router.get("/notify-configs", dependencies=[Depends(require_admin_user)])
def list_configs(request: Request):
    """Admin-only read: the returned `params` contain plaintext channel secrets
    (bot tokens / SMTP passwords), so API-key holders must not see them."""
    db = request.app.state.db
    return [_row_out(r) for r in db.rows("SELECT * FROM notify_configs ORDER BY id")]


@router.post("/notify-configs", dependencies=[Depends(require_admin_user)], status_code=201)
def create_config(body: schema.NotifyIn, request: Request):
    body.type = body.type.lower()
    _validate(body.type, body.params)
    db = request.app.state.db
    mid = db.execute(
        "INSERT INTO notify_configs (name, type, enabled, match_from, match_contains, params) "
        "VALUES (?,?,?,?,?,?)",
        (
            body.name.strip(),
            body.type,
            1 if body.enabled else 0,
            body.match_from.strip(),
            body.match_contains.strip(),
            json.dumps(body.params, ensure_ascii=False),
        ),
    )
    return _row_out(db.row("SELECT * FROM notify_configs WHERE id=?", (mid,)))


@router.put("/notify-configs/{cfg_id}", dependencies=[Depends(require_admin_user)])
def update_config(cfg_id: int, body: schema.NotifyIn, request: Request):
    body.type = body.type.lower()
    _validate(body.type, body.params)
    db = request.app.state.db
    if not db.row("SELECT id FROM notify_configs WHERE id=?", (cfg_id,)):
        raise HTTPException(404, "Notify config not found")
    db.execute(
        "UPDATE notify_configs SET name=?, type=?, enabled=?, match_from=?, match_contains=?, params=? WHERE id=?",
        (
            body.name.strip(),
            body.type,
            1 if body.enabled else 0,
            body.match_from.strip(),
            body.match_contains.strip(),
            json.dumps(body.params, ensure_ascii=False),
            cfg_id,
        ),
    )
    return _row_out(db.row("SELECT * FROM notify_configs WHERE id=?", (cfg_id,)))


@router.delete("/notify-configs/{cfg_id}", dependencies=[Depends(require_admin_user)])
def delete_config(cfg_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM notify_configs WHERE id=?", (cfg_id,)):
        raise HTTPException(404, "Notify config not found")
    db.execute("DELETE FROM notify_configs WHERE id=?", (cfg_id,))
    return {"ok": True}


@router.post("/notify-configs/{cfg_id}/test", dependencies=[Depends(require_admin_user)])
def test_config(cfg_id: int, request: Request):
    db = request.app.state.db
    cfg = db.row("SELECT * FROM notify_configs WHERE id=?", (cfg_id,))
    if not cfg:
        raise HTTPException(404, "Notify config not found")
    forwarder = request.app.state.forwarder
    return forwarder.test(db, cfg)


@router.get("/forward-logs", dependencies=[Depends(authenticate)])
def list_logs(request: Request, limit: int = 100, message_id: int = 0):
    db = request.app.state.db
    total = db.row("SELECT COUNT(*) AS c FROM forward_logs")["c"]
    rows = db.rows(
        "SELECT * FROM forward_logs WHERE (?=0 OR message_id=?) ORDER BY id DESC LIMIT ?",
        (message_id, message_id, limit),
    )
    return {"total": total, "items": rows}