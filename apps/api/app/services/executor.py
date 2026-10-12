"""Human decisions and allowlisted execution."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.events import queue_event
from app.models import (
    ActionExecution,
    HumanApproval,
    Incident,
    RemediationProposal,
    RootCauseHypothesis,
    ScenarioExecution,
    User,
    VerificationResult,
    utcnow,
)
from app.policy import apply_modifications, assert_approved_step, canonical_hash, load_approved, readiness_error
from app.security import has_permission
from app.services import easidoc, knowledge
from app.services import servicenow
from app.util import audit, iso, touch_incident


def decide(db: Session, user: User, approval_id: str, decision: str, comment: str = "", modifications: dict | None = None) -> dict:
    if not has_permission(user, "approval.decide"):
        raise AppError("FORBIDDEN", "You cannot decide approvals.", 403)
    approval = db.get(HumanApproval, approval_id)
    if approval is None or approval.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Approval was not found.", 404)
    if approval.status not in {"pending", "needs_investigation"}:
        raise AppError("APPROVAL_CLOSED", "This approval is no longer awaiting a decision.", 409)
    proposal = db.get(RemediationProposal, approval.proposal_id)
    if proposal is None:
        raise AppError("NOT_FOUND", "The remediation proposal was not found.", 404)
    payload = apply_modifications(proposal.payload, modifications)
    if modifications:
        error = easidoc.validate_step_values(payload)
        if error:
            raise AppError("INVALID_METADATA", error, 422)
        _assert_participant(db, user, payload)
    if decision == "approve":
        problem = readiness_error(payload) or easidoc.validate_step_values(payload)
        if problem:
            raise AppError("HUMAN_INPUT_REQUIRED", problem, 422)
        _assert_participant(db, user, payload)
        approval.status = "approved"
        event_name = "HUMAN_APPROVAL_GRANTED"
    elif decision == "reject":
        if not comment.strip():
            raise AppError("COMMENT_REQUIRED", "A comment is required to reject a remediation.", 422)
        approval.status = "rejected"
        event_name = "HUMAN_APPROVAL_REJECTED"
    elif decision == "modify":
        approval.status = "pending"
        event_name = "HUMAN_APPROVAL_REQUESTED"
    elif decision == "investigate":
        approval.status = "needs_investigation"
        event_name = "HUMAN_APPROVAL_REQUESTED"
    elif decision == "escalate":
        if not comment.strip():
            raise AppError("COMMENT_REQUIRED", "A comment is required to escalate.", 422)
        approval.status = "escalated"
        incident = db.get(Incident, approval.incident_id)
        if incident:
            incident.state = "escalated"
            touch_incident(incident)
        event_name = "HUMAN_APPROVAL_REJECTED"
    else:
        raise AppError("INVALID_DECISION", "Decision must be approve, reject, modify, investigate, or escalate.", 422)
    digest = canonical_hash(payload)
    proposal.payload = payload
    proposal.payload_hash = digest
    proposal.row_version = int(proposal.row_version or 1) + 1
    approval.payload = payload
    approval.payload_hash = digest
    approval.decision = decision
    approval.comment = comment
    approval.decided_by = user.id
    approval.decided_at = utcnow()
    if decision == "modify":
        approval.decided_by = None
        approval.decided_at = None
    incident = db.get(Incident, approval.incident_id)
    queue_event(
        db,
        account_id=user.account_id,
        name=event_name,
        source="ams",
        environment=incident.environment if incident else "simulator",
        incident_id=approval.incident_id,
        document_id=payload.get("document_id"),
        payload={"approval_id": approval.id, "decision": decision},
    )
    audit(db, user, f"approval.{decision}", "human_approval", approval.id, {"comment": comment, "payload_hash": digest})
    db.flush()
    return _approval_brief(approval)


def execute(db: Session, user: User, approval_id: str, *, continuation: bool = False) -> dict:
    if not continuation and not has_permission(user, "action.execute"):
        raise AppError("FORBIDDEN", "You cannot execute remediations.", 403)
    approval = load_approved(db, user, approval_id)
    settings = get_settings()
    if settings.segregation_of_duties and not continuation and approval.decided_by == user.id:
        raise AppError("SEGREGATION_OF_DUTIES", "A different person must execute this approval.", 403)
    proposal = db.get(RemediationProposal, approval.proposal_id)
    if proposal is None or proposal.payload_hash != approval.payload_hash:
        raise AppError("APPROVAL_INVALIDATED", "The action changed after approval.", 409)
    payload = approval.payload
    for step in payload.get("steps") or []:
        if step.get("action") not in {
            "easidoc.assign_participant",
            "easidoc.update_metadata",
            "easidoc.start_approval",
            "easidoc.await_event",
            "verify_state",
            "record_outcome",
            "draft_knowledge",
            "servicenow.add_work_note",
        }:
            raise AppError("ACTION_REJECTED", "The approved payload contains an action that is not allowlisted.", 403)
    incident = db.get(Incident, approval.incident_id)
    if incident is None:
        raise AppError("NOT_FOUND", "Incident was not found.", 404)
    progress = dict(approval.progress or {})
    completed = set(progress.get("completed_steps") or [])
    if not completed:
        _assert_current_versions(db, payload)
    queue_event(
        db,
        account_id=user.account_id,
        name="ACTION_EXECUTION_STARTED",
        source="ams",
        environment=incident.environment,
        incident_id=incident.id,
        document_id=payload.get("document_id"),
        scenario_execution_id=incident.scenario_execution_id,
        payload={"approval_id": approval.id, "continuation": continuation},
    )
    results = []
    for step in payload.get("steps") or []:
        if step["id"] in completed:
            continue
        outcome = _run_step(db, user, approval, incident, step)
        results.append({"id": step["id"], "action": step["action"], **outcome})
        if outcome.get("waiting"):
            progress.update({"state": "waiting", "waiting_for": (step.get("params") or {}).get("event"), "completed_steps": sorted(completed)})
            approval.progress = progress
            db.flush()
            return {"status": "waiting", "approval_id": approval.id, "results": results, "incident_state": incident.state}
        if not outcome.get("ok"):
            progress.update({"state": "failed", "completed_steps": sorted(completed), "failed_step": step["id"]})
            approval.progress = progress
            db.flush()
            return {"status": "failed", "approval_id": approval.id, "results": results, "incident_state": incident.state}
        completed.add(step["id"])
    progress.update({"state": "completed", "completed_steps": sorted(completed)})
    approval.progress = progress
    db.flush()
    return {"status": "completed", "approval_id": approval.id, "results": results, "incident_state": incident.state}


def advance_for_workflow(db: Session, user: User, workflow_id: str) -> list[dict]:
    approvals = db.query(HumanApproval).filter(HumanApproval.account_id == user.account_id, HumanApproval.status == "approved").all()
    results = []
    for approval in approvals:
        progress = approval.progress or {}
        if progress.get("state") != "waiting":
            continue
        if approval.payload.get("workflow_id") != workflow_id:
            continue
        results.append(execute(db, user, approval.id, continuation=True))
    return results


def rollback(db: Session, user: User, execution_id: str) -> dict:
    if not has_permission(user, "action.rollback"):
        raise AppError("FORBIDDEN", "You cannot roll back actions.", 403)
    execution = db.get(ActionExecution, execution_id)
    if execution is None or execution.account_id != user.account_id:
        raise AppError("NOT_FOUND", "Execution was not found.", 404)
    if execution.action != "easidoc.assign_participant":
        raise AppError("ROLLBACK_REJECTED", "Only a participant assignment can be rolled back from here.", 409)
    workflow_id = (execution.request or {}).get("workflow_id")
    state = easidoc.unassign_participant(db, user, workflow_id)
    execution.status = "rolled_back"
    audit(db, user, "action.rollback", "action_execution", execution.id, {})
    return {"status": "rolled_back", "document": state}


def _run_step(db: Session, user: User, approval: HumanApproval, incident: Incident, step: dict) -> dict:
    action = step["action"]
    started = utcnow()
    record = ActionExecution(
        account_id=user.account_id,
        approval_id=approval.id,
        incident_id=incident.id,
        action=action,
        status="running",
        request={"step": step, "workflow_id": approval.payload.get("workflow_id"), "document_id": approval.payload.get("document_id")},
        started_at=started,
    )
    db.add(record)
    db.flush()
    try:
        result = _dispatch(db, user, approval, incident, step)
        record.status = "waiting" if result.get("waiting") else "succeeded" if result.get("ok") else "failed"
        record.result = result
        record.finished_at = utcnow()
        return result
    except AppError as exc:
        record.status = "failed"
        record.result = {"ok": False, "code": exc.code, "message": exc.message, "details": exc.details}
        record.finished_at = utcnow()
        return record.result


def _dispatch(db: Session, user: User, approval: HumanApproval, incident: Incident, step: dict) -> dict:
    action = step["action"]
    params = step.get("params") or {}
    payload = approval.payload
    if action == "easidoc.assign_participant":
        from app.models import WorkflowInstance

        workflow = db.get(WorkflowInstance, payload.get("workflow_id"))
        easidoc.assign_participant(
            db,
            user,
            workflow.id,
            params["participant_id"],
            workflow.row_version,
            approval,
        )
        return {"ok": True, "message": "Participant assigned. The review is not complete."}
    if action == "easidoc.await_event":
        from app.models import WorkflowInstance

        workflow = db.get(WorkflowInstance, payload.get("workflow_id"))
        if workflow and workflow.status == "completed":
            return {"ok": True, "waiting": False, "message": "The periodic review is complete."}
        return {"ok": True, "waiting": True, "message": "Waiting for the assigned participant to complete the review."}
    if action == "easidoc.update_metadata":
        from app.models import Document

        document = db.get(Document, payload.get("document_id"))
        values = {}
        for field in ("document_history", "planned_effective_date"):
            text = str(params.get(field) or "").strip()
            if text:
                values[field] = text
        easidoc.update_metadata(
            db,
            user,
            document.id,
            expected_version=document.row_version,
            values=values,
            approval=approval,
        )
        return {"ok": True, "message": "Confirmed metadata was saved."}
    if action == "easidoc.start_approval":
        from app.models import Document

        document = db.get(Document, payload.get("document_id"))
        db.refresh(document)
        easidoc.start_approval(db, user, document.id, document.row_version, approval)
        return {"ok": True, "message": "Approval workflow started."}
    if action == "verify_state":
        recovered, summary, checks = _verify(db, incident, params.get("scenario"))
        row = VerificationResult(incident_id=incident.id, approval_id=approval.id, recovered=recovered, summary=summary, checks=checks)
        db.add(row)
        db.flush()
        if recovered:
            queue_event(
                db,
                account_id=user.account_id,
                name="REMEDIATION_VERIFIED",
                source="ams",
                environment=incident.environment,
                incident_id=incident.id,
                document_id=incident.document_id,
                scenario_execution_id=incident.scenario_execution_id,
                payload={"verification_id": row.id, "recovered": True},
            )
        return {"ok": True, "recovered": recovered, "summary": summary, "checks": checks, "verification_id": row.id}
    if action == "draft_knowledge":
        proposal = db.get(RemediationProposal, approval.proposal_id)
        hypothesis = db.get(RootCauseHypothesis, proposal.hypothesis_id) if proposal and proposal.hypothesis_id else None
        verification = (
            db.query(VerificationResult).filter(VerificationResult.approval_id == approval.id).order_by(VerificationResult.created_at.desc()).first()
        )
        draft = knowledge.draft_from_outcome(db, user.account_id, incident, verification, hypothesis)
        return {"ok": True, "knowledge_id": None if draft is None else draft.id, "status": "draft" if draft else "not_created"}
    if action == "record_outcome":
        verification = (
            db.query(VerificationResult).filter(VerificationResult.incident_id == incident.id).order_by(VerificationResult.created_at.desc()).first()
        )
        if verification is None or not verification.recovered:
            incident.state = "remediation_failed"
            touch_incident(incident)
            return {"ok": True, "resolved": False, "message": "The incident was not resolved because verification did not pass."}
        incident.state = "resolved"
        incident.resolution_code = "verified_remediation"
        incident.close_notes = verification.summary
        incident.resolved_at = utcnow()
        incident.servicenow_sync_status = incident.servicenow_sync_status or "not_linked"
        touch_incident(incident)
        if incident.scenario_execution_id:
            execution = db.get(ScenarioExecution, incident.scenario_execution_id)
            if execution:
                execution.status = "recovered"
        queue_event(
            db,
            account_id=user.account_id,
            name="INCIDENT_RESOLVED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            scenario_execution_id=incident.scenario_execution_id,
            payload={"number": incident.number, "resolution_code": incident.resolution_code, "closed": False},
        )
        return {"ok": True, "resolved": True, "message": "The local incident is resolved. It was not closed."}
    if action == "servicenow.add_work_note":
        verification = (
            db.query(VerificationResult).filter(VerificationResult.incident_id == incident.id).order_by(VerificationResult.created_at.desc()).first()
        )
        summary = verification.summary if verification else "No verification record was produced."
        note = (
            f"AMS approved remediation for {incident.number}. Verification recovered="
            f"{bool(verification and verification.recovered)}. {summary} "
            "This note was written by the action executor from stored evidence."
        )
        result = servicenow.post_work_note(db, user, incident, note)
        if result.get("status") == "confirmed":
            queue_event(
                db,
                account_id=user.account_id,
                name="SERVICENOW_UPDATE_CONFIRMED",
                source="servicenow",
                environment="production",
                incident_id=incident.id,
                payload={"sys_id": incident.external_sys_id},
            )
        return {"ok": True, **result}
    raise AppError("ACTION_REJECTED", f"{action} is not allowlisted.", 403)


def _verify(db: Session, incident: Incident, scenario: str | None) -> tuple[bool, str, list]:
    from app.models import Document

    if not incident.document_id:
        return False, "No application document is linked, so recovery cannot be verified.", [{"name": "document", "ok": False}]
    document = db.get(Document, incident.document_id)
    snap = easidoc.snapshot(db, document)
    checks = []
    if scenario == "missing_participant":
        review = snap.get("review") or {}
        assigned = bool(review.get("assigned_participant_id")) or any(
            task.get("assigned_participant_id") for task in review.get("tasks") or []
        )
        checks.append({"name": "participant_assigned", "ok": assigned})
        checks.append({"name": "review_completed", "ok": review.get("status") == "completed"})
        checks.append({"name": "approval_started", "ok": bool(snap.get("approval_started"))})
    elif scenario == "missing_metadata":
        checks.append({"name": "metadata_complete", "ok": not snap.get("missing_fields"), "missing_fields": snap.get("missing_fields")})
        checks.append({"name": "approval_started", "ok": bool(snap.get("approval_started"))})
    else:
        checks.append({"name": "rule", "ok": False, "detail": "No verification rule is defined for this incident."})
    recovered = bool(checks) and all(item["ok"] for item in checks)
    if recovered:
        summary = f"{snap.get('number')} recovered. The approval workflow is in progress and the blocking gate is clear."
    else:
        failed = [item["name"] for item in checks if not item["ok"]]
        summary = f"{snap.get('number')} is not recovered. Failed checks: {', '.join(failed)}."
    return recovered, summary, checks


def _assert_current_versions(db: Session, payload: dict) -> None:
    from app.models import Document, WorkflowInstance

    if payload.get("document_id") and payload.get("document_version") is not None:
        document = db.get(Document, payload["document_id"])
        if document and document.row_version != payload["document_version"]:
            raise AppError("STALE_RECORD", "The document changed after approval. Investigate again before executing.", 409)
    if payload.get("workflow_id") and payload.get("workflow_version") is not None:
        workflow = db.get(WorkflowInstance, payload["workflow_id"])
        if workflow and workflow.row_version != payload["workflow_version"]:
            raise AppError("STALE_RECORD", "The workflow changed after approval. Investigate again before executing.", 409)


def _assert_participant(db: Session, user: User, payload: dict) -> None:
    for step in payload.get("steps") or []:
        if step.get("action") != "easidoc.assign_participant":
            continue
        participant_id = (step.get("params") or {}).get("participant_id")
        if participant_id and step.get("resource_id"):
            easidoc.assert_participant_eligible(db, user, step["resource_id"], participant_id)


def _approval_brief(approval: HumanApproval) -> dict:
    return {
        "id": approval.id,
        "status": approval.status,
        "decision": approval.decision,
        "comment": approval.comment,
        "payload_hash": approval.payload_hash,
        "payload": approval.payload,
        "progress": approval.progress or {},
    }
