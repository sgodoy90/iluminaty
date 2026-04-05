from pathlib import Path

from fastapi.testclient import TestClient

from iluminaty import server
from iluminaty.app_behavior_cache import AppBehaviorCache
from iluminaty.intent import Intent
from iluminaty.resolver import ResolutionResult


class _PerceptionReadyStub:
    def get_readiness(self):
        return {
            "timestamp_ms": 1,
            "readiness": True,
            "uncertainty": 0.1,
            "reasons": ["ready_for_action"],
            "task_phase": "interaction",
            "active_surface": "brave :: chat",
            "risk_mode": "safe",
            "tick_id": 7,
            "staleness_ms": 10,
            "domain_policy": {"max_staleness_ms": {"safe": 1500, "hybrid": 1500, "raw": 4000}},
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        _ = context_tick_id
        _ = max_staleness_ms
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 7, "staleness_ms": 10}

    def record_action_feedback(self, action: str, success: bool, message: str = ""):
        _ = action
        _ = success
        _ = message


class _SafetyStub:
    is_killed = False

    def check_action(self, action: str, category: str):
        _ = action
        _ = category
        return {"allowed": True, "reason": "ok"}


class _IntentStub:
    def classify_or_default(self, instruction: str):
        _ = instruction
        return Intent(
            action="click",
            params={"x": 20, "y": 20},
            confidence=0.9,
            raw_input="click",
            category="normal",
        )


class _ResolverStub:
    def resolve(self, action: str, params: dict):
        _ = params
        return ResolutionResult(
            action=action,
            method_used="mock",
            success=True,
            message="ok",
            attempts=[],
            total_ms=3.2,
        )


def _setup_state(db_path: Path):
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionReadyStub()
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverStub()
    server._state.verifier = None
    server._state.recovery = None
    server._state.audit = None
    server._state.autonomy = None
    server._state.runtime_profile = "standard"
    server._state.behavior_cache = AppBehaviorCache(db_path=str(db_path))
    server._state.audio_interrupt = None


def test_execute_persists_behavior_outcome_and_exposes_hint(tmp_path: Path):
    db_path = tmp_path / "behavior.sqlite3"
    _setup_state(db_path)
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    # First call creates history.
    first = client.post("/action/execute", json={"instruction": "click save", "mode": "SAFE"})
    assert first.status_code == 200
    assert first.json()["result"]["success"] is True

    # Second call should include behavior hint section.
    second = client.post("/action/execute", json={"instruction": "click save", "mode": "SAFE"})
    payload = second.json()
    assert second.status_code == 200
    assert "behavior" in payload
    assert "hint" in payload["behavior"]

    stats = client.get("/behavior/stats")
    assert stats.status_code == 200
    assert stats.json()["enabled"] is True
    assert stats.json()["entries"] >= 2
