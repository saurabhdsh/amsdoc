"""In-process event bus. Database rows are the source of truth; sockets only broadcast."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

pending_events: ContextVar[list[dict] | None] = ContextVar("ams_pending_events", default=None)


class EventBus:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: list[asyncio.Queue] = []

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def publish(self, event: dict[str, Any]) -> None:
        loop = self.loop
        for queue in list(self._subscribers):
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(_offer, queue, event)
            else:
                _offer(queue, event)


def _offer(queue: asyncio.Queue, event: dict) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        return


bus = EventBus()


def queue_event(db, **kwargs) -> dict:
    from app.models import ApplicationEvent, utcnow

    event = ApplicationEvent(
        account_id=kwargs["account_id"],
        application_id=kwargs.get("application_id"),
        scenario_execution_id=kwargs.get("scenario_execution_id"),
        incident_id=kwargs.get("incident_id"),
        document_id=kwargs.get("document_id"),
        name=kwargs["name"],
        source=kwargs.get("source", "ams"),
        environment=kwargs.get("environment", "simulator"),
        payload=kwargs.get("payload") or {},
        created_at=utcnow(),
    )
    db.add(event)
    db.flush()
    data = serialize_event(event)
    db.info.setdefault("ams_events", []).append(data)
    return data


def serialize_event(event) -> dict:
    created = event.created_at
    if created is not None and created.tzinfo is None:
        from datetime import timezone

        created = created.replace(tzinfo=timezone.utc)
    return {
        "type": "event",
        "id": event.id,
        "event_uuid": event.event_uuid,
        "account_id": event.account_id,
        "application_id": event.application_id,
        "scenario_execution_id": event.scenario_execution_id,
        "incident_id": event.incident_id,
        "document_id": event.document_id,
        "name": event.name,
        "source": event.source,
        "environment": event.environment,
        "payload": event.payload or {},
        "created_at": created.isoformat() if created else None,
    }
