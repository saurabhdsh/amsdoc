"""Operational metrics computed from persisted records."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    ActionExecution,
    AgentRun,
    AgentStep,
    Application,
    Document,
    HumanApproval,
    Incident,
    IncidentEvidence,
    RemediationProposal,
    User,
    VerificationResult,
    WorkflowInstance,
)
from app.util import as_utc, parse_dt


def kpis(db: Session, user: User, filters: dict) -> dict:
    if filters.get("account_id") and filters["account_id"] != user.account_id:
        return _empty()
    query = db.query(Incident).filter(Incident.account_id == user.account_id)
    if filters.get("application_id"):
        query = query.filter(Incident.application_id == filters["application_id"])
    if filters.get("business_service"):
        query = query.filter(Incident.business_service == filters["business_service"])
    if filters.get("priority"):
        query = query.filter(Incident.priority == filters["priority"])
    if filters.get("assignment_group"):
        query = query.filter(Incident.assignment_group == filters["assignment_group"])
    if filters.get("environment"):
        query = query.filter(Incident.environment == filters["environment"])
    if filters.get("source"):
        query = query.filter(Incident.source == filters["source"])
    opened_from = parse_dt(filters.get("opened_from"))
    opened_to = parse_dt(filters.get("opened_to"))
    rows = []
    for incident in query.all():
        opened = as_utc(incident.opened_at)
        if opened_from and opened and opened < opened_from:
            continue
        if opened_to and opened and opened > opened_to:
            continue
        rows.append(incident)
    active_states = {"new", "investigating", "awaiting_approval", "in_progress", "on_hold", "remediation_failed", "escalated", "awaiting_information"}
    now = datetime.now(timezone.utc)
    active = [row for row in rows if row.state in active_states]
    ids = [row.id for row in rows]
    runs = db.query(AgentRun).filter(AgentRun.incident_id.in_(ids)).all() if ids else []
    proposals = db.query(RemediationProposal).filter(RemediationProposal.incident_id.in_(ids)).all() if ids else []
    approvals = db.query(HumanApproval).filter(HumanApproval.incident_id.in_(ids)).all() if ids else []
    verifications = db.query(VerificationResult).filter(VerificationResult.incident_id.in_(ids)).all() if ids else []
    steps = []
    if runs:
        steps = db.query(AgentStep).filter(AgentStep.run_id.in_([run.id for run in runs])).all()
    evidence = db.query(IncidentEvidence).filter(IncidentEvidence.incident_id.in_(ids), IncidentEvidence.kind == "knowledge").all() if ids else []
    investigated = {run.incident_id for run in runs}
    durations = []
    for incident in rows:
        if incident.resolved_at and incident.opened_at:
            durations.append((as_utc(incident.resolved_at) - as_utc(incident.opened_at)).total_seconds())
    run_durations = []
    for run in runs:
        if run.finished_at and run.started_at:
            run_durations.append((as_utc(run.finished_at) - as_utc(run.started_at)).total_seconds())
    by_source = Counter(row.source for row in rows)
    by_priority = Counter(row.priority for row in active)
    sla = [row for row in active if row.sla_due_at and as_utc(row.sla_due_at) and as_utc(row.sla_due_at) < now]
    reopened = [row for row in rows if row.resolution_code and row.state in active_states]
    documents = db.query(Document).filter(Document.account_id == user.account_id).all()
    blocked = 0
    if documents:
        document_ids = [row.id for row in documents]
        blocked_ids = {
            row.document_id
            for row in db.query(WorkflowInstance).filter(WorkflowInstance.document_id.in_(document_ids), WorkflowInstance.status == "blocked").all()
        }
        blocked = len(blocked_ids)
    health = None if not documents else round((len(documents) - blocked) / len(documents), 3)
    successful_steps = sum(1 for step in steps if step.status == "completed")
    return {
        "incidents": len(rows),
        "active_incidents": len(active),
        "by_priority": dict(sorted(by_priority.items())),
        "by_source": dict(by_source),
        "simulated_incidents": by_source.get("easidoc_simulator", 0),
        "servicenow_incidents": by_source.get("servicenow", 0),
        "manual_incidents": by_source.get("manual", 0),
        "sla_exposure": len(sla),
        "mttr_seconds": None if not durations else round(sum(durations) / len(durations), 1),
        "average_investigation_seconds": None if not run_durations else round(sum(run_durations) / len(run_durations), 1),
        "investigation_coverage": 0 if not rows else round(len(investigated) / len(rows), 3),
        "recommendations": len(proposals),
        "recommendations_approved": sum(1 for row in approvals if row.status == "approved"),
        "verified_resolutions": sum(1 for row in verifications if row.recovered),
        "escalations": sum(1 for row in approvals if row.status == "escalated") + sum(1 for row in rows if row.state == "escalated"),
        "reopen_count": len(reopened),
        "knowledge_reuse": 0 if not investigated else round(len({row.incident_id for row in evidence}) / len(investigated), 3),
        "agent_success": 0 if not steps else round(successful_steps / len(steps), 3),
        "application_health": health,
        "applications": [{"id": row.id, "name": row.name, "environment": row.environment, "label": row.label} for row in db.query(Application).filter_by(account_id=user.account_id)],
        "simulated_data_separated": True,
    }


def _empty() -> dict:
    return {
        "incidents": 0,
        "active_incidents": 0,
        "by_priority": {},
        "by_source": {},
        "simulated_incidents": 0,
        "servicenow_incidents": 0,
        "manual_incidents": 0,
        "sla_exposure": 0,
        "mttr_seconds": None,
        "average_investigation_seconds": None,
        "investigation_coverage": 0,
        "recommendations": 0,
        "recommendations_approved": 0,
        "verified_resolutions": 0,
        "escalations": 0,
        "reopen_count": 0,
        "knowledge_reuse": 0,
        "agent_success": 0,
        "application_health": None,
        "applications": [],
        "simulated_data_separated": True,
    }
