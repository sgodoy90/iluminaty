"""Tests for IPAEngine — full pipeline tests."""
import time
import numpy as np
import pytest
from unittest.mock import MagicMock
from PIL import Image

from ipa.types import PatchFrame, VisualContext, MotionField


class TestEngineWithMockEncoder:
    """Test engine pipeline using mocked encoder."""

    @pytest.fixture
    def mock_engine(self):
        from ipa.engine import IPAEngine

        engine = IPAEngine()

        # Mock the encoder — returns deterministic 64-dim vectors (imagehash level)
        mock_enc = MagicMock()
        mock_enc.dim       = 64
        mock_enc.n_patches = 1
        mock_enc.grid_size = 1
        mock_enc.is_loaded = True

        np.random.seed(42)
        base_patches = np.random.randn(1, 64).astype(np.float32)
        base_patches /= np.linalg.norm(base_patches) + 1e-8  # normalize

        mock_enc.encode_patches.return_value = base_patches
        mock_enc.encode_cls.return_value     = base_patches[0]
        mock_enc.encode_text.return_value    = base_patches[0]
        mock_enc.stats.return_value = {
            "loaded": True, "backend": "mock", "dim": 64
        }

        engine._encoder = mock_enc
        return engine

    def test_feed_first_frame_is_keyframe(self, mock_engine):
        img   = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        frame = mock_engine.feed(img)
        assert frame.frame_type == "I"

    def test_feed_second_frame_is_pframe(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        mock_engine.feed(img)
        frame2 = mock_engine.feed(img)
        assert frame2.frame_type == "P"

    def test_feed_window_change_triggers_keyframe(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        mock_engine.feed(img)
        frame2 = mock_engine.feed(img, metadata={"window_changed": True})
        assert frame2.frame_type == "I"

    def test_context_after_feed(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for _ in range(5):
            mock_engine.feed(img, metadata={"window_name": "Test"})
        ctx = mock_engine.context(seconds=30)
        assert isinstance(ctx, VisualContext)
        assert ctx.confidence > 0

    def test_motion_after_feed(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for _ in range(5):
            mock_engine.feed(img)
        motion = mock_engine.motion(seconds=5)
        assert isinstance(motion, MotionField)

    def test_context_to_text(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for i in range(10):
            mock_engine.feed(img, metadata={"window_name": f"App{i%2}"})
        ctx  = mock_engine.context()
        text = ctx.to_text()
        assert isinstance(text, str)
        assert "Scene:" in text

    def test_status(self, mock_engine):
        status = mock_engine.status()
        assert "encoder"    in status
        assert "compressor" in status
        assert "stream"     in status
        assert status["frame_count"] == 0

    def test_reset(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for _ in range(5):
            mock_engine.feed(img)
        assert mock_engine.status()["frame_count"] == 5
        mock_engine.reset()
        assert mock_engine.status()["frame_count"] == 0
        assert mock_engine.status()["stream"]["frames"] == 0

    def test_timeline(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for i in range(10):
            mock_engine.feed(img, metadata={"window_name": f"App{i//5}"})
        tl = mock_engine.timeline(seconds=60)
        assert isinstance(tl, list)

    def test_search_returns_list(self, mock_engine):
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        for _ in range(5):
            mock_engine.feed(img)
        query   = np.random.randn(64).astype(np.float32)
        results = mock_engine.search(query, top_k=3)
        assert isinstance(results, list)
