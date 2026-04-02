from iluminaty import server
from iluminaty.ring_buffer import RingBuffer


def test_latest_slot_for_monitor_does_not_fallback_to_global():
    original_buffer = server._state.buffer
    try:
        buf = RingBuffer(max_seconds=5, target_fps=1.0)
        buf.push(b"a", 10, 10, monitor_id=1, skip_if_unchanged=False)
        buf.push(b"b", 10, 10, monitor_id=2, skip_if_unchanged=False)
        server._state.buffer = buf

        slot, resolved_mid = server._latest_slot_for_monitor(3)
        assert slot is None
        assert resolved_mid == 3
    finally:
        server._state.buffer = original_buffer


def test_latest_slot_for_monitor_returns_requested_monitor_when_available():
    original_buffer = server._state.buffer
    try:
        buf = RingBuffer(max_seconds=5, target_fps=1.0)
        buf.push(b"a", 10, 10, monitor_id=1, skip_if_unchanged=False)
        buf.push(b"b", 10, 10, monitor_id=2, skip_if_unchanged=False)
        server._state.buffer = buf

        slot, resolved_mid = server._latest_slot_for_monitor(2)
        assert slot is not None
        assert getattr(slot, "monitor_id", None) == 2
        assert resolved_mid == 2
    finally:
        server._state.buffer = original_buffer

