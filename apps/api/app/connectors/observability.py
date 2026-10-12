"""Observability connectors. Unconfigured adapters do not invent telemetry."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy.orm import Session

from app.constants import ENVIRONMENT_LABEL
from app.models import ApplicationLog, WorkflowInstance, utcnow
from app.util import as_utc, iso

DECLARED = {
    "easidoc": ["logs", "events", "metrics"],
    "splunk": ["logs", "events"],
    "datadog": ["logs", "metrics", "events"],
    "opensearch": ["logs", "events"],
    "azure_monitor": ["logs", "metrics", "traces"],
    "generic_logs": ["logs"],
}


class ConnectorResult(dict):
    pass


def not_configured(name: str, message: str) -> dict:
    return {
        "connector": name,
        "ok": False,
        "supported": False,
        "status": "not_configured",
        "message": message,
        "declared_capabilities": DECLARED.get(name, []),
        "logs": [],
        "metrics": [],
        "traces": [],
        "events": [],
    }


def normalize(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "timestamp": row.get("timestamp"),
                "level": row.get("level"),
                "message": row.get("message"),
                "signature": row.get("signature") or "",
                "correlation_id": row.get("correlation_id") or "",
                "attributes": row.get("attributes") or {},
                "source": row.get("source"),
                "environment": row.get("environment"),
            }
        )
    return normalized


class EasidocLogConnector:
    name = "easidoc"
    declared_capabilities = DECLARED["easidoc"]

    def __init__(self, db: Session, account_id: str):
        self.db = db
        self.account_id = account_id

    def connect(self) -> dict:
        return {"ok": True, "status": "connected", "connector": self.name, "environment_label": ENVIRONMENT_LABEL}

    def test_connection(self) -> dict:
        return {**self.connect(), "declared_capabilities": self.declared_capabilities, "message": "Easidoc simulator logs are available."}

    def query_logs(self, query: dict) -> dict:
        logs = self.db.query(ApplicationLog).filter(ApplicationLog.account_id == self.account_id, ApplicationLog.environment == "simulator")
        if query.get("application_id"):
            logs = logs.filter(ApplicationLog.application_id == query["application_id"])
        if query.get("incident_id"):
            logs = logs.filter(ApplicationLog.incident_id == query["incident_id"])
        if query.get("document_id"):
            logs = logs.filter(ApplicationLog.document_id == query["document_id"])
        if query.get("signature"):
            logs = logs.filter(ApplicationLog.signature == query["signature"])
        if query.get("correlation_id"):
            logs = logs.filter(ApplicationLog.correlation_id == query["correlation_id"])
        if query.get("level"):
            logs = logs.filter(ApplicationLog.level == query["level"].upper())
        if query.get("q"):
            logs = logs.filter(ApplicationLog.message.ilike(f"%{query['q'][:120]}%"))
        time_from = query.get("time_from")
        time_to = query.get("time_to")
        rows = logs.order_by(ApplicationLog.timestamp.asc()).all()
        filtered = []
        for row in rows:
            stamp = as_utc(row.timestamp)
            if time_from and stamp and stamp < time_from:
                continue
            if time_to and stamp and stamp > time_to:
                continue
            filtered.append(row)
        limit = min(int(query.get("limit") or 200), 200)
        truncated = len(filtered) > limit
        selected = filtered[-limit:]
        payload = [self._row(row) for row in selected]
        return {
            "connector": self.name,
            "ok": True,
            "supported": True,
            "status": "ok",
            "environment": "simulator",
            "environment_label": ENVIRONMENT_LABEL,
            "declared_capabilities": self.declared_capabilities,
            "logs": normalize(payload),
            "frequency": _frequency(payload),
            "groups": _groups(payload),
            "baseline": _baseline(payload, query.get("opened_at")),
            "correlation_ids": sorted({row["correlation_id"] for row in payload if row["correlation_id"]}),
            "signatures": sorted({row["signature"] for row in payload if row["signature"]}),
            "truncated": truncated,
        }

    def get_metrics(self, _query: dict | None = None) -> dict:
        workflows = self.db.query(WorkflowInstance).filter(WorkflowInstance.account_id == self.account_id).all()
        counts = Counter(row.status for row in workflows)
        return {
            "connector": self.name,
            "ok": True,
            "supported": True,
            "environment": "simulator",
            "environment_label": ENVIRONMENT_LABEL,
            "metrics": [{"name": f"workflow.{key}", "value": value, "simulated": True} for key, value in sorted(counts.items())],
        }

    def get_traces(self, _query: dict | None = None) -> dict:
        return {
            "connector": self.name,
            "ok": False,
            "supported": False,
            "status": "unsupported",
            "message": "The Easidoc simulator does not emit distributed traces.",
            "traces": [],
        }

    def get_events(self, query: dict | None = None) -> dict:
        from app.events import serialize_event
        from app.models import ApplicationEvent

        limit = min(int((query or {}).get("limit") or 100), 200)
        rows = (
            self.db.query(ApplicationEvent)
            .filter(ApplicationEvent.account_id == self.account_id, ApplicationEvent.environment == "simulator")
            .order_by(ApplicationEvent.id.desc())
            .limit(limit)
            .all()
        )
        return {"connector": self.name, "ok": True, "supported": True, "events": [serialize_event(row) for row in reversed(rows)]}

    def normalize_results(self, raw: dict) -> list[dict]:
        return normalize(raw.get("logs") or [])

    def _row(self, row: ApplicationLog) -> dict:
        return {
            "id": row.id,
            "timestamp": iso(row.timestamp),
            "level": row.level,
            "message": row.message,
            "signature": row.signature,
            "correlation_id": row.correlation_id,
            "attributes": row.attributes or {},
            "source": "easidoc_simulator",
            "environment": "simulator",
            "document_id": row.document_id,
            "incident_id": row.incident_id,
        }


class RemoteConnector:
    def __init__(self, name: str, configured: bool, detail: str = ""):
        self.name = name
        self.configured = configured
        self.detail = detail
        self.declared_capabilities = DECLARED.get(name, [])

    def connect(self) -> dict:
        if not self.configured:
            return not_configured(self.name, self.detail or f"{self.name} credentials are not configured.")
        return {"ok": True, "status": "configured", "connector": self.name}

    def test_connection(self) -> dict:
        if not self.configured:
            return not_configured(self.name, self.detail or f"{self.name} credentials are not configured.")
        return {
            "connector": self.name,
            "ok": False,
            "supported": True,
            "status": "not_verified",
            "message": "Credentials are present, but this process has not completed a live handshake in this request.",
            "declared_capabilities": self.declared_capabilities,
        }

    def query_logs(self, _query: dict) -> dict:
        return not_configured(self.name, self.detail or f"{self.name} is not configured. No logs were invented.")

    def get_metrics(self, _query: dict | None = None) -> dict:
        if "metrics" not in self.declared_capabilities:
            return {"connector": self.name, "ok": False, "supported": False, "status": "unsupported", "message": f"{self.name} metrics are not declared.", "metrics": []}
        return not_configured(self.name, f"{self.name} metrics are not configured.")

    def get_traces(self, _query: dict | None = None) -> dict:
        if "traces" not in self.declared_capabilities:
            return {"connector": self.name, "ok": False, "supported": False, "status": "unsupported", "message": f"{self.name} traces are not declared.", "traces": []}
        return not_configured(self.name, f"{self.name} traces are not configured.")

    def get_events(self, _query: dict | None = None) -> dict:
        if "events" not in self.declared_capabilities:
            return {"connector": self.name, "ok": False, "supported": False, "status": "unsupported", "message": f"{self.name} events are not declared.", "events": []}
        return not_configured(self.name, f"{self.name} events are not configured.")

    def normalize_results(self, raw: dict) -> list[dict]:
        return normalize(raw.get("logs") or [])


def build_connector(name: str, db: Session, account_id: str, configured: bool = False):
    if name in {"easidoc", "easidoc_simulator"}:
        return EasidocLogConnector(db, account_id)
    return RemoteConnector(name, configured=configured)


def _frequency(rows: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        stamp = (row.get("timestamp") or "")[:16]
        bucket = buckets.setdefault(stamp, {"bucket": stamp, "errors": 0, "warnings": 0, "info": 0})
        level = (row.get("level") or "").upper()
        if level == "ERROR":
            bucket["errors"] += 1
        elif level == "WARN":
            bucket["warnings"] += 1
        else:
            bucket["info"] += 1
    return list(buckets.values())


def _groups(rows: list[dict]) -> list[dict]:
    counter: Counter = Counter(row.get("signature") or "(none)" for row in rows)
    return [{"signature": key, "count": value} for key, value in counter.most_common(12)]


def _baseline(rows: list[dict], opened_at) -> dict:
    opened = as_utc(opened_at) if not isinstance(opened_at, str) else None
    if isinstance(opened_at, str):
        from app.util import parse_dt

        opened = parse_dt(opened_at)
    if opened is None:
        errors = sum(1 for row in rows if row.get("level") == "ERROR")
        return {"available": False, "incident_window_errors": errors, "baseline_errors": None}
    incident_errors = 0
    baseline_errors = 0
    for row in rows:
        from app.util import parse_dt

        stamp = parse_dt(row.get("timestamp"))
        if stamp is None or row.get("level") != "ERROR":
            continue
        if opened - timedelta(hours=2) <= stamp < opened - timedelta(minutes=15):
            baseline_errors += 1
        elif opened - timedelta(minutes=15) <= stamp <= opened + timedelta(minutes=30):
            incident_errors += 1
    return {
        "available": True,
        "incident_window_errors": incident_errors,
        "baseline_errors": baseline_errors,
        "opened_at": iso(opened),
    }
