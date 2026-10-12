"""Agent investigation. LangGraph sequences the same node functions when it is installed."""

from __future__ import annotations

import json
import re
from typing import TypedDict

from sqlalchemy.orm import Session

from app.connectors.observability import build_connector
from app.events import queue_event
from app.models import (
    AgentRun,
    AgentStep,
    HumanApproval,
    Incident,
    IncidentEvidence,
    Participant,
    RemediationProposal,
    RootCauseHypothesis,
    User,
    utcnow,
)
from app.policy import canonical_hash
from app.services import easidoc
from app.services.diagnosis import analyze
from app.services.gateway import draft_recommendation, narrate
from app.util import iso


class InvestigationState(TypedDict, total=False):
    halt: bool
    error: str
    classification: dict
    enrichment: dict
    log_summary: dict
    knowledge: list
    analysis: dict
    proposal_payload: dict
    proposal_id: str
    approval_id: str
    model_narrative: dict


def investigate(db: Session, user: User, incident_id: str) -> dict:
    incident = db.get(Incident, incident_id)
    if incident is None or incident.account_id != user.account_id:
        from app.errors import AppError

        raise AppError("NOT_FOUND", "Incident was not found.", 404)
    original_state = incident.state
    existing_rows = (
        db.query(HumanApproval)
        .filter(HumanApproval.incident_id == incident.id, HumanApproval.account_id == user.account_id)
        .order_by(HumanApproval.created_at.desc())
        .all()
    )
    for approval in existing_rows:
        if approval.status in {"pending", "needs_investigation"}:
            return {"status": "existing", "incident_id": incident.id, "approval_id": approval.id, "run_id": None}
        if approval.status == "approved" and (approval.progress or {}).get("state") not in {"completed", "failed", "rolled_back"}:
            return {"status": "already_in_progress", "incident_id": incident.id, "approval_id": approval.id, "run_id": None}
    run = AgentRun(incident_id=incident.id, account_id=user.account_id, status="running", orchestrator=_orchestrator_name())
    db.add(run)
    db.flush()
    incident.state = "investigating"
    incident.updated_at = utcnow()
    queue_event(
        db,
        account_id=user.account_id,
        name="INVESTIGATION_STARTED",
        source="ams",
        environment=incident.environment,
        incident_id=incident.id,
        application_id=incident.application_id,
        document_id=incident.document_id,
        scenario_execution_id=incident.scenario_execution_id,
        payload={"run_id": run.id, "orchestrator": run.orchestrator},
    )

    def record(agent: str, output: dict, status: str) -> None:
        db.add(
            AgentStep(
                run_id=run.id,
                agent=agent,
                status=status,
                output={key: value for key, value in output.items() if key != "halt"},
                finished_at=utcnow(),
            )
        )
        db.flush()

    def wrap(agent: str, fn):
        def inner(state: InvestigationState) -> dict:
            if state.get("halt"):
                record(agent, {"skipped": True}, "skipped")
                return {}
            try:
                output = fn(state)
                record(agent, output, "completed")
                return output
            except Exception as exc:
                from app.errors import AppError

                if isinstance(exc, AppError):
                    raise
                output = {"halt": True, "error": f"{agent} failed: {exc.__class__.__name__}"}
                record(agent, output, "failed")
                return output

        return inner

    def intake(_state: InvestigationState) -> dict:
        text = f"{incident.short_description}\n{incident.description}".lower()
        if "review_participant_not_found" in text:
            issue = "missing_review_participant"
        elif "required_metadata_missing" in text:
            issue = "missing_metadata"
        elif "approval" in text:
            issue = "approval_blocked"
        else:
            issue = "unclassified"
        classification = {
            "issue_type": issue,
            "priority": incident.priority,
            "source": incident.source,
            "source_note": "Incident text is a hint only. Later agents use live application state.",
        }
        return {"classification": classification}

    def enrich(_state: InvestigationState) -> dict:
        enrichment = {"application": incident.business_service, "cmdb_ci": incident.cmdb_ci, "document": None}
        if incident.document_id:
            from app.models import Document

            document = db.get(Document, incident.document_id)
            if document and document.account_id == user.account_id:
                enrichment["document"] = easidoc.snapshot(db, document)
                _evidence(db, incident, run, "application_state", "easidoc_simulator", f"State of {document.number}", easidoc.snapshot(db, document)["status"], document.id)
        return {"enrichment": enrichment}

    def logs(state: InvestigationState) -> dict:
        if incident.source == "servicenow":
            summary = _servicenow_logs(db, user, incident)
            for row in (summary.get("lines") or [])[:5]:
                _evidence(db, incident, run, "log", "servicenow", row.get("signature") or "syslog", row.get("message") or "", row.get("timestamp") or "")
            if summary.get("steps"):
                _evidence(
                    db,
                    incident,
                    run,
                    "activity",
                    "servicenow",
                    "Work note",
                    "\n".join(summary["steps"])[:800],
                    incident.external_number or incident.number,
                )
        else:
            summary = _easidoc_logs(db, user, incident)
            for row in summary.get("logs") or []:
                if row.get("level") in {"ERROR", "WARN"}:
                    _evidence(db, incident, run, "log", "easidoc_simulator", row.get("signature") or "log", row["message"][:400], row.get("correlation_id") or "")
        excerpts = list(summary.get("excerpts") or [])
        compact = {
            "status": summary.get("status"),
            "environment_label": summary.get("environment_label"),
            "signatures": summary.get("signatures") or [],
            "correlation_ids": summary.get("correlation_ids") or [],
            "frequency": summary.get("frequency") or [],
            "baseline": summary.get("baseline") or {},
            "groups": summary.get("groups") or [],
            "excerpts": excerpts,
            "truncated": summary.get("truncated", False),
            "log_count": summary.get("log_count", len(summary.get("logs") or [])),
            "searched": summary.get("searched") or [],
            "steps": summary.get("steps") or [],
            "activity": summary.get("activity") or [],
            "lines": summary.get("lines") or summary.get("logs") or [],
            "message": summary.get("message") or "",
            "connector": summary.get("connector") or "",
        }
        queue_event(
            db,
            account_id=user.account_id,
            name="LOG_ANALYSIS_COMPLETED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            scenario_execution_id=incident.scenario_execution_id,
            payload={"signatures": compact["signatures"], "log_count": compact["log_count"]},
        )
        return {"log_summary": compact}

    def knowledge(state: InvestigationState) -> dict:
        from app.services import knowledge as knowledge_service

        signatures = (state.get("log_summary") or {}).get("signatures") or []
        query = " ".join([incident.short_description, *signatures])[:500]
        hits = knowledge_service.search(
            db,
            user,
            query,
            application_code="easidoc" if incident.source == "easidoc_simulator" else None,
            limit=8,
        )
        hits = _relevant_knowledge(incident, hits)[:4]
        for hit in hits:
            _evidence(db, incident, run, "knowledge", hit["source"], hit["title"], hit["excerpt"], hit["id"], {"trusted": hit["trusted"], "status": hit["status"]})
        queue_event(
            db,
            account_id=user.account_id,
            name="KNOWLEDGE_RETRIEVED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            payload={"count": len(hits), "titles": [hit["title"] for hit in hits]},
        )
        return {"knowledge": hits}

    def rca(state: InvestigationState) -> dict:
        snapshot = (state.get("enrichment") or {}).get("document") or {"number": incident.number, "document_id": None}
        signatures = (state.get("log_summary") or {}).get("signatures") or []
        knowledge_hits = state.get("knowledge") or []
        analysis = analyze(
            snapshot,
            signatures,
            {
                "close_notes": incident.close_notes,
                "description": incident.description,
                "knowledge": [hit.get("title") for hit in knowledge_hits],
            },
        )
        for hypothesis in analysis["hypotheses"]:
            db.add(
                RootCauseHypothesis(
                    incident_id=incident.id,
                    run_id=run.id,
                    statement=hypothesis["statement"],
                    status=hypothesis["status"],
                    confidence=hypothesis["confidence"],
                    supporting=hypothesis["supporting"],
                    contradicting=hypothesis["contradicting"],
                    assumptions=hypothesis["assumptions"],
                    further_checks=hypothesis["further_checks"],
                )
            )
        db.flush()
        brief = analysis["summary"] + " " + " ".join(item["statement"] for item in analysis["hypotheses"])
        narrative = narrate(brief)
        queue_event(
            db,
            account_id=user.account_id,
            name="ROOT_CAUSE_HYPOTHESIS_CREATED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            payload={"case": analysis["case"], "confirmed": analysis["confirmed"]},
        )
        return {"analysis": analysis, "model_narrative": narrative}

    def recommend(state: InvestigationState) -> dict:
        analysis = state.get("analysis") or {}
        snapshot = (state.get("enrichment") or {}).get("document")
        log_summary = state.get("log_summary") or {}
        payload = _proposal_payload(db, user, incident, analysis, snapshot, state.get("knowledge") or [], original_state, log_summary)
        payload = _apply_openai_draft(payload, incident, analysis, state.get("knowledge") or [], log_summary)
        queue_event(
            db,
            account_id=user.account_id,
            name="REMEDIATION_RECOMMENDED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            payload={"available": bool(payload.get("steps")), "case": analysis.get("case")},
        )
        return {"proposal_payload": payload}

    def approval(state: InvestigationState) -> dict:
        payload = state.get("proposal_payload") or {}
        hypothesis = (
            db.query(RootCauseHypothesis)
            .filter(RootCauseHypothesis.run_id == run.id, RootCauseHypothesis.status.in_(["confirmed", "supported"]))
            .order_by(RootCauseHypothesis.created_at.asc())
            .first()
        )
        disposition = payload.get("disposition") or ("needs_approval" if payload.get("steps") else "insufficient")
        proposal = RemediationProposal(
            incident_id=incident.id,
            run_id=run.id,
            hypothesis_id=None if hypothesis is None else hypothesis.id,
            summary=payload.get("summary") or "No recommendation was produced.",
            payload=payload,
            payload_hash=canonical_hash(payload),
            risk=payload.get("risk", "medium"),
            status=disposition,
        )
        db.add(proposal)
        db.flush()
        if not payload.get("steps"):
            incident.state = original_state if incident.source == "servicenow" else "awaiting_information"
            return {"approval_id": "", "proposal_id": proposal.id, "disposition": disposition}
        for pending in db.query(HumanApproval).filter_by(incident_id=incident.id, status="pending").all():
            pending.status = "superseded"
        row = HumanApproval(
            account_id=user.account_id,
            proposal_id=proposal.id,
            incident_id=incident.id,
            status="pending",
            payload=payload,
            payload_hash=proposal.payload_hash,
            progress={"state": "pending", "completed_steps": []},
            requested_by=user.id,
        )
        db.add(row)
        db.flush()
        incident.state = original_state if incident.source == "servicenow" else "awaiting_approval"
        queue_event(
            db,
            account_id=user.account_id,
            name="HUMAN_APPROVAL_REQUESTED",
            source="ams",
            environment=incident.environment,
            incident_id=incident.id,
            document_id=incident.document_id,
            scenario_execution_id=incident.scenario_execution_id,
            payload={"approval_id": row.id, "proposal_id": proposal.id},
        )
        return {"approval_id": row.id, "proposal_id": proposal.id}

    nodes = [
        ("intake", wrap("intake", intake)),
        ("enrichment", wrap("enrichment", enrich)),
        ("logs", wrap("logs", logs)),
        ("knowledge", wrap("knowledge", knowledge)),
        ("rca", wrap("rca", rca)),
        ("recommendation", wrap("recommendation", recommend)),
        ("approval", wrap("approval", approval)),
    ]
    final = _run_graph(nodes, {})
    run.status = "failed" if final.get("halt") else "completed"
    run.error = final.get("error") or ""
    run.finished_at = utcnow()
    run.checkpoint = {
        "case": (final.get("analysis") or {}).get("case"),
        "approval_id": final.get("approval_id"),
        "model_narrative": final.get("model_narrative"),
        "orchestrator": run.orchestrator,
    }
    db.flush()
    return {"status": run.status, "run_id": run.id, "incident_id": incident.id, "approval_id": final.get("approval_id") or None, "orchestrator": run.orchestrator}


def _apply_openai_draft(payload: dict, incident: Incident, analysis: dict, knowledge: list, log_summary: dict | None = None) -> dict:
    import re

    logs = log_summary or {}
    recorded_steps = list(payload.get("guidance") or []) if logs.get("steps") else []
    facts = {
        "incident_number": incident.number,
        "short_description": incident.short_description,
        "description": (incident.description or "")[:1500],
        "close_notes": (incident.close_notes or "")[:800],
        "disposition": payload.get("disposition"),
        "deterministic_summary": payload.get("summary"),
        "deterministic_guidance": payload.get("guidance") or [],
        "deterministic_verification": payload.get("verification_plan") or [],
        "knowledge_titles": [str(hit.get("title") or "") for hit in knowledge if hit.get("title")][:4],
        "hypothesis": (analysis or {}).get("summary") or "",
        "syslog_lines": [line.get("message") for line in (logs.get("lines") or [])[:5]],
        "syslog_searched": logs.get("searched") or [],
        "work_note_steps": logs.get("steps") or [],
    }
    draft = draft_recommendation(facts)
    payload["recommendation_version"] = 5
    payload["model"] = {
        "used": bool(draft.get("used")),
        "provider": draft.get("provider"),
        "model": draft.get("model"),
        "message": draft.get("message") or "",
    }
    if not draft.get("used"):
        return payload
    allowed = {item.upper() for item in re.findall(r"INC\d+", json.dumps(facts), flags=re.I)}

    def allowed_text(text: str) -> bool:
        cited = {item.upper() for item in re.findall(r"INC\d+", text, flags=re.I)}
        return cited <= allowed

    if recorded_steps:
        payload["guidance"] = recorded_steps
        label = "Bedrock" if draft.get("provider") == "bedrock" else "The model"
        payload["model"]["message"] = f"{label} reviewed the draft. The steps stay the work note, because that note is the evidence."
        return payload
    summary = str(draft.get("summary") or "").strip()
    if summary and allowed_text(summary):
        payload["summary"] = summary[:800]
    guidance = [line[:300] for line in draft.get("guidance") or [] if allowed_text(str(line))]
    if guidance:
        payload["guidance"] = guidance[:6]
    outcome_raw = draft.get("expected_outcome") or ""
    outcome = " ".join(str(item).strip() for item in outcome_raw if str(item).strip()) if isinstance(outcome_raw, list) else str(outcome_raw).strip()
    if outcome and allowed_text(outcome):
        payload["expected_outcome"] = outcome[:400]
    checks = [str(line)[:300] for line in draft.get("verification_plan") or [] if allowed_text(str(line))]
    if checks:
        payload["verification_plan"] = checks[:4]
    return payload


def _relevant_knowledge(incident: Incident, hits: list[dict]) -> list[dict]:
    from app.util import tokenize

    generic = {"create", "created", "issue", "issues", "unable", "error", "failed", "failure", "prod", "user", "users", "new", "application", "system", "team"}
    focus = set(tokenize(f"{incident.short_description} {incident.description or ''}")) - generic
    if not focus:
        return hits[:4]
    chosen = []
    for hit in hits:
        title_tokens = set(tokenize(hit.get("title") or ""))
        excerpt_tokens = set(tokenize(hit.get("excerpt") or ""))
        if focus & title_tokens or len(focus & excerpt_tokens) >= 2:
            chosen.append(hit)
    return chosen


def _proposal_payload(db: Session, user: User, incident: Incident, analysis: dict, snapshot: dict | None, knowledge: list | None = None, original_state: str = "", log_summary: dict | None = None) -> dict:
    case = (analysis or {}).get("case")
    rejected = [
        {
            "action": "easidoc.force_start_approval",
            "status": "rejected_by_policy",
            "reason": "Workflow prerequisites cannot be bypassed, even if an operator or model asks for it.",
        }
    ]
    base = {
        "application": "easidoc" if incident.source == "easidoc_simulator" else incident.business_service,
        "environment": incident.environment,
        "document_id": None if snapshot is None else snapshot.get("document_id"),
        "document_version": None if snapshot is None else snapshot.get("row_version"),
        "workflow_id": None if not snapshot or not snapshot.get("review") else snapshot["review"]["id"],
        "workflow_version": None if not snapshot or not snapshot.get("review") else snapshot["review"]["row_version"],
        "incident_id": incident.id,
        "risk": "low",
        "rejected_options": rejected,
        "steps": [],
        "closes_incident": False,
        "disposition": "insufficient",
        "guidance": [],
        "resolution_notes": "",
        "recommendation_version": 5,
    }
    if snapshot is None or case in {None, "unknown"}:
        return _operational_recommendation(base, incident, analysis, knowledge or [], original_state, db, log_summary)
    if case == "review_incomplete":
        base["disposition"] = "advisory"
        base["summary"] = (analysis or {}).get("summary") or "The assigned participant still has to complete the review."
        base["guidance"] = [
            "The assigned participant must complete the periodic review.",
            "Do not start approval before that person finishes the review.",
        ]
        base["expected_outcome"] = "Approval can start only after the assigned person completes the review."
        base["verification_plan"] = ["The review workflow is completed by the assigned participant."]
        base["prerequisites"] = []
        base["rollback"] = []
        return base
    if case == "healthy":
        base["disposition"] = "advisory"
        base["summary"] = (analysis or {}).get("summary") or "No blocked approval gate is present."
        base["guidance"] = ["No application change is recommended."]
        base["expected_outcome"] = "No application change."
        base["verification_plan"] = ["Confirm the workflow is not blocked before closing the incident."]
        base["prerequisites"] = []
        base["rollback"] = []
        return base
    if case == "missing_participant":
        review = snapshot.get("review") or {}
        chosen = _suggest_participant(db, user.account_id, review.get("required_function_id"))
        if chosen is None:
            base["summary"] = "No eligible Quality Assurance participant is available to assign."
            base["expected_outcome"] = "No assignment is attempted."
            base["prerequisites"] = ["Add an active participant in the Quality Assurance responsible function."]
            base["verification_plan"] = []
            base["rollback"] = []
            return base
        base["summary"] = f"Assign {chosen.name} to the Auto Periodic Review task, wait for that person to complete the review, then start approval."
        base["expected_outcome"] = "The participant is assigned, the review is completed by that person, and the draft approval workflow starts."
        base["prerequisites"] = [
            "The selected person belongs to the Quality Assurance responsible function.",
            "A resolver approves this exact participant.",
            "The assigned participant personally completes the review.",
        ]
        base["verification_plan"] = [
            "Confirm the task shows the approved participant.",
            "Confirm the review workflow is completed only after that participant acts.",
            "Confirm the approval workflow is in progress.",
        ]
        base["disposition"] = "needs_approval"
        base["rollback"] = ["If the review has not been completed, remove the participant assignment."]
        base["guidance"] = [
            f"Assign {chosen.name} to the Auto Periodic Review task.",
            "Wait until that person completes the review.",
            "Start approval only after the review is complete.",
        ]
        base["steps"] = [
            _step("assign", "easidoc.assign_participant", "workflow", review.get("id"), incident.environment, {"participant_id": chosen.id}),
            _step("await_review", "easidoc.await_event", "workflow", review.get("id"), incident.environment, {"event": "PERIODIC_REVIEW_COMPLETED"}),
            _step("start_approval", "easidoc.start_approval", "document", snapshot.get("document_id"), incident.environment, {}),
            _step("verify", "verify_state", "incident", incident.id, incident.environment, {"scenario": "missing_participant"}),
            _step("learn", "draft_knowledge", "incident", incident.id, incident.environment, {}),
            _step("record", "record_outcome", "incident", incident.id, incident.environment, {}),
            _step("servicenow", "servicenow.add_work_note", "incident", incident.id, incident.environment, {}),
        ]
        return base
    if case in {"missing_metadata", "multiple"}:
        missing = list(snapshot.get("missing_fields") or [])
        params = {"missing_fields": missing, "document_history": "", "planned_effective_date": ""}
        base["summary"] = "A person must confirm the missing metadata. The agent will not invent Document History or Planned Effective Date."
        base["expected_outcome"] = "The confirmed values are saved, validation passes, and the approval workflow starts."
        base["prerequisites"] = ["A person types the real value for every missing field."]
        base["verification_plan"] = ["Confirm the missing fields are populated.", "Confirm validation passed.", "Confirm the approval workflow is in progress."]
        base["rollback"] = ["Metadata history keeps the prior values. Correct the fields again if the confirmed values were wrong."]
        base["risk"] = "medium"
        base["disposition"] = "needs_approval"
        base["guidance"] = [
            "Ask the document owner for the real value of every missing field.",
            "Do not invent Document History or Planned Effective Date.",
            "Start approval only after those values are confirmed.",
        ]
        base["steps"] = [
            _step("metadata", "easidoc.update_metadata", "document", snapshot.get("document_id"), incident.environment, params),
            _step("start_approval", "easidoc.start_approval", "document", snapshot.get("document_id"), incident.environment, {}),
            _step("verify", "verify_state", "incident", incident.id, incident.environment, {"scenario": "missing_metadata"}),
            _step("learn", "draft_knowledge", "incident", incident.id, incident.environment, {}),
            _step("record", "record_outcome", "incident", incident.id, incident.environment, {}),
            _step("servicenow", "servicenow.add_work_note", "incident", incident.id, incident.environment, {}),
        ]
        return base
    base["summary"] = analysis.get("summary") or "No safe remediation is available."
    base["expected_outcome"] = "No application change."
    base["prerequisites"] = []
    base["verification_plan"] = ["Do not close the incident from this investigation alone."]
    base["rollback"] = []
    base["guidance"] = ["Collect the missing application evidence before recommending a close."]
    return base


def _operational_recommendation(base: dict, incident: Incident, analysis: dict, knowledge: list, original_state: str, db: Session | None = None, log_summary: dict | None = None) -> dict:
    from app.util import tokenize

    logs = log_summary or {}
    base["logs"] = _public_logs(logs)
    notes = (incident.close_notes or "").strip()
    generic = {"create", "created", "issue", "issues", "unable", "error", "failed", "failure", "prod", "user", "users", "new", "application", "system", "team", "resolve", "resolved", "solution", "solutions"}
    focus = set(tokenize(incident.short_description)) - generic
    articles = []
    for hit in knowledge:
        title = str(hit.get("title") or "")
        if focus and focus & set(tokenize(title)):
            articles.append(title)
    articles = articles[:3]
    described = _described_actions(incident)
    similar = _similar_resolution(db, incident) if db is not None else None
    already_closed = original_state in {"resolved", "closed"}
    guidance = described + _resolution_checks(incident)
    if similar:
        number, prior = similar
        guidance = [f"Closed incident {number} was resolved by: {prior}"] + guidance
    if articles:
        guidance = [f"Compare the failure with {title}." for title in articles] + guidance
    base["resolution_notes"] = notes[:800]
    base["closes_incident"] = False
    base["prerequisites"] = []
    base["rollback"] = []
    if logs.get("steps") and not (already_closed and notes):
        base["disposition"] = "advisory"
        base["summary"] = logs.get("message") or f"Follow the work note on {incident.number} before anyone closes it."
        base["guidance"] = list(logs["steps"])
        base["expected_outcome"] = "The incident stays open until a person confirms the recorded checks and closes it in ServiceNow."
        base["verification_plan"] = [
            "The services named in the work note have been checked.",
            "The user confirms the failure is gone.",
            "A person writes the close in ServiceNow.",
        ]
        return _attach_servicenow_note(base, incident)
    if logs.get("lines"):
        quoted = [
            f"Read the ServiceNow syslog line from {line.get('timestamp') or 'the recorded time'}: {str(line.get('message') or '')[:220]}"
            for line in logs["lines"][:3]
        ]
        guidance = quoted + guidance
    if already_closed and notes:
        base["disposition"] = "advisory"
        base["summary"] = f"Do not close {incident.number} again. Confirm the recorded ServiceNow resolution still holds, then leave the incident {original_state}."
        base["guidance"] = ["Read the recorded resolution below.", "Ask the user whether the failure is still present.", *guidance[:2]]
        base["expected_outcome"] = "No new close is sent to ServiceNow."
        base["verification_plan"] = ["The user confirms the failure is gone.", "The recorded resolution matches the work that was done."]
        return _attach_servicenow_note(base, incident)
    if notes or articles or described:
        base["disposition"] = "advisory"
        base["summary"] = f"Do not close {incident.number} yet. Confirm these checks with the user. A person may resolve it only after that confirmation."
        base["expected_outcome"] = "The incident stays open until a person closes it. This platform does not write the close."
        base["verification_plan"] = [
            "The cited checks match this failure.",
            "The user confirms the process works after the check.",
            "A person writes the close notes in ServiceNow.",
        ]
        base["guidance"] = guidance
        return _attach_servicenow_note(base, incident)
    base["disposition"] = "insufficient"
    base["summary"] = f"There is not enough evidence to recommend closing {incident.number}."
    base["guidance"] = guidance
    base["expected_outcome"] = "No close recommendation until the checks below are answered."
    base["verification_plan"] = ["Do not mark the incident resolved from this investigation alone."]
    if analysis.get("summary"):
        base["guidance"] = guidance
    return _attach_servicenow_note(base, incident)


def _attach_servicenow_note(base: dict, incident: Incident) -> dict:
    """Ask a person to approve posting the recommendation as a ServiceNow work note."""
    if incident.source != "servicenow" or not incident.external_sys_id:
        return base
    lines = [str(base.get("summary") or "").strip()]
    lines.extend(str(item).strip() for item in (base.get("guidance") or []) if str(item).strip())
    body = " ".join(line for line in lines if line)[:3200]
    if not body:
        return base
    number = incident.external_number or incident.number
    note = f"AMS recommendation for {number}. {body} A person approved this note. The incident was not closed in ServiceNow."
    base["disposition"] = "needs_approval"
    base["expected_outcome"] = f"After approval, this text is posted as a work note on {number}. ServiceNow state and close notes stay unchanged."
    base["steps"] = [
        _step("servicenow", "servicenow.add_work_note", "incident", incident.id, incident.environment, {"note": note[:3900]}),
    ]
    return base


def _described_actions(incident: Incident) -> list[str]:
    lines = [line.strip(" -\t") for line in (incident.description or "").splitlines() if line.strip()]
    chosen = []
    for line in lines:
        lowered = line.lower()
        if "similar incident" in lowered or re.search(r"\binc\d+\b", lowered):
            continue
        if any(word in lowered for word in ("idoc", "manually", "restart", "reboot", "unlock")):
            chosen.append(line[:300])
    return chosen[:2]


def _similar_resolution(db: Session, incident: Incident) -> tuple[str, str] | None:
    from app.util import tokenize

    ignored = {
        "create", "created", "issue", "issues", "unable", "error", "failed", "failure", "prod", "user", "users",
        "new", "application", "system", "team", "resolve", "resolved", "solution", "solutions", "mes", "pharmasuite",
        "windows", "outlook", "server", "site", "remote", "europe", "cloud",
    }
    focus = set(tokenize(incident.short_description)) - ignored
    if len(focus) < 2:
        return None
    rows = (
        db.query(Incident)
        .filter(
            Incident.account_id == incident.account_id,
            Incident.id != incident.id,
            Incident.source == "servicenow",
            Incident.state.in_(["resolved", "closed"]),
            Incident.close_notes != "",
        )
        .all()
    )
    best: Incident | None = None
    best_score = 0
    for row in rows:
        overlap = focus & (set(tokenize(f"{row.short_description} {row.close_notes}")) - ignored)
        if len(overlap) < 2:
            continue
        if len(overlap) > best_score:
            best = row
            best_score = len(overlap)
    if best is None:
        return None
    notes = " ".join((best.close_notes or "").split())
    return best.external_number or best.number, notes[:280]


def _resolution_checks(incident: Incident) -> list[str]:
    text = f"{incident.short_description} {incident.description or ''}".lower()
    checks = []
    if "process order" in text or " idoc" in f" {text}" or "mes" in text or "pharmasuite" in text:
        checks.append("Confirm whether the process order exists in the source system and whether MES received it.")
        checks.append("If an IDoc or interface message failed, record its status and the time it failed.")
    if any(word in text for word in ("login", "password", "access", "locked")):
        checks.append("Confirm the account state and whether the reporting user can sign in again.")
    if any(word in text for word in ("reboot", "cpu", "memory", "disk", "server")):
        checks.append("Confirm the server or resource alert has cleared and the service is responding.")
    if not checks:
        checks.append("Capture the exact error, the time it occurred, and what has already been tried.")
    checks.append("Ask the user to confirm the failure is gone before anyone closes the incident.")
    return checks


def _step(step_id: str, action: str, resource_type: str, resource_id: str | None, environment: str, params: dict) -> dict:
    return {
        "id": step_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "environment": environment,
        "params": params,
    }


def _suggest_participant(db: Session, account_id: str, function_id: str | None) -> Participant | None:
    if not function_id:
        return None
    rows = (
        db.query(Participant)
        .filter(Participant.account_id == account_id, Participant.function_id == function_id, Participant.active.is_(True))
        .order_by(Participant.name.asc())
        .all()
    )
    return next((row for row in rows if row.user_id), rows[0] if rows else None)


def _servicenow_logs(db: Session, user: User, incident: Incident) -> dict:
    from app.services.servicenow import collect_incident_logs, resolve_client

    try:
        client, _meta, _row = resolve_client(db, user)
        return collect_incident_logs(client, incident)
    except Exception:
        return {
            "status": "error",
            "connector": "servicenow",
            "environment_label": "ServiceNow",
            "message": "ServiceNow logs could not be read for this incident.",
            "logs": [],
            "lines": [],
            "steps": [],
            "activity": [],
            "searched": [],
            "signatures": [],
            "excerpts": [],
            "log_count": 0,
        }


def _easidoc_logs(db: Session, user: User, incident: Incident) -> dict:
    connector = build_connector("easidoc", db, user.account_id)
    summary = connector.query_logs(
        {
            "incident_id": incident.id,
            "application_id": incident.application_id,
            "opened_at": iso(incident.opened_at),
            "limit": 200,
        }
    )
    excerpts = []
    for row in summary.get("logs") or []:
        if row.get("level") in {"ERROR", "WARN"} and len(excerpts) < 5:
            excerpts.append(row["message"][:400])
    summary["excerpts"] = excerpts
    summary["lines"] = [
        {
            "timestamp": row.get("timestamp") or "",
            "level": row.get("level") or "",
            "source": row.get("source") or "easidoc_simulator",
            "message": (row.get("message") or "")[:500],
            "signature": row.get("signature") or "",
        }
        for row in (summary.get("logs") or [])
        if row.get("level") in {"ERROR", "WARN"}
    ][:5]
    summary["steps"] = []
    summary["activity"] = []
    summary["searched"] = []
    if summary["lines"]:
        summary["message"] = "These simulator log lines were used for the recommendation."
    else:
        summary["message"] = "No simulator log line is stored for this incident."
    return summary


def _public_logs(logs: dict) -> dict:
    lines = []
    for line in (logs.get("lines") or [])[:5]:
        lines.append(
            {
                "timestamp": str(line.get("timestamp") or ""),
                "level": str(line.get("level") or ""),
                "source": str(line.get("source") or ""),
                "message": str(line.get("message") or "")[:500],
                "signature": str(line.get("signature") or ""),
            }
        )
    activity = []
    for row in (logs.get("activity") or [])[:4]:
        activity.append({"at": str(row.get("at") or ""), "kind": str(row.get("kind") or ""), "text": str(row.get("text") or "")[:2000]})
    return {
        "source": logs.get("connector") or "",
        "searched": list(logs.get("searched") or []),
        "matched": bool(lines),
        "message": str(logs.get("message") or ""),
        "lines": lines,
        "activity": activity,
    }


def _evidence(db, incident, run, kind, source, title, excerpt, reference, payload=None) -> None:
    db.add(
        IncidentEvidence(
            incident_id=incident.id,
            run_id=run.id,
            kind=kind,
            source=source,
            title=title[:300],
            excerpt=(excerpt or "")[:800],
            reference=reference or "",
            payload=payload or {},
        )
    )


def _orchestrator_name() -> str:
    try:
        import langgraph  # noqa: F401

        return "langgraph"
    except Exception:
        return "builtin"


def _run_graph(nodes: list[tuple[str, object]], state: dict) -> dict:
    if _orchestrator_name() == "langgraph":
        return _run_langgraph(nodes, state)
    return _run_builtin(nodes, state)


def _run_langgraph(nodes, state: dict) -> dict:
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(InvestigationState)
    previous = None
    for name, fn in nodes:
        graph.add_node(name, fn)
        if previous is None:
            graph.add_edge(START, name)
        else:
            graph.add_edge(previous, name)
        previous = name
    if previous:
        graph.add_edge(previous, END)
    return graph.compile().invoke(state)


def _run_builtin(nodes, state: dict) -> dict:
    current = dict(state)
    for _name, fn in nodes:
        if current.get("halt"):
            break
        update = fn(current) or {}
        current.update(update)
    return current
