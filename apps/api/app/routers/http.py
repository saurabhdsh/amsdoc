"""HTTP and WebSocket routes."""

from __future__ import annotations

import asyncio
import secrets
import time

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.observability import EasidocLogConnector, RemoteConnector, _frequency, _groups
from app.constants import AGENTS, ENVIRONMENT_LABEL
from app.database import get_db
from app.errors import AppError
from app.events import bus, serialize_event
from app.idempotency import begin_idempotent, store_idempotent
from app.models import (
    ActionExecution,
    AgentRun,
    AgentStep,
    Application,
    ApplicationEvent,
    ApplicationLog,
    AuditRecord,
    Document,
    HumanApproval,
    Incident,
    IncidentEvidence,
    KnowledgeDocument,
    PlatformSetting,
    RemediationProposal,
    RootCauseHypothesis,
    User,
    VerificationResult,
    utcnow,
)
from app.schemas import (
    AssignIn,
    ConnectorIn,
    ConversationIn,
    DecideIn,
    IncidentIn,
    KnowledgeTransitionIn,
    LinkIn,
    LoginIn,
    MessageIn,
    MetadataIn,
    OidcCallbackIn,
    RetentionIn,
    SearchIn,
    SeedIn,
    UserIn,
    VersionIn,
)
from app.security import authenticate_token, create_access_token, get_current_user, has_permission, permission_list, rate_limit, require, verify_password
from app.seed import DEV_ACCOUNTS
from app.services import analytics, conversation, easidoc, executor, investigation, knowledge, servicenow
from app.util import audit, iso, next_number, parse_dt, sla_deadline

router = APIRouter(prefix="/api/v1")
_oidc_states: dict[str, float] = {}


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "account_id": user.account_id,
        "participant_id": user.participant_id,
        "permissions": permission_list(user),
    }


def incident_dict(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "number": incident.number,
        "source": incident.source,
        "source_label": {"servicenow": "ServiceNow", "easidoc_simulator": "Easidoc simulator", "manual": "Manual entry"}.get(incident.source, incident.source),
        "live_integration": incident.source == "servicenow",
        "environment": incident.environment,
        "external_sys_id": incident.external_sys_id,
        "external_number": incident.external_number,
        "short_description": incident.short_description,
        "description": incident.description,
        "state": incident.state,
        "priority": incident.priority,
        "impact": incident.impact,
        "urgency": incident.urgency,
        "assignment_group": incident.assignment_group,
        "assigned_to": incident.assigned_to,
        "category": incident.category,
        "subcategory": incident.subcategory,
        "business_service": incident.business_service,
        "cmdb_ci": incident.cmdb_ci,
        "application_id": incident.application_id,
        "document_id": incident.document_id,
        "scenario_execution_id": incident.scenario_execution_id,
        "opened_at": iso(incident.opened_at),
        "updated_at": iso(incident.updated_at),
        "resolved_at": iso(incident.resolved_at),
        "closed_at": iso(incident.closed_at),
        "close_notes": incident.close_notes,
        "resolution_code": incident.resolution_code,
        "sla_due_at": iso(incident.sla_due_at),
        "journal": incident.journal or [],
        "servicenow_sync_status": incident.servicenow_sync_status,
        "row_version": incident.row_version,
    }


def _replay(db: Session, user: User, request: Request, body: dict):
    found = begin_idempotent(db, user, request, body)
    if found:
        return JSONResponse(status_code=found["code"], content=found["body"])
    return None


def _store(db: Session, user: User, request: Request, body: dict, payload: dict, code: int = 200):
    store_idempotent(db, user, request, body, code, payload)
    return payload


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(__import__("sqlalchemy").text("SELECT 1"))
    settings = get_settings()
    client, _stored, _row = servicenow.resolve_client(db, _any_user(db)) if _any_user(db) else (None, {}, None)
    return {
        "status": "ok",
        "database": "ok",
        "redis": "configured" if settings.redis_url else "not_configured",
        "servicenow": "configured" if client and client.configured else "not_configured",
        "model_gateway": __import__("app.services.gateway", fromlist=["status"]).status(),
        "simulator": {"status": "ok", "label": ENVIRONMENT_LABEL},
    }


def _any_user(db: Session) -> User | None:
    return db.query(User).first()


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    host = request.client.host if request.client else "unknown"
    rate_limit(f"login:{host}", 30)
    key = body.email.strip().lower()
    if key in {"admin", "administrator"}:
        key = "yugal@ams.local"
    password = body.password.strip()
    user = db.query(User).filter(or_(User.email == key, func.lower(User.name) == key)).one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AppError("UNAUTHENTICATED", "Username or password is incorrect.", 401)
    audit(db, user, "auth.login", "user", user.id, {})
    return {"token": create_access_token(user.id), "user": user_dict(user)}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_dict(user)


@router.get("/auth/dev-accounts")
def dev_accounts():
    if get_settings().environment != "local":
        raise AppError("NOT_FOUND", "Not found.", 404)
    return {"accounts": DEV_ACCOUNTS, "note": "Local development accounts only. They are not created when ENVIRONMENT is production."}


@router.get("/auth/oidc/start")
def oidc_start():
    settings = get_settings()
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise AppError("NOT_CONFIGURED", "OIDC is not configured.", 409)
    state = secrets.token_urlsafe(24)
    _oidc_states[state] = time.time()
    url = (
        f"{settings.oidc_issuer.rstrip('/')}/authorize?response_type=code&client_id={settings.oidc_client_id}"
        f"&redirect_uri={settings.oidc_redirect_uri}&scope=openid%20email%20profile&state={state}"
    )
    return {"authorization_url": url, "state": state}


@router.post("/auth/oidc/callback")
def oidc_callback(body: OidcCallbackIn, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise AppError("NOT_CONFIGURED", "OIDC is not configured.", 409)
    issued = _oidc_states.pop(body.state, None)
    if issued is None or time.time() - issued > 600:
        raise AppError("INVALID_STATE", "OIDC state was not recognized.", 400)
    import httpx

    token_response = httpx.post(
        f"{settings.oidc_issuer.rstrip('/')}/token",
        data={
            "grant_type": "authorization_code",
            "code": body.code,
            "redirect_uri": settings.oidc_redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        },
        timeout=20,
    )
    if token_response.status_code >= 400:
        raise AppError("OIDC_FAILED", "The identity provider rejected the sign-in.", 502)
    access_token = token_response.json().get("access_token")
    info = httpx.get(f"{settings.oidc_issuer.rstrip('/')}/userinfo", headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    if info.status_code >= 400:
        raise AppError("OIDC_FAILED", "The identity provider did not return a profile.", 502)
    email = (info.json().get("email") or "").lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None or not user.is_active:
        raise AppError("FORBIDDEN", "No AMS account exists for this identity.", 403)
    return {"token": create_access_token(user.id), "user": user_dict(user)}


@router.get("/incidents")
def list_incidents(
    user: User = Depends(require("incident.read")),
    db: Session = Depends(get_db),
    q: str | None = None,
    source: str | None = None,
    environment: str | None = None,
    state: str | None = None,
    priority: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    query = db.query(Incident).filter(Incident.account_id == user.account_id)
    if source:
        query = query.filter(Incident.source == source)
    if environment:
        query = query.filter(Incident.environment == environment)
    if state:
        query = query.filter(Incident.state == state)
    if priority:
        query = query.filter(Incident.priority == priority)
    if q:
        like = f"%{q[:120]}%"
        query = query.filter(or_(Incident.number.ilike(like), Incident.short_description.ilike(like)))
    total = query.count()
    rows = query.order_by(Incident.opened_at.desc()).offset(offset).limit(min(limit, 200)).all()
    return {"total": total, "items": [incident_dict(row) for row in rows]}


@router.post("/incidents")
def create_incident(body: IncidentIn, user: User = Depends(require("incident.create")), db: Session = Depends(get_db)):
    if body.priority not in {"1", "2", "3", "4", "5"}:
        raise AppError("INVALID_PRIORITY", "Priority must be 1 through 5.", 422)
    application = None
    if body.application_id:
        application = db.get(Application, body.application_id)
        if application is None or application.account_id != user.account_id:
            raise AppError("NOT_FOUND", "Application was not found.", 404)
    document = None
    if body.document_id:
        document = db.get(Document, body.document_id)
        if document is None or document.account_id != user.account_id:
            raise AppError("NOT_FOUND", "Document was not found.", 404)
    environment = "simulator" if application and application.environment == "simulator" else "production"
    source = "easidoc_simulator" if environment == "simulator" else "manual"
    incident = Incident(
        account_id=user.account_id,
        number=next_number(db, "AMS", "incident"),
        source=source,
        environment=environment,
        short_description=body.short_description.strip(),
        description=body.description.strip(),
        priority=body.priority,
        state="new",
        application_id=None if application is None else application.id,
        document_id=None if document is None else document.id,
        scenario_execution_id=None if document is None else document.scenario_execution_id,
        business_service="" if application is None else application.name,
        cmdb_ci="" if application is None else application.label,
        sla_due_at=sla_deadline(body.priority),
        servicenow_sync_status="not_linked",
        raw_payload={"origin": "manual"},
    )
    db.add(incident)
    db.flush()
    audit(db, user, "incident.create", "incident", incident.id, {})
    return incident_dict(incident)


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, user: User = Depends(require("incident.read")), db: Session = Depends(get_db)):
    return incident_dict(_incident(db, user, incident_id))


@router.post("/incidents/{incident_id}/investigate")
def investigate_incident(incident_id: str, user: User = Depends(require("incident.investigate")), db: Session = Depends(get_db)):
    result = investigation.investigate(db, user, incident_id)
    result["investigation"] = _investigation(db, _incident(db, user, incident_id))
    return result


@router.get("/incidents/{incident_id}/investigation")
def get_investigation(incident_id: str, user: User = Depends(require("incident.read")), db: Session = Depends(get_db)):
    return _investigation(db, _incident(db, user, incident_id))


@router.post("/incidents/{incident_id}/link-servicenow")
def link_servicenow(incident_id: str, body: LinkIn, user: User = Depends(require("incident.investigate")), db: Session = Depends(get_db)):
    incident = _incident(db, user, incident_id)
    result = servicenow.link_incident(db, user, incident, body.number.strip())
    audit(db, user, "incident.link_servicenow", "incident", incident.id, {"number": body.number})
    return {**result, "incident": incident_dict(incident)}


@router.get("/agents")
def agents(user: User = Depends(require("incident.read"))):
    return {"agents": AGENTS, "orchestrator": investigation._orchestrator_name(), "viewer": user.role}


@router.get("/agents/runs")
def agent_runs(user: User = Depends(require("incident.read")), db: Session = Depends(get_db)):
    runs = db.query(AgentRun).filter(AgentRun.account_id == user.account_id).order_by(AgentRun.started_at.desc()).limit(40).all()
    payloads = []
    for run in runs:
        incident = db.get(Incident, run.incident_id)
        steps = db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.started_at.asc()).all()
        payloads.append(
            {
                "id": run.id,
                "status": run.status,
                "orchestrator": run.orchestrator,
                "incident_id": run.incident_id,
                "incident_number": None if incident is None else incident.number,
                "started_at": iso(run.started_at),
                "finished_at": iso(run.finished_at),
                "error": run.error,
                "steps": [{"agent": step.agent, "status": step.status, "output": step.output} for step in steps],
            }
        )
    return {"items": payloads}


@router.get("/approvals")
def list_approvals(user: User = Depends(require("approval.read")), db: Session = Depends(get_db), status: str | None = None):
    query = db.query(HumanApproval).filter(HumanApproval.account_id == user.account_id)
    if status:
        query = query.filter(HumanApproval.status == status)
    rows = query.order_by(HumanApproval.created_at.desc()).limit(100).all()
    return {"items": [_approval_view(db, row) for row in rows]}


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: DecideIn, request: Request, user: User = Depends(require("approval.decide")), db: Session = Depends(get_db)):
    payload = body.model_dump()
    replay = _replay(db, user, request, payload)
    if replay:
        return replay
    result = executor.decide(db, user, approval_id, body.decision, body.comment, body.modifications)
    approval = db.get(HumanApproval, approval_id)
    return _store(db, user, request, payload, _approval_view(db, approval) | {"result": result})


@router.post("/actions/{approval_id}/execute")
def execute_action(approval_id: str, request: Request, user: User = Depends(require("action.execute")), db: Session = Depends(get_db)):
    body = {"approval_id": approval_id}
    replay = _replay(db, user, request, body)
    if replay:
        return replay
    result = executor.execute(db, user, approval_id)
    return _store(db, user, request, body, result)


@router.post("/actions/executions/{execution_id}/rollback")
def rollback_action(execution_id: str, request: Request, user: User = Depends(require("action.rollback")), db: Session = Depends(get_db)):
    body = {"execution_id": execution_id}
    replay = _replay(db, user, request, body)
    if replay:
        return replay
    return _store(db, user, request, body, executor.rollback(db, user, execution_id))


@router.get("/knowledge")
def list_knowledge(status: str | None = None, user: User = Depends(require("knowledge.read")), db: Session = Depends(get_db)):
    return {"items": knowledge.list_documents(db, user, status)}


@router.get("/knowledge/{document_id}")
def get_knowledge(document_id: str, user: User = Depends(require("knowledge.read")), db: Session = Depends(get_db)):
    return knowledge.get_document(db, user, document_id)


@router.post("/knowledge/search")
def search_knowledge(body: SearchIn, user: User = Depends(require("knowledge.read")), db: Session = Depends(get_db)):
    return {"items": knowledge.search(db, user, body.query, application_code=body.application_code)}


@router.post("/knowledge/upload")
async def upload_knowledge(
    file: UploadFile = File(...),
    application_code: str = Form(""),
    user: User = Depends(require("knowledge.write")),
    db: Session = Depends(get_db),
):
    payload = await file.read()
    if len(payload) > 5_000_000:
        raise AppError("FILE_TOO_LARGE", "Uploads are limited to 5 MB.", 413)
    return knowledge.create_upload(db, user, file.filename or "document.txt", payload, application_code)


@router.post("/knowledge/{document_id}/transition")
def transition_knowledge(document_id: str, body: KnowledgeTransitionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return knowledge.transition(db, user, document_id, body.status)


@router.get("/conversations")
def list_conversations(user: User = Depends(require("conversation.use")), db: Session = Depends(get_db)):
    return {"items": conversation.list_conversations(db, user)}


@router.post("/conversations")
def create_conversation(body: ConversationIn, user: User = Depends(require("conversation.use")), db: Session = Depends(get_db)):
    return conversation.create_conversation(db, user, body.application_id, body.document_id)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(require("conversation.use")), db: Session = Depends(get_db)):
    return conversation.get_conversation(db, user, conversation_id)


@router.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: str, body: ConversationIn, user: User = Depends(require("conversation.use")), db: Session = Depends(get_db)):
    return conversation.update_conversation(db, user, conversation_id, body.application_id, body.document_id)


@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: str, body: MessageIn, user: User = Depends(require("conversation.use")), db: Session = Depends(get_db)):
    return conversation.post_message(db, user, conversation_id, body.content)


@router.get("/logs")
def query_logs(
    user: User = Depends(require("logs.read")),
    db: Session = Depends(get_db),
    connector: str = "easidoc",
    incident_id: str | None = None,
    document_id: str | None = None,
    application_id: str | None = None,
    signature: str | None = None,
    correlation_id: str | None = None,
    q: str | None = None,
    level: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
):
    if connector == "servicenow":
        if not incident_id:
            raise AppError("INVALID_FILTER", "Choose an incident before reading ServiceNow logs.", 422)
        incident = _incident(db, user, incident_id)
        from app.services.servicenow import collect_incident_logs, resolve_client, work_note_log_lines

        client, _meta, _row = resolve_client(db, user)
        if not client.configured:
            raise AppError("NOT_CONFIGURED", "ServiceNow is not configured. No logs were invented.", 409)
        report = collect_incident_logs(client, incident)
        syslog_logs = list(report.get("logs") or [])
        display = syslog_logs or work_note_log_lines(report.get("activity") or [], report.get("steps") or [], report.get("searched") or [], incident.number)
        record_source = "syslog" if syslog_logs else "work_note" if display else "none"
        if q:
            needle = q.strip().lower()
            display = [line for line in display if needle in line.get("message", "").lower()]
        if signature:
            display = [line for line in display if line.get("signature") == signature]
        if correlation_id:
            display = [line for line in display if line.get("correlation_id") == correlation_id]
        errors = sum(1 for line in display if line.get("level") == "ERROR")
        return {
            "connector": "servicenow",
            "ok": report.get("status") != "error",
            "status": report.get("status") or "ok",
            "environment_label": "ServiceNow",
            "message": report.get("message") or "",
            "searched": report.get("searched") or [],
            "activity": report.get("activity") or [],
            "record_source": record_source,
            "syslog_count": len(syslog_logs),
            "logs": display,
            "frequency": _frequency(display),
            "groups": _groups(display),
            "baseline": {"available": bool(display), "incident_window_errors": errors, "baseline_errors": None},
        }
    if connector not in {"easidoc", "easidoc_simulator"}:
        result = RemoteConnector(connector, configured=False).query_logs({})
        raise AppError("NOT_CONFIGURED", result["message"], 409, result)
    opened_at = None
    if incident_id:
        incident = _incident(db, user, incident_id)
        opened_at = iso(incident.opened_at)
        application_id = application_id or incident.application_id
    adapter = EasidocLogConnector(db, user.account_id)
    return adapter.query_logs(
        {
            "incident_id": incident_id,
            "document_id": document_id,
            "application_id": application_id,
            "signature": signature,
            "correlation_id": correlation_id,
            "q": q,
            "level": level,
            "time_from": parse_dt(time_from),
            "time_to": parse_dt(time_to),
            "opened_at": opened_at,
        }
    )


@router.get("/analytics/kpis")
def analytics_kpis(
    user: User = Depends(require("analytics.read")),
    db: Session = Depends(get_db),
    application_id: str | None = None,
    business_service: str | None = None,
    account_id: str | None = None,
    priority: str | None = None,
    assignment_group: str | None = None,
    environment: str | None = None,
    source: str | None = None,
    opened_from: str | None = None,
    opened_to: str | None = None,
):
    return analytics.kpis(
        db,
        user,
        {
            "application_id": application_id,
            "business_service": business_service,
            "account_id": account_id,
            "priority": priority,
            "assignment_group": assignment_group,
            "environment": environment,
            "source": source,
            "opened_from": opened_from,
            "opened_to": opened_to,
        },
    )


@router.get("/search")
def search(q: str = Query(min_length=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    like = f"%{q[:120]}%"
    incidents = []
    if has_permission(user, "incident.read"):
        incidents = [
            incident_dict(row)
            for row in db.query(Incident)
            .filter(Incident.account_id == user.account_id, or_(Incident.number.ilike(like), Incident.short_description.ilike(like)))
            .limit(8)
            .all()
        ]
    documents = []
    if has_permission(user, "simulator.read"):
        documents = [
            {"id": row.id, "number": row.number, "title": row.title, "environment_label": ENVIRONMENT_LABEL}
            for row in db.query(Document)
            .filter(Document.account_id == user.account_id, or_(Document.number.ilike(like), Document.title.ilike(like)))
            .limit(8)
            .all()
        ]
    articles = knowledge.search(db, user, q, limit=8) if has_permission(user, "knowledge.read") else []
    return {"incidents": incidents, "documents": documents, "knowledge": articles}


@router.get("/events")
def list_events(after_id: int = 0, incident_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(ApplicationEvent).filter(ApplicationEvent.account_id == user.account_id, ApplicationEvent.id > after_id)
    if incident_id:
        _incident(db, user, incident_id)
        query = query.filter(ApplicationEvent.incident_id == incident_id)
    rows = query.order_by(ApplicationEvent.id.asc()).limit(300).all()
    return {"items": [serialize_event(row) for row in rows]}


@router.get("/applications")
def applications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Application).filter(Application.account_id == user.account_id).all()
    return {
        "items": [
            {"id": row.id, "name": row.name, "code": row.code, "environment": row.environment, "label": row.label, "description": row.description}
            for row in rows
        ]
    }


@router.get("/connectors")
def connectors(user: User = Depends(require("connector.read")), db: Session = Depends(get_db)):
    snow = servicenow.public_config(db, user)
    items = [snow]
    items.append(EasidocLogConnector(db, user.account_id).test_connection())
    for name in ("splunk", "datadog", "opensearch", "azure_monitor", "generic_logs"):
        items.append(RemoteConnector(name, configured=False).test_connection())
    return {"items": items}


@router.get("/connectors/servicenow")
def get_servicenow(user: User = Depends(require("connector.read")), db: Session = Depends(get_db)):
    return servicenow.public_config(db, user)


@router.put("/connectors/servicenow")
def put_servicenow(body: ConnectorIn, user: User = Depends(require("connector.admin")), db: Session = Depends(get_db)):
    result = servicenow.save_config(db, user, body.model_dump())
    audit(db, user, "connector.servicenow.update", "connector", "servicenow", {"write_mode": body.write_mode})
    return result


@router.post("/connectors/servicenow/test")
def test_servicenow(user: User = Depends(require("connector.admin")), db: Session = Depends(get_db)):
    return servicenow.test_and_record(db, user)


@router.post("/connectors/servicenow/sync")
def sync_servicenow(user: User = Depends(require("connector.admin")), db: Session = Depends(get_db)):
    return servicenow.sync_account(db, user)


@router.get("/admin/users")
def list_users(user: User = Depends(require("admin.manage")), db: Session = Depends(get_db)):
    rows = db.query(User).filter(User.account_id == user.account_id).order_by(User.email).all()
    return {"items": [user_dict(row) for row in rows]}


@router.post("/admin/users")
def create_user(body: UserIn, user: User = Depends(require("admin.manage")), db: Session = Depends(get_db)):
    if body.role not in {"admin", "resolver", "investigator", "operator", "viewer"}:
        raise AppError("INVALID_ROLE", "Unknown role.", 422)
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise AppError("DUPLICATE_USER", "A user with that email already exists.", 409)
    from app.security import hash_password

    created = User(
        account_id=user.account_id,
        email=body.email.lower(),
        name=body.name,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(created)
    db.flush()
    audit(db, user, "admin.user.create", "user", created.id, {"role": body.role})
    return user_dict(created)


@router.get("/admin/audit")
def audit_log(user: User = Depends(require("audit.read")), db: Session = Depends(get_db)):
    rows = db.query(AuditRecord).filter(AuditRecord.account_id == user.account_id).order_by(AuditRecord.created_at.desc()).limit(200).all()
    return {
        "items": [
            {
                "id": row.id,
                "actor_id": row.actor_id,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "details": row.details,
                "created_at": iso(row.created_at),
            }
            for row in rows
        ]
    }


@router.get("/admin/retention")
def get_retention(user: User = Depends(require("admin.manage")), db: Session = Depends(get_db)):
    row = db.query(PlatformSetting).filter_by(account_id=user.account_id, key="retention_days").one_or_none()
    return {"days": 90 if row is None else int((row.value or {}).get("days", 90))}


@router.put("/admin/retention")
def put_retention(body: RetentionIn, user: User = Depends(require("admin.manage")), db: Session = Depends(get_db)):
    row = db.query(PlatformSetting).filter_by(account_id=user.account_id, key="retention_days").one_or_none()
    if row is None:
        row = PlatformSetting(account_id=user.account_id, key="retention_days", value={"days": body.days})
        db.add(row)
    else:
        row.value = {"days": body.days}
    return {"days": body.days}


@router.post("/admin/retention/run")
def run_retention(user: User = Depends(require("admin.manage")), db: Session = Depends(get_db)):
    row = db.query(PlatformSetting).filter_by(account_id=user.account_id, key="retention_days").one_or_none()
    days = 90 if row is None else int((row.value or {}).get("days", 90))
    cutoff = utcnow() - __import__("datetime").timedelta(days=days)
    deleted = (
        db.query(ApplicationLog)
        .filter(ApplicationLog.account_id == user.account_id, ApplicationLog.timestamp < cutoff)
        .delete(synchronize_session=False)
    )
    audit(db, user, "admin.retention.run", "application_log", "", {"deleted": deleted, "days": days})
    return {"deleted_logs": deleted, "retention_days": days}


@router.get("/simulator/scenarios")
def simulator_scenarios(user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return {"environment_label": ENVIRONMENT_LABEL, "items": easidoc.list_scenarios(db, user)}


@router.post("/simulator/scenarios/{scenario_id}/seed")
def simulator_seed(scenario_id: str, body: SeedIn, request: Request, user: User = Depends(require("simulator.seed")), db: Session = Depends(get_db)):
    payload = body.model_dump()
    replay = _replay(db, user, request, payload | {"scenario_id": scenario_id, "op": "seed"})
    if replay:
        return replay
    state = easidoc.seed_scenario(db, user, scenario_id, body.variant)
    return _store(db, user, request, payload | {"scenario_id": scenario_id, "op": "seed"}, state)


@router.post("/simulator/scenarios/{scenario_id}/reset")
def simulator_reset(scenario_id: str, body: SeedIn, request: Request, user: User = Depends(require("simulator.seed")), db: Session = Depends(get_db)):
    payload = body.model_dump() | {"scenario_id": scenario_id, "op": "reset"}
    replay = _replay(db, user, request, payload)
    if replay:
        return replay
    return _store(db, user, request, payload, easidoc.reset_scenario(db, user, scenario_id, body.variant))


@router.get("/simulator/scenarios/{scenario_id}/state")
def simulator_state(scenario_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return easidoc.scenario_state(db, user, scenario_id)


@router.get("/simulator/documents")
def simulator_documents(user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return {"environment_label": ENVIRONMENT_LABEL, "items": easidoc.list_documents(db, user)}


@router.get("/simulator/documents/{document_id}")
def simulator_document(document_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return easidoc.get_document(db, user, document_id)


@router.get("/simulator/documents/{document_id}/versions")
def simulator_versions(document_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return {"items": easidoc.list_versions(db, user, document_id)}


@router.get("/simulator/documents/{document_id}/timeline")
def simulator_timeline(document_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return {"environment_label": ENVIRONMENT_LABEL, "items": easidoc.timeline(db, user, document_id)}


@router.patch("/simulator/documents/{document_id}/metadata")
def simulator_metadata(document_id: str, body: MetadataIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = body.model_dump()
    replay = _replay(db, user, request, payload | {"document_id": document_id})
    if replay:
        return replay
    approval = None
    if body.approval_id:
        approval = executor.load_approved(db, user, body.approval_id) if False else __import__("app.policy", fromlist=["load_approved"]).load_approved(db, user, body.approval_id)
    values = {}
    if body.document_history is not None:
        values["document_history"] = body.document_history
    if body.planned_effective_date is not None:
        values["planned_effective_date"] = body.planned_effective_date
    state = easidoc.update_metadata(
        db,
        user,
        document_id,
        expected_version=body.expected_version,
        values=values,
        approval=approval,
        human_confirmation=body.human_confirmation,
    )
    return _store(db, user, request, payload | {"document_id": document_id}, state)


@router.post("/simulator/documents/{document_id}/start-approval")
def simulator_start(document_id: str, body: VersionIn, request: Request, user: User = Depends(require("easidoc.approval.start")), db: Session = Depends(get_db)):
    payload = body.model_dump() | {"document_id": document_id}
    replay = _replay(db, user, request, payload)
    if replay:
        return replay
    return _store(db, user, request, payload, easidoc.start_approval(db, user, document_id, body.expected_version))


@router.get("/simulator/workflows/{workflow_id}")
def simulator_workflow(workflow_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    workflow = easidoc._workflow_for_account(db, user, workflow_id)
    return easidoc._workflow_dict(db, workflow)


@router.get("/simulator/workflows/{workflow_id}/tasks")
def simulator_tasks(workflow_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    workflow = easidoc._workflow_for_account(db, user, workflow_id)
    return {"items": easidoc._workflow_dict(db, workflow)["tasks"]}


@router.get("/simulator/workflows/{workflow_id}/eligible-participants")
def eligible_participants(workflow_id: str, user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    workflow = easidoc._workflow_for_account(db, user, workflow_id)
    document = db.get(Document, workflow.document_id)
    state = easidoc.public_state(db, document)
    return {"environment_label": ENVIRONMENT_LABEL, "items": state["eligible_participants"]}


@router.post("/simulator/workflows/{workflow_id}/participants")
def simulator_assign(workflow_id: str, body: AssignIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = body.model_dump()
    replay = _replay(db, user, request, payload | {"workflow_id": workflow_id})
    if replay:
        return replay
    from app.policy import load_approved

    approval = load_approved(db, user, body.approval_id)
    state = easidoc.assign_participant(db, user, workflow_id, body.participant_id, body.expected_version, approval)
    return _store(db, user, request, payload | {"workflow_id": workflow_id}, state)


@router.post("/simulator/workflows/{workflow_id}/complete-review")
def simulator_complete(workflow_id: str, body: VersionIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = body.model_dump() | {"workflow_id": workflow_id, "op": "complete"}
    replay = _replay(db, user, request, payload)
    if replay:
        return replay
    state = easidoc.complete_review(db, user, workflow_id, body.expected_version)
    advanced = executor.advance_for_workflow(db, user, workflow_id)
    state = easidoc.get_document(db, user, state["document"]["id"])
    state["runbook"] = advanced
    return _store(db, user, request, payload, state)


@router.get("/simulator/logs")
def simulator_logs(user: User = Depends(require("simulator.read")), db: Session = Depends(get_db), document_id: str | None = None):
    return EasidocLogConnector(db, user.account_id).query_logs({"document_id": document_id})


@router.get("/simulator/events")
def simulator_events(user: User = Depends(require("simulator.read")), db: Session = Depends(get_db)):
    return EasidocLogConnector(db, user.account_id).get_events({"limit": 100})


@router.get("/simulator/health")
def simulator_health(user: User = Depends(require("simulator.read"))):
    return {"status": "ok", "environment": "simulator", "environment_label": ENVIRONMENT_LABEL, "account_id": user.account_id}


@router.websocket("/events/stream")
async def events_stream(websocket: WebSocket):
    await _stream(websocket, "ams")


@router.websocket("/simulator/events/stream")
async def simulator_stream(websocket: WebSocket):
    await _stream(websocket, "simulator")


async def _stream(websocket: WebSocket, channel: str) -> None:
    await websocket.accept()
    queue = bus.subscribe()
    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        token = message.get("token")
        after_id = int(message.get("after_id") or 0)
        from app import database as database_module

        db = database_module.SessionLocal()
        try:
            user = authenticate_token(db, token)
            query = db.query(ApplicationEvent).filter(ApplicationEvent.account_id == user.account_id, ApplicationEvent.id > after_id)
            if channel == "simulator":
                query = query.filter(ApplicationEvent.environment == "simulator")
            rows = query.order_by(ApplicationEvent.id.asc()).limit(300).all()
            backlog = [serialize_event(row) for row in rows]
            account_id = user.account_id
        except AppError:
            await websocket.send_json({"type": "error", "error": {"code": "UNAUTHENTICATED", "message": "Sign in again to continue."}})
            await websocket.close(code=4401)
            return
        finally:
            db.close()
        await websocket.send_json({"type": "hello", "channel": channel, "environment_label": ENVIRONMENT_LABEL})
        seen = set()
        for item in backlog:
            seen.add(item["id"])
            await websocket.send_json(item)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            if event.get("account_id") != account_id or event.get("id") in seen:
                continue
            if channel == "simulator" and event.get("environment") != "simulator":
                continue
            seen.add(event.get("id"))
            await websocket.send_json(event)
    finally:
        bus.unsubscribe(queue)


def _incident(db: Session, user: User, incident_id: str) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None or incident.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Incident was not found.", 404)
    return incident


def _investigation(db: Session, incident: Incident) -> dict:
    runs = db.query(AgentRun).filter(AgentRun.incident_id == incident.id).order_by(AgentRun.started_at.asc()).all()
    run_ids = [run.id for run in runs]
    steps = db.query(AgentStep).filter(AgentStep.run_id.in_(run_ids)).order_by(AgentStep.started_at.asc()).all() if run_ids else []
    evidence = db.query(IncidentEvidence).filter(IncidentEvidence.incident_id == incident.id).all()
    hypotheses = db.query(RootCauseHypothesis).filter(RootCauseHypothesis.incident_id == incident.id).all()
    proposals = db.query(RemediationProposal).filter(RemediationProposal.incident_id == incident.id).all()
    approvals = db.query(HumanApproval).filter(HumanApproval.incident_id == incident.id).order_by(HumanApproval.created_at.desc()).all()
    executions = db.query(ActionExecution).filter(ActionExecution.incident_id == incident.id).order_by(ActionExecution.started_at.asc()).all()
    verifications = db.query(VerificationResult).filter(VerificationResult.incident_id == incident.id).all()
    return {
        "incident": incident_dict(incident),
        "runs": [
            {"id": run.id, "status": run.status, "orchestrator": run.orchestrator, "error": run.error, "started_at": iso(run.started_at), "finished_at": iso(run.finished_at), "checkpoint": run.checkpoint}
            for run in runs
        ],
        "steps": [{"id": step.id, "run_id": step.run_id, "agent": step.agent, "status": step.status, "output": step.output} for step in steps],
        "evidence": [
            {"id": row.id, "run_id": row.run_id, "kind": row.kind, "source": row.source, "title": row.title, "excerpt": row.excerpt, "reference": row.reference}
            for row in evidence
        ],
        "hypotheses": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "statement": row.statement,
                "status": row.status,
                "confidence": row.confidence,
                "supporting": row.supporting,
                "contradicting": row.contradicting,
                "assumptions": row.assumptions,
                "further_checks": row.further_checks,
            }
            for row in hypotheses
        ],
        "proposals": [
            {"id": row.id, "run_id": row.run_id, "summary": row.summary, "risk": row.risk, "status": row.status, "payload": row.payload, "payload_hash": row.payload_hash}
            for row in proposals
        ],
        "approvals": [_approval_view(db, row) for row in approvals],
        "executions": [
            {"id": row.id, "action": row.action, "status": row.status, "result": row.result, "started_at": iso(row.started_at), "finished_at": iso(row.finished_at)}
            for row in executions
        ],
        "verifications": [
            {"id": row.id, "recovered": row.recovered, "summary": row.summary, "checks": row.checks, "created_at": iso(row.created_at)} for row in verifications
        ],
    }


def _approval_view(db: Session, approval: HumanApproval) -> dict:
    incident = db.get(Incident, approval.incident_id)
    proposal = db.get(RemediationProposal, approval.proposal_id)
    hypothesis = None
    if proposal and proposal.hypothesis_id:
        row = db.get(RootCauseHypothesis, proposal.hypothesis_id)
        if row:
            hypothesis = {"id": row.id, "statement": row.statement, "status": row.status, "supporting": row.supporting, "contradicting": row.contradicting}
    evidence = db.query(IncidentEvidence).filter(IncidentEvidence.incident_id == approval.incident_id).all()
    simulator = None
    document_id = (approval.payload or {}).get("document_id")
    if document_id:
        document = db.get(Document, document_id)
        if document and incident and document.account_id == incident.account_id:
            simulator = easidoc.public_state(db, document)
    return {
        "id": approval.id,
        "status": approval.status,
        "decision": approval.decision,
        "comment": approval.comment,
        "payload": approval.payload,
        "payload_hash": approval.payload_hash,
        "progress": approval.progress or {},
        "created_at": iso(approval.created_at),
        "decided_at": iso(approval.decided_at),
        "incident": None if incident is None else incident_dict(incident),
        "hypothesis": hypothesis,
        "evidence": [{"id": row.id, "kind": row.kind, "title": row.title, "excerpt": row.excerpt, "source": row.source} for row in evidence],
        "simulator": simulator,
        "risk": None if proposal is None else proposal.risk,
        "summary": None if proposal is None else proposal.summary,
    }
