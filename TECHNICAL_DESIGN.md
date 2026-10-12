# Technical design

## Agent graph

`investigate()` records an `AgentRun`. If LangGraph imports, the same node functions run on a `StateGraph`. Otherwise a linear runner calls them in order. Nodes: intake, enrichment, logs, knowledge, root cause, recommendation, approval packaging.

`diagnosis.analyze()` is authoritative. It returns one of `missing_participant`, `missing_metadata`, `review_incomplete`, `multiple`, `healthy`, or `unknown`. A hypothesis is `confirmed` only when the supporting gates are present and the contradicting gate is absent. The optional model gateway may add narration and is stored as non-authoritative.

The recommendation payload contains the document version, workflow version, allowlisted steps, and rejected options. `force_start_approval` is a rejected option. It is never a step.

## Case 1 runbook

1. `easidoc.assign_participant` for an eligible QA participant who has a user account.
2. Wait until the review workflow emits `PERIODIC_REVIEW_COMPLETED`.
3. `easidoc.start_approval`.
4. Verify participant assignment, review completion, and approval start.
5. Draft knowledge in `draft` status.
6. Record the local incident outcome.
7. Add a ServiceNow work note only when the incident is linked and write mode allows it.

The first execute returns `waiting` after assignment. `complete-review` is accepted only from the user linked to the assigned participant. The route then calls `advance_for_workflow`, which resumes the approved payload.

## Case 2 runbook

`easidoc.update_metadata` writes only the fields the human confirmed. Empty and placeholder values are rejected. The executor then starts approval, verifies, drafts knowledge, and records the outcome.

Direct metadata edits without `human_confirmation` and an approval token are forbidden.

## Approval integrity

The payload is hashed with canonical JSON. `participant_id`, `document_history`, and `planned_effective_date` are the only editable fields, and each applies only to the matching step. A later payload change produces a different hash and the approval no longer loads.

## Realtime

`ApplicationEvent.id` is the replay cursor. Clients connect to `/api/v1/events/stream` or `/api/v1/simulator/events/stream`, send `{token, after_id}`, receive `hello`, then the backlog, then live events. Persisted rows are committed before broadcast.

## Observability connectors

Each adapter declares `query_logs`, `get_metrics`, `get_traces`, and `get_events` support. The Easidoc adapter serves logs, events, and workflow metrics. Traces are unsupported. Remote adapters are unconfigured until credentials exist.

## Frontend

Twelve routes share one shell, one bearer token in `sessionStorage`, and TanStack Query. The Easidoc page is a split workspace: simulator modules on the left, the incident investigation and approval actions on the right. Vite proxies `/api` to the API, including WebSockets.
