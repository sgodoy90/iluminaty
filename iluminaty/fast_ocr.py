"""
ILUMINATY - Fast OCR (native per-platform, no subprocess)
==========================================================
Replaces the RapidOCR subprocess worker (~1100ms) with native
platform OCR engines that run in the same process:

  Windows  : WinRT Windows.Media.Ocr  ~30–80ms
  macOS    : Vision framework (pyobjc) ~30–60ms
  Linux    : pytesseract (Tesseract)   ~100–200ms

Interface (same on all platforms):
  ocr_image(image_bytes, lang='en') -> OcrResult

OcrResult fields:
  text        : str          — full joined text
  blocks      : list[dict]   — [{text,x,y,w,h,confidence}]
  latency_ms  : float        — wall-clock inference time
  engine      : str          — 'winrt' | 'vision' | 'tesseract' | 'none'

Design:
  - Lazy init: engine loaded on first call, reused after
  - phash cache: same frame hash → instant cached result
  - Thread-safe: threading.Lock on engine init
  - Graceful fallback: WinRT unavailable → tesseract
  - Zero subprocesses — runs in the calling thread
"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("iluminaty.fast_ocr")

# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class OcrResult:
    text: str = ""
    blocks: list = field(default_factory=list)
    latency_ms: float = 0.0
    engine: str = "none"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "blocks": self.blocks,
            "latency_ms": round(self.latency_ms, 1),
            "engine": self.engine,
        }


_EMPTY = OcrResult()

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache: dict[str, OcrResult] = {}
_CACHE_MAX = 64

def _cache_get(phash: str) -> Optional[OcrResult]:
    return _cache.get(phash)

def _cache_put(phash: str, result: OcrResult) -> None:
    if len(_cache) >= _CACHE_MAX:
        del _cache[next(iter(_cache))]
    _cache[phash] = result


# ── Windows — WinRT ───────────────────────────────────────────────────────────

_winrt_engine = None
_winrt_lock = threading.Lock()
_winrt_available: Optional[bool] = None


def _winrt_init() -> bool:
    global _winrt_engine, _winrt_available
    if _winrt_available is not None:
        return _winrt_available
    with _winrt_lock:
        if _winrt_available is not None:
            return _winrt_available
        try:
            import winsdk.windows.media.ocr as _ocr
            engine = _ocr.OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                # Try English explicitly
                import winsdk.windows.globalization as _glob
                engine = _ocr.OcrEngine.try_create_from_language(_glob.Language("en"))
            if engine is None:
                raise RuntimeError("No OCR language pack available")
            _winrt_engine = engine
            _winrt_available = True
            log.info(f"WinRT OCR ready — lang={engine.recognizer_language.language_tag}")
        except Exception as e:
            log.warning(f"WinRT OCR unavailable: {e}")
            _winrt_available = False
    return _winrt_available


def _winrt_ocr(image_bytes: bytes) -> OcrResult:
    """Run WinRT OCR synchronously by wrapping the async API."""
    import asyncio
    import winsdk.windows.graphics.imaging as _img
    import winsdk.windows.storage.streams as _streams
    import winsdk.windows.media.ocr as _ocr

    async def _run():
        stream = _streams.InMemoryRandomAccessStream()
        writer = _streams.DataWriter(stream)
        writer.write_bytes(bytearray(image_bytes))
        await writer.store_async()
        stream.seek(0)
        decoder = await _img.BitmapDecoder.create_async(stream)
        soft_bmp = await decoder.get_software_bitmap_async()
        return await _winrt_engine.recognize_async(soft_bmp)

    t0 = time.perf_counter()
    try:
        # Run in a fresh event loop (avoids conflicts with uvicorn's loop)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        elapsed = (time.perf_counter() - t0) * 1000

        blocks = []
        text_parts = []
        if result and result.lines:
            for line in result.lines:
                for word in line.words:
                    r = word.bounding_rect
                    blocks.append({
                        "text": word.text,
                        "x": int(r.x), "y": int(r.y),
                        "w": int(r.width), "h": int(r.height),
                        "confidence": 95,  # WinRT doesn't expose confidence
                    })
                    text_parts.append(word.text)

        return OcrResult(
            text=" ".join(text_parts),
            blocks=blocks,
            latency_ms=elapsed,
            engine="winrt",
        )
    except Exception as e:
        log.warning(f"WinRT OCR error: {e}")
        return OcrResult(engine="winrt_error", latency_ms=(time.perf_counter()-t0)*1000)


# ── macOS — Vision framework ──────────────────────────────────────────────────

_vision_available: Optional[bool] = None
_vision_lock = threading.Lock()


def _vision_init() -> bool:
    global _vision_available
    if _vision_available is not None:
        return _vision_available
    with _vision_lock:
        if _vision_available is not None:
            return _vision_available
        try:
            import Vision  # pyobjc-framework-Vision
            _vision_available = True
            log.info("macOS Vision OCR ready")
        except Exception as e:
            log.warning(f"Vision OCR unavailable: {e}")
            _vision_available = False
    return _vision_available


def _vision_ocr(image_bytes: bytes) -> OcrResult:
    """macOS Vision framework OCR."""
    t0 = time.perf_counter()
    try:
        import Cocoa
        import Vision
        import Quartz

        ns_data = Cocoa.NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        ci_image = Quartz.CIImage.imageWithData_(ns_data)

        handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(
            ci_image, {}
        )
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # accurate
        request.setUsesLanguageCorrection_(True)

        handler.performRequests_error_([request], None)

        blocks, parts = [], []
        for obs in (request.results() or []):
            text = obs.topCandidates_(1)[0].string()
            bbox = obs.boundingBox()
            blocks.append({
                "text": text,
                "x": int(bbox.origin.x * 1000),
                "y": int(bbox.origin.y * 1000),
                "w": int(bbox.size.width * 1000),
                "h": int(bbox.size.height * 1000),
                "confidence": round(obs.confidence * 100, 1),
            })
            parts.append(text)

        return OcrResult(
            text="\n".join(parts),
            blocks=blocks,
            latency_ms=(time.perf_counter() - t0) * 1000,
            engine="vision",
        )
    except Exception as e:
        log.warning(f"Vision OCR error: {e}")
        return OcrResult(engine="vision_error", latency_ms=(time.perf_counter()-t0)*1000)


# ── Linux/fallback — Tesseract ────────────────────────────────────────────────

_tesseract_available: Optional[bool] = None
_tesseract_lock = threading.Lock()


def _tesseract_init() -> bool:
    global _tesseract_available
    if _tesseract_available is not None:
        return _tesseract_available
    with _tesseract_lock:
        if _tesseract_available is not None:
            return _tesseract_available
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _tesseract_available = True
            log.info("Tesseract OCR ready")
        except Exception as e:
            log.warning(f"Tesseract unavailable: {e}")
            _tesseract_available = False
    return _tesseract_available


def _tesseract_ocr(image_bytes: bytes) -> OcrResult:
    """Tesseract OCR — Linux/fallback."""
    t0 = time.perf_counter()
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        blocks, parts = [], []
        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text or int(data["conf"][i]) < 30:
                continue
            blocks.append({
                "text": text,
                "x": int(data["left"][i]),
                "y": int(data["top"][i]),
                "w": int(data["width"][i]),
                "h": int(data["height"][i]),
                "confidence": round(float(data["conf"][i]), 1),
            })
            parts.append(text)

        return OcrResult(
            text=" ".join(parts),
            blocks=blocks,
            latency_ms=(time.perf_counter() - t0) * 1000,
            engine="tesseract",
        )
    except Exception as e:
        log.warning(f"Tesseract OCR error: {e}")
        return OcrResult(engine="tesseract_error", latency_ms=(time.perf_counter()-t0)*1000)


# ── Public API ────────────────────────────────────────────────────────────────

def ocr_image(image_bytes: bytes, phash: Optional[str] = None, lang: str = "en") -> OcrResult:
    """
    Run OCR on image_bytes using the best available engine for this platform.

    Args:
        image_bytes: raw image bytes (PNG, JPEG, BMP, WebP)
        phash:       optional perceptual hash for caching (same hash = instant)
        lang:        language hint (used by tesseract; WinRT/Vision auto-detect)

    Returns:
        OcrResult with .text, .blocks, .latency_ms, .engine
    """
    if not image_bytes:
        return _EMPTY

    # Cache hit
    if phash:
        cached = _cache_get(phash)
        if cached is not None:
            return cached

    # Pick engine by platform
    result: OcrResult

    if sys.platform == "win32":
        if _winrt_init():
            result = _winrt_ocr(image_bytes)
        elif _tesseract_init():
            result = _tesseract_ocr(image_bytes)
        else:
            result = OcrResult(engine="none")

    elif sys.platform == "darwin":
        if _vision_init():
            result = _vision_ocr(image_bytes)
        elif _tesseract_init():
            result = _tesseract_ocr(image_bytes)
        else:
            result = OcrResult(engine="none")

    else:  # Linux
        if _tesseract_init():
            result = _tesseract_ocr(image_bytes)
        else:
            result = OcrResult(engine="none")

    # Store in cache
    if phash and result.text:
        _cache_put(phash, result)

    return result


def ocr_available() -> bool:
    """True if any OCR engine is available on this platform."""
    if sys.platform == "win32":
        return _winrt_init() or _tesseract_init()
    elif sys.platform == "darwin":
        return _vision_init() or _tesseract_init()
    else:
        return _tesseract_init()


def engine_name() -> str:
    """Name of the active OCR engine."""
    if sys.platform == "win32":
        return "winrt" if _winrt_init() else ("tesseract" if _tesseract_init() else "none")
    elif sys.platform == "darwin":
        return "vision" if _vision_init() else ("tesseract" if _tesseract_init() else "none")
    else:
        return "tesseract" if _tesseract_init() else "none"
