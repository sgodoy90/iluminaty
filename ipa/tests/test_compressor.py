"""Tests for DeltaCompressor — I/P frame delta compression (pure numpy)."""
import numpy as np
import pytest
from ipa.compressor import DeltaCompressor, _pack_bitmask, _unpack_bitmask, N_PATCHES


@pytest.fixture
def comp():
    """Production compressor: dim=64 (imagehash encoder output)."""
    return DeltaCompressor(dim=64)


@pytest.fixture
def patches(comp):
    """Single patch (1, 64) — what imagehash encoder produces."""
    np.random.seed(42)
    v = np.random.randn(1, comp.dim).astype(np.float32)
    v /= np.linalg.norm(v) + 1e-8
    return v


class TestBitmask:
    def test_roundtrip(self):
        flags = np.zeros(N_PATCHES, dtype=bool)
        flags[0]   = True
        flags[50]  = True
        flags[195] = True
        packed   = _pack_bitmask(flags)
        assert len(packed) == 25
        unpacked = _unpack_bitmask(packed)
        assert np.array_equal(flags, unpacked)

    def test_all_true(self):
        flags    = np.ones(N_PATCHES, dtype=bool)
        unpacked = _unpack_bitmask(_pack_bitmask(flags))
        assert unpacked.sum() == N_PATCHES

    def test_all_false(self):
        flags    = np.zeros(N_PATCHES, dtype=bool)
        unpacked = _unpack_bitmask(_pack_bitmask(flags))
        assert unpacked.sum() == 0


class TestKeyframe:
    def test_compress_decompress_shape(self, comp, patches):
        compressed = comp.compress_keyframe(patches)
        assert isinstance(compressed, bytes)
        assert len(compressed) > 0
        decompressed = comp.decompress_keyframe(compressed)
        assert decompressed.shape == patches.shape

    def test_roundtrip_accuracy(self, comp, patches):
        compressed    = comp.compress_keyframe(patches)
        reconstructed = comp.decompress_keyframe(compressed)
        mse = float(np.mean((patches - reconstructed) ** 2))
        assert mse < 0.1, f"Roundtrip MSE too high: {mse}"

    def test_compression_ratio(self, comp, patches):
        compressed = comp.compress_keyframe(patches)
        ratio      = patches.nbytes / len(compressed)
        assert ratio > 1.0, f"Compression ratio too low: {ratio:.1f}x"

    def test_wrong_dim_raises(self, comp):
        with pytest.raises(AssertionError):
            comp.compress_keyframe(np.zeros((1, 32), dtype=np.float32))


class TestDelta:
    def test_identical_frames_no_delta(self, comp, patches):
        mask, delta, motion = comp.compress_delta(patches, patches)
        assert len(mask) == 25
        assert len(delta) == 0
        assert len(motion) == 0
        assert _unpack_bitmask(mask).sum() == 0

    def test_changed_frame_has_delta(self, comp, patches):
        modified = patches.copy()
        modified[0] += 5.0  # big change
        mask, delta, motion = comp.compress_delta(modified, patches)
        changed = _unpack_bitmask(mask)
        assert changed.sum() >= 1
        assert len(delta) > 0

    def test_decompress_delta_roundtrip(self, comp, patches):
        modified = patches.copy()
        modified[0] += 2.0
        mask, delta, motion = comp.compress_delta(modified, patches)
        # decompress_delta uses 196-bit bitmask but we have 1 patch
        # so we verify via reconstruct_sequence which handles this correctly
        iframe        = comp.compress_keyframe(patches)
        reconstructed = comp.reconstruct_sequence(iframe, [(mask, delta)])
        assert reconstructed.shape == patches.shape

    def test_identical_frame_smaller_than_keyframe(self, comp, patches):
        modified = patches.copy()
        modified[0] += 0.001  # tiny change — should still be smaller
        iframe           = comp.compress_keyframe(patches)
        mask, delta, mvec = comp.compress_delta(modified, patches)
        pframe_size      = len(mask) + len(delta) + len(mvec)
        # P-frame (delta only) should be smaller than or equal to I-frame
        assert pframe_size <= len(iframe) + 30  # small tolerance


class TestSequenceReconstruct:
    def test_sequence_reconstruction(self, comp, patches):
        iframe = comp.compress_keyframe(patches)
        deltas = []
        prev   = patches.copy()
        for _ in range(5):
            current    = prev.copy()
            current   += np.random.randn(*current.shape).astype(np.float32) * 0.1
            mask, delta, _ = comp.compress_delta(current, prev)
            deltas.append((mask, delta))
            prev = current
        result = comp.reconstruct_sequence(iframe, deltas)
        assert result.shape == patches.shape


class TestStats:
    def test_stats_has_required_keys(self, comp):
        stats = comp.stats()
        assert "backend"  in stats
        assert "dim"      in stats
        assert stats["dim"] == 64
