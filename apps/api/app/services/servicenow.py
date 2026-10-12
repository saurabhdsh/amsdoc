"""ServiceNow table API client and synchronization.

A missing configuration returns not_configured. This module never fabricates incidents.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import ConnectorConfig, Incident, KnowledgeDocument, SyncCheckpoint, User, utcnow
from app.services import knowledge as knowledge_service
from app.util import iso, next_number, sla_deadline

FILTER_PATTERN = re.compile(r"[A-Za-z0-9 ._-]{0,80}")
SN_STATE = {"1": "new", "2": "in_progress", "3": "on_hold", "6": "resolved", "7": "closed", "8": "canceled"}


class ServiceNowClient:
    def __init__(self, base_url: str, username: str, password: str, *, transport=None, backoff: tuple[float, ...] = (0.4, 0.8, 1.6), min_interval: float = 0.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.backoff = backoff
        self.min_interval = min_interval
        self._transport = transport
        self._last_call = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    def test_connection(self) -> dict:
        if not self.configured:
            return {"ok": False, "status": "not_configured", "message": "ServiceNow instance URL and credentials are not configured."}
        incident = self._get("/api/now/table/incident", params={"sysparm_limit": "1", "sysparm_fields": "sys_id,number"})
        knowledge_read = True
        knowledge_error = ""
        try:
            self._get("/api/now/table/kb_knowledge", params={"sysparm_limit": "1", "sysparm_fields": "sys_id,number"})
        except AppError as exc:
            knowledge_read = False
            knowledge_error = exc.message
        return {
            "ok": True,
            "status": "connected",
            "incident_read": True,
            "knowledge_read": knowledge_read,
            "knowledge_error": knowledge_error,
            "sample_present": bool((incident.get("result") or [])),
            "message": "ServiceNow accepted the incident read request.",
        }

    def fetch_incidents(self, query: str, offset: int, limit: int) -> list[dict]:
        payload = self._get(
            "/api/now/table/incident",
            params={
                "sysparm_query": query,
                "sysparm_limit": str(limit),
                "sysparm_offset": str(offset),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
        )
        result = payload.get("result") or []
        if not isinstance(result, list):
            raise AppError("SERVICENOW_ERROR", "ServiceNow returned an unexpected incident payload.", 502)
        return result

    def fetch_knowledge(self, offset: int, limit: int) -> list[dict]:
        payload = self._get(
            "/api/now/table/kb_knowledge",
            params={
                "sysparm_query": "workflow_state=published^ORDERBYsys_updated_on",
                "sysparm_limit": str(limit),
                "sysparm_offset": str(offset),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
        )
        result = payload.get("result") or []
        return result if isinstance(result, list) else []

    def get_incident(self, sys_id: str) -> dict:
        payload = self._get(f"/api/now/table/incident/{sys_id}", params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"})
        return payload.get("result") or {}

    def get_by_number(self, number: str) -> dict:
        if not FILTER_PATTERN.fullmatch(number):
            raise AppError("INVALID_FILTER", "Incident number contains unsupported characters.", 422)
        rows = self.fetch_incidents(f"number={number}", 0, 1)
        if not rows:
            raise AppError("NOT_FOUND", f"ServiceNow did not return incident {number}.", 404)
        return rows[0]

    def fetch_journal(self, sys_id: str) -> list[dict]:
        if not FILTER_PATTERN.fullmatch(sys_id):
            raise AppError("INVALID_FILTER", "ServiceNow sys_id contains unsupported characters.", 422)
        payload = self._get(
            "/api/now/table/sys_journal_field",
            params={
                "sysparm_query": f"element_id={sys_id}^ORDERBYsys_created_on",
                "sysparm_limit": "20",
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,sys_created_on,element,value",
            },
        )
        result = payload.get("result") or []
        return result if isinstance(result, list) else []

    def search_syslog(self, terms: list[str]) -> list[dict]:
        lines: list[dict] = []
        seen: set[str] = set()
        for term in terms[:4]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", term):
                continue
            payload = self._get(
                "/api/now/table/syslog",
                params={
                    "sysparm_query": f"messageLIKE{term}^ORDERBYDESCsys_created_on",
                    "sysparm_limit": "8",
                    "sysparm_display_value": "true",
                    "sysparm_fields": "sys_id,sys_created_on,level,source,message",
                },
            )
            for row in payload.get("result") or []:
                message = str(row.get("message") or "")
                if term.lower() not in message.lower():
                    continue
                key = str(row.get("sys_id") or message[:80])
                if key in seen:
                    continue
                seen.add(key)
                lines.append(
                    {
                        "id": key,
                        "timestamp": str(row.get("sys_created_on") or ""),
                        "level": str(row.get("level") or ""),
                        "source": str(row.get("source") or "syslog"),
                        "message": message[:500],
                        "signature": term,
                        "correlation_id": "",
                    }
                )
        return lines[:8]

    def add_work_note(self, sys_id: str, note: str, expected_mod_count: str | None) -> dict:
        current = self.get_incident(sys_id)
        current_mod = _value(current.get("sys_mod_count"))
        if expected_mod_count is not None and current_mod and str(current_mod) != str(expected_mod_count):
            raise AppError("STALE_RECORD", "The ServiceNow incident changed after it was last read.", 409, {"sys_mod_count": current_mod})
        patched = self._patch(f"/api/now/table/incident/{sys_id}", {"work_notes": note})
        verified = note in json.dumps(patched.get("result") or {})
        journal_error = ""
        if not verified:
            try:
                journals = self._get(
                    "/api/now/table/sys_journal_field",
                    params={"sysparm_query": f"element_id={sys_id}^element=work_notes", "sysparm_limit": "20", "sysparm_display_value": "true"},
                )
                verified = any(note in str(row.get("value") or "") for row in journals.get("result") or [])
            except AppError as exc:
                journal_error = exc.message
        return {
            "status": "confirmed" if verified else "accepted_unverified",
            "sys_id": sys_id,
            "verified": verified,
            "journal_error": journal_error,
            "response_number": _display((patched.get("result") or {}).get("number")),
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._send("GET", path, params=params)

    def _patch(self, path: str, body: dict) -> dict:
        return self._send("PATCH", path, json_body=body)

    def _send(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        if not self.configured:
            raise AppError("NOT_CONFIGURED", "ServiceNow is not configured.", 409)
        url = f"{self.base_url}{path}"
        last_error: AppError | None = None
        attempts = len(self.backoff) + 1
        for attempt in range(attempts):
            if self.min_interval:
                wait = self.min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
            try:
                with httpx.Client(transport=self._transport, timeout=20.0) as client:
                    response = client.request(method, url, params=params, json=json_body, auth=(self.username, self.password), headers={"Accept": "application/json"})
                self._last_call = time.monotonic()
            except httpx.TransportError as exc:
                last_error = AppError("UPSTREAM_UNAVAILABLE", "ServiceNow could not be reached.", 502)
                if attempt < attempts - 1:
                    time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                    continue
                raise last_error from exc
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = AppError(
                    "UPSTREAM_THROTTLED" if response.status_code == 429 else "UPSTREAM_ERROR",
                    f"ServiceNow returned {response.status_code}.",
                    502,
                )
                if attempt < attempts - 1:
                    time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
                    continue
                raise last_error
            if response.status_code in {401, 403}:
                raise AppError("SERVICENOW_AUTH", "ServiceNow rejected the credentials or the account lacks permission.", 502)
            if response.status_code >= 400:
                raise AppError("SERVICENOW_ERROR", response.text[:400] or "ServiceNow request failed.", 502)
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise AppError("SERVICENOW_ERROR", "ServiceNow returned a response that was not JSON.", 502) from exc
        raise last_error or AppError("SERVICENOW_ERROR", "ServiceNow request failed.", 502)


def resolve_client(db: Session, user: User, *, transport=None, backoff: tuple[float, ...] = (0.4, 0.8, 1.6)) -> tuple[ServiceNowClient, dict, ConnectorConfig | None]:
    settings = get_settings()
    row = db.query(ConnectorConfig).filter_by(account_id=user.account_id, connector="servicenow").one_or_none()
    stored = row.settings or {} if row else {}
    if settings.servicenow_instance_url and settings.servicenow_username and settings.servicenow_password:
        client = ServiceNowClient(
            settings.servicenow_instance_url,
            settings.servicenow_username,
            settings.servicenow_password,
            transport=transport,
            backoff=backoff,
        )
        return client, stored, row
    if row and row.secret_blob:
        try:
            secret = json.loads(_decrypt(row.secret_blob))
        except Exception as exc:
            raise AppError("SECRET_UNREADABLE", "Stored ServiceNow credentials could not be decrypted.", 409) from exc
        client = ServiceNowClient(stored.get("instance_url", ""), secret.get("username", ""), secret.get("password", ""), transport=transport, backoff=backoff)
        return client, stored, row
    return ServiceNowClient("", "", "", transport=transport, backoff=backoff), stored, row


def public_config(db: Session, user: User) -> dict:
    client, stored, row = resolve_client(db, user)
    return {
        "connector": "servicenow",
        "configured": client.configured,
        "status": "not_configured" if not client.configured else (row.status if row else "configured"),
        "instance_url": stored.get("instance_url") or (get_settings().servicenow_instance_url if client.configured else ""),
        "username": _username(db, user) if client.configured else "",
        "password_set": client.configured,
        "assignment_group": stored.get("assignment_group", ""),
        "priorities": stored.get("priorities") or [],
        "historical_days": stored.get("historical_days", 30),
        "sync_frequency_seconds": stored.get("sync_frequency_seconds", 300),
        "write_mode": stored.get("write_mode", "read_only"),
        "auto_sync": bool(stored.get("auto_sync", False)),
        "last_tested_at": iso(row.last_tested_at) if row else None,
        "last_error": row.last_error if row else "",
        "live_integration": False,
    }


def save_config(db: Session, user: User, payload: dict) -> dict:
    instance_url = (payload.get("instance_url") or "").strip().rstrip("/")
    if instance_url and not instance_url.startswith("https://"):
        raise AppError("INVALID_CONFIGURATION", "ServiceNow instance URL must start with https://.", 422)
    group = payload.get("assignment_group") or ""
    if not FILTER_PATTERN.fullmatch(group):
        raise AppError("INVALID_FILTER", "Assignment group filter contains unsupported characters.", 422)
    priorities = [str(item) for item in payload.get("priorities") or []]
    if any(item not in {"1", "2", "3", "4", "5"} for item in priorities):
        raise AppError("INVALID_FILTER", "Priorities must be between 1 and 5.", 422)
    write_mode = payload.get("write_mode") or "read_only"
    if write_mode not in {"read_only", "approved_write"}:
        raise AppError("INVALID_CONFIGURATION", "Write mode must be read_only or approved_write.", 422)
    historical_days = int(payload.get("historical_days") or 30)
    frequency = int(payload.get("sync_frequency_seconds") or 300)
    if not 1 <= historical_days <= 365 or not 60 <= frequency <= 86400:
        raise AppError("INVALID_CONFIGURATION", "Check the historical window and sync frequency.", 422)
    row = db.query(ConnectorConfig).filter_by(account_id=user.account_id, connector="servicenow").one_or_none()
    if row is None:
        row = ConnectorConfig(account_id=user.account_id, connector="servicenow", settings={}, secret_blob="")
        db.add(row)
    username = payload.get("username") or ""
    password = payload.get("password")
    existing_secret = {}
    if row.secret_blob:
        try:
            existing_secret = json.loads(_decrypt(row.secret_blob))
        except Exception:
            existing_secret = {}
    if password:
        secret = {"username": username or existing_secret.get("username", ""), "password": password}
        row.secret_blob = _encrypt(json.dumps(secret))
    elif username and existing_secret.get("password"):
        existing_secret["username"] = username
        row.secret_blob = _encrypt(json.dumps(existing_secret))
    row.settings = {
        "instance_url": instance_url,
        "assignment_group": group,
        "priorities": priorities,
        "historical_days": historical_days,
        "sync_frequency_seconds": frequency,
        "write_mode": write_mode,
        "auto_sync": bool(payload.get("auto_sync", False)),
    }
    row.status = "configured" if (password or existing_secret.get("password") or get_settings().servicenow_password) and instance_url else "not_configured"
    row.last_error = ""
    db.flush()
    return public_config(db, user)


def test_and_record(db: Session, user: User) -> dict:
    client, _stored, row = resolve_client(db, user)
    if row is None:
        row = ConnectorConfig(account_id=user.account_id, connector="servicenow", settings={}, secret_blob="")
        db.add(row)
        db.flush()
    try:
        result = client.test_connection()
    except AppError as exc:
        row.status = "error"
        row.last_error = exc.message
        row.last_tested_at = utcnow()
        raise
    row.last_tested_at = utcnow()
    row.last_error = "" if result.get("ok") else result.get("message", "")
    row.status = "connected" if result.get("ok") else result.get("status", "error")
    result["live_integration"] = bool(result.get("ok"))
    return result


def sync_account(db: Session, user: User, *, transport=None, backoff: tuple[float, ...] = (0.4, 0.8, 1.6)) -> dict:
    client, stored, _row = resolve_client(db, user, transport=transport, backoff=backoff)
    if not client.configured:
        raise AppError("NOT_CONFIGURED", "ServiceNow is not configured. No incidents were synchronized.", 409)
    checkpoint = db.query(SyncCheckpoint).filter_by(account_id=user.account_id, connector="servicenow").one_or_none()
    if checkpoint is None:
        checkpoint = SyncCheckpoint(account_id=user.account_id, connector="servicenow", cursor="")
        db.add(checkpoint)
        db.flush()
    since = checkpoint.cursor
    if not since:
        since = (utcnow() - timedelta(days=int(stored.get("historical_days") or 30))).strftime("%Y-%m-%d %H:%M:%S")
    clauses = [f"sys_updated_on>{since}", "ORDERBYsys_updated_on"]
    group = stored.get("assignment_group") or ""
    if group:
        clauses.insert(0, f"assignment_group.name={group}")
    priorities = stored.get("priorities") or []
    if priorities:
        clauses.insert(0, "priorityIN" + ",".join(priorities))
    query = "^".join(clauses)
    offset = 0
    created = 0
    updated = 0
    latest = since
    while offset <= 5000:
        page = client.fetch_incidents(query, offset, 100)
        if not page:
            break
        for record in page:
            sys_id = _value(record.get("sys_id"))
            if not sys_id:
                continue
            action = _upsert_incident(db, user, record)
            created += action == "created"
            updated += action == "updated"
            stamp = _value(record.get("sys_updated_on"))
            if stamp and stamp > latest:
                latest = stamp
        if len(page) < 100:
            break
        offset += 100
    knowledge = _sync_knowledge(db, user, client)
    checkpoint.cursor = latest
    checkpoint.updated_at = utcnow()
    db.flush()
    return {
        "ok": True,
        "status": "synchronized",
        "live_integration": True,
        "created": created,
        "updated": updated,
        "knowledge_upserted": knowledge,
        "cursor": latest,
    }


def link_incident(db: Session, user: User, incident: Incident, number: str) -> dict:
    client, _stored, _row = resolve_client(db, user)
    if not client.configured:
        raise AppError("NOT_CONFIGURED", "ServiceNow is not configured. The incident was not linked.", 409)
    record = client.get_by_number(number)
    incident.external_sys_id = _value(record.get("sys_id"))
    incident.external_number = _display(record.get("number"))
    incident.sys_mod_count = _value(record.get("sys_mod_count"))
    incident.servicenow_sync_status = "linked"
    db.flush()
    return {"linked": True, "external_number": incident.external_number, "external_sys_id": incident.external_sys_id, "live_integration": True}


def post_work_note(db: Session, user: User, incident: Incident, note: str) -> dict:
    _client, stored, _row = resolve_client(db, user)
    if not incident.external_sys_id:
        return {"ok": True, "status": "not_linked", "live_integration": False, "message": "No ServiceNow incident is linked. Nothing was sent."}
    if stored.get("write_mode") != "approved_write":
        return {"ok": True, "status": "skipped_read_only", "live_integration": False, "message": "ServiceNow writes are disabled. The connector is read only."}
    client, _stored, _row = resolve_client(db, user)
    if not client.configured:
        return {"ok": True, "status": "not_configured", "live_integration": False, "message": "ServiceNow is not configured. Nothing was sent."}
    result = client.add_work_note(incident.external_sys_id, note, incident.sys_mod_count)
    incident.servicenow_sync_status = result["status"]
    journal = list(incident.journal or [])
    journal.append({"at": iso(utcnow()), "type": "work_note", "text": note, "servicenow_status": result["status"]})
    incident.journal = journal
    return {"ok": True, "live_integration": result["status"] == "confirmed", **result}


def _upsert_incident(db: Session, user: User, record: dict) -> str:
    sys_id = _value(record.get("sys_id"))
    existing = (
        db.query(Incident)
        .filter(Incident.account_id == user.account_id, Incident.source == "servicenow", Incident.external_sys_id == sys_id)
        .one_or_none()
    )
    state = SN_STATE.get(_value(record.get("state")), _display(record.get("state")) or "new")
    number = _clip(_display(record.get("number")) or next_number(db, "SN", "servicenow"), 40)
    if existing is None and number:
        taken = db.query(Incident).filter(Incident.account_id == user.account_id, Incident.number == number).one_or_none()
        if taken is not None and taken.external_sys_id != sys_id:
            number = _clip(f"{number}-{sys_id[:8]}", 40)
    values = dict(
        external_number=_clip(_display(record.get("number")), 40),
        number=number,
        short_description=_clip(_display(record.get("short_description")), 500),
        description=_display(record.get("description")),
        state=_clip(state, 40),
        priority=_clip(_value(record.get("priority")) or _display(record.get("priority")) or "3", 8),
        impact=_clip(_value(record.get("impact")), 8),
        urgency=_clip(_value(record.get("urgency")), 8),
        assignment_group=_clip(_display(record.get("assignment_group")), 200),
        assigned_to=_clip(_display(record.get("assigned_to")), 200),
        category=_clip(_display(record.get("category")), 120),
        subcategory=_clip(_display(record.get("subcategory")), 120),
        business_service=_clip(_display(record.get("business_service")), 200),
        cmdb_ci=_clip(_display(record.get("cmdb_ci")), 200),
        close_notes=_display(record.get("close_notes")),
        resolution_code=(_display(record.get("close_code")) or "")[:64],
        sys_mod_count=_value(record.get("sys_mod_count")),
        raw_payload={"sys_id": sys_id, "source": "servicenow"},
        servicenow_sync_status="synchronized",
        environment="production",
        opened_at=_timestamp(record.get("opened_at")) or _timestamp(record.get("sys_created_on")) or utcnow(),
        resolved_at=_timestamp(record.get("resolved_at")),
        closed_at=_timestamp(record.get("closed_at")),
        updated_at=utcnow(),
    )
    if existing is None:
        incident = Incident(account_id=user.account_id, source="servicenow", external_sys_id=sys_id, **values)
        db.add(incident)
        db.flush()
        return "created"
    for key, value in values.items():
        if key == "number":
            continue
        setattr(existing, key, value)
    return "updated"


def _sync_knowledge(db: Session, user: User, client: ServiceNowClient) -> int:
    try:
        rows = client.fetch_knowledge(0, 200)
    except AppError:
        return 0
    count = 0
    for record in rows:
        sys_id = _value(record.get("sys_id"))
        number = _display(record.get("number"))
        title = (_display(record.get("short_description")) or "ServiceNow article")[:340]
        if number:
            title = f"{number} — {title}"[:400]
        text = _strip_html(_display(record.get("text")))
        if not sys_id or len(text) < 20:
            continue
        knowledge_service.upsert_document(
            db,
            account_id=user.account_id,
            title=title,
            content=text,
            source="servicenow",
            doc_type="knowledge_article",
            status="published",
            source_id=sys_id,
            metadata={"number": _display(record.get("number")), "live_integration": True},
        )
        count += 1
    return count


def _clip(value: str, limit: int) -> str:
    return (value or "")[:limit]


def _timestamp(field) -> datetime | None:
    raw = _value(field).replace("T", " ")[:19]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _display(field) -> str:
    if isinstance(field, dict):
        return str(field.get("display_value") or field.get("value") or "")
    return "" if field is None else str(field)


def _value(field) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return "" if field is None else str(field)


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&nbsp;", " ").strip()


def collect_incident_logs(client: ServiceNowClient, incident: Incident) -> dict:
    """Read ServiceNow syslog and the incident journal. Never invent a log line."""
    terms = _log_terms(incident.short_description, incident.description)
    lines: list[dict] = []
    activity: list[dict] = []
    steps: list[str] = []
    error = ""
    if client.configured and incident.external_sys_id:
        try:
            lines = client.search_syslog(terms)
            for row in client.fetch_journal(incident.external_sys_id):
                text = _strip_html(str(row.get("value") or ""))
                if not text:
                    continue
                kind = str(row.get("element") or "journal")
                activity.append({"at": str(row.get("sys_created_on") or ""), "kind": kind, "text": text[:2000]})
                if kind == "work_notes":
                    steps.extend(_numbered_steps(text))
        except AppError as exc:
            error = exc.message
    steps = steps[:8]
    return {
        "status": "error" if error else "ok",
        "connector": "servicenow",
        "environment_label": "ServiceNow",
        "searched": terms,
        "logs": lines,
        "lines": lines,
        "activity": activity[:6],
        "steps": steps,
        "signatures": [],
        "correlation_ids": [],
        "frequency": [],
        "baseline": {},
        "groups": [],
        "excerpts": [line["message"][:400] for line in lines[:5]],
        "truncated": False,
        "log_count": len(lines),
        "message": error or _log_message(incident.number or incident.external_number or "this incident", terms, lines, steps),
    }


def _log_terms(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in re.findall(r"\b[A-Za-z]{1,8}\d{4,}\b", text or ""):
            if match.upper().startswith("INC"):
                continue
            if match not in found:
                found.append(match)
    return found[:4]


def work_note_log_lines(activity: list[dict], steps: list[str], terms: list[str], incident_number: str) -> list[dict]:
    """Turn the recorded work note into log rows. These are not syslog lines."""
    stamp = next((str(row.get("at") or "") for row in activity if row.get("kind") == "work_notes" and row.get("at")), "")
    signature = terms[0] if terms else incident_number
    rows = []
    for index, step in enumerate(steps, start=1):
        lowered = step.lower()
        if "failed" in lowered or "failure" in lowered:
            level = "ERROR"
        elif "not running" in lowered:
            level = "WARN"
        else:
            level = "INFO"
        rows.append(
            {
                "id": f"work-note-{index}",
                "timestamp": stamp,
                "level": level,
                "source": "servicenow_work_note",
                "message": step,
                "signature": signature,
                "correlation_id": incident_number,
            }
        )
    return rows


def _numbered_steps(text: str) -> list[str]:
    steps = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*\d+[\).:-]\s*", "", line).strip()
        if not cleaned or cleaned.lower().startswith("attaching"):
            continue
        steps.append(cleaned[:300])
    return steps


def _log_message(number: str, terms: list[str], lines: list[dict], steps: list[str]) -> str:
    shown = ", ".join(terms) or "a specific identifier"
    if lines:
        return f"ServiceNow syslog returned {len(lines)} line(s) for {shown}. Read those lines before closing {number}."
    if steps:
        return (
            f"ServiceNow syslog has no line for {shown}. "
            f"The recommendation follows the work note on {number}. Read that note and check the services it names before anyone closes the incident."
        )
    return f"ServiceNow syslog has no line for {shown}, and {number} has no work note to follow."


def _fernet():
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def _username(db: Session, user: User) -> str:
    settings = get_settings()
    if settings.servicenow_username:
        return settings.servicenow_username
    row = db.query(ConnectorConfig).filter_by(account_id=user.account_id, connector="servicenow").one_or_none()
    if not row or not row.secret_blob:
        return ""
    try:
        return json.loads(_decrypt(row.secret_blob)).get("username", "")
    except Exception:
        return ""
