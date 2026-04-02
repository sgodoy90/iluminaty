import time

from iluminaty.cursor_tracker import CursorTracker


class _ActionsStub:
    def __init__(self):
        self._x = 100
        self._y = 200

    def get_mouse_position(self):
        self._x += 1
        self._y += 2
        return {"x": self._x, "y": self._y}


def test_cursor_tracker_sampling_loop():
    tracker = CursorTracker(actions=_ActionsStub(), poll_ms=10, max_history=32)
    tracker.start()
    time.sleep(0.08)
    tracker.stop()
    status = tracker.status()
    assert status["samples"] >= 3
    assert status["running"] is False
    cursor = status["cursor"]
    assert int(cursor["x"]) > 100
    assert int(cursor["y"]) > 200


def test_cursor_tracker_sample_once_without_actions():
    tracker = CursorTracker(actions=None, poll_ms=20)
    snap = tracker.sample_once()
    assert "x" in snap and "y" in snap
    assert isinstance(snap["timestamp_ms"], int)
