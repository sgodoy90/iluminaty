from fastapi.testclient import TestClient

from iluminaty import server
from iluminaty.intent import Intent
from iluminaty.resolver import ResolutionResult


class _PerceptionStub:
    def __init__(self, ready: bool):
        self._ready = ready

    def get_readiness(self):
        return {
            "timestamp_ms": 1,
            "readiness": self._ready,
            "uncertainty": 0.8 if not self._ready else 0.1,
            "reasons": ["high_uncertainty"] if not self._ready else ["ready_for_action"],
            "task_phase": "loading" if not self._ready else "interaction",
            "active_surface": "editor",
            "risk_mode": "safe",
        }

    def record_action_feedback(self, action: str, success: bool, message: str = ""):
        return None


class _SafetyStub:
    is_killed = False

    def check_action(self, action: str, category: str):
        return {"allowed": True, "reason": "ok"}


class _IntentStub:
    def classify_or_default(self, instruction: str):
        return Intent(
            action="click",
            params={"x": 1, "y": 1},
            confidence=0.9,
            raw_input=instruction,
            category="normal",
        )


class _ResolverStub:
    def resolve(self, action: str, params: dict):
        return ResolutionResult(
            action=action,
            method_used="mock",
            success=True,
            message="ok",
            attempts=[],
            total_ms=1.5,
        )


def _setup_state(perception_ready: bool):
    server._state.api_key = None
    server._state.perception = _PerceptionStub(ready=perception_ready)
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverStub()
    server._state.verifier = None
    server._state.recovery = None
    server._state.audit = None
    server._state.autonomy = None


def test_safe_precheck_blocks_when_not_ready():
    _setup_state(perception_ready=False)
    client = TestClient(server.app)

    response = client.post(
        "/action/precheck",
        json={"instruction": "click save", "mode": "SAFE"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["readiness_check"]["allowed"] is False


def test_raw_precheck_skips_readiness_block():
    _setup_state(perception_ready=False)
    client = TestClient(server.app)

    response = client.post(
        "/action/precheck",
        json={"instruction": "click save", "mode": "RAW"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["blocked"] is False
    assert payload["readiness_check"]["reason"] == "skipped"


def test_token_endpoints_require_auth_when_api_key_enabled():
    _setup_state(perception_ready=True)
    server._state.api_key = "secret"
    client = TestClient(server.app)

    unauth = client.get("/tokens/status")
    auth = client.get("/tokens/status", headers={"x-api-key": "secret"})

    assert unauth.status_code == 401
    assert auth.status_code == 200
