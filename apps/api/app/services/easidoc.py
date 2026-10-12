"""Easidoc digital twin. All state changes are persisted and labeled as simulated."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.constants import ENVIRONMENT_LABEL
from app.errors import AppError
from app.events import queue_event
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationLog,
    ApprovalWorkflow,
    Document,
    DocumentMetadata,
    DocumentVersion,
    Incident,
    Participant,
    ParticipantAssignment,
    ResponsibleFunction,
    ScenarioDefinition,
    ScenarioExecution,
    User,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowTask,
    utcnow,
)
from app.policy import assert_approved_step
from app.security import has_permission
from app.util import audit, bump, iso, next_number, sla_deadline, touch_incident

PLACEHOLDERS = {"tbd", "n/a", "na", "none", "null", "unknown", "-", "todo", "test"}


def list_scenarios(db: Session, user: User) -> list[dict]:
    definitions = db.query(ScenarioDefinition).order_by(ScenarioDefinition.id).all()
    payloads = []
    for definition in definitions:
        latest = (
            db.query(ScenarioExecution)
            .filter(
                ScenarioExecution.account_id == user.account_id,
                ScenarioExecution.scenario_id == definition.id,
            )
            .order_by(ScenarioExecution.created_at.desc())
            .first()
        )
        payloads.append(
            {
                "id": definition.id,
                "name": definition.name,
                "description": definition.description,
                "variants": definition.variants or [],
                "environment_label": ENVIRONMENT_LABEL,
                "latest_execution": None
                if latest is None
                else {
                    "id": latest.id,
                    "variant": latest.variant,
                    "status": latest.status,
                    "document_id": latest.document_id,
                    "incident_id": latest.incident_id,
                    "created_at": iso(latest.created_at),
                },
            }
        )
    return payloads


def seed_scenario(db: Session, user: User, scenario_id: str, variant: str | None) -> dict:
    definition = db.get(ScenarioDefinition, scenario_id)
    if definition is None:
        raise AppError("NOT_FOUND", "That simulator scenario does not exist.", 404)
    if scenario_id == "missing-document-metadata":
        variant = variant or "A"
        if variant not in {"A", "B", "C"}:
            raise AppError("INVALID_VARIANT", "Use variant A, B, or C.", 422)
    else:
        variant = None
    _purge_scenario(db, user.account_id, scenario_id)
    if scenario_id == "missing-periodic-review-participant":
        execution = _seed_missing_participant(db, user)
    elif scenario_id == "missing-document-metadata":
        execution = _seed_missing_metadata(db, user, variant or "A")
    else:
        raise AppError("NOT_FOUND", "That simulator scenario does not exist.", 404)
    audit(db, user, "simulator.seed", "scenario", scenario_id, {"execution_id": execution.id, "variant": variant})
    return scenario_state(db, user, scenario_id)


def reset_scenario(db: Session, user: User, scenario_id: str, variant: str | None) -> dict:
    return seed_scenario(db, user, scenario_id, variant)


def scenario_state(db: Session, user: User, scenario_id: str) -> dict:
    definition = db.get(ScenarioDefinition, scenario_id)
    if definition is None:
        raise AppError("NOT_FOUND", "That simulator scenario does not exist.", 404)
    execution = (
        db.query(ScenarioExecution)
        .filter(ScenarioExecution.account_id == user.account_id, ScenarioExecution.scenario_id == scenario_id)
        .order_by(ScenarioExecution.created_at.desc())
        .first()
    )
    document = None
    if execution and execution.document_id:
        document = _document_for_account(db, user, execution.document_id)
    return {
        "scenario_id": scenario_id,
        "name": definition.name,
        "description": definition.description,
        "environment": "simulator",
        "environment_label": ENVIRONMENT_LABEL,
        "execution": None
        if execution is None
        else {
            "id": execution.id,
            "variant": execution.variant,
            "status": execution.status,
            "document_id": execution.document_id,
            "incident_id": execution.incident_id,
        },
        "document": None if document is None else public_state(db, document),
    }


def list_documents(db: Session, user: User) -> list[dict]:
    rows = (
        db.query(Document)
        .filter(Document.account_id == user.account_id)
        .order_by(Document.updated_at.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "number": row.number,
            "title": row.title,
            "status": row.status,
            "row_version": row.row_version,
            "environment": "simulator",
            "environment_label": ENVIRONMENT_LABEL,
            "scenario_execution_id": row.scenario_execution_id,
        }
        for row in rows
    ]


def get_document(db: Session, user: User, document_id: str) -> dict:
    return public_state(db, _document_for_account(db, user, document_id))


def list_versions(db: Session, user: User, document_id: str) -> list[dict]:
    document = _document_for_account(db, user, document_id)
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.created_at.asc())
        .all()
    )
    return [_version_dict(version) for version in versions]


def timeline(db: Session, user: User, document_id: str) -> list[dict]:
    document = _document_for_account(db, user, document_id)
    events = (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.account_id == user.account_id, ApplicationEvent.document_id == document.id)
        .order_by(ApplicationEvent.id.asc())
        .all()
    )
    from app.events import serialize_event

    return [serialize_event(event) for event in events]


def update_metadata(
    db: Session,
    user: User,
    document_id: str,
    *,
    expected_version: int,
    values: dict,
    approval=None,
    human_confirmation: bool = False,
) -> dict:
    document = _document_for_account(db, user, document_id)
    if document.row_version != expected_version:
        raise AppError("STALE_RECORD", "The document changed. Reload it and try again.", 409)
    draft = _draft(db, document)
    if draft is None:
        raise AppError("DRAFT_REQUIRED", "Metadata can only be changed on a draft version.", 409)
    provided = {key: values[key] for key in ("document_history", "planned_effective_date") if key in values and values[key] is not None}
    if approval is not None:
        assert_approved_step(approval, "easidoc.update_metadata", document.id, {key: provided.get(key, "") for key in provided})
    elif human_confirmation and has_permission(user, "document.metadata.write"):
        pass
    else:
        raise AppError(
            "HUMAN_CONFIRMATION_REQUIRED",
            "Metadata changes require a person to confirm the values, or an approved remediation for those exact values.",
            403,
        )
    invalid = validate_metadata_values(provided)
    if invalid:
        raise AppError("INVALID_METADATA", invalid, 422)
    if "document_history" in provided:
        draft.document_history = provided["document_history"].strip()
        _record_metadata(db, draft.id, "document_history", draft.document_history, user.id)
    if "planned_effective_date" in provided:
        draft.planned_effective_date = provided["planned_effective_date"].strip()
        _record_metadata(db, draft.id, "planned_effective_date", draft.planned_effective_date, user.id)
    bump(draft)
    bump(document)
    missing = _missing_fields(draft)
    queue_event(
        db,
        account_id=user.account_id,
        name="DOCUMENT_METADATA_UPDATED",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        scenario_execution_id=document.scenario_execution_id,
        application_id=document.application_id,
        payload={"fields": list(provided), "missing_fields": missing, "simulated": True},
    )
    _log(
        db,
        document,
        level="INFO",
        signature="DOCUMENT_METADATA_UPDATED",
        message=f"DOCUMENT_METADATA_UPDATED document={document.number} fields={','.join(provided)}",
    )
    if not missing:
        queue_event(
            db,
            account_id=user.account_id,
            name="VALIDATION_PASSED",
            source="easidoc_simulator",
            environment="simulator",
            document_id=document.id,
            scenario_execution_id=document.scenario_execution_id,
            application_id=document.application_id,
            payload={"document_number": document.number, "simulated": True},
        )
        _log(
            db,
            document,
            level="INFO",
            signature="VALIDATION_PASSED",
            message=f"VALIDATION_PASSED document={document.number} mandatory metadata is complete",
        )
    audit(db, user, "easidoc.update_metadata", "document", document.id, {"fields": list(provided), "approval_id": getattr(approval, "id", None)})
    state = public_state(db, document)
    state["validation"] = {"passed": not missing, "missing_fields": missing}
    return state


def start_approval(db: Session, user: User, document_id: str, expected_version: int, approval=None) -> dict:
    document = _document_for_account(db, user, document_id)
    if approval is not None:
        assert_approved_step(approval, "easidoc.start_approval", document.id, {})
    elif not has_permission(user, "easidoc.approval.start"):
        raise AppError("FORBIDDEN", "You cannot start an Easidoc approval workflow.", 403)
    if document.row_version != expected_version:
        raise AppError("STALE_RECORD", "The document changed. Reload it and try again.", 409)
    existing = _workflow(db, document.id, "approval")
    if existing and existing.status == "in_progress":
        return public_state(db, document)
    failure = approval_block(db, document)
    if failure:
        _record_blocked_attempt(db, user, document, failure)
        raise AppError(failure["code"], failure["message"], 422, failure["details"])
    definition = _definition(db, document.application_id, "document_approval")
    workflow = WorkflowInstance(
        account_id=user.account_id,
        definition_id=definition.id,
        document_id=document.id,
        scenario_execution_id=document.scenario_execution_id,
        kind="approval",
        status="in_progress",
    )
    db.add(workflow)
    db.flush()
    db.add(WorkflowTask(workflow_instance_id=workflow.id, code="DOCUMENT_APPROVAL", name="Document approval", status="in_progress"))
    db.add(ApprovalWorkflow(workflow_instance_id=workflow.id, document_id=document.id, status="in_progress", started_at=utcnow()))
    document.status = "in_approval"
    bump(document)
    queue_event(
        db,
        account_id=user.account_id,
        name="APPROVAL_WORKFLOW_STARTED",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        scenario_execution_id=document.scenario_execution_id,
        application_id=document.application_id,
        incident_id=_incident_id(db, document),
        payload={"workflow_id": workflow.id, "document_number": document.number, "simulated": True},
    )
    _log(
        db,
        document,
        level="INFO",
        signature="APPROVAL_WORKFLOW_STARTED",
        message=f"APPROVAL_WORKFLOW_STARTED document={document.number} workflow={workflow.id}",
    )
    audit(db, user, "easidoc.start_approval", "document", document.id, {"workflow_id": workflow.id})
    return public_state(db, document)


def assign_participant(
    db: Session,
    user: User,
    workflow_id: str,
    participant_id: str,
    expected_version: int,
    approval,
) -> dict:
    workflow = _workflow_for_account(db, user, workflow_id)
    assert_approved_step(approval, "easidoc.assign_participant", workflow.id, {"participant_id": participant_id})
    if workflow.row_version != expected_version:
        raise AppError("STALE_RECORD", "The workflow changed. Reload it and try again.", 409)
    if workflow.kind != "periodic_review":
        raise AppError("INVALID_WORKFLOW", "Participants are assigned on the Auto Periodic Review workflow.", 409)
    if workflow.status == "completed":
        raise AppError("REVIEW_ALREADY_COMPLETED", "The periodic review is already complete.", 409)
    participant = _eligible_participant(db, user, workflow, participant_id)
    task = _review_task(db, workflow)
    if task.assigned_participant_id == participant.id and task.status == "assigned":
        return public_state(db, _document_for_account(db, user, workflow.document_id))
    task.assigned_participant_id = participant.id
    task.status = "assigned"
    bump_task(task)
    workflow.status = "active"
    workflow.block_code = "AWAITING_REVIEW_COMPLETION"
    workflow.block_details = {"participant_id": participant.id, "participant_name": participant.name}
    bump(workflow)
    db.add(
        ParticipantAssignment(
            task_id=task.id,
            participant_id=participant.id,
            assigned_by=user.id,
            approval_id=approval.id,
            active=True,
        )
    )
    document = _document_for_account(db, user, workflow.document_id)
    queue_event(
        db,
        account_id=user.account_id,
        name="PARTICIPANT_ASSIGNED",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        scenario_execution_id=document.scenario_execution_id,
        application_id=document.application_id,
        incident_id=_incident_id(db, document),
        payload={"workflow_id": workflow.id, "participant_id": participant.id, "participant_name": participant.name, "simulated": True},
    )
    queue_event(
        db,
        account_id=user.account_id,
        name="PARTICIPANT_NOTIFIED",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        application_id=document.application_id,
        scenario_execution_id=document.scenario_execution_id,
        payload={
            "participant_email": participant.email,
            "channel": "simulator_activity",
            "external_email_sent": False,
            "message": f"{participant.name} was notified inside the simulator. No external email was sent.",
            "simulated": True,
        },
    )
    _log(
        db,
        document,
        level="INFO",
        signature="PARTICIPANT_ASSIGNED",
        message=f"PARTICIPANT_ASSIGNED document={document.number} participant={participant.email} review_still_required=true",
    )
    audit(db, user, "easidoc.assign_participant", "workflow", workflow.id, {"participant_id": participant.id, "approval_id": approval.id})
    return public_state(db, document)


def complete_review(db: Session, user: User, workflow_id: str, expected_version: int) -> dict:
    workflow = _workflow_for_account(db, user, workflow_id)
    if workflow.row_version != expected_version:
        raise AppError("STALE_RECORD", "The workflow changed. Reload it and try again.", 409)
    task = _review_task(db, workflow)
    if not task.assigned_participant_id:
        raise AppError("PARTICIPANT_REQUIRED", "Assign a participant before the review can be completed.", 409)
    if user.participant_id != task.assigned_participant_id:
        raise AppError("FORBIDDEN", "Only the assigned participant can complete this review.", 403)
    document = _document_for_account(db, user, workflow.document_id)
    if task.status == "completed" and workflow.status == "completed":
        return public_state(db, document)
    task.status = "completed"
    task.completed_by = user.id
    task.completed_at = utcnow()
    bump_task(task)
    workflow.status = "completed"
    workflow.block_code = None
    workflow.block_details = {"completed_by": user.id}
    bump(workflow)
    queue_event(
        db,
        account_id=user.account_id,
        name="PERIODIC_REVIEW_COMPLETED",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        scenario_execution_id=document.scenario_execution_id,
        application_id=document.application_id,
        incident_id=_incident_id(db, document),
        payload={"workflow_id": workflow.id, "completed_by": user.name, "simulated": True},
    )
    _log(
        db,
        document,
        level="INFO",
        signature="PERIODIC_REVIEW_COMPLETED",
        message=f"PERIODIC_REVIEW_COMPLETED document={document.number} participant={user.email}",
    )
    audit(db, user, "easidoc.complete_review", "workflow", workflow.id, {})
    db.flush()
    return public_state(db, document)


def unassign_participant(db: Session, user: User, workflow_id: str) -> dict:
    workflow = _workflow_for_account(db, user, workflow_id)
    task = _review_task(db, workflow)
    if task.status == "completed":
        raise AppError("ROLLBACK_REJECTED", "The review is already complete, so the assignment cannot be rolled back.", 409)
    task.assigned_participant_id = None
    task.status = "blocked"
    bump_task(task)
    workflow.status = "blocked"
    workflow.block_code = "REVIEW_PARTICIPANT_NOT_FOUND"
    workflow.block_details = {"rolled_back": True}
    bump(workflow)
    db.query(ParticipantAssignment).filter(ParticipantAssignment.task_id == task.id, ParticipantAssignment.active.is_(True)).update(
        {"active": False}
    )
    document = _document_for_account(db, user, workflow.document_id)
    queue_event(
        db,
        account_id=user.account_id,
        name="ACTION_ROLLED_BACK",
        source="easidoc_simulator",
        environment="simulator",
        document_id=document.id,
        application_id=document.application_id,
        scenario_execution_id=document.scenario_execution_id,
        payload={"workflow_id": workflow.id, "simulated": True},
    )
    audit(db, user, "easidoc.rollback_assignment", "workflow", workflow.id, {})
    return public_state(db, document)


def assert_participant_eligible(db: Session, user: User, workflow_id: str, participant_id: str) -> Participant:
    workflow = _workflow_for_account(db, user, workflow_id)
    return _eligible_participant(db, user, workflow, participant_id)


def validate_metadata_values(values: dict) -> str | None:
    if "document_history" in values:
        error = _validate_history(str(values.get("document_history") or ""))
        if error:
            return error
    if "planned_effective_date" in values:
        error = _validate_date(str(values.get("planned_effective_date") or ""))
        if error:
            return error
    return None


def approval_block(db: Session, document: Document) -> dict | None:
    draft = _draft(db, document)
    if draft is None:
        return {"code": "DRAFT_REQUIRED", "message": "Approval starts from a draft version.", "details": {}}
    missing = _missing_fields(draft)
    if missing:
        return {
            "code": "REQUIRED_METADATA_MISSING",
            "message": "Approval cannot start while mandatory metadata is blank.",
            "details": {"missing_fields": missing, "document_number": document.number},
        }
    review = _workflow(db, document.id, "periodic_review")
    if review and review.status != "completed":
        task = _review_task(db, review)
        if not task.assigned_participant_id:
            return {
                "code": "REVIEW_PARTICIPANT_NOT_FOUND",
                "message": "Approval cannot start because the Auto Periodic Review task has no participant.",
                "details": {"workflow_id": review.id, "task_code": task.code, "responsible_function": "QA"},
            }
        return {
            "code": "PERIODIC_REVIEW_INCOMPLETE",
            "message": "Approval cannot start until the assigned participant completes the periodic review.",
            "details": {"workflow_id": review.id, "participant_id": task.assigned_participant_id},
        }
    return None


def public_state(db: Session, document: Document) -> dict:
    versions = (
        db.query(DocumentVersion).filter(DocumentVersion.document_id == document.id).order_by(DocumentVersion.created_at.asc()).all()
    )
    workflows = db.query(WorkflowInstance).filter(WorkflowInstance.document_id == document.id).all()
    workflow_payloads = [_workflow_dict(db, workflow) for workflow in workflows]
    review = next((item for item in workflow_payloads if item["kind"] == "periodic_review"), None)
    draft = next((version for version in versions if version.state == "draft"), None)
    participants = []
    if review and review.get("required_function_id"):
        rows = (
            db.query(Participant)
            .filter(
                Participant.account_id == document.account_id,
                Participant.function_id == review["required_function_id"],
                Participant.active.is_(True),
            )
            .order_by(Participant.name)
            .all()
        )
        functions = {row.id: row for row in db.query(ResponsibleFunction).filter(ResponsibleFunction.account_id == document.account_id)}
        participants = [
            {
                "id": row.id,
                "name": row.name,
                "email": row.email,
                "function_code": functions[row.function_id].code,
                "function_name": functions[row.function_id].name,
                "has_user": bool(row.user_id),
            }
            for row in rows
        ]
    incident = (
        db.query(Incident)
        .filter(Incident.document_id == document.id, Incident.account_id == document.account_id)
        .order_by(Incident.opened_at.desc())
        .first()
    )
    from app.events import serialize_event

    events = (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.document_id == document.id)
        .order_by(ApplicationEvent.id.desc())
        .limit(40)
        .all()
    )
    return {
        "environment": "simulator",
        "environment_label": ENVIRONMENT_LABEL,
        "document": {
            "id": document.id,
            "number": document.number,
            "title": document.title,
            "document_type": document.document_type,
            "status": document.status,
            "row_version": document.row_version,
            "application_id": document.application_id,
            "scenario_execution_id": document.scenario_execution_id,
            "updated_at": iso(document.updated_at),
        },
        "versions": [_version_dict(version) for version in versions],
        "workflows": workflow_payloads,
        "eligible_participants": participants,
        "missing_fields": _missing_fields(draft) if draft else ["document_history", "planned_effective_date"],
        "approval_started": any(item["kind"] == "approval" and item["status"] == "in_progress" for item in workflow_payloads),
        "incident_id": None if incident is None else incident.id,
        "timeline": [serialize_event(event) for event in reversed(events)],
    }


def snapshot(db: Session, document: Document) -> dict:
    state = public_state(db, document)
    review = next((item for item in state["workflows"] if item["kind"] == "periodic_review"), None)
    draft = next((item for item in state["versions"] if item["state"] == "draft"), None)
    effective = next((item for item in state["versions"] if item["state"] == "effective"), None)
    return {
        "document_id": document.id,
        "number": document.number,
        "title": document.title,
        "status": document.status,
        "row_version": document.row_version,
        "application_id": document.application_id,
        "scenario_execution_id": document.scenario_execution_id,
        "draft": draft,
        "effective": effective,
        "missing_fields": state["missing_fields"] if draft else [],
        "eligible_participants": state.get("eligible_participants") or [],
        "review": review,
        "approval_started": state["approval_started"],
        "environment": "simulator",
        "environment_label": ENVIRONMENT_LABEL,
    }


def validate_step_values(payload: dict) -> str | None:
    for step in payload.get("steps") or []:
        if step.get("action") != "easidoc.update_metadata":
            continue
        params = step.get("params") or {}
        present = {}
        for field in ("document_history", "planned_effective_date"):
            if str(params.get(field) or "").strip():
                present[field] = params[field]
        error = validate_metadata_values(present)
        if error:
            return error
    return None


def _seed_missing_participant(db: Session, user: User) -> ScenarioExecution:
    application = _application(db, user.account_id)
    function = db.query(ResponsibleFunction).filter_by(account_id=user.account_id, code="QA").one()
    execution = ScenarioExecution(account_id=user.account_id, scenario_id="missing-periodic-review-participant", status="seeded")
    db.add(execution)
    db.flush()
    document = Document(
        account_id=user.account_id,
        application_id=application.id,
        scenario_execution_id=execution.id,
        number="DOC-1042",
        title="Supplier Qualification Procedure",
        document_type="SOP",
        status="effective",
    )
    db.add(document)
    db.flush()
    effective = _add_version(
        db,
        document,
        label="3.0",
        state="effective",
        history="Version 3.0 aligned supplier scoring with the quality manual and was approved by Quality Assurance.",
        effective_date="2025-06-01",
        body="Effective procedure for qualifying suppliers. This version is controlled and remains in effect.",
    )
    draft = _add_version(
        db,
        document,
        label="3.1",
        state="draft",
        history="Version 3.1 adds a requirement to record the supplier risk rationale before approval.",
        effective_date="2026-11-15",
        body="Draft update. Approval cannot start while the Auto Periodic Review of the effective version has no participant.",
    )
    document.effective_version_id = effective.id
    document.draft_version_id = draft.id
    definition = _definition(db, application.id, "auto_periodic_review")
    workflow = WorkflowInstance(
        account_id=user.account_id,
        definition_id=definition.id,
        document_id=document.id,
        scenario_execution_id=execution.id,
        kind="periodic_review",
        status="blocked",
        required_function_id=function.id,
        block_code="REVIEW_PARTICIPANT_NOT_FOUND",
        block_details={"responsible_function": "QA", "participant_count": 0},
    )
    db.add(workflow)
    db.flush()
    db.add(
        WorkflowTask(
            workflow_instance_id=workflow.id,
            code="AUTO_PERIODIC_REVIEW",
            name="Auto Periodic Review",
            status="blocked",
        )
    )
    incident = _open_incident(
        db,
        user,
        document,
        execution,
        application,
        short="Easidoc approval blocked: periodic review participant missing",
        description=(
            "DOC-1042 Supplier Qualification Procedure cannot start approval. "
            "The Auto-start Periodic Review workflow is active and the Auto Periodic Review task has no participant. "
            "Error signature: REVIEW_PARTICIPANT_NOT_FOUND. This incident was opened by the Easidoc simulator, not by ServiceNow."
        ),
        signature="REVIEW_PARTICIPANT_NOT_FOUND",
    )
    execution.document_id = document.id
    execution.incident_id = incident.id
    correlation = uuid.uuid4().hex[:12]
    _seed_baseline_logs(db, document, incident, correlation)
    failure = approval_block(db, document)
    assert failure and failure["code"] == "REVIEW_PARTICIPANT_NOT_FOUND"
    _record_blocked_attempt(db, user, document, failure, incident_id=incident.id, correlation_id=correlation)
    queue_event(
        db,
        account_id=user.account_id,
        name="INCIDENT_DETECTED",
        source="ams",
        environment="simulator",
        application_id=application.id,
        document_id=document.id,
        incident_id=incident.id,
        scenario_execution_id=execution.id,
        payload={"number": incident.number, "source": "easidoc_simulator", "simulated": True},
    )
    return execution


def _seed_missing_metadata(db: Session, user: User, variant: str) -> ScenarioExecution:
    application = _application(db, user.account_id)
    execution = ScenarioExecution(
        account_id=user.account_id,
        scenario_id="missing-document-metadata",
        variant=variant,
        status="seeded",
    )
    db.add(execution)
    db.flush()
    history = "Initial draft prepared for the deviation process update." if variant == "B" else ""
    effective_date = "2026-12-01" if variant == "A" else ""
    missing = []
    if variant in {"A", "C"}:
        missing.append("document_history")
    if variant in {"B", "C"}:
        missing.append("planned_effective_date")
    document = Document(
        account_id=user.account_id,
        application_id=application.id,
        scenario_execution_id=execution.id,
        number="DOC-2088",
        title="Deviation Management SOP",
        document_type="SOP",
        status="draft",
    )
    db.add(document)
    db.flush()
    draft = _add_version(
        db,
        document,
        label="0.1",
        state="draft",
        history=history,
        effective_date=effective_date,
        body="Draft deviation management procedure. Approval is unavailable until mandatory metadata is complete.",
    )
    document.draft_version_id = draft.id
    incident = _open_incident(
        db,
        user,
        document,
        execution,
        application,
        short="Easidoc approval blocked: mandatory metadata missing",
        description=(
            f"DOC-2088 Deviation Management SOP is a draft and approval was rejected. "
            f"Variant {variant}. Missing fields: {', '.join(missing)}. "
            "Error signature: REQUIRED_METADATA_MISSING. This incident was opened by the Easidoc simulator, not by ServiceNow."
        ),
        signature="REQUIRED_METADATA_MISSING",
    )
    execution.document_id = document.id
    execution.incident_id = incident.id
    correlation = uuid.uuid4().hex[:12]
    _seed_baseline_logs(db, document, incident, correlation)
    failure = approval_block(db, document)
    assert failure and failure["code"] == "REQUIRED_METADATA_MISSING"
    _record_blocked_attempt(db, user, document, failure, incident_id=incident.id, correlation_id=correlation)
    queue_event(
        db,
        account_id=user.account_id,
        name="INCIDENT_DETECTED",
        source="ams",
        environment="simulator",
        application_id=application.id,
        document_id=document.id,
        incident_id=incident.id,
        scenario_execution_id=execution.id,
        payload={"number": incident.number, "variant": variant, "source": "easidoc_simulator", "simulated": True},
    )
    return execution


def _record_blocked_attempt(db: Session, user: User, document: Document, failure: dict, incident_id: str | None = None, correlation_id: str | None = None) -> None:
    correlation_id = correlation_id or uuid.uuid4().hex[:12]
    incident_id = incident_id or _incident_id(db, document)
    queue_event(
        db,
        account_id=document.account_id,
        name=failure["code"],
        source="easidoc_simulator",
        environment="simulator",
        application_id=document.application_id,
        document_id=document.id,
        incident_id=incident_id,
        scenario_execution_id=document.scenario_execution_id,
        payload={**failure["details"], "message": failure["message"], "simulated": True, "correlation_id": correlation_id},
    )
    _log(
        db,
        document,
        level="ERROR",
        signature=failure["code"],
        correlation_id=correlation_id,
        incident_id=incident_id,
        message=(
            f"{failure['code']} document={document.number} correlation_id={correlation_id} "
            f"details={failure['details']}"
        ),
        attributes={"details": failure["details"], "simulated": True},
    )
    _log(
        db,
        document,
        level="WARN",
        signature=failure["code"],
        correlation_id=correlation_id,
        incident_id=incident_id,
        message=f"Approval initiation rejected for {document.number}. {failure['message']}",
        attributes={"simulated": True},
    )


def _open_incident(db, user, document, execution, application, short, description, signature) -> Incident:
    opened = utcnow()
    incident = Incident(
        account_id=user.account_id,
        number=next_number(db, "AMS", "incident"),
        source="easidoc_simulator",
        environment="simulator",
        short_description=short,
        description=description,
        state="new",
        priority="2",
        impact="2",
        urgency="2",
        assignment_group="AMS Application Support",
        category="application",
        subcategory="workflow",
        business_service="Easidoc Quality Management",
        cmdb_ci="Easidoc Digital Twin",
        application_id=application.id,
        scenario_execution_id=execution.id,
        document_id=document.id,
        opened_at=opened,
        sla_due_at=sla_deadline("2", opened),
        servicenow_sync_status="not_linked",
        raw_payload={"origin": "easidoc_simulator", "signature": signature, "simulated": True},
    )
    db.add(incident)
    db.flush()
    return incident


def _seed_baseline_logs(db, document, incident, correlation: str) -> None:
    opened = incident.opened_at
    for offset, message in (
        (120, "periodic review scheduler heartbeat status=ok"),
        (90, "document library index refresh completed"),
        (70, "workflow engine idle"),
    ):
        _log(
            db,
            document,
            level="INFO",
            message=message,
            timestamp=opened - timedelta(minutes=offset),
            incident_id=incident.id,
            correlation_id=f"hb-{offset}",
            attributes={"window": "baseline", "simulated": True},
        )


def _purge_scenario(db: Session, account_id: str, scenario_id: str) -> None:
    from app.models import (
        ActionExecution,
        AgentRun,
        AgentStep,
        Conversation,
        ConversationMessage,
        HumanApproval,
        IncidentEvidence,
        KnowledgeChunk,
        KnowledgeDocument,
        RemediationProposal,
        RootCauseHypothesis,
        VerificationResult,
    )

    executions = (
        db.query(ScenarioExecution)
        .filter(ScenarioExecution.account_id == account_id, ScenarioExecution.scenario_id == scenario_id)
        .all()
    )
    for execution in executions:
        incidents = db.query(Incident).filter(Incident.scenario_execution_id == execution.id).all()
        incident_ids = [row.id for row in incidents]
        if incident_ids:
            run_ids = [row.id for row in db.query(AgentRun).filter(AgentRun.incident_id.in_(incident_ids)).all()]
            if run_ids:
                db.query(AgentStep).filter(AgentStep.run_id.in_(run_ids)).delete(synchronize_session=False)
                db.query(AgentRun).filter(AgentRun.id.in_(run_ids)).delete(synchronize_session=False)
            db.query(VerificationResult).filter(VerificationResult.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            db.query(ActionExecution).filter(ActionExecution.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            db.query(HumanApproval).filter(HumanApproval.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            db.query(RemediationProposal).filter(RemediationProposal.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            db.query(RootCauseHypothesis).filter(RootCauseHypothesis.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            db.query(IncidentEvidence).filter(IncidentEvidence.incident_id.in_(incident_ids)).delete(synchronize_session=False)
            drafts = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.account_id == account_id,
                    KnowledgeDocument.source == "ai_draft",
                    KnowledgeDocument.source_id.in_(incident_ids),
                )
                .all()
            )
            draft_ids = [row.id for row in drafts]
            if draft_ids:
                db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id.in_(draft_ids)).delete(synchronize_session=False)
                db.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(draft_ids)).delete(synchronize_session=False)
        conversations = db.query(Conversation).filter(Conversation.document_id == execution.document_id).all() if execution.document_id else []
        conversation_ids = [row.id for row in conversations]
        if conversation_ids:
            db.query(ConversationMessage).filter(ConversationMessage.conversation_id.in_(conversation_ids)).delete(synchronize_session=False)
            db.query(Conversation).filter(Conversation.id.in_(conversation_ids)).delete(synchronize_session=False)
        db.query(ApplicationLog).filter(ApplicationLog.scenario_execution_id == execution.id).delete(synchronize_session=False)
        db.query(ApplicationEvent).filter(ApplicationEvent.scenario_execution_id == execution.id).delete(synchronize_session=False)
        workflows = db.query(WorkflowInstance).filter(WorkflowInstance.scenario_execution_id == execution.id).all()
        for workflow in workflows:
            tasks = db.query(WorkflowTask).filter(WorkflowTask.workflow_instance_id == workflow.id).all()
            for task in tasks:
                db.query(ParticipantAssignment).filter(ParticipantAssignment.task_id == task.id).delete(synchronize_session=False)
            db.query(WorkflowTask).filter(WorkflowTask.workflow_instance_id == workflow.id).delete(synchronize_session=False)
            db.query(ApprovalWorkflow).filter(ApprovalWorkflow.workflow_instance_id == workflow.id).delete(synchronize_session=False)
        db.query(WorkflowInstance).filter(WorkflowInstance.scenario_execution_id == execution.id).delete(synchronize_session=False)
        if execution.document_id:
            versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == execution.document_id).all()
            for version in versions:
                db.query(DocumentMetadata).filter(DocumentMetadata.version_id == version.id).delete(synchronize_session=False)
            db.query(DocumentVersion).filter(DocumentVersion.document_id == execution.document_id).delete(synchronize_session=False)
            db.query(Document).filter(Document.id == execution.document_id).delete(synchronize_session=False)
        if incident_ids:
            db.query(Incident).filter(Incident.id.in_(incident_ids)).delete(synchronize_session=False)
        db.delete(execution)
    db.flush()


def _add_version(db, document: Document, *, label: str, state: str, history: str, effective_date: str, body: str) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        version_label=label,
        state=state,
        title=document.title,
        body=body,
        document_history=history,
        planned_effective_date=effective_date,
    )
    db.add(version)
    db.flush()
    _record_metadata(db, version.id, "document_history", history, None)
    _record_metadata(db, version.id, "planned_effective_date", effective_date, None)
    return version


def _record_metadata(db, version_id: str, field: str, value: str, user_id: str | None) -> None:
    db.add(DocumentMetadata(version_id=version_id, field_name=field, field_value=value, updated_by=user_id))


def _missing_fields(draft: DocumentVersion | None) -> list[str]:
    if draft is None:
        return ["document_history", "planned_effective_date"]
    missing = []
    if not (draft.document_history or "").strip():
        missing.append("document_history")
    if not (draft.planned_effective_date or "").strip():
        missing.append("planned_effective_date")
    return missing


def _validate_history(value: str) -> str | None:
    text = value.strip()
    if not text:
        return "Document History is blank."
    if text.lower() in PLACEHOLDERS:
        return "Document History cannot be a placeholder."
    if len(text) < 12:
        return "Document History must describe the change."
    return None


def _validate_date(value: str) -> str | None:
    text = value.strip()
    if not text:
        return "Planned Effective Date is blank."
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return "Planned Effective Date must use YYYY-MM-DD."
    if parsed.year < 1990 or parsed.year > 2100:
        return "Planned Effective Date is outside the accepted range."
    return None


def _document_for_account(db: Session, user: User, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Document was not found.", 404)
    return document


def _workflow_for_account(db: Session, user: User, workflow_id: str) -> WorkflowInstance:
    workflow = db.get(WorkflowInstance, workflow_id)
    if workflow is None or workflow.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Workflow was not found.", 404)
    return workflow


def _workflow(db: Session, document_id: str, kind: str) -> WorkflowInstance | None:
    return db.query(WorkflowInstance).filter_by(document_id=document_id, kind=kind).one_or_none()


def _review_task(db: Session, workflow: WorkflowInstance) -> WorkflowTask:
    task = (
        db.query(WorkflowTask)
        .filter(WorkflowTask.workflow_instance_id == workflow.id, WorkflowTask.code == "AUTO_PERIODIC_REVIEW")
        .one_or_none()
    )
    if task is None:
        raise AppError("NOT_FOUND", "The Auto Periodic Review task was not found.", 404)
    return task


def _draft(db: Session, document: Document) -> DocumentVersion | None:
    if not document.draft_version_id:
        return db.query(DocumentVersion).filter_by(document_id=document.id, state="draft").one_or_none()
    return db.get(DocumentVersion, document.draft_version_id)


def _definition(db: Session, application_id: str, code: str) -> WorkflowDefinition:
    row = db.query(WorkflowDefinition).filter_by(application_id=application_id, code=code).one_or_none()
    if row is None:
        raise AppError("NOT_CONFIGURED", f"Workflow definition {code} is not installed.", 500)
    return row


def _application(db: Session, account_id: str) -> Application:
    row = db.query(Application).filter_by(account_id=account_id, code="easidoc").one_or_none()
    if row is None:
        raise AppError("NOT_CONFIGURED", "The Easidoc simulator application is not installed.", 500)
    return row


def _eligible_participant(db: Session, user: User, workflow: WorkflowInstance, participant_id: str) -> Participant:
    participant = db.get(Participant, participant_id)
    if participant is None or participant.account_id != user.account_id or not participant.active:
        raise AppError("PARTICIPANT_NOT_ELIGIBLE", "That participant is not available in this account.", 422)
    if workflow.required_function_id and participant.function_id != workflow.required_function_id:
        raise AppError(
            "PARTICIPANT_NOT_ELIGIBLE",
            "That participant is outside the responsible function required by this review.",
            422,
        )
    return participant


def _incident_id(db: Session, document: Document) -> str | None:
    incident = (
        db.query(Incident)
        .filter(Incident.document_id == document.id, Incident.state.notin_(["closed"]))
        .order_by(Incident.opened_at.desc())
        .first()
    )
    return None if incident is None else incident.id


def _version_dict(version: DocumentVersion) -> dict:
    return {
        "id": version.id,
        "version_label": version.version_label,
        "state": version.state,
        "title": version.title,
        "body": version.body,
        "document_history": version.document_history,
        "planned_effective_date": version.planned_effective_date,
        "row_version": version.row_version,
        "created_at": iso(version.created_at),
        "updated_at": iso(version.updated_at),
    }


def _workflow_dict(db: Session, workflow: WorkflowInstance) -> dict:
    definition = db.get(WorkflowDefinition, workflow.definition_id)
    tasks = db.query(WorkflowTask).filter(WorkflowTask.workflow_instance_id == workflow.id).all()
    task_payloads = []
    for task in tasks:
        participant = db.get(Participant, task.assigned_participant_id) if task.assigned_participant_id else None
        task_payloads.append(
            {
                "id": task.id,
                "code": task.code,
                "name": task.name,
                "status": task.status,
                "assigned_participant_id": task.assigned_participant_id,
                "assigned_participant_name": None if participant is None else participant.name,
                "row_version": task.row_version,
                "completed_at": iso(task.completed_at),
            }
        )
    return {
        "id": workflow.id,
        "kind": workflow.kind,
        "name": definition.name if definition else workflow.kind,
        "status": workflow.status,
        "block_code": workflow.block_code,
        "block_details": workflow.block_details or {},
        "row_version": workflow.row_version,
        "required_function_id": workflow.required_function_id,
        "tasks": task_payloads,
        "updated_at": iso(workflow.updated_at),
    }


def _log(
    db,
    document: Document,
    *,
    level: str,
    message: str,
    signature: str = "",
    correlation_id: str = "",
    incident_id: str | None = None,
    attributes: dict | None = None,
    timestamp: datetime | None = None,
) -> None:
    db.add(
        ApplicationLog(
            account_id=document.account_id,
            application_id=document.application_id,
            scenario_execution_id=document.scenario_execution_id,
            incident_id=incident_id,
            document_id=document.id,
            timestamp=timestamp or utcnow(),
            level=level,
            logger="easidoc.workflow",
            message=message,
            signature=signature,
            correlation_id=correlation_id,
            attributes=attributes or {"simulated": True},
            environment="simulator",
        )
    )


def bump_task(task: WorkflowTask) -> None:
    task.row_version = int(task.row_version or 1) + 1
