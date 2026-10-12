"""Idempotency keys for mutation endpoints."""

from __future__ import annotations

import hashlib
import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import IdempotencyRecord, User


def request_hash(body: dict) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def begin_idempotent(db: Session, user: User, request: Request, body: dict) -> dict | None:
    key = request.headers.get("idempotency-key", "").strip()
    if not key:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header with this request.", 400)
    if len(key) > 200:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is too long.", 400)
    digest = request_hash(body)
    existing = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.account_id == user.account_id, IdempotencyRecord.key == key)
        .one_or_none()
    )
    if existing is None:
        return None
    if existing.request_hash != digest:
        raise AppError("IDEMPOTENCY_CONFLICT", "This Idempotency-Key was already used with a different request.", 409)
    return {"code": existing.response_code, "body": json.loads(existing.response_body)}


def store_idempotent(db: Session, user: User, request: Request, body: dict, code: int, response: dict) -> None:
    key = request.headers.get("idempotency-key", "").strip()
    db.add(
        IdempotencyRecord(
            account_id=user.account_id,
            key=key,
            request_hash=request_hash(body),
            response_code=code,
            response_body=json.dumps(response),
        )
    )
