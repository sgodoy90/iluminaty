from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionReadyStub:
    def get_readiness(self):
        return {
            "timestamp_ms": 1,
            "readiness": True,
            "uncertainty": 0.1,
            "reasons": ["ready_for_action"],
            "task_phase": "interaction",
            "active_surface": "editor",
            "risk_mode": "safe",
            "tick_id": 7,
            "staleness_ms": 5,
            "domain_policy": {"max_staleness_ms": {"safe": 1500, "hybrid": 1500, "raw": 4000}},
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        _ = context_tick_id
        _ = max_staleness_ms
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 7, "staleness_ms": 5}


class _SafetyStub:
    is_killed = False

    def check_action(self, action: str, category: str):
        _ = action
        _ = category
        return {"allowed": True, "reason": "ok"}


def test_runtime_profile_enterprise_requires_raw_ack_for_destructive():
    original_api_key = server._state.api_key
    original_perception = server._state.perception
    original_safety = server._state.safety
    original_profile = server._state.runtime_profile
    try:
        server._state.api_key = None
        server._state.perception = _PerceptionReadyStub()
        server._state.safety = _SafetyStub()
        server._state.runtime_profile = "standard"
        client = TestClient(server.app)

        set_profile = client.post("/runtime/profile", json={"profile": "enterprise"})
        assert set_profile.status_code == 200
        assert set_profile.json()["profile"] == "enterprise"

        blocked = client.post(
            "/action/precheck",
            json={
                "action": "close_window",
                "params": {"title": "notepad"},
                "category": "destructive",
                "mode": "RAW",
            },
        )
        assert blocked.status_code == 200
        assert blocked.json()["blocked"] is True
        assert blocked.json()["profile_check"]["reason"] == "enterprise_raw_requires_ack"

        allowed = client.post(
            "/action/precheck",
            json={
                "action": "close_window",
                "params": {"title": "notepad", "enterprise_raw_ack": True},
                "category": "destructive",
                "mode": "RAW",
            },
        )
        assert allowed.status_code == 200
        assert allowed.json()["blocked"] is False
    finally:
        server._state.api_key = original_api_key
        server._state.perception = original_perception
        server._state.safety = original_safety
        server._state.runtime_profile = original_profile
