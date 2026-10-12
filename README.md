# AI for AMS

Autonomous Operations Intelligence. A modular monolith for incident investigation, human-approved remediation, and the Easidoc digital twin.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r apps/api/requirements.txt
.venv/bin/uvicorn app.main:create_app --factory --app-dir apps/api --host 127.0.0.1 --port 8000
```

```bash
cd apps/web
npm install
npm run dev
```

Open http://127.0.0.1:5173. The API defaults to SQLite at `apps/api/data/ams.db`.

When `ENVIRONMENT=local`, the API creates these accounts. They are not created in production.

| Person | Email | Password | Role |
| --- | --- | --- | --- |
| Avery Chen | admin@ams.local | LocalAdmin!2026 | admin |
| Jordan Blake | resolver@ams.local | LocalResolver!2026 | resolver |
| Mina Patel | investigator@ams.local | LocalInvest!2026 | investigator |
| Priya Shah | priya.shah@ams.local | LocalReviewer!2026 | operator |
| Chris Nguyen | viewer@ams.local | LocalViewer!2026 | viewer |

## What is live, and what is simulated

- Easidoc is labeled **EASIDOC — SIMULATED APPLICATION**. Its documents, logs, and events come from the simulator API and are stored as `source=easidoc_simulator`.
- ServiceNow is live only after `SERVICENOW_INSTANCE_URL`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD` are set, or an administrator saves them in Application Connectors. Until then the connector status is `not_configured` and no incidents are invented.
- Splunk, Datadog, OpenSearch, Azure Monitor, and the generic log connector return `not_configured`. Log investigation uses the Easidoc simulator adapter.
- Optional narration uses an OpenAI-compatible endpoint or Ollama when configured. Diagnosis and the action plan come from the evidence engine, not from model text.
- AI-drafted knowledge stays in `draft` until a person moves it through review and publish.

## Tests

```bash
.venv/bin/pytest tests
cd apps/web && npm run build
```

## Docker

`docker compose up --build` starts Postgres, Redis, the API, and the web app for local development. Redis is present for later workers. The event bus in this build is in-process. Compose sets `ENVIRONMENT=local`, so the local accounts above are created.
