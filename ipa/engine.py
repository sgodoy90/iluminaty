"""IPA v3 — Engine: Main orchestrator for the Compressed Visual Stream pipeline.

Usage:
    from ipa import IPAEngine

    engine = IPAEngine()
    frame  = engine.feed(pil_image)        # Feed a frame
    ctx    = engine.context(seconds=30)    # Get visual context
    motion = engine.motion(seconds=5)      # Get motion field
"""
from __future__ import annotations
import time
import logging
from typing import Optional

import numpy as np
from PIL import Image

from ipa.types import PatchFrame, MotionField, VisualContext, SearchResult
from ipa.encoder import VisualEncoder
from ipa.compressor import DeltaCompressor, _pack_bitmask, N_PATCHES
from ipa.stream import VisualStream

log = logging.getLogger("ipa.engine")


class IPAEngine:
    """IPA v3 — Compressed Visual Stream engine.

    Captures frames, compresses using I/P-frame codec,
    detects motion and changes, maintains temporal buffer.

    The AI receives real screen images via see_now/what_changed.
    IPAEngine is the infrastructure layer — not the visual intelligence.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}

        # Encoder: imagehash-based, pure Python, no external model
        image_size = cfg.get("image_size", 224)
        hash_size  = cfg.get("hash_size", 8)  # 8x8 = 64-dim vector

        # Compressor config
        similarity_threshold = cfg.get("similarity_threshold", 0.92)

        # Stream config
        max_frames       = cfg.get("max_frames", 10800)  # 1hr @ 3fps
        keyframe_interval = cfg.get("keyframe_interval_s", 10.0)

        # Build components
        self._encoder = VisualEncoder(
            image_size=image_size,
            hash_size=hash_size,
        )

        # Compressor and stream are lazy-initialized on first feed()
        # so we know the real dim after the encoder loads
        self._compressor_threshold = similarity_threshold
        self._compressor: Optional[DeltaCompressor] = None
        self._stream_max_frames = max_frames
        self._stream_keyframe_interval = int(keyframe_interval)
        self._stream: Optional[VisualStream] = None

        # State
        self._keyframe_interval_s = keyframe_interval
        self._last_keyframe_time: float = 0.0
        self._last_patches: Optional[np.ndarray] = None
        self._frame_count = 0
        self._enabled = True

    @property
    def encoder(self) -> VisualEncoder:
        return self._encoder

    @property
    def stream(self) -> Optional[VisualStream]:
        return self._stream

    def feed(
        self,
        image: Image.Image,
        timestamp: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> PatchFrame:
        """Process a frame: encode → compress → store in buffer.

        Args:
            image:     PIL Image (any size)
            timestamp: Unix timestamp (default: now)
            metadata:  Optional {monitor_id, window_name, scene_hint}

        Returns:
            PatchFrame stored in the buffer
        """
        ts   = timestamp or time.time()
        meta = metadata or {}

        # Encode — actual dim known after first encode
        patches = self._encoder.encode_patches(image)

        # Lazy-init compressor + stream after first encode (dim is now known)
        if self._compressor is None:
            actual_dim = patches.shape[-1]
            self._compressor = DeltaCompressor(
                dim=actual_dim,
                similarity_threshold=self._compressor_threshold,
            )
            self._stream = VisualStream(
                max_frames=self._stream_max_frames,
                keyframe_interval=self._stream_keyframe_interval,
                compressor=self._compressor,
            )

        # CLS embedding for stream search (same as patches for level 0)
        cls_vec        = self._encoder.encode_cls(image)
        cls_compressed = self._compressor.compress_vectors(cls_vec.reshape(1, -1))

        # Decide I-frame or P-frame
        is_keyframe = self._should_keyframe(ts, meta)

        if is_keyframe or self._last_patches is None:
            grid_bytes   = self._compressor.compress_keyframe(patches)
            all_changed  = np.ones(N_PATCHES, dtype=bool)
            change_mask  = _pack_bitmask(all_changed)
            motion_bytes = b""
            frame_type   = "I"
            n_changed    = N_PATCHES
            self._last_keyframe_time = ts
        else:
            change_mask, grid_bytes, motion_bytes = self._compressor.compress_delta(
                patches, self._last_patches
            )
            frame_type = "P"
            from ipa.compressor import _unpack_bitmask
            n_changed = int(_unpack_bitmask(change_mask).sum())

        self._last_patches = patches
        self._frame_count += 1

        frame = PatchFrame(
            timestamp=ts,
            frame_type=frame_type,
            patch_grid=grid_bytes,
            change_mask=change_mask,
            motion_vectors=motion_bytes,
            cls_embedding=cls_compressed,
            n_changed=n_changed,
            metadata=meta,
        )

        self._stream.push(frame)
        return frame

    def _should_keyframe(self, ts: float, meta: dict) -> bool:
        if self._frame_count == 0:
            return True
        if ts - self._last_keyframe_time >= self._keyframe_interval_s:
            return True
        if meta.get("window_changed", False):
            return True
        if meta.get("scene_changed", False):
            return True
        return False

    def context(self, seconds: float = 30.0, monitor_id: Optional[int] = None) -> VisualContext:
        """Get visual context — scene state, motion, timeline."""
        if self._stream is None:
            return VisualContext(scene_state="empty", confidence=0.0, token_estimate=50)
        return self._stream.get_context(seconds=seconds, monitor_id=monitor_id)

    def motion(self, seconds: float = 5.0) -> MotionField:
        """Get current motion field."""
        if self._stream is None:
            return MotionField(motion_type="static")
        return self._stream.get_motion(seconds=seconds)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        time_range_seconds: Optional[float] = None,
    ) -> list[SearchResult]:
        """Search buffer by vector similarity (imagehash-based).

        Note: text-to-image search requires a text encoder (not available
        in the current lightweight build). Pass a frame's cls_vector directly.
        """
        if self._stream is None:
            return []
        return self._stream.search(
            query_embedding=query_vector,
            top_k=top_k,
            time_range_seconds=time_range_seconds,
        )

    def timeline(self, seconds: float = 60.0) -> list:
        """Get scene transition timeline."""
        if self._stream is None:
            return []
        return self._stream.get_timeline(seconds=seconds)

    def reset(self) -> None:
        """Clear buffer and state, keep encoder loaded."""
        if self._stream is not None:
            self._stream = VisualStream(
                max_frames=self._stream.max_frames,
                keyframe_interval=self._stream.keyframe_interval,
                compressor=self._compressor,
            )
        self._last_patches = None
        self._last_keyframe_time = 0.0
        self._frame_count = 0
        log.info("Engine reset — buffer cleared")

    def status(self) -> dict:
        """Full engine status."""
        comp_stats   = self._compressor.stats() if self._compressor else {"backend": "not_initialized"}
        stream_stats = self._stream.stats()      if self._stream     else {"frames": 0}
        return {
            "enabled":     self._enabled,
            "frame_count": self._frame_count,
            "encoder":     self._encoder.stats(),
            "compressor":  comp_stats,
            "stream":      stream_stats,
        }
