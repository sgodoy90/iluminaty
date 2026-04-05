from fastapi.testclient import TestClient

from iluminaty import server
from iluminaty.intent import Intent
from iluminaty.resolver import ResolutionResult


class _PerceptionReadyStub:
    def get_readiness(self):
        return {
            "timestamp_ms": 10,
            "readiness": True,
            "uncertainty": 0.1,
            "reasons": ["ready_for_action"],
            "task_phase": "interaction",
            "active_surface": "editor",
            "risk_mode": "safe",
            "tick_id": 42,
            "staleness_ms": 10,
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        if context_tick_id is not None and int(context_tick_id) != 42:
            return {"allowed": False, "reason": "context_tick_mismatch", "latest_tick_id": 42, "staleness_ms": 10}
        if int(max_staleness_ms) < 10:
            return {"allowed": False, "reason": "context_stale", "latest_tick_id": 42, "staleness_ms": 10}
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 42, "staleness_ms": 10}

    def get_world_state(self):
        return {
            "tick_id": 42,
            "task_phase": "interaction",
            "active_surface": "editor",
            "staleness_ms": 10,
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
            params={},
            confidence=0.9,
            raw_input=instruction,
            category="normal",
        )


class _ResolverCaptureStub:
    def __init__(self):
        self.last_action = None
        self.last_params = None

    def resolve(self, action: str, params: dict):
        self.last_action = action
        self.last_params = dict(params)
        return ResolutionResult(
            action=action,
            method_used="mock",
            success=True,
            message="ok",
            attempts=[],
            total_ms=1.1,
        )


class _GroundingStub:
    def status(self):
        return {
            "enabled": True,
            "profile": "balanced",
            "mode": "hybrid_ui_text",
            "stats": {"resolves": 1, "success_rate_pct": 100.0, "blocked_rate_pct": 0.0, "avg_latency_ms": 2.0},
        }

    def resolve(
        self,
        *,
        query: str,
        role=None,
        monitor_id=None,
        mode="SAFE",
        category="normal",
        context_tick_id=None,
        max_staleness_ms=None,
        top_k=5,
    ):
        if query == "missing":
            return {
                "success": False,
                "blocked": True,
                "reason": "grounding_not_found",
                "target": None,
                "candidates": [],
                "world_ref": {"tick_id": 42, "staleness_ms": 10},
                "context_check": {"allowed": True, "reason": "fresh"},
            }
        return {
            "success": True,
            "blocked": False,
            "reason": "ok",
            "target": {
                "name": "Save",
                "role": "button",
                "center_xy": [320, 240],
                "bbox": {"x": 300, "y": 220, "w": 40, "h": 40},
                "confidence": 0.93,
                "tick_id": 42,
                "monitor_id": 1,
                "staleness_ms": 10,
                "evidence_refs": ["ui:save"],
            },
            "candidates": [
                {
                    "name": "Save",
                    "role": "button",
                    "center_xy": [320, 240],
                    "bbox": {"x": 300, "y": 220, "w": 40, "h": 40},
                    "confidence": 0.93,
                    "source": "hybrid",
                    "tick_id": 42,
                    "monitor_id": 1,
                    "staleness_ms": 10,
                    "evidence_refs": ["ui:save"],
                }
            ],
            "world_ref": {"tick_id": 42, "staleness_ms": 10},
            "context_check": {"allowed": True, "reason": "fresh"},
        }


def _setup_state():
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionReadyStub()
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverCaptureStub()
    server._state.verifier = None
    server._state.recovery = None
    server._state.audit = None
    server._state.autonomy = None
    server._state.grounding = _GroundingStub()
    return server._state.resolver


def test_grounding_status_and_resolve_endpoints():
    _setup_state()
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    status = client.get("/grounding/status")
    resolve = client.post("/grounding/resolve", json={"query": "save", "role": "button"})

    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert resolve.status_code == 200
    assert resolve.json()["success"] is True
    assert resolve.json()["target"]["center_xy"] == [320, 240]


def test_action_execute_with_grounding_injects_coordinates():
    resolver = _setup_state()
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    response = client.post(
        "/action/execute",
        json={
            "instruction": "click save",
            "mode": "SAFE",
            "use_grounding": True,
            "target_query": "save",
            "target_role": "button",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["result"]["success"] is True
    assert resolver.last_action == "click"
    assert resolver.last_params["x"] == 320
    assert resolver.last_params["y"] == 240


def test_action_precheck_with_grounding_reports_block():
    _setup_state()
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    response = client.post(
        "/action/precheck",
        json={
            "instruction": "click unknown",
            "mode": "SAFE",
            "use_grounding": True,
            "target_query": "missing",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["grounding_check"]["allowed"] is False
    assert payload["grounding_check"]["reason"] == "grounding_not_found"
