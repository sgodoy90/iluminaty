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
import time
import threading
from typing import Optional, Callable

import mss
from PIL import Image

from .ring_buffer import RingBuffer


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
        
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_fps(self) -> float:
        return self._current_fps

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
            # Sampling rapido: si el frame tiene mucha variacion de intensidad
            # en bloques pequenos, probablemente es texto/codigo -> subir calidad
            try:
                import numpy as np
                small = img.resize((64, 64))
                arr = np.array(small.convert("L"))
                local_std = arr.std()
                if local_std > 60:  # mucho contraste = texto/UI
                    quality = min(quality + 15, 95)
            except ImportError:
                pass  # sin numpy, usar calidad fija

        if fmt == "webp":
            img.save(buf, format="WEBP", quality=quality, method=4)
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
                    
                    # Empujar al ring buffer
                    was_stored = self.buffer.push(
                        frame_bytes=frame_bytes,
                        width=img.width,
                        height=img.height,
                        region=str(grab_area),
                        mime_type=mime_type,
                        skip_if_unchanged=self.config.skip_unchanged,
                    )
                    
                    # Adaptar FPS
                    self._adapt_fps(was_stored)
                    
                    # Callback si hay uno registrado
                    if was_stored and self._on_frame:
                        self._on_frame(self.buffer.get_latest())
                        
                except Exception as e:
                    # No crashear el loop por un frame fallido
                    print(f"[iluminaty] capture error: {e}")
                
                # Dormir hasta el proximo frame
                elapsed = time.time() - loop_start
                sleep_time = max(0, (1.0 / self._current_fps) - elapsed)
                
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
