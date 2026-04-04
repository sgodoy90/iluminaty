"""Tests for VisualStream — ring buffer, motion, search, timeline."""
import time
import threading
import numpy as np
import pytest
from ipa.compressor import DeltaCompressor, _pack_bitmask
from ipa.stream import VisualStream, _classify_motion
from ipa.types import PatchFrame


@pytest.fixture
def comp():
    return DeltaCompressor(dim=64)


@pytest.fixture
def stream(comp):
    return VisualStream(max_frames=100, compressor=comp)


def _make_frame(comp, timestamp=None, n_changed=20, ftype="P", monitor_id=1, window="TestApp"):
    ts = timestamp or time.time()
    mask = np.zeros(196, dtype=bool)
    if n_changed > 0:
        mask[np.random.choice(196, min(n_changed, 196), replace=False)] = True
    delta = comp.compress_vectors(np.random.randn(max(n_changed, 1), 64).astype(np.float32) * 0.1)
    cls = comp.compress_vectors(np.random.randn(1, 64).astype(np.float32))
    return PatchFrame(
        timestamp=ts,
        frame_type=ftype,
        patch_grid=delta,
        change_mask=_pack_bitmask(mask),
        motion_vectors=b"",
        cls_embedding=cls,
        n_changed=n_changed,
        metadata={"monitor_id": monitor_id, "window_name": window, "scene_hint": "test"},
    )


class TestPush:
    def test_push_single(self, stream, comp):
        frame = _make_frame(comp)
        stream.push(frame)
        assert stream.stats()["frames"] == 1

    def test_push_eviction(self, comp):
        s = VisualStream(max_frames=5, compressor=comp)
        for _ in range(10):
            s.push(_make_frame(comp))
        assert s.stats()["frames"] == 5

    def test_push_counts(self, stream, comp):
        stream.push(_make_frame(comp, ftype="I"))
        stream.push(_make_frame(comp, ftype="P"))
        stream.push(_make_frame(comp, ftype="P"))
        stats = stream.stats()
        assert stats["i_frames"] == 1
        assert stats["p_frames"] == 2


class TestMotion:
    def test_static(self):
        masks = [np.zeros(196, dtype=np.float32) for _ in range(10)]
        motion = _classify_motion(masks)
        assert motion.motion_type == "static"

    def test_empty(self):
        motion = _classify_motion([])
        assert motion.motion_type == "static"

    def test_video_detection(self):
        masks = [np.zeros(196, dtype=np.float32) for _ in range(10)]
        for m in masks:
            m[np.random.choice(196, 140, replace=False)] = 1.0
        motion = _classify_motion(masks)
        assert motion.motion_type == "video"

    def test_cursor_detection(self):
        masks = []
        for i in range(10):
            m = np.zeros(196, dtype=np.float32)
            # Tight cluster: 2 patches in a 2x1 area
            m[50] = 1.0
            m[51] = 1.0
            masks.append(m)
        motion = _classify_motion(masks)
        assert motion.motion_type == "cursor", f"Expected cursor, got {motion.motion_type}"

    def test_stream_motion(self, stream, comp):
        for _ in range(10):
            stream.push(_make_frame(comp, n_changed=5))
        motion = stream.get_motion(seconds=10)
        assert motion.motion_type != ""


class TestSearch:
    def test_search_returns_results(self, stream, comp):
        for _ in range(10):
            stream.push(_make_frame(comp))
        query = np.random.randn(64).astype(np.float32)
        results = stream.search(query, top_k=3)
        assert len(results) <= 3
        # Results should be sorted by similarity (descending)
        if len(results) >= 2:
            assert results[0].similarity >= results[1].similarity

    def test_search_empty_buffer(self, stream):
        query = np.random.randn(64).astype(np.float32)
        results = stream.search(query)
        assert results == []


class TestTimeline:
    def test_timeline_clusters(self, stream, comp):
        # Push frames from different windows
        now = time.time()
        for i in range(5):
            stream.push(_make_frame(comp, timestamp=now - 10 + i, window="VSCode"))
        for i in range(5):
            stream.push(_make_frame(comp, timestamp=now - 5 + i, window="Chrome"))

        tl = stream.get_timeline(seconds=20)
        assert len(tl) >= 2
        assert tl[0].window_name == "VSCode"
        assert tl[1].window_name == "Chrome"


class TestContext:
    def test_empty_context(self, stream):
        ctx = stream.get_context()
        assert ctx.scene_state in ("empty", "no_data")

    def test_context_with_frames(self, stream, comp):
        for _ in range(20):
            stream.push(_make_frame(comp))
        ctx = stream.get_context(seconds=30)
        assert ctx.confidence > 0
        assert ctx.token_estimate > 0

    def test_context_text(self, stream, comp):
        for _ in range(10):
            stream.push(_make_frame(comp))
        ctx = stream.get_context()
        text = ctx.to_text()
        assert isinstance(text, str)
        assert len(text) > 0


class TestThreadSafety:
    def test_concurrent_push(self, comp):
        s = VisualStream(max_frames=1000, compressor=comp)
        errors = []

        def pusher():
            try:
                for _ in range(100):
                    s.push(_make_frame(comp))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=pusher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert s.stats()["frames"] <= 1000


class TestMemory:
    def test_memory_bytes(self, stream, comp):
        for _ in range(50):
            stream.push(_make_frame(comp))
        mem = stream.memory_bytes
        assert mem > 0
        # 50 frames should be well under 1 MB
        assert mem < 1024 * 1024
