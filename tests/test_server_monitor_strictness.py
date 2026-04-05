from fastapi.testclient import TestClient

from iluminaty import server


def test_vision_snapshot_returns_monitor_specific_not_available_error(monkeypatch):
    original_api_key = server._state.api_key
    original_buffer = server._state.buffer
    original_vision = server._state.vision
    try:
        server._state.api_key = "test-key"
        server._state.buffer = object()
        server._state.vision = object()
        monkeypatch.setattr(server, "_latest_slot_for_monitor", lambda monitor_id=None: (None, monitor_id))
        client = TestClient(server.app, headers={"x-api-key": "test-key"})

        response = client.get("/vision/snapshot", params={"monitor_id": 4})
        payload = response.json()

        assert response.status_code == 404
        assert payload["detail"]["error"] == "monitor_frame_not_available"
        assert int(payload["detail"]["monitor_id"]) == 4
    finally:
        server._state.api_key = original_api_key
        server._state.buffer = original_buffer
        server._state.vision = original_vision


def test_vision_snapshot_returns_generic_no_frames_error_when_monitor_not_requested(monkeypatch):
    original_api_key = server._state.api_key
    original_buffer = server._state.buffer
    original_vision = server._state.vision
    try:
        server._state.api_key = "test-key"
        server._state.buffer = object()
        server._state.vision = object()
        monkeypatch.setattr(server, "_latest_slot_for_monitor", lambda monitor_id=None: (None, None))
        client = TestClient(server.app, headers={"x-api-key": "test-key"})

        response = client.get("/vision/snapshot")
        payload = response.json()

        assert response.status_code == 404
        assert payload["detail"]["error"] == "no_frames_in_buffer"
    finally:
        server._state.api_key = original_api_key
        server._state.buffer = original_buffer
        server._state.vision = original_vision
