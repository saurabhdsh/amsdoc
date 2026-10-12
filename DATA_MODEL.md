# Data model

All operational tables live in one database. Account id is on tenant-owned rows. SQLite is created with `create_all` at startup. Alembic is configured for PostgreSQL migrations.

## Identity

- Account, User. Passwords are PBKDF2 hashes. Roles: admin, resolver, investigator, operator, viewer.

## Easidoc twin

- Application, ScenarioDefinition, ScenarioExecution
- Document, DocumentVersion, DocumentMetadata
- WorkflowDefinition, WorkflowInstance, WorkflowTask, ParticipantAssignment
- ResponsibleFunction, Participant
- ApprovalWorkflow is the document approval inside the simulator. It is distinct from HumanApproval.

## Operations

- Incident, with unique `(account_id, number)` and unique `(account_id, source, external_sys_id)` when the external id is present
- IncidentEvidence, AgentRun, AgentStep
- RootCauseHypothesis, RemediationProposal, HumanApproval, ActionExecution, VerificationResult
- AuditRecord, IdempotencyRecord, SyncCheckpoint, ConnectorConfig, NumberSequence, PlatformSetting
- ApplicationEvent, ApplicationLog

## Knowledge and conversation

- KnowledgeDocument, KnowledgeChunk. Status moves draft → reviewed → approved → published → superseded.
- Conversation, ConversationMessage. Citations and the evidence context are stored on the assistant message.

## Concurrency

Documents, workflows, and tasks carry `row_version`. Mutations compare the expected version and return `STALE_RECORD` on mismatch. Idempotency keys replay the stored JSON body for the same request hash.
