"""IPA v3 — Visual Encoder: imagehash-based patch encoding.

100% nuestro. Sin dependencias de Google, OpenAI, ni modelos externos.
Sin torch. Sin transformers.

Qué hace:
  Convierte un frame de pantalla en un vector compacto de 64 dimensiones
  usando perceptual hash (imagehash). Dos frames similares producen vectores
  cercanos. Dos frames distintos producen vectores lejanos.

Para qué sirve en IPA:
  - Detectar si un patch cambió entre frames (cosine similarity)
  - Calcular change_mask: qué zonas de la pantalla cambiaron
  - Alimentar el buffer de stream para buscar frames similares

Para qué NO sirve (y no necesita):
  - "Entender" qué hay en la imagen (eso lo hace Claude/GPT-4o directamente)
  - Búsqueda semántica por texto (feature secundaria, no esencial para ojos)
  - Clasificar objetos o elementos visuales

Deps: pillow, imagehash (pure Python, ~50KB)
"""
from __future__ import annotations
import time
import logging
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger("ipa.encoder")

# Embedding dimension — 8x8 perceptual hash = 64 bits
DIM = 64
DEFAULT_IMAGE_SIZE = 224


class VisualEncoder:
    """Lightweight imagehash encoder for IPA v3.

    Produces 64-dimensional float32 vectors from screen frames.
    Instant load (~0ms), instant encode (~0.5ms per frame).
    No model downloads, no GPU required, no external services.

    Use case: change detection, motion tracking, frame similarity search.
    The AI (Claude/GPT-4o) sees the actual screen image via see_now —
    this encoder is for the internal IPA pipeline, not for AI comprehension.
    """

    def __init__(
        self,
        image_size: int = DEFAULT_IMAGE_SIZE,
        hash_size: int = 8,  # 8x8 = 64 bits
    ):
        self.image_size = image_size
        self.hash_size  = hash_size
        self._loaded    = False
        self._load_time_ms: float = 0.0
        self._latency_history: list[float] = []
        self._imagehash_available = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def dim(self) -> int:
        return self.hash_size * self.hash_size  # 64 for hash_size=8

    @property
    def n_patches(self) -> int:
        """IPA pipeline expects n_patches for grid. We return 1 (global hash)."""
        return 1

    @property
    def grid_size(self) -> int:
        return 1

    def load(self) -> None:
        """Verify imagehash is available. Called automatically on first encode."""
        if self._loaded:
            return
        t0 = time.perf_counter()
        try:
            import imagehash  # noqa: F401
            self._imagehash_available = True
            log.info("IPA encoder ready (imagehash, dim=%d)", self.dim)
        except ImportError:
            self._imagehash_available = False
            log.warning(
                "imagehash not installed — encoder returns zero vectors. "
                "Fix: pip install imagehash"
            )
        self._load_time_ms = (time.perf_counter() - t0) * 1000
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    # ── Core encode ───────────────────────────────────────────────────────────

    def encode_patches(self, image: Image.Image) -> np.ndarray:
        """Encode image → (1, 64) float32 array.

        Returns a single patch vector (global hash of the whole image).
        Shape (1, dim) for compatibility with the IPA compressor pipeline.
        """
        if not self._loaded:
            self.load()
        t0 = time.perf_counter()
        result = self._hash_to_vector(image).reshape(1, -1)
        self._latency_history.append((time.perf_counter() - t0) * 1000)
        if len(self._latency_history) > 200:
            self._latency_history = self._latency_history[-100:]
        return result

    def encode_cls(self, image: Image.Image) -> np.ndarray:
        """Encode image → (64,) float32 vector for stream search."""
        if not self._loaded:
            self.load()
        return self._hash_to_vector(image)

    def encode_text(self, text: str) -> np.ndarray:
        """Text encoding not supported — returns zero vector.

        IPA does not do text-to-image search at the encoder level.
        Semantic queries are handled by the AI directly via see_now/what_changed.
        """
        return np.zeros(self.dim, dtype=np.float32)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _hash_to_vector(self, image: Image.Image) -> np.ndarray:
        """Convert image to normalized 64-dim float32 vector via pHash."""
        if not self._imagehash_available:
            return np.zeros(self.dim, dtype=np.float32)
        try:
            import imagehash
            if image.mode != "RGB":
                image = image.convert("RGB")
            h = imagehash.phash(image, hash_size=self.hash_size)
            bits = h.hash.flatten().astype(np.float32)
            norm = np.linalg.norm(bits) + 1e-8
            return bits / norm
        except Exception:
            return np.zeros(self.dim, dtype=np.float32)

    def stats(self) -> dict:
        hist = self._latency_history
        return {
            "loaded":         self._loaded,
            "backend":        "imagehash" if self._imagehash_available else "zeros",
            "dim":            self.dim,
            "n_patches":      self.n_patches,
            "hash_size":      self.hash_size,
            "image_size":     self.image_size,
            "load_time_ms":   round(self._load_time_ms, 1),
            "encode_calls":   len(hist),
            "avg_latency_ms": round(sum(hist) / len(hist), 1) if hist else 0,
        }
