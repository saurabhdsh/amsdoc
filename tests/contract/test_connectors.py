from app.connectors.observability import DECLARED, RemoteConnector, normalize
from app.resilience import call_with_retry


def test_remote_connectors_declare_capabilities_without_fake_data():
    for name, capabilities in DECLARED.items():
        if name == "easidoc":
            continue
        connector = RemoteConnector(name, configured=False)
        result = connector.test_connection()
        assert result["status"] == "not_configured"
        assert result["declared_capabilities"] == capabilities
        logs = connector.query_logs({})
        assert logs["logs"] == []
        assert logs["status"] == "not_configured"
        assert connector.get_traces({})["traces"] == []


def test_normalize_results_keeps_source_fields():
    rows = normalize([{"timestamp": "t", "level": "ERROR", "message": "m", "signature": "S", "correlation_id": "c", "source": "easidoc_simulator", "environment": "simulator"}])
    assert rows[0]["signature"] == "S"
    assert rows[0]["environment"] == "simulator"


def test_retry_recovers_from_transient_failure():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("upstream")
        return {"ok": True}

    assert call_with_retry(flaky, attempts=2)["ok"] is True
    assert calls["n"] == 2
