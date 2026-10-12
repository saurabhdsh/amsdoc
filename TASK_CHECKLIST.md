# Task checklist

- [x] Repository foundation, configuration, and health
- [x] Persistent domain model and request-scoped transactions
- [x] JWT, RBAC, account isolation, audit, idempotency
- [x] Event persistence and WebSocket replay
- [x] Easidoc scenario 1 and scenario 2, including explicit review completion
- [x] Knowledge ingestion, hybrid search, and draft lifecycle
- [x] Investigation agents, evidence-backed RCA, and allowlisted recommendations
- [x] Human approval, execution, verification, and assignment rollback
- [x] ServiceNow client with honest unconfigured behavior
- [x] Conversation workspace grounded in the selected document
- [x] Twelve React workspaces bound to the API
- [x] Pytest acceptance and contract tests
- [x] Docker Compose, Kubernetes manifests, and CI workflow
- [ ] Live ServiceNow synchronization — blocked on instance credentials
- [ ] Live Splunk, Datadog, OpenSearch, and Azure Monitor — blocked on credentials
- [ ] External embedding model and pgvector ANN index — local hash embeddings are used instead
- [ ] Playwright browser suite — UI is verified in the IDE browser for the acceptance flows
- [ ] Redis-backed event bus — Redis is deployed in Compose; the running bus is in-process
