---
title: Easidoc REQUIRED_METADATA_MISSING before approval
doc_type: known_error
application: easidoc
source: authored
source_id: easidoc-required-metadata-missing
---

# Mandatory metadata missing on a draft

Easidoc rejects approval when Document History or Planned Effective Date is blank on the draft version. The simulator returns `REQUIRED_METADATA_MISSING` and lists the missing fields. The document remains in Draft and the approval workflow does not start.

## Resolution

1. Open the draft and inspect Document History and Planned Effective Date.
2. Identify which of those fields are blank. Do not guess or invent either value.
3. Ask the document owner to confirm the real Document History text and the real Planned Effective Date.
4. Save only the confirmed values through the metadata API.
5. Revalidate. Continue only after `VALIDATION_PASSED`.
6. Start the approval workflow and verify that it is in progress.

## Validation

Document History must be a real change description, not a placeholder such as TBD or N/A. Planned Effective Date must be a calendar date in `YYYY-MM-DD` form.

## Do not

Do not let an agent fill either field from model output. Do not start approval while either mandatory field is blank.
