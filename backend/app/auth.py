from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

_UPPER_RE = re.compile(r"[A-Z]")
_LOWER_RE = re.compile(r"[a-z]")
_DIGIT_RE = re.compile(r"\d")


def validate_password_strength(password: str) -> None:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if len(password) > 128:
        raise ValueError("Password must not exceed 128 characters")
    if not _UPPER_RE.search(password) or not _LOWER_RE.search(password) or not _DIGIT_RE.search(password):
        raise ValueError(
            "Password must include at least one uppercase letter, one lowercase letter, and one digit"
        )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: uuid.UUID, email: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def revoke_access_token(token: str) -> None:
    payload = decode_access_token(token)
    jti = payload.get("jti")
    if not jti:
        return
    exp = payload.get("exp")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    ttl = max(1, int(exp) - now_ts) if exp else get_settings().jwt_expire_minutes * 60
    try:
        get_redis().setex(f"revoked_jti:{jti}", ttl, "1")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist JWT revocation in Redis: %s", exc)


def is_token_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    try:
        return bool(get_redis().exists(f"revoked_jti:{jti}"))
    except Exception as exc:  # noqa: BLE001
        if get_settings().environment.strip().lower() == "production":
            logger.error("Redis unavailable during token revocation check: %s", exc)
            return True
        return False


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(creds.credentials)
    if is_token_revoked(payload.get("jti")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    try:
        user_id = uuid.UUID(payload.get("sub", ""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
