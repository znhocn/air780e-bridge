"""Authentication: admin account + JWT + client API key."""

import hashlib
import hmac
import secrets
import time
from datetime import datetime

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

bearer = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=401, detail="Not authenticated or invalid credentials", headers={"WWW-Authenticate": "Bearer"}
)

PBKDF2_ROUNDS = 240_000


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "ak_" + secrets.token_urlsafe(28)


def hash_password(password: str) -> tuple[str, str]:
    """Returns (salt, password_hash) using PBKDF2-SHA256."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS
    )
    return salt, dk.hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS
    )
    return hmac.compare_digest(dk.hex(), expected)


def create_token(sub: str = "admin") -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "iat": now,
        "exp": now + settings.jwt_expires_hours * 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        return False


def format_ts(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return s


def require_admin_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    """Admin-only dependency that returns the authenticated admin's username."""
    if cred is None:
        raise UNAUTHORIZED
    try:
        data = jwt.decode(cred.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise UNAUTHORIZED
    username = data.get("sub")
    if not username:
        raise UNAUTHORIZED
    return username


def authenticate(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """Unified auth: accepts either an admin JWT or an enabled API key.

    The admin page uses JWT; external clients use API keys. Both work against
    the same REST API.
    """
    if cred is None:
        raise UNAUTHORIZED
    if verify_token(cred.credentials):
        return {"type": "admin", "name": "admin"}
    db = request.app.state.db
    row = db.row(
        "SELECT * FROM api_keys WHERE key_hash=? AND active=1",
        (hash_api_key(cred.credentials),),
    )
    if not row:
        raise UNAUTHORIZED
    db.execute(
        "UPDATE api_keys SET last_used=datetime('now','localtime') WHERE id=?",
        (row["id"],),
    )
    return {"type": "apikey", "name": row["name"], "id": row["id"]}