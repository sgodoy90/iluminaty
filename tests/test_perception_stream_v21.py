from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionStreamStub:
    def __init__(self):
        self._ts = 1000

    def get_world_state(self):
        self._ts += 1
        return {
            "timestamp_ms": self._ts,
            "tick_id": self._ts,
            "task_phase": "interaction",
            "active_surface": "editor",
            "readiness": True,
            "uncertainty": 0.1,
            "risk_mode": "safe",
        }

    def get_readiness(self):
        return {
            "timestamp_ms": self._ts,
            "tick_id": self._ts,
            "readiness": True,
            "uncertainty": 0.1,
            "reasons": ["ready_for_action"],
            "task_phase": "interaction",
            "active_surface": "editor",
            "risk_mode": "safe",
            "staleness_ms": 2,
        }

    def get_events(self, last_seconds: float = 3, min_importance: float = 0.1):
        return []

    def get_visual_facts_delta(self, since_ms: int, monitor_id=None):
        return [{
            "kind": "surface",
            "text": "editor visible",
            "confidence": 0.8,
            "monitor": 1,
            "timestamp_ms": self._ts,
            "source": "test",
            "evidence_ref": "fr_test",
        }]


def test_perception_stream_includes_tick_and_visual_delta():
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionStreamStub()

    client = TestClient(server.app)
    with client.websocket_connect("/perception/stream?interval_ms=100&include_events=false&token=test-key") as ws:
        payload = ws.receive_json()

    assert payload["type"] == "perception_world"
    assert "tick_id" in payload
    assert "world" in payload
    assert "readiness" in payload
    assert "visual_facts_delta" in payload
