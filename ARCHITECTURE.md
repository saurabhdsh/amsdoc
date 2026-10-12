# Architecture

AI for AMS is a modular monolith. One FastAPI process owns persistence, integrations, agent orchestration, and the Easidoc simulator. A React application talks only to that API. A separate worker process is not required for the acceptance flows.

```text
apps/web          React, Vite, TanStack Query
apps/api          FastAPI, SQLAlchemy, LangGraph
database/seeds    Published knowledge articles
tests             pytest acceptance, contract, connector tests
infrastructure    Docker, Kubernetes, CI
```

## Request path

1. The browser sends a bearer token. Mutations that change state also send `Idempotency-Key`.
2. A route checks RBAC, then a service mutates SQLAlchemy models inside the request session.
3. Domain events are queued on the session and inserted before commit.
4. After a successful commit, the in-process event bus broadcasts the persisted rows.
5. WebSocket clients authenticate with the first message and replay events after `after_id`. The database remains the source of truth.

## Boundaries

| Concern | Module |
| --- | --- |
| Easidoc state machine | `app/services/easidoc.py` |
| Incident agents | `app/services/investigation.py`, `app/services/diagnosis.py` |
| Approval and execution | `app/policy.py`, `app/services/executor.py` |
| Knowledge | `app/services/knowledge.py` |
| Conversation | `app/services/conversation.py` |
| ServiceNow | `app/services/servicenow.py` |
| Log adapters | `app/connectors/observability.py` |
| Metrics | `app/services/analytics.py` |

The simulator implements a small application contract: seed, reset, state, actions, logs, and events. Easidoc is the first implementation. Agents call it through the action executor, not through a private import in the UI.

## Data stores

Local development uses SQLite. Docker Compose uses PostgreSQL. On PostgreSQL the API attempts `CREATE EXTENSION vector`. Embeddings are stored as JSON vectors from a local hashing embedder, so retrieval works without an external embedding API and without pgvector. Hybrid ranking mixes token overlap and cosine similarity. Only `published` articles are returned to investigations.

## What is intentionally not faked

- Unconfigured ServiceNow, Splunk, Datadog, OpenSearch, Azure Monitor, and generic logs return an empty, labeled failure.
- A ServiceNow work note is marked confirmed only when the journal read-back matches.
- Resolving a local incident does not close a ServiceNow record.
- Model output cannot add an action to an allowlist.
