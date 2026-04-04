import time

from iluminaty.visual_engine import LocalSmolVLMProvider, VisualTask


def _task(priority: float, monitor: int = 1) -> VisualTask:
    return VisualTask(
        ref_id="ref_1",
        tick_id=1,
        timestamp_ms=1,
        monitor=monitor,
        frame_bytes=b"x",
        mime_type="image/webp",
        app_name="editor",
        window_title="main",
        ocr_text="save file",
        motion_summary="change",
        priority=priority,
    )


def test_vlm_scheduler_respects_min_interval():
    provider = LocalSmolVLMProvider(caption_enabled=False)
    provider._caption_enabled = True
    provider._status = "ready"
    provider._caption_backend = True
    provider._backend_mode = "mock"
    provider._caption_min_interval_ms = 1200
    provider._caption_keepalive_ms = 1200
    provider._caption_priority_threshold = 0.5

    calls = {"n": 0}

    def _fake_caption(_image_bytes: bytes) -> str:
        calls["n"] += 1
        return "mock caption"

    provider._caption = _fake_caption

    provider.analyze(_task(priority=0.9))
    provider.analyze(_task(priority=0.9))

    assert calls["n"] == 1


def test_vlm_scheduler_low_priority_requires_keepalive():
    provider = LocalSmolVLMProvider(caption_enabled=False)
    provider._caption_enabled = True
    provider._status = "ready"
    provider._caption_backend = True
    provider._backend_mode = "mock"
    provider._caption_min_interval_ms = 0
    provider._caption_keepalive_ms = 5000
    provider._caption_priority_threshold = 0.8

    calls = {"n": 0}

    def _fake_caption(_image_bytes: bytes) -> str:
        calls["n"] += 1
        return "mock caption"

    provider._caption = _fake_caption

    provider._last_caption_ms_by_monitor[1] = int(time.time() * 1000) - 1000
    provider.analyze(_task(priority=0.2))
    assert calls["n"] == 0

    provider._last_caption_ms_by_monitor[1] = int(time.time() * 1000) - 6000
    provider.analyze(_task(priority=0.2))
    assert calls["n"] == 1

