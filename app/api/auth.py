"""登录 / 首次部署创建管理员"""

from fastapi import APIRouter, HTTPException, Request

from .. import schema
from ..auth import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _admin_count(db) -> int:
    return db.row("SELECT COUNT(*) AS c FROM admins")["c"]


@router.get("/setup-required")
def setup_required(request: Request):
    return {"setup_required": _admin_count(request.app.state.db) == 0}


@router.post("/setup", status_code=201)
def setup(body: schema.SetupRequest, request: Request):
    """首次部署创建管理员账户；已有管理员后不再允许。"""
    db = request.app.state.db
    if _admin_count(db) > 0:
        raise HTTPException(409, "管理员已存在，请直接登录")
    username = body.username.strip()
    if not (2 <= len(username) <= 64):
        raise HTTPException(422, "用户名长度需为 2-64 个字符")
    if db.row("SELECT id FROM admins WHERE username=?", (username,)):
        raise HTTPException(409, "用户名已存在")
    salt, ph = hash_password(body.password)
    db.execute(
        "INSERT INTO admins (username, password_hash, salt) VALUES (?,?,?)",
        (username, ph, salt),
    )
    return {"ok": True, "token": create_token(username)}


@router.post("/login")
def login(body: schema.LoginRequest, request: Request):
    db = request.app.state.db
    username = body.username.strip()
    row = db.row(
        "SELECT username, password_hash, salt FROM admins WHERE username=?", (username,)
    )
    if not row or not verify_password(body.password, row["salt"], row["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(row["username"])}