"""ServiceNow client behavior against a mocked HTTP transport."""

import json

import httpx

from app.services.servicenow import ServiceNowClient


def _client(handler, backoff=(0, 0, 0)):
    return ServiceNowClient("https://example.service-now.com", "svc", "secret", transport=httpx.MockTransport(handler), backoff=backoff, min_interval=0)


def test_connection_reports_auth_failure():
    def handler(_request):
        return httpx.Response(401, json={"error": "unauthorized"})

    try:
        _client(handler).test_connection()
        assert False, "expected auth failure"
    except Exception as exc:
        assert exc.code == "SERVICENOW_AUTH"


def test_pagination_and_retry(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "throttle"})
        offset = int(request.url.params.get("sysparm_offset", "0"))
        if offset == 0:
            return httpx.Response(200, json={"result": [{"sys_id": {"value": "a"}, "number": {"display_value": "INC001"}}]})
        return httpx.Response(200, json={"result": []})

    client = _client(handler)
    first = client.fetch_incidents("ORDERBYsys_updated_on", 0, 1)
    second = client.fetch_incidents("ORDERBYsys_updated_on", 1, 1)
    assert first[0]["sys_id"]["value"] == "a"
    assert second == []
    assert calls["n"] >= 2


def test_stale_write_is_rejected():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"sys_id": "abc", "sys_mod_count": {"value": "5"}, "number": {"display_value": "INC9"}}})
        return httpx.Response(200, json={"result": {}})

    try:
        _client(handler).add_work_note("abc", "verified note", "4")
        assert False, "expected stale record"
    except Exception as exc:
        assert exc.code == "STALE_RECORD"


def test_work_note_confirmed_when_journal_matches():
    def handler(request: httpx.Request):
        if "sys_journal_field" in str(request.url):
            return httpx.Response(200, json={"result": [{"value": "verified note"}]})
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {"number": "INC9"}})
        return httpx.Response(200, json={"result": {"sys_id": "abc", "sys_mod_count": {"value": "3"}}})

    result = _client(handler).add_work_note("abc", "verified note", "3")
    assert result["status"] == "confirmed"
    assert result["verified"] is True


def test_sync_deduplicates_records(client):
    from tests.conftest import auth_admin

    headers = auth_admin(client)
    saved = client.put(
        "/api/v1/connectors/servicenow",
        json={
            "instance_url": "https://example.service-now.com",
            "username": "svc",
            "password": "secret",
            "write_mode": "read_only",
            "historical_days": 7,
            "sync_frequency_seconds": 300,
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["password_set"] is True
    assert "password" not in saved.json()

    pages = {"n": 0}

    def handler(request: httpx.Request):
        pages["n"] += 1
        if "kb_knowledge" in str(request.url):
            return httpx.Response(200, json={"result": []})
        offset = int(request.url.params.get("sysparm_offset", "0"))
        if offset == 0 and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": {"value": "sys1"},
                            "number": {"display_value": "INC1000001"},
                            "short_description": {"display_value": "Mail gateway delay"},
                            "description": {"display_value": "Queued messages increased."},
                            "state": {"value": "2"},
                            "priority": {"value": "2"},
                            "sys_updated_on": {"value": "2026-10-01 00:00:00"},
                            "sys_mod_count": {"value": "1"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"result": []})

    from app.database import SessionLocal
    from app.models import User
    from app.services.servicenow import sync_account

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email="admin@ams.local").one()
        import httpx

        first = sync_account(db, user, transport=httpx.MockTransport(handler), backoff=(0, 0, 0))
        second = sync_account(db, user, transport=httpx.MockTransport(handler), backoff=(0, 0, 0))
        db.commit()
    finally:
        db.close()
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    incidents = client.get("/api/v1/incidents?source=servicenow", headers=headers).json()
    assert incidents["total"] == 1
    assert incidents["items"][0]["live_integration"] is True
    assert incidents["items"][0]["number"] == "INC1000001"
    assert json.loads  # keep json import used if assertions expand
