"""Small shared helpers."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AuditRecord, Incident, NumberSequence, User, utcnow

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "are", "was", "were",
    "has", "have", "not", "but", "can", "cannot", "you", "its", "our",
}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def bump(obj, field: str = "updated_at") -> None:
    obj.row_version = int(obj.row_version or 1) + 1
    if hasattr(obj, field):
        setattr(obj, field, utcnow())


def audit(db: Session, user: User | None, action: str, resource_type: str, resource_id: str, details: dict | None = None) -> None:
    db.add(
        AuditRecord(
            account_id=user.account_id if user else details.get("account_id", "") if details else "",
            actor_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id or "",
            details=details or {},
        )
    )


def next_number(db: Session, prefix: str, sequence_name: str) -> str:
    row = db.get(NumberSequence, sequence_name)
    if row is None:
        row = NumberSequence(name=sequence_name, value=1000)
        db.add(row)
        db.flush()
    row.value += 1
    db.flush()
    return f"{prefix}{row.value:07d}"


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2 and token not in STOPWORDS]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return as_utc(parsed)


def sla_deadline(priority: str, opened_at: datetime | None = None) -> datetime:
    hours = {"1": 4, "2": 8, "3": 24, "4": 72, "5": 120}.get(str(priority), 24)
    start = as_utc(opened_at) or utcnow()
    return start + timedelta(hours=hours)


def touch_incident(incident: Incident) -> None:
    incident.updated_at = utcnow()
    incident.row_version = int(incident.row_version or 1) + 1


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())
