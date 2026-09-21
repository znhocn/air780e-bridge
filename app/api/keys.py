"""API key management (admin auth)"""

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import schema
from ..auth import authenticate, format_ts, hash_api_key, new_api_key

router = APIRouter(prefix="/api/keys", tags=["keys"])


@router.get("", dependencies=[Depends(authenticate)])
def list_keys(request: Request):
    db = request.app.state.db
    rows = db.rows(
        "SELECT id,name,key_prefix,active,created_at,last_used FROM api_keys ORDER BY id DESC"
    )
    return [
        {
            **r,
            "created_at": format_ts(r.get("created_at")),
            "last_used": format_ts(r.get("last_used")),
        }
        for r in rows
    ]


@router.post("", dependencies=[Depends(authenticate)], status_code=201)
def create_key(body: schema.ApiKeyCreate, request: Request):
    db = request.app.state.db
    key = new_api_key()
    mid = db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix) VALUES (?,?,?)",
        (body.name, hash_api_key(key), key[:8]),
    )
    row = db.row("SELECT id,name,created_at FROM api_keys WHERE id=?", (mid,))
    return {
        "id": row["id"],
        "name": row["name"],
        "key": key,
        "key_prefix": key[:8],
        "created_at": format_ts(row.get("created_at")),
    }


@router.delete("/{key_id}", dependencies=[Depends(authenticate)])
def delete_key(key_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM api_keys WHERE id=?", (key_id,)):
        raise HTTPException(404, "API key not found")
    db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    return {"ok": True}


@router.post("/{key_id}/revoke", dependencies=[Depends(authenticate)])
def revoke_key(key_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM api_keys WHERE id=?", (key_id,)):
        raise HTTPException(404, "API key not found")
    db.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
    return {"ok": True}


@router.post("/{key_id}/enable", dependencies=[Depends(authenticate)])
def enable_key(key_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM api_keys WHERE id=?", (key_id,)):
        raise HTTPException(404, "API key not found")
    db.execute("UPDATE api_keys SET active=1 WHERE id=?", (key_id,))
    return {"ok": True}