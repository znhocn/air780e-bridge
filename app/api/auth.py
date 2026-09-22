"""Login / first-deployment admin creation / change password"""

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import schema
from ..auth import create_token, hash_password, require_admin_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Simple in-memory login brute-force guard: lock a (IP, username) pair out briefly
# after several failed attempts within a window (PBKDF2 is the first line of defense).
# Scoping by IP+username means a failed guess on the admin name from one source
# never locks out the real admin from elsewhere.
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 600.0  # seconds; count resets after this
_LOGIN_BLOCK = 120.0  # seconds of lockout once the cap is hit
_LOGIN_ATTEMPTS: dict[str, list] = {}  # key -> [fail_count, last_fail_ts]
_login_lock = threading.Lock()

# setup () must create at most one admin even under concurrent first-boot requests
_setup_lock = threading.Lock()


def _login_key(request: Request, username: str) -> str:
    ip = (request.client.host if request.client else "") or ""
    return f"{ip}@{username}"


def _login_blocked(key: str) -> bool:
    with _login_lock:
        rec = _LOGIN_ATTEMPTS.get(key)
        if not rec:
            return False
        fails, last = rec
        if time.time() - last > _LOGIN_WINDOW:
            _LOGIN_ATTEMPTS.pop(key, None)
            return False
        return fails >= _LOGIN_MAX_FAILS


def _login_failed(key: str):
    now = time.time()
    with _login_lock:
        fails, last = _LOGIN_ATTEMPTS.get(key, (0, now))
        if fails == 0 or now - last > _LOGIN_WINDOW:
            last = now
            fails = 0
        _LOGIN_ATTEMPTS[key] = [fails + 1, last]


def _login_succeeded(key: str):
    with _login_lock:
        _LOGIN_ATTEMPTS.pop(key, None)


def _admin_count(db) -> int:
    return db.row("SELECT COUNT(*) AS c FROM admins")["c"]


@router.get("/setup-required")
def setup_required(request: Request):
    return {"setup_required": _admin_count(request.app.state.db) == 0}


@router.post("/setup", status_code=201)
def setup(body: schema.SetupRequest, request: Request):
    """Create the admin account on first deployment; disallowed once an admin exists."""
    db = request.app.state.db
    with _setup_lock:
        if _admin_count(db) > 0:
            raise HTTPException(409, "Admin already exists, please log in")
        username = body.username.strip()
        if not (2 <= len(username) <= 64):
            raise HTTPException(422, "Username must be 2-64 characters")
        if db.row("SELECT id FROM admins WHERE username=?", (username,)):
            raise HTTPException(409, "Username already exists")
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
    key = _login_key(request, username)
    if _login_blocked(key):
        raise HTTPException(429, "Too many failed login attempts, try again later")
    row = db.row(
        "SELECT username, password_hash, salt FROM admins WHERE username=?", (username,)
    )
    if not row or not verify_password(body.password, row["salt"], row["password_hash"]):
        _login_failed(key)
        raise HTTPException(401, "Invalid username or password")
    _login_succeeded(key)
    return {"token": create_token(row["username"])}


@router.post("/change-password")
def change_password(
    body: schema.ChangePasswordRequest,
    request: Request,
    username: str = Depends(require_admin_user),
):
    """Change the currently logged-in admin's password."""
    db = request.app.state.db
    row = db.row(
        "SELECT password_hash, salt FROM admins WHERE username=?", (username,)
    )
    if not row:
        raise HTTPException(404, "Admin not found")
    if not verify_password(body.current_password, row["salt"], row["password_hash"]):
        raise HTTPException(403, "Current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(422, "New password must differ from the current password")
    salt, ph = hash_password(body.new_password)
    db.execute(
        "UPDATE admins SET password_hash=?, salt=? WHERE username=?",
        (ph, salt, username),
    )
    return {"ok": True}