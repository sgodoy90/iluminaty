import time

from iluminaty import perception
from iluminaty.perception import MonitorPerceptionState, PerceptionEngine


def test_inactive_monitor_skip_policy_respects_config():
    state = MonitorPerceptionState(
        monitor_id=2,
        inactive_skip_every=4,
        inactive_force_interval_s=30.0,
    )
    state.last_analyzed = time.time()

    assert state.should_analyze(False) is False
    assert state.should_analyze(False) is False
    assert state.should_analyze(False) is False
    assert state.should_analyze(False) is True


def test_active_monitor_resets_skip_counter():
    state = MonitorPerceptionState(
        monitor_id=1,
        inactive_skip_every=5,
        inactive_force_interval_s=30.0,
    )
    state.frame_skip_counter = 4

    assert state.should_analyze(True) is True
    assert state.frame_skip_counter == 0


def test_check_window_is_throttled(monkeypatch):
    monkeypatch.setattr(perception, "HAS_VISION", False)
    engine = PerceptionEngine(buffer=None)
    engine._window_probe_min_interval = 0.5

    calls = {"n": 0}

    def _fake_win():
        calls["n"] += 1
        return {
            "name": "Codex",
            "app_name": "Codex",
            "window_title": "Editor",
            "title": "Editor",
            "bounds": {},
            "pid": 123,
        }

    monkeypatch.setattr(perception, "HAS_VISION", True)
    monkeypatch.setattr(perception, "get_active_window_info", _fake_win)

    changed_1, _ = engine._check_window(monitor=1)
    changed_2, _ = engine._check_window(monitor=1)

    assert changed_1 is True
    assert changed_2 is False
    assert calls["n"] == 1


def test_check_phash_fallback_is_neutral(monkeypatch):
    monkeypatch.setattr(perception, "HAS_VISION", False)
    engine = PerceptionEngine(buffer=None)
    mon_state = MonitorPerceptionState(monitor_id=1)

    monkeypatch.setattr(perception, "HAS_IMAGEHASH", False)
    assert engine._check_phash(mon_state, b"dummy-bytes") == 0
