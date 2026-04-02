import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace

from iluminaty.perception import PerceptionEngine


@dataclass
class _ActiveMonitor:
    id: int


class _MonitorManagerStub:
    def __init__(self, active_id: int):
        self.active_id = int(active_id)

    def detect_active_from_window(self, window_bounds: dict) -> int:
        _ = window_bounds
        return self.active_id

    def get_active_monitor(self):
        return _ActiveMonitor(id=self.active_id)


class _BufferStub:
    def __init__(self):
        self._base = time.time()
        self._step = 0

    def get_latest_per_monitor(self):
        self._step += 1
        now = self._base + (self._step * 0.01)
        return {
            1: SimpleNamespace(
                timestamp=now,
                frame_bytes=b"m1",
                mime_type="image/webp",
                change_score=0.5,
                monitor_id=1,
                width=640,
                height=360,
            ),
            2: SimpleNamespace(
                timestamp=now,
                frame_bytes=b"m2",
                mime_type="image/webp",
                change_score=0.5,
                monitor_id=2,
                width=640,
                height=360,
            ),
            3: SimpleNamespace(
                timestamp=now,
                frame_bytes=b"m3",
                mime_type="image/webp",
                change_score=0.5,
                monitor_id=3,
                width=640,
                height=360,
            ),
        }


class _VisualStub:
    def __init__(self):
        self.tasks = []

    def enqueue(self, task):
        self.tasks.append(task)
        return True

    def stats(self):
        return {"provider": "stub", "processed": len(self.tasks)}


def test_deep_loop_prioritizes_active_monitor_with_secondary_presence():
    engine = PerceptionEngine(
        buffer=_BufferStub(),
        monitor_mgr=_MonitorManagerStub(active_id=2),
        deep_loop_hz=2.0,
        fast_loop_hz=8.0,
    )
    engine._visual = _VisualStub()
    engine._deep_loop_interval = 0.05
    engine._secondary_heartbeat_ms = 250
    engine._deep_loop_stats["secondary_heartbeat_ms"] = 250
    engine._last_window_info = {
        "bounds": {"left": 100, "top": 100, "width": 600, "height": 400}
    }

    engine._running = True
    worker = threading.Thread(target=engine._deep_loop, daemon=True)
    worker.start()
    time.sleep(0.4)
    engine._running = False
    worker.join(timeout=1.0)

    state = engine.get_state()
    deep = state["deep_loop"]

    assert deep["active_monitor_id"] == 2
    assert deep["enqueued_active"] > 0
    assert deep["enqueued_secondary"] > 0
    assert deep["skipped_inactive"] > 0
    assert deep["enqueued_active"] >= deep["enqueued_secondary"]
    assert deep["per_monitor"]["2"]["enqueued_active"] > 0


def test_resolve_active_monitor_falls_back_to_last_known():
    class _FlakyMonitorManager:
        def __init__(self):
            self._active = 3
            self.detect_ok = True
            self.get_ok = True

        def detect_active_from_window(self, window_bounds: dict) -> int:
            _ = window_bounds
            if not self.detect_ok:
                raise RuntimeError("detect failed")
            return self._active

        def get_active_monitor(self):
            if not self.get_ok:
                raise RuntimeError("get failed")
            return _ActiveMonitor(id=self._active)

    mgr = _FlakyMonitorManager()
    engine = PerceptionEngine(buffer=_BufferStub(), monitor_mgr=mgr)
    engine._last_window_info = {
        "bounds": {"left": 10, "top": 10, "width": 10, "height": 10}
    }

    first = engine._resolve_active_vlm_monitor()
    assert first == 3

    mgr.detect_ok = False
    mgr.get_ok = False
    second = engine._resolve_active_vlm_monitor()
    assert second == 3
