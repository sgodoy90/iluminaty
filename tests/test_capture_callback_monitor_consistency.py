from types import SimpleNamespace

from iluminaty.capture import CaptureConfig, ScreenCapture


class _BufferStub:
    def __init__(self, *, monitor_slot=None, global_slot=None, raise_on_monitor=False):
        self._monitor_slot = monitor_slot
        self._global_slot = global_slot
        self._raise_on_monitor = bool(raise_on_monitor)

    def get_latest_for_monitor(self, monitor_id: int):
        _ = monitor_id
        if self._raise_on_monitor:
            raise RuntimeError("monitor fetch failed")
        return self._monitor_slot

    def get_latest(self):
        return self._global_slot


def test_callback_slot_strict_for_pinned_monitor_without_fallback():
    monitor_slot = None
    global_slot = SimpleNamespace(monitor_id=9)
    capture = ScreenCapture(
        buffer=_BufferStub(monitor_slot=monitor_slot, global_slot=global_slot),
        config=CaptureConfig(monitor=2),
    )
    slot = capture._latest_slot_for_callback()
    assert slot is None


def test_callback_slot_uses_requested_monitor_when_available():
    monitor_slot = SimpleNamespace(monitor_id=2)
    global_slot = SimpleNamespace(monitor_id=9)
    capture = ScreenCapture(
        buffer=_BufferStub(monitor_slot=monitor_slot, global_slot=global_slot),
        config=CaptureConfig(monitor=2),
    )
    slot = capture._latest_slot_for_callback()
    assert slot is not None
    assert int(getattr(slot, "monitor_id", 0)) == 2


def test_callback_slot_falls_back_to_global_in_auto_monitor_mode():
    monitor_slot = None
    global_slot = SimpleNamespace(monitor_id=0)
    capture = ScreenCapture(
        buffer=_BufferStub(monitor_slot=monitor_slot, global_slot=global_slot),
        config=CaptureConfig(monitor=0),
    )
    slot = capture._latest_slot_for_callback()
    assert slot is not None
    assert int(getattr(slot, "monitor_id", -1)) == 0
