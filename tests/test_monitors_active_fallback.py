from iluminaty.monitors import MonitorInfo, MonitorManager


def test_detect_active_from_window_without_bounds_keeps_last_known_monitor():
    mgr = MonitorManager()
    mgr._monitors = [
        MonitorInfo(id=2, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(id=3, left=1920, top=0, width=1920, height=1080, is_primary=False),
    ]
    mgr._active_monitor_id = 3

    resolved = mgr.detect_active_from_window({})
    assert resolved == 3


def test_detect_active_from_window_without_bounds_falls_back_to_first_when_last_missing():
    mgr = MonitorManager()
    mgr._monitors = [
        MonitorInfo(id=4, left=0, top=0, width=1920, height=1080, is_primary=True),
        MonitorInfo(id=5, left=1920, top=0, width=1920, height=1080, is_primary=False),
    ]
    mgr._active_monitor_id = 999

    resolved = mgr.detect_active_from_window({})
    assert resolved == 4

