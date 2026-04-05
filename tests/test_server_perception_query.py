from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionQueryStub:
    def query_visual(self, question: str, at_ms=None, window_seconds: float = 30, monitor_id=None):
        return {
            "answer": f"stub:{question}",
            "confidence": 0.77,
            "evidence_refs": ["fr_1"],
            "frame_refs": [{"ref_id": "fr_1", "timestamp_ms": 1}],
            "source": "stub",
            "timestamp_ms": 1,
            "tick_id": 2,
            "monitor": monitor_id or 0,
        }


def test_perception_query_endpoint():
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionQueryStub()
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    response = client.post(
        "/perception/query",
        json={"question": "que paso", "window_seconds": 10},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["answer"].startswith("stub:")
    assert payload["confidence"] == 0.77
