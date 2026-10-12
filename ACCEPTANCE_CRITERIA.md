# Acceptance criteria

## Case 1 — missing periodic review participant

- Seed `missing-periodic-review-participant` creates DOC-1042 with an effective version, a draft, a blocked Auto Periodic Review, and `REVIEW_PARTICIPANT_NOT_FOUND`.
- Starting approval while the review is open is rejected.
- Investigation recommends assigning an eligible QA participant and does not complete the review itself.
- Human approval is required before assignment.
- After assignment, the review stays incomplete until Priya Shah completes it.
- Approval starts only after `PERIODIC_REVIEW_COMPLETED`.
- The local incident becomes `resolved` with `closed_at` empty and `servicenow_sync_status=not_linked` when no ServiceNow record is linked.

## Case 2 — missing metadata

- Variants A, B, and C reject approval with `REQUIRED_METADATA_MISSING` and name the blank fields.
- The assistant does not invent Document History or Planned Effective Date.
- Approved, human-confirmed values are persisted, validation passes, and approval starts.
- Placeholder text and invalid dates are rejected.

## Conversation

Asking “I cannot start the approval workflow in Easidoc.” against the selected document distinguishes the two cases and cites the matching published article.

## ServiceNow

Without credentials, status is `not_configured`. Contract tests cover authentication failure, pagination with retry, stale `sys_mod_count`, journal confirmation, and duplicate sync using a mock transport. Those tests do not claim a live instance was contacted.

## Platform

- Cross-account reads return nothing or not found.
- Dev accounts are hidden when `ENVIRONMENT` is not `local`.
- Repeated seed with the same idempotency key returns the stored body.
- Analytics separates `easidoc_simulator`, `servicenow`, and `manual`.
- WebSocket replay returns events that were committed before the socket subscribed.
