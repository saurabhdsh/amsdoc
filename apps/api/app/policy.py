"""Policy checks for human approval and action execution."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.constants import ALLOWLISTED_ACTIONS, EDITABLE_PARAMS
from app.errors import AppError
from app.models import HumanApproval, RemediationProposal, User


def canonical_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def apply_modifications(payload: dict, modifications: dict | None) -> dict:
    if not modifications:
        return payload
    unknown = set(modifications) - EDITABLE_PARAMS
    if unknown:
        raise AppError(
            "MODIFICATION_REJECTED",
            "Only participant and metadata values can be edited on an approved action.",
            422,
            {"rejected_fields": sorted(unknown)},
        )
    updated = json.loads(json.dumps(payload))
    for step in updated.get("steps", []):
        params = step.setdefault("params", {})
        for key, value in modifications.items():
            if key in params or key in EDITABLE_PARAMS and _step_accepts(step, key):
                params[key] = value
    return updated


def _step_accepts(step: dict, key: str) -> bool:
    action = step.get("action")
    if key == "participant_id":
        return action == "easidoc.assign_participant"
    if key in {"document_history", "planned_effective_date"}:
        return action == "easidoc.update_metadata"
    return False


def readiness_error(payload: dict) -> str | None:
    for step in payload.get("steps", []):
        action = step.get("action")
        if action not in ALLOWLISTED_ACTIONS:
            return f"Action {action} is not allowlisted."
        params = step.get("params") or {}
        if action == "easidoc.assign_participant" and not str(params.get("participant_id") or "").strip():
            return "Choose an eligible participant before approving assignment."
        if action == "easidoc.update_metadata":
            for field in params.get("missing_fields") or []:
                if not str(params.get(field) or "").strip():
                    return f"Enter a confirmed value for {field.replace('_', ' ')} before approving."
    if not payload.get("steps"):
        return "There is no allowlisted action to approve."
    return None


def load_approved(db: Session, user: User, approval_id: str) -> HumanApproval:
    approval = db.get(HumanApproval, approval_id)
    if approval is None or approval.account_id != user.account_id:
        raise AppError("APPROVAL_REQUIRED", "A matching human approval is required.", 403)
    if approval.status != "approved":
        raise AppError("APPROVAL_REQUIRED", "This remediation has not been approved.", 403)
    proposal = db.get(RemediationProposal, approval.proposal_id)
    if proposal is None or proposal.payload_hash != approval.payload_hash:
        raise AppError(
            "APPROVAL_INVALIDATED",
            "The action changed after approval, so the approval is no longer valid.",
            409,
        )
    if canonical_hash(approval.payload) != approval.payload_hash:
        raise AppError("APPROVAL_INVALIDATED", "The approved payload failed integrity verification.", 409)
    return approval


def assert_approved_step(approval: HumanApproval, action: str, resource_id: str, params: dict) -> dict:
    if action not in ALLOWLISTED_ACTIONS:
        raise AppError("ACTION_REJECTED", f"Action {action} is not allowlisted.", 403)
    matches = [step for step in approval.payload.get("steps", []) if step.get("action") == action]
    if not matches:
        raise AppError("ACTION_NOT_APPROVED", f"{action} is not part of the approved payload.", 403)
    step = matches[0]
    if step.get("resource_id") and step["resource_id"] != resource_id:
        raise AppError("APPROVAL_MISMATCH", "The approval is bound to a different resource.", 409)
    if approval.payload.get("environment") not in (None, "", "simulator", "production"):
        raise AppError("APPROVAL_MISMATCH", "The approval environment is not recognized.", 409)
    for key, value in params.items():
        if str((step.get("params") or {}).get(key)) != str(value):
            raise AppError(
                "APPROVAL_MISMATCH",
                "The requested values do not match the approved payload.",
                409,
                {"field": key},
            )
    return step
