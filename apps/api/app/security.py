"""Authentication, password hashing, and RBAC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import ROLE_PERMISSIONS
from app.database import get_db
from app.errors import AppError
from app.models import User

_bearer = HTTPBearer(auto_error=False)
_buckets: dict[str, list[float]] = defaultdict(list)


def _iterations() -> int:
    raw = int(__import__("os").getenv("PASSWORD_ITERATIONS", "120000"))
    return min(max(raw, 1000), 400_000)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _iterations())
    return base64.b64encode(salt + digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(stored.encode())
    except Exception:
        return False
    salt, digest = raw[:16], raw[16:]
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _iterations())
    return hmac.compare_digest(digest, test)


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.access_token_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def authenticate_token(db: Session, token: str) -> User:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AppError("UNAUTHENTICATED", "Sign in again to continue.", 401) from exc
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise AppError("UNAUTHENTICATED", "Sign in again to continue.", 401)
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AppError("UNAUTHENTICATED", "Sign in again to continue.", 401)
    return authenticate_token(db, credentials.credentials)


def has_permission(user: User, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(user.role, set())
    return "*" in granted or permission in granted


def require(*permissions: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        missing = [item for item in permissions if not has_permission(user, item)]
        if missing:
            raise AppError("FORBIDDEN", "You do not have permission to perform this action.", 403)
        return user

    return dependency


def permission_list(user: User) -> list[str]:
    granted = ROLE_PERMISSIONS.get(user.role, set())
    if "*" in granted:
        names = set()
        for perms in ROLE_PERMISSIONS.values():
            names.update(item for item in perms if item != "*")
        names.update(
            {
                "admin.manage",
                "connector.admin",
                "knowledge.publish",
                "audit.read",
                "action.rollback",
            }
        )
        return sorted(names)
    return sorted(granted)


def rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    now = time.time()
    recent = [stamp for stamp in _buckets[key] if now - stamp < window_seconds]
    if len(recent) >= limit:
        raise AppError("RATE_LIMITED", "Too many requests. Try again shortly.", 429)
    recent.append(now)
    _buckets[key] = recent
