from fastapi.testclient import TestClient

from iluminaty import server
from iluminaty.intent import Intent
from iluminaty.resolver import ResolutionResult


class _PerceptionStub:
    def __init__(self, ready: bool, safe_staleness_ms: int = 1500):
        self._ready = ready
        self._safe_staleness_ms = int(safe_staleness_ms)

    def get_readiness(self):
        return {
            "timestamp_ms": 1,
            "readiness": self._ready,
            "uncertainty": 0.8 if not self._ready else 0.1,
            "reasons": ["high_uncertainty"] if not self._ready else ["ready_for_action"],
            "task_phase": "loading" if not self._ready else "interaction",
            "active_surface": "editor",
            "risk_mode": "safe",
            "tick_id": 7,
            "staleness_ms": 20,
            "domain_pack": "general",
            "domain_confidence": 0.0,
            "domain_policy": {
                "max_staleness_ms": {
                    "safe": self._safe_staleness_ms,
                    "hybrid": self._safe_staleness_ms,
                    "raw": 4000,
                },
            },
        }

    def record_action_feedback(self, action: str, success: bool, message: str = ""):
        return None

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        if context_tick_id is not None and int(context_tick_id) != 7:
            return {"allowed": False, "reason": "context_tick_mismatch", "latest_tick_id": 7, "staleness_ms": 20}
        if int(max_staleness_ms) < 20:
            return {"allowed": False, "reason": "context_stale", "latest_tick_id": 7, "staleness_ms": 20}
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 7, "staleness_ms": 20}


class _PerceptionBusyArbiterStub(_PerceptionStub):
    def register_worker_intent(self, intent: dict, source: str = "api"):
        return {"intent_id": "intent_busy_1", "action": intent.get("action", "unknown"), "source": source}

    def claim_action_lease(self, owner: str, ttl_ms=None, force: bool = False):
        _ = owner
        _ = ttl_ms
        _ = force
        return {
            "granted": False,
            "owner": "server-action-executor",
            "held_by": "another-worker",
            "reason": "arbiter_busy",
        }

    def release_action_lease(self, owner: str, success: bool, message: str = ""):
        _ = owner
        _ = success
        _ = message
        return {"released": True}

    def record_worker_verification(self, **kwargs):
        _ = kwargs
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


def _setup_state(perception_ready: bool, safe_staleness_ms: int = 1500):
    server._state.api_key = None
    server._state.perception = _PerceptionStub(ready=perception_ready, safe_staleness_ms=safe_staleness_ms)
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverStub()
    server._state.verifier = None
    server._state.recovery = None
    server._state.audit = None
    server._state.autonomy = None


def _setup_state_with_perception(perception):
    server._state.api_key = None
    server._state.perception = perception
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


def test_execute_blocks_when_context_stale_in_safe_mode():
    _setup_state(perception_ready=True)
    client = TestClient(server.app)
    response = client.post(
        "/action/execute",
        json={
            "instruction": "click save",
            "mode": "SAFE",
            "context_tick_id": 7,
            "max_staleness_ms": 5,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["result"]["success"] is False
    assert payload["precheck"]["context_check"]["reason"] == "context_stale"


def test_precheck_uses_domain_policy_staleness_when_not_provided():
    _setup_state(perception_ready=True, safe_staleness_ms=10)
    client = TestClient(server.app)
    response = client.post(
        "/action/precheck",
        json={
            "instruction": "click save",
            "mode": "SAFE",
            "context_tick_id": 7,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["max_staleness_ms"] == 10
    assert payload["context_check"]["reason"] == "context_stale"


def test_execute_returns_arbiter_busy_when_worker_lease_denied():
    _setup_state_with_perception(_PerceptionBusyArbiterStub(ready=True))
    client = TestClient(server.app)
    response = client.post(
        "/action/execute",
        json={
            "instruction": "click save",
            "mode": "SAFE",
            "context_tick_id": 7,
            "max_staleness_ms": 1500,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["result"]["success"] is False
    assert payload["result"]["method_used"] == "arbiter"
    assert payload["result"]["message"] == "arbiter_busy"


def test_precheck_blocks_click_target_outside_monitor(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "params": {"x": 99999, "y": 20},
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["target_check"]["reason"] == "target_out_of_bounds"


def test_precheck_blocks_when_cursor_drift_exceeded(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_cursor_snapshot",
        lambda: {"x": 300, "y": 300, "timestamp_ms": 1, "source": "tracker"},
    )

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "params": {
                "x": 100,
                "y": 120,
                "expected_cursor_x": 100,
                "expected_cursor_y": 120,
                "max_cursor_drift_px": 12,
            },
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["cursor_drift_check"]["reason"] == "cursor_drift_exceeded"


def test_precheck_allows_when_cursor_unavailable(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_cursor_snapshot",
        lambda: {"x": 0, "y": 0, "timestamp_ms": 1, "source": "none"},
    )

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "params": {
                "x": 100,
                "y": 120,
                "expected_cursor_x": 100,
                "expected_cursor_y": 120,
            },
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["cursor_drift_check"]["allowed"] is True
    assert payload["cursor_drift_check"]["reason"] == "cursor_unavailable"
    assert payload["blocked"] is False


def test_execute_reports_cursor_drift_block_reason(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_cursor_snapshot",
        lambda: {"x": 999, "y": 999, "timestamp_ms": 1, "source": "tracker"},
    )

    response = client.post(
        "/action/execute",
        json={
            "action": "click",
            "params": {
                "x": 100,
                "y": 120,
                "expected_cursor_x": 100,
                "expected_cursor_y": 120,
                "max_cursor_drift_px": 10,
            },
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["result"]["success"] is False
    assert payload["result"]["message"] == "cursor_drift_exceeded"


def test_precheck_blocks_high_risk_when_orientation_window_unknown(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_active_window_snapshot",
        lambda: {"handle": None, "title": "", "monitor_id": None},
    )
    monkeypatch.setattr(server, "_resolve_active_monitor_id", lambda: None)

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "category": "destructive",
            "params": {"x": 100, "y": 80},
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["orientation_check"]["reason"] == "orientation_active_window_unknown"


def test_precheck_blocks_high_risk_when_active_and_target_monitor_mismatch(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            },
            {
                "id": 2,
                "left": 1920,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 3840,
                "bottom": 1080,
                "is_primary": False,
                "is_active": False,
            },
        ],
    )
    monkeypatch.setattr(
        server,
        "_active_window_snapshot",
        lambda: {"handle": 123, "title": "Editor", "monitor_id": 1},
    )

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "category": "destructive",
            "params": {"x": 2400, "y": 160},
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["orientation_check"]["reason"] == "orientation_monitor_mismatch"


def test_precheck_skips_orientation_for_normal_actions(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_active_window_snapshot",
        lambda: {"handle": None, "title": "", "monitor_id": None},
    )
    monkeypatch.setattr(server, "_resolve_active_monitor_id", lambda: None)

    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "category": "normal",
            "params": {"x": 100, "y": 80},
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["orientation_check"]["applies"] is False
    assert payload["blocked"] is False


def test_precheck_includes_navigation_cycle_contract():
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    response = client.post(
        "/action/precheck",
        json={"instruction": "click save", "mode": "SAFE"},
    )
    payload = response.json()
    cycle = payload["navigation_cycle"]

    assert response.status_code == 200
    assert cycle["phase_order"] == ["orient", "locate", "focus", "read", "act", "verify"]
    assert cycle["focus"]["state"] == "pending"
    assert cycle["verify"]["state"] == "pending"
    assert cycle["act"]["state"] in {"ready", "blocked"}


def test_execute_includes_navigation_cycle_contract():
    _setup_state(perception_ready=True)
    client = TestClient(server.app)

    response = client.post(
        "/action/execute",
        json={"instruction": "click save", "mode": "SAFE"},
    )
    payload = response.json()
    cycle = payload["navigation_cycle"]

    assert response.status_code == 200
    assert payload["result"]["success"] is True
    assert cycle["phase_order"] == ["orient", "locate", "focus", "read", "act", "verify"]
    assert cycle["act"]["state"] == "ok"
    assert cycle["verify"]["state"] in {"ok", "failed", "skipped"}


def test_precheck_blocks_when_ui_semantics_denies_target(monkeypatch):
    _setup_state(perception_ready=True)
    client = TestClient(server.app)
    monkeypatch.setattr(
        server,
        "_ui_semantics_check",
        lambda intent, mode, task_phase=None: {
            "allowed": False,
            "applies": True,
            "reason": "target_not_interactable",
        },
    )
    monkeypatch.setattr(
        server,
        "_monitor_layout_snapshot",
        lambda: [
            {
                "id": 1,
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080,
                "right": 1920,
                "bottom": 1080,
                "is_primary": True,
                "is_active": True,
            }
        ],
    )
    response = client.post(
        "/action/precheck",
        json={
            "action": "click",
            "params": {"x": 100, "y": 100},
            "mode": "SAFE",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["ui_semantics_check"]["reason"] == "target_not_interactable"
