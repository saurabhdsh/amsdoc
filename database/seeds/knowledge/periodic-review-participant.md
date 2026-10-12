---
title: Easidoc REVIEW_PARTICIPANT_NOT_FOUND blocks approval
doc_type: known_error
application: easidoc
source: authored
source_id: easidoc-review-participant-not-found
---

# Missing Auto Periodic Review participant

Easidoc blocks a draft approval when the effective document still has an active Auto-start Periodic Review workflow and the Auto Periodic Review task has no participant.

## Error

The simulator records `REVIEW_PARTICIPANT_NOT_FOUND`. Approval stays blocked until that review workflow is completed. Assigning a participant does not complete the review.

## Resolution

1. Confirm the document version, the periodic review workflow, and the task have no assigned participant.
2. Filter participants by the Quality Assurance responsible function.
3. Ask a resolver to approve the specific participant assignment. Do not invent a participant.
4. Assign that participant through the simulator API.
5. Notify the participant inside the simulator.
6. Wait for that participant to complete the Auto Periodic Review task. The platform must not complete the task automatically.
7. After `PERIODIC_REVIEW_COMPLETED`, start the draft approval workflow.
8. Confirm the approval workflow is in progress before resolving the incident.

## Do not

Do not force-start approval while the review task is open. Do not mark the review complete just because a participant was assigned.
