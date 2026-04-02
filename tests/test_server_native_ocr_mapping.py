from types import SimpleNamespace

from iluminaty import server


def test_map_slot_region_to_monitor_native_scales_coordinates():
    nx, ny, nw, nh = server._map_slot_region_to_monitor_native(
        slot_width=1280,
        slot_height=720,
        monitor_width=2560,
        monitor_height=1440,
        region_x=100,
        region_y=50,
        region_w=200,
        region_h=100,
    )
    assert (nx, ny, nw, nh) == (200, 100, 400, 200)


def test_map_slot_region_to_monitor_native_clamps_to_bounds():
    nx, ny, nw, nh = server._map_slot_region_to_monitor_native(
        slot_width=1280,
        slot_height=720,
        monitor_width=1920,
        monitor_height=1080,
        region_x=1200,
        region_y=680,
        region_w=200,
        region_h=200,
    )
    assert nx >= 0 and ny >= 0
    assert nx + nw <= 1920
    assert ny + nh <= 1080
    assert nw >= 1 and nh >= 1


def test_native_capture_region_from_slot_uses_mapped_desktop_rect(monkeypatch):
    slot = SimpleNamespace(width=1280, height=720)

    monkeypatch.setattr(
        server,
        "_monitor_geometry",
        lambda monitor_id: {"id": int(monitor_id), "left": 100, "top": 50, "width": 2560, "height": 1440},
    )

    calls = {}

    def _fake_capture(left: int, top: int, width: int, height: int):
        calls["rect"] = (left, top, width, height)
        return b"native-bytes"

    monkeypatch.setattr(server, "_native_capture_rect_bytes", _fake_capture)

    payload, mapping = server._native_capture_region_from_slot(
        slot=slot,
        monitor_id=2,
        region_x=100,
        region_y=50,
        region_w=200,
        region_h=100,
    )

    assert payload == b"native-bytes"
    assert mapping is not None
    # Region doubled from 1280x720 to 2560x1440 + monitor desktop offset (100,50)
    assert calls["rect"] == (300, 150, 400, 200)
    assert mapping["native_monitor_region"] == {"x": 200, "y": 100, "w": 400, "h": 200}
    assert mapping["native_desktop_region"] == {"x": 300, "y": 150, "w": 400, "h": 200}

