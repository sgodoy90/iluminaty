import threading
import time
from types import SimpleNamespace

from iluminaty.action_watchers import ActionCompletionWatcher


class _BufferStub:
    def __init__(self):
        self._slot = SimpleNamespace(timestamp=time.time(), change_score=0.0)
        self._lock = threading.Lock()

    def set_slot(self, change_score: float):
        with self._lock:
            self._slot = SimpleNamespace(timestamp=time.time(), change_score=float(change_score))

    def get_latest(self):
        with self._lock:
            return self._slot

    def get_latest_for_monitor(self, monitor_id: int):
        _ = monitor_id
        return self.get_latest()


def test_action_watcher_settles_after_change():
    buf = _BufferStub()
    watcher = ActionCompletionWatcher(buffer=buf)
    since = buf.get_latest().timestamp

    def producer():
        time.sleep(0.05)
        buf.set_slot(0.5)   # changed
        time.sleep(0.06)
        buf.set_slot(0.0)   # settled

    t = threading.Thread(target=producer, daemon=True)
    t.start()
    result = watcher.wait_for_settle(
        monitor_id=1,
        since_timestamp=since,
        timeout_ms=1000,
        settle_ms=40,
        poll_ms=10,
        change_threshold=0.1,
    )
    t.join(timeout=1)

    assert result["completed"] is True
    assert result["reason"] in {"settled_after_change", "no_change_observed"}
    stats = watcher.stats()
    assert stats["calls"] >= 1


def test_action_watcher_returns_fast_when_no_change():
    buf = _BufferStub()
    watcher = ActionCompletionWatcher(buffer=buf)
    since = buf.get_latest().timestamp
    result = watcher.wait_for_settle(
        monitor_id=1,
        since_timestamp=since,
        timeout_ms=800,
        settle_ms=120,
        poll_ms=10,
        idle_grace_ms=120,
    )
    assert result["completed"] is True
    assert result["reason"] == "no_change_observed"
