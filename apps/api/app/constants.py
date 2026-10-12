"""Shared constants for agents, permissions, and allowlisted actions."""

from __future__ import annotations

ENVIRONMENT_LABEL = "EASIDOC — SIMULATED APPLICATION"

EVENT_NAMES = [
    "INCIDENT_DETECTED",
    "INVESTIGATION_STARTED",
    "LOG_ANALYSIS_COMPLETED",
    "KNOWLEDGE_RETRIEVED",
    "ROOT_CAUSE_HYPOTHESIS_CREATED",
    "REMEDIATION_RECOMMENDED",
    "HUMAN_APPROVAL_REQUESTED",
    "HUMAN_APPROVAL_GRANTED",
    "HUMAN_APPROVAL_REJECTED",
    "ACTION_EXECUTION_STARTED",
    "REVIEW_PARTICIPANT_NOT_FOUND",
    "PARTICIPANT_ASSIGNED",
    "PARTICIPANT_NOTIFIED",
    "PERIODIC_REVIEW_COMPLETED",
    "PERIODIC_REVIEW_INCOMPLETE",
    "REQUIRED_METADATA_MISSING",
    "DOCUMENT_METADATA_UPDATED",
    "VALIDATION_PASSED",
    "APPROVAL_WORKFLOW_STARTED",
    "REMEDIATION_VERIFIED",
    "SERVICENOW_UPDATE_CONFIRMED",
    "INCIDENT_RESOLVED",
    "ACTION_ROLLED_BACK",
]

ALLOWLISTED_ACTIONS = {
    "easidoc.assign_participant",
    "easidoc.update_metadata",
    "easidoc.start_approval",
    "easidoc.await_event",
    "verify_state",
    "record_outcome",
    "draft_knowledge",
    "servicenow.add_work_note",
}

EDITABLE_PARAMS = {"participant_id", "document_history", "planned_effective_date"}

SOURCE_LABELS = {
    "servicenow": "ServiceNow",
    "easidoc_simulator": "Easidoc simulator",
    "manual": "Manual entry",
    "authored": "Authored runbook",
    "ai_draft": "AI draft — not approved",
}

AGENTS = [
    {
        "id": "intake",
        "name": "Incident Intake Agent",
        "summary": "Normalizes the incident, classifies the issue, and sets investigation priority.",
        "tools": ["incident.read"],
    },
    {
        "id": "enrichment",
        "name": "Context Enrichment Agent",
        "summary": "Loads the application, document, workflow, and configuration context.",
        "tools": ["simulator.read", "incident.read"],
    },
    {
        "id": "logs",
        "name": "Log Investigation Agent",
        "summary": "Reads a bounded log window and extracts signatures and correlation IDs.",
        "tools": ["logs.read"],
    },
    {
        "id": "knowledge",
        "name": "Historical Knowledge Agent",
        "summary": "Retrieves published knowledge only. Drafts are never treated as trusted.",
        "tools": ["knowledge.read"],
    },
    {
        "id": "rca",
        "name": "Root Cause Analysis Agent",
        "summary": "Builds competing hypotheses and separates evidence from assumptions.",
        "tools": ["incident.read"],
    },
    {
        "id": "recommendation",
        "name": "Resolution Recommendation Agent",
        "summary": "Ranks allowlisted remediations and rejects policy-violating options.",
        "tools": ["incident.read"],
    },
    {
        "id": "approval",
        "name": "Human Approval Agent",
        "summary": "Packages the exact action payload and waits for a human decision.",
        "tools": ["approval.read"],
    },
    {
        "id": "execution",
        "name": "Action Execution Agent",
        "summary": "Runs an approved allowlisted action and records the upstream response.",
        "tools": ["action.execute"],
    },
    {
        "id": "verification",
        "name": "Verification Agent",
        "summary": "Re-reads application state after execution. It does not assume success.",
        "tools": ["simulator.read", "logs.read"],
    },
    {
        "id": "learning",
        "name": "Knowledge Learning Agent",
        "summary": "Drafts a knowledge article from a verified outcome and leaves it unpublished.",
        "tools": ["knowledge.write"],
    },
    {
        "id": "conversation",
        "name": "Conversational Support Agent",
        "summary": "Answers from application evidence and coordinates the same governed tools.",
        "tools": ["conversation.use", "simulator.read", "knowledge.read"],
    },
]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "resolver": {
        "incident.read",
        "incident.investigate",
        "incident.create",
        "approval.read",
        "approval.decide",
        "action.execute",
        "action.rollback",
        "knowledge.read",
        "knowledge.write",
        "knowledge.review",
        "simulator.read",
        "simulator.seed",
        "easidoc.approval.start",
        "logs.read",
        "analytics.read",
        "conversation.use",
        "connector.read",
        "audit.read",
    },
    "investigator": {
        "incident.read",
        "incident.investigate",
        "incident.create",
        "approval.read",
        "knowledge.read",
        "knowledge.write",
        "simulator.read",
        "logs.read",
        "analytics.read",
        "conversation.use",
        "connector.read",
    },
    "operator": {
        "incident.read",
        "simulator.read",
        "simulator.seed",
        "easidoc.approval.start",
        "easidoc.review.complete",
        "document.metadata.write",
        "knowledge.read",
        "logs.read",
        "analytics.read",
        "conversation.use",
    },
    "viewer": {
        "incident.read",
        "knowledge.read",
        "analytics.read",
        "simulator.read",
        "logs.read",
        "approval.read",
    },
}
