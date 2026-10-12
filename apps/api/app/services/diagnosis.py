"""Deterministic diagnosis shared by investigation and conversation."""

from __future__ import annotations


def analyze(snapshot: dict, signatures: list[str] | None = None, context: dict | None = None) -> dict:
    signatures = signatures or []
    context = context or {}
    signature_set = set(signatures)
    number = snapshot.get("number") or "the document"
    missing = list(snapshot.get("missing_fields") or [])
    review = snapshot.get("review")
    assigned = bool(
        review
        and (
            review.get("assigned_participant_id")
            or any(task.get("assigned_participant_id") for task in review.get("tasks") or [])
        )
    )
    review_open = bool(review and review.get("status") != "completed")
    participant_live = bool(review_open and not assigned)
    participant_signal = "REVIEW_PARTICIPANT_NOT_FOUND" in signature_set or (
        review and review.get("block_code") == "REVIEW_PARTICIPANT_NOT_FOUND"
    )
    metadata_signal = "REQUIRED_METADATA_MISSING" in signature_set or bool(missing)
    hypotheses: list[dict] = []

    if participant_live or participant_signal:
        supporting = []
        contradicting = []
        if participant_live:
            supporting.append(f"{number} has an open Auto Periodic Review task and no participant is assigned.")
        if review and review.get("block_code") == "REVIEW_PARTICIPANT_NOT_FOUND":
            supporting.append("The workflow block code is REVIEW_PARTICIPANT_NOT_FOUND.")
        if "REVIEW_PARTICIPANT_NOT_FOUND" in signature_set:
            supporting.append("A simulator log line records REVIEW_PARTICIPANT_NOT_FOUND.")
        if missing:
            contradicting.append("Mandatory metadata is also blank, so this is not the only gate.")
        if assigned and review and review.get("status") == "completed":
            contradicting.append("The periodic review is already completed.")
        status = _status(supporting, contradicting, strong=participant_live and participant_signal and not missing)
        hypotheses.append(
            {
                "statement": f"Approval for {number} is blocked because the Auto Periodic Review task has no eligible participant assigned.",
                "status": status,
                "confidence": "high" if status == "confirmed" else "medium" if supporting else "low",
                "supporting": supporting,
                "contradicting": contradicting,
                "assumptions": ["The responsible function on the review task is the correct filter for eligible participants."],
                "further_checks": [] if supporting else ["Open the periodic review task and confirm whether a participant is assigned."],
            }
        )
    elif review and review.get("status") == "completed":
        hypotheses.append(
            {
                "statement": f"A missing periodic-review participant is not the cause for {number}. The review workflow is completed.",
                "status": "rejected",
                "confidence": "high",
                "supporting": ["The periodic review workflow status is completed."],
                "contradicting": [],
                "assumptions": [],
                "further_checks": [],
            }
        )

    if missing or "REQUIRED_METADATA_MISSING" in signature_set:
        supporting = []
        contradicting = []
        if missing:
            labels = ", ".join(_label(field) for field in missing)
            supporting.append(f"The draft is missing {labels}.")
        if "REQUIRED_METADATA_MISSING" in signature_set:
            supporting.append("A simulator log line records REQUIRED_METADATA_MISSING.")
        if snapshot.get("status") == "draft" or (snapshot.get("draft") or {}).get("state") == "draft":
            supporting.append("The version under review is a draft.")
        if not missing:
            contradicting.append("Both mandatory metadata fields are populated on the draft.")
        status = _status(supporting, contradicting, strong=bool(missing) and "REQUIRED_METADATA_MISSING" in signature_set and not participant_live)
        hypotheses.append(
            {
                "statement": f"Approval for {number} cannot start because mandatory draft metadata is blank.",
                "status": status,
                "confidence": "high" if status == "confirmed" else "medium" if missing else "low",
                "supporting": supporting,
                "contradicting": contradicting,
                "assumptions": [],
                "further_checks": ["Ask the document owner for the real values. Do not invent Document History or Planned Effective Date."]
                if missing
                else [],
            }
        )
    elif snapshot.get("document_id"):
        hypotheses.append(
            {
                "statement": f"Missing Document History or Planned Effective Date is not the cause for {number}. Both fields are populated.",
                "status": "rejected",
                "confidence": "high",
                "supporting": ["Document History and Planned Effective Date are both populated on the draft."],
                "contradicting": [],
                "assumptions": [],
                "further_checks": [],
            }
        )

    if review_open and assigned:
        hypotheses.append(
            {
                "statement": f"A participant is assigned on {number}, but the Auto Periodic Review task is not complete, so approval remains blocked.",
                "status": "confirmed" if review and review.get("block_code") == "AWAITING_REVIEW_COMPLETION" else "supported",
                "confidence": "high",
                "supporting": ["The review task has an assignee and the workflow is not completed."],
                "contradicting": [],
                "assumptions": [],
                "further_checks": ["The assigned participant must complete the review. Assignment alone is not completion."],
            }
        )

    if not snapshot.get("document_id"):
        notes = str(context.get("close_notes") or "").strip()
        articles = [str(item) for item in (context.get("knowledge") or []) if item][:4]
        supporting = []
        if notes:
            supporting.append(f"ServiceNow resolution notes: {notes[:500]}")
        for title in articles:
            supporting.append(f"Matching published article: {title}")
        if supporting:
            hypotheses.append(
                {
                    "statement": f"Published knowledge and the ServiceNow record are the available explanation for {number}.",
                    "status": "supported",
                    "confidence": "medium" if notes else "low",
                    "supporting": supporting,
                    "contradicting": ["The live application state was not read for this ServiceNow incident."],
                    "assumptions": ["Knowledge matches are hints. They are not a confirmed root cause."],
                    "further_checks": ["Confirm the cited article against the current failure before changing anything."],
                }
            )

    case = _case(participant_live, missing, review_open and assigned, snapshot)
    if not hypotheses:
        hypotheses.append(
            {
                "statement": f"The available evidence does not identify a root cause for {number}.",
                "status": "insufficient_evidence",
                "confidence": "low",
                "supporting": [],
                "contradicting": [],
                "assumptions": [],
                "further_checks": ["Collect the application log signature and the current workflow state before recommending a change."],
            }
        )
    confirmed = [item for item in hypotheses if item["status"] == "confirmed"]
    if case == "multiple":
        summary = (
            f"{number} has more than one approval gate open. Metadata and the periodic review both need to be resolved, "
            "and neither cause is confirmed as the sole root cause."
        )
    elif case == "missing_participant":
        summary = f"{number} cannot start approval because the Auto Periodic Review task has no participant."
    elif case == "missing_metadata":
        labels = ", ".join(_label(field) for field in missing) or "mandatory metadata"
        summary = f"{number} cannot start approval because {labels} is blank on the draft."
    elif case == "review_incomplete":
        summary = f"{number} has a review participant, and approval stays blocked until that participant completes the review."
    elif case == "healthy":
        summary = f"{number} does not currently have a blocked approval gate."
    else:
        notes = str(context.get("close_notes") or "").strip()
        articles = [str(item) for item in (context.get("knowledge") or []) if item]
        if notes or articles:
            summary = f"{number} is explained from the ServiceNow record and published knowledge. No change is proposed until a person confirms it."
        else:
            summary = f"There is not enough evidence to confirm a root cause for {number}."
    return {
        "case": case,
        "summary": summary,
        "hypotheses": hypotheses,
        "confirmed": bool(confirmed) and case not in {"multiple", "unknown"},
    }


def _case(participant_live: bool, missing: list[str], review_waiting: bool, snapshot: dict) -> str:
    gates = []
    if missing:
        gates.append("missing_metadata")
    if participant_live:
        gates.append("missing_participant")
    elif review_waiting:
        gates.append("review_incomplete")
    if len(gates) > 1:
        return "multiple"
    if gates:
        return gates[0]
    if snapshot.get("approval_started"):
        return "healthy"
    if snapshot.get("document_id"):
        return "healthy"
    return "unknown"


def _status(supporting: list[str], contradicting: list[str], strong: bool) -> str:
    if not supporting:
        return "insufficient_evidence"
    if contradicting:
        return "supported"
    if strong and len(supporting) >= 2:
        return "confirmed"
    return "supported"


def _label(field: str) -> str:
    return {"document_history": "Document History", "planned_effective_date": "Planned Effective Date"}.get(field, field)
