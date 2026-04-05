from fastapi.testclient import TestClient

from iluminaty import server
from iluminaty.audio import AudioInterruptDetector
from iluminaty.intent import Intent


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
            "staleness_ms": 10,
            "domain_policy": {"max_staleness_ms": {"safe": 1500, "hybrid": 1500, "raw": 4000}},
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        _ = context_tick_id
        _ = max_staleness_ms
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 7, "staleness_ms": 10}


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
            params={"x": 10, "y": 10},
            confidence=0.9,
            raw_input="click",
            category="normal",
        )


def _setup_state():
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionReadyStub()
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.audio_interrupt = AudioInterruptDetector(hold_ms=3000)
    server._state.runtime_profile = "standard"


def test_precheck_blocks_when_audio_interrupt_active():
    _setup_state()
    server._state.audio_interrupt.ingest_transcript("stop", source="test")
    client = TestClient(server.app, headers={"x-api-key": "test-key"})
    response = client.post("/action/precheck", json={"instruction": "click save", "mode": "SAFE"})
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is True
    assert payload["audio_interrupt_check"]["reason"] == "audio_interrupt_blocked"


def test_precheck_raw_skips_audio_interrupt_block():
    _setup_state()
    server._state.audio_interrupt.ingest_transcript("stop", source="test")
    client = TestClient(server.app, headers={"x-api-key": "test-key"})
    response = client.post("/action/precheck", json={"instruction": "click save", "mode": "RAW"})
    payload = response.json()
    assert response.status_code == 200
    assert payload["blocked"] is False
    assert payload["audio_interrupt_check"]["reason"] == "skipped"


def test_audio_interrupt_status_and_ack_endpoints():
    _setup_state()
    server._state.audio_interrupt.ingest_transcript("stop", source="test")
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    status = client.get("/audio/interrupt/status")
    assert status.status_code == 200
    assert status.json()["blocked"] is True

    ack = client.post("/audio/interrupt/ack")
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] is True
