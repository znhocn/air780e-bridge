"""认证：管理端账户 + JWT + 客户端 API Key"""

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
    status_code=401, detail="未认证或凭证无效", headers={"WWW-Authenticate": "Bearer"}
)

PBKDF2_ROUNDS = 240_000


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "ak_" + secrets.token_urlsafe(28)


def hash_password(password: str) -> tuple[str, str]:
    """返回 (salt, password_hash)，PKDF2-SHA256。"""
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


def require_admin(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> HTTPAuthorizationCredentials:
    if cred is None or not verify_token(cred.credentials):
        raise UNAUTHORIZED
    return cred


def authenticate(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """统一鉴权：接受管理端 JWT 或启用的 API Key 两种凭证。

    管理页面使用 JWT，外部客户端使用 API Key，二者均可用同一套 REST API。
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