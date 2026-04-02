"""
ILUMINATY - Screen Capture Engine
==================================
Captura nativa del OS con mínimo overhead.
Usa `mss` que es puro Python + ctypes (DXGI en Windows, CoreGraphics en Mac).

Características:
- Adaptive FPS: sube cuando hay actividad, baja en idle
- Region capture: pantalla completa o ventana específica
- JPEG compression: configurable quality (menor = menos RAM/tokens)
- Zero-disk: frames van directo al ring buffer en RAM
"""

import io
import logging
import os
import time
import threading
from typing import Optional, Callable

import mss
from PIL import Image

from .ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


class CaptureConfig:
    """Configuracion de captura."""
    def __init__(
        self,
        fps: float = 1.0,
        quality: int = 80,
        image_format: str = "webp",   # "jpeg", "webp", "png"
        max_width: int = 1280,
        monitor: int = 0,
        region: Optional[dict] = None,
        skip_unchanged: bool = True,
        adaptive_fps: bool = True,
        min_fps: float = 0.2,
        max_fps: float = 5.0,
        smart_quality: bool = True,    # adapta calidad segun contenido
        smart_quality_sample_every: int = 4,
        webp_method: int = 4,
    ):
        self.fps = fps
        self.quality = quality
        self.image_format = image_format
        self.max_width = max_width
        self.monitor = monitor
        self.region = region
        self.skip_unchanged = skip_unchanged
        self.adaptive_fps = adaptive_fps
        self.min_fps = min_fps
        self.max_fps = max_fps
        self.smart_quality = smart_quality
        self.smart_quality_sample_every = max(1, int(smart_quality_sample_every))
        self.webp_method = max(0, min(6, int(webp_method)))


class ScreenCapture:
    """
    Motor de captura de pantalla.
    Corre en un thread dedicado, empuja frames al ring buffer.
    """

    def __init__(self, buffer: RingBuffer, config: Optional[CaptureConfig] = None):
        self.buffer = buffer
        self.config = config or CaptureConfig()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_fps = self.config.fps
        self._consecutive_unchanged = 0
        self._on_frame: Optional[Callable] = None  # callback opcional
        self._smart_quality_counter = 0
        self._smart_quality_value = self.config.quality
        self._burst_lock = threading.Lock()
        self._burst_until = 0.0
        self._burst_fps = 0.0
        self._burst_count = 0
        self._last_burst_reason = ""
        # Cache env-var webp method at init time (not per-frame)
        _env_method = os.environ.get("ILUMINATY_WEBP_METHOD", "").strip()
        if _env_method:
            try:
                self._webp_method = max(0, min(6, int(_env_method)))
            except Exception:
                self._webp_method = self.config.webp_method
        else:
            self._webp_method = self.config.webp_method
        
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_fps(self) -> float:
        return self._effective_fps()

    def _effective_fps(self) -> float:
        """
        Effective FPS used by the capture loop.
        During short motion triggers we temporarily lift FPS without
        permanently changing adaptive baseline.
        """
        with self._burst_lock:
            if time.time() < self._burst_until:
                return max(float(self._current_fps), float(self._burst_fps))
        return float(self._current_fps)

    def trigger_burst(
        self,
        *,
        duration_ms: int = 220,
        fps: Optional[float] = None,
        reason: str = "motion",
    ) -> dict:
        """
        Trigger a temporary FPS burst (trigger-based capture).
        This is used by perception when it detects sudden UI motion.
        """
        try:
            dur_ms = int(duration_ms)
        except Exception:
            dur_ms = 220
        dur_ms = max(50, min(3000, dur_ms))

        if fps is None:
            target_fps = max(float(self.config.max_fps), float(self._current_fps))
        else:
            try:
                target_fps = float(fps)
            except Exception:
                target_fps = max(float(self.config.max_fps), float(self._current_fps))
        target_fps = max(float(self.config.min_fps), min(max(target_fps, 1.0), 30.0))

        now = time.time()
        until = now + (dur_ms / 1000.0)
        with self._burst_lock:
            self._burst_until = max(float(self._burst_until), float(until))
            self._burst_fps = max(float(self._burst_fps), float(target_fps))
            self._burst_count += 1
            self._last_burst_reason = str(reason or "motion")
            eff = max(float(self._current_fps), float(self._burst_fps))

        return {
            "triggered": True,
            "duration_ms": int(dur_ms),
            "target_fps": round(float(target_fps), 2),
            "effective_fps": round(float(eff), 2),
            "reason": str(reason or "motion"),
            "count": int(self._burst_count),
        }

    def _resize_frame(self, img: Image.Image) -> Image.Image:
        """Redimensiona si excede max_width, manteniendo aspect ratio."""
        if img.width <= self.config.max_width:
            return img
        ratio = self.config.max_width / img.width
        new_h = int(img.height * ratio)
        return img.resize((self.config.max_width, new_h), Image.LANCZOS)

    def _compress_frame(self, img: Image.Image) -> tuple[bytes, str]:
        """
        Comprime frame al formato configurado. Retorna (bytes, mime_type).
        
        Smart quality: si el frame tiene mucho texto/UI (bordes duros),
        sube calidad automaticamente para que OCR lea mejor.
        """
        buf = io.BytesIO()
        fmt = self.config.image_format.lower()
        quality = self.config.quality

        # Smart quality: detectar si hay mucho contraste (texto/UI)
        if self.config.smart_quality:
            # Sample only every N frames and reuse last decision.
            self._smart_quality_counter += 1
            if self._smart_quality_counter % max(1, self.config.smart_quality_sample_every) == 0:
                try:
                    import numpy as np
                    # Resize to 32x32 instead of 64x64 — 4x fewer pixels, same signal
                    small = img.resize((32, 32))
                    arr = np.frombuffer(small.convert("L").tobytes(), dtype=np.uint8)
                    local_std = arr.std()
                    if local_std > 60:  # mucho contraste = texto/UI
                        self._smart_quality_value = min(self.config.quality + 15, 95)
                    else:
                        self._smart_quality_value = self.config.quality
                except ImportError:
                    logger.debug("numpy not available; smart quality boost disabled")
                    self._smart_quality_value = self.config.quality
            quality = self._smart_quality_value

        if fmt == "webp":
            method = self._webp_method  # cached at init — not read from env per frame
            img.save(buf, format="WEBP", quality=quality, method=method)
            mime = "image/webp"
        elif fmt == "png":
            img.save(buf, format="PNG", optimize=True)
            mime = "image/png"
        else:  # jpeg default
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            mime = "image/jpeg"

        return buf.getvalue(), mime

    def _adapt_fps(self, frame_changed: bool):
        """
        FPS adaptativo:
        - Si nada cambia por 5+ frames → baja FPS (ahorra CPU)
        - Si algo cambia → sube FPS (captura más detalle)
        """
        if not self.config.adaptive_fps:
            return
            
        if frame_changed:
            self._consecutive_unchanged = 0
            # Subir FPS gradualmente
            self._current_fps = min(
                self._current_fps * 1.5,
                self.config.max_fps
            )
        else:
            self._consecutive_unchanged += 1
            if self._consecutive_unchanged >= 5:
                # Bajar FPS gradualmente
                self._current_fps = max(
                    self._current_fps * 0.7,
                    self.config.min_fps
                )

    def _capture_loop(self):
        """Loop principal de captura. Corre en thread dedicado."""
        with mss.mss() as sct:
            while self._running:
                loop_start = time.time()
                
                try:
                    # Determinar qué capturar
                    if self.config.region:
                        grab_area = self.config.region
                    elif self.config.monitor == 0:
                        # Todos los monitores combinados
                        grab_area = sct.monitors[0]
                    else:
                        grab_area = sct.monitors[
                            min(self.config.monitor, len(sct.monitors) - 1)
                        ]
                    
                    # Capturar
                    raw = sct.grab(grab_area)
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    
                    # Redimensionar
                    img = self._resize_frame(img)
                    
                    # Comprimir en formato optimo (RAM only)
                    frame_bytes, mime_type = self._compress_frame(img)
                    
                    # Empujar al ring buffer (IPA: tag with monitor_id)
                    was_stored = self.buffer.push(
                        frame_bytes=frame_bytes,
                        width=img.width,
                        height=img.height,
                        region=str(grab_area),
                        mime_type=mime_type,
                        skip_if_unchanged=self.config.skip_unchanged,
                        monitor_id=self.config.monitor,
                    )
                    
                    # Adaptar FPS
                    self._adapt_fps(was_stored)
                    
                    # Callback si hay uno registrado
                    if was_stored and self._on_frame:
                        slot = self._latest_slot_for_callback()
                        if slot is not None:
                            self._on_frame(slot)
                        
                except Exception as e:
                    # No crashear el loop por un frame fallido
                    logger.error("[iluminaty] capture error: %s", e)
                
                # Dormir hasta el proximo frame
                elapsed = time.time() - loop_start
                effective_fps = max(0.1, float(self._effective_fps()))
                sleep_time = max(0, (1.0 / effective_fps) - elapsed)
                
                # BUG-007 fix: use time.sleep instead of creating Event per iteration
                if sleep_time > 0 and self._running:
                    time.sleep(sleep_time)

    def start(self):
        """Inicia la captura en background."""
        if self._running:
            return
        self._running = True
        self._current_fps = self.config.fps
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Detiene la captura. El buffer se mantiene hasta que se limpie o muera el proceso."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def on_frame(self, callback: Callable):
        """Registra callback que se llama cuando hay un frame nuevo."""
        self._on_frame = callback

    def _latest_slot_for_callback(self):
        """
        Pick callback slot without cross-monitor contamination.
        For pinned monitor captures (>0), never fall back to global latest.
        """
        requested_monitor = int(getattr(self.config, "monitor", 0) or 0)
        if hasattr(self.buffer, "get_latest_for_monitor"):
            try:
                slot = self.buffer.get_latest_for_monitor(requested_monitor)
            except Exception:
                slot = None
            if slot is not None:
                return slot
            if requested_monitor > 0:
                # Strict pinned monitor mode.
                return None
        if hasattr(self.buffer, "get_latest"):
            return self.buffer.get_latest()
        return None
