# Implementation plan

| Phase | State | Notes |
| --- | --- | --- |
| 0 Design | Done | This set of documents matches the code that was built. |
| 1 Foundation | Done | FastAPI factory, SQLite or Postgres, JWT, RBAC, events, health. |
| 2 ServiceNow | Done, gated | Client, sync, stale-write checks, and UI exist. Live data requires credentials. |
| 3 Knowledge | Done | Seed articles, upload, lifecycle, hybrid search. Hash embeddings, not an external model. |
| 4 Easidoc | Done | Both scenarios, persistent workflow, logs, events. |
| 5 Conversation | Done | Evidence-backed replies for the selected document. |
| 6 Agents | Done | LangGraph when installed, shared nodes, investigation studio. |
| 7 Approval and execution | Done | Policy, executor, verification, rollback of an uncompleted assignment. |
| 8 Analytics and deploy | Done for the local stack | KPI API and dashboard, Docker Compose, Kubernetes stubs, CI. |

## Remaining external work

- Provide ServiceNow instance URL, user, password, and table permissions before treating sync as live.
- Provide Splunk, Datadog, OpenSearch, or Azure credentials before those adapters return data.
- Set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET` to use the SSO start and callback. The callback does not auto-provision users.
- Set `OPENAI_API_KEY` or `OLLAMA_BASE_URL` only if narration is wanted. It does not choose actions.
