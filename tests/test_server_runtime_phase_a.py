from fastapi.testclient import TestClient

from iluminaty import server


class _CursorStub:
    def status(self):
        return {
            "running": True,
            "poll_ms": 20,
            "samples": 10,
            "errors": 0,
            "cursor": {"x": 10, "y": 20, "timestamp_ms": 1},
            "staleness_ms": 5,
        }


class _WatcherStub:
    def stats(self):
        return {
            "calls": 3,
            "completed": 2,
            "timeouts": 1,
            "last_reason": "timeout",
            "avg_wait_ms": 120.0,
        }


def test_runtime_phase_a_endpoints():
    original_api_key = server._state.api_key
    original_cursor = server._state.cursor_tracker
    original_watcher = server._state.action_watcher
    try:
        server._state.api_key = None
        server._state.cursor_tracker = _CursorStub()
        server._state.action_watcher = _WatcherStub()
        client = TestClient(server.app)

        cursor = client.get("/runtime/cursor")
        assert cursor.status_code == 200
        assert cursor.json()["running"] is True

        watcher = client.get("/runtime/action-watcher")
        assert watcher.status_code == 200
        assert watcher.json()["calls"] == 3
    finally:
        server._state.api_key = original_api_key
        server._state.cursor_tracker = original_cursor
        server._state.action_watcher = original_watcher
