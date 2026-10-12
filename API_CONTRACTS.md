# API contracts

Base path `/api/v1`. Errors are `{ "error": { "code", "message", "details" } }`. Mutations that change simulator or approval state require `Idempotency-Key`.

## Auth

- `POST /auth/login`
- `GET /auth/me`
- `GET /auth/dev-accounts` returns 404 unless `ENVIRONMENT=local`
- `GET /auth/oidc/start`, `POST /auth/oidc/callback`

## Incidents and agents

- `GET/POST /incidents`
- `GET /incidents/{id}`
- `POST /incidents/{id}/investigate`
- `GET /incidents/{id}/investigation`
- `POST /incidents/{id}/link-servicenow`
- `GET /agents`, `GET /agents/runs`

## Approvals

- `GET /approvals`
- `POST /approvals/{id}/decide` with `approve | reject | modify | investigate | escalate`
- `POST /actions/{approval_id}/execute`
- `POST /actions/executions/{id}/rollback`

## Knowledge, conversation, logs, analytics

- `GET /knowledge`, `GET /knowledge/{id}`, `POST /knowledge/search`, `POST /knowledge/upload`, `POST /knowledge/{id}/transition`
- `GET/POST /conversations`, `GET/PATCH /conversations/{id}`, `POST /conversations/{id}/messages`
- `GET /logs?connector=easidoc`
- `GET /analytics/kpis`
- `GET /search?q=`
- `GET /events?after_id=`
- WebSocket `/events/stream` and `/simulator/events/stream`

## Connectors and admin

- `GET /connectors`
- `GET/PUT /connectors/servicenow`
- `POST /connectors/servicenow/test`
- `POST /connectors/servicenow/sync`
- `GET/POST /admin/users`
- `GET /admin/audit`
- `GET/PUT /admin/retention`, `POST /admin/retention/run`

## Simulator

- `GET /simulator/scenarios`
- `POST /simulator/scenarios/{id}/seed`
- `POST /simulator/scenarios/{id}/reset`
- `GET /simulator/scenarios/{id}/state`
- `GET /simulator/documents` and `GET /simulator/documents/{id}`
- `GET /simulator/documents/{id}/versions`
- `GET /simulator/documents/{id}/timeline`
- `PATCH /simulator/documents/{id}/metadata`
- `POST /simulator/documents/{id}/start-approval`
- `GET /simulator/workflows/{id}` and `/tasks` and `/eligible-participants`
- `POST /simulator/workflows/{id}/participants`
- `POST /simulator/workflows/{id}/complete-review`
- `GET /simulator/logs`, `/events`, `/health`

Scenario ids: `missing-periodic-review-participant`, `missing-document-metadata` with variant `A`, `B`, or `C`.
