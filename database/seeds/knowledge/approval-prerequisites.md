---
title: Easidoc approval prerequisites
doc_type: sop
application: easidoc
source: authored
source_id: easidoc-approval-prerequisites
---

# Approval prerequisites

A draft can enter the document approval workflow only when every gate below is true.

- The selected version is a draft.
- Document History is populated with the change description.
- Planned Effective Date is a valid date.
- If an Auto-start Periodic Review workflow exists, that workflow is completed by the assigned participant.
- The participant belongs to the responsible function required by the review task.

`REVIEW_PARTICIPANT_NOT_FOUND` and `REQUIRED_METADATA_MISSING` are different failures. Investigate the document that the user selected. Do not apply the participant procedure to a metadata failure, or the metadata procedure to a missing participant.

Simulated Easidoc events are produced by the digital twin. They are not production Easidoc events.
