"""
ILUMINATY - OCR Worker
========================
Runs RapidOCR in a background thread to avoid blocking the main event loop.

Uses threading (not multiprocessing) because:
1. DirectML/GPU releases the GIL during GPU computation
2. spawn-based multiprocessing has issues in uvicorn server context on Windows
3. Thread-based approach is simpler and works well when GPU is available

For CPU-only ONNX: the worker thread still blocks the GIL during inference,
but the perception loop runs at low frequency (10-15s) so impact is minimal.
"""
from __future__ import annotations

import io
import logging
import queue
import threading
import time
from typing import Optional

log = logging.getLogger("iluminaty.ocr_worker")


class OCRWorker:
    """Manages OCR inference in a background thread.

    Enqueue frames non-blocking. Read results when ready.
    Falls back gracefully if RapidOCR is unavailable.
    """

    def __init__(self):
        self._request_q: queue.Queue = queue.Queue(maxsize=6)
        self._lock = threading.Lock()
        self._latest: dict[int, dict] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._available = False
        self._ocr = None

    def start(self) -> bool:
        """Start the OCR worker thread. Returns True if RapidOCR is available."""
        try:
            from rapidocr import RapidOCR
            self._ocr = RapidOCR()
            self._available = True
        except ImportError:
            log.warning("RapidOCR not available — OCR worker disabled")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="ocr-worker",
        )
        self._thread.start()
        log.info("OCR worker thread started")
        return True

    def stop(self) -> None:
        self._running = False
        try:
            self._request_q.put_nowait(None)
        except Exception:
            pass

    def enqueue(self, frame_bytes: bytes, frame_hash: Optional[str],
                monitor_id: int) -> bool:
        """Queue a frame for OCR. Non-blocking — drops if queue is full."""
        if not self._available:
            return False
        try:
            self._request_q.put_nowait((frame_bytes, frame_hash, monitor_id))
            return True
        except queue.Full:
            return False

    def get_result(self, monitor_id: int) -> Optional[dict]:
        with self._lock:
            return self._latest.get(monitor_id)

    def get_all_results(self) -> dict[int, dict]:
        with self._lock:
            return dict(self._latest)

    @property
    def available(self) -> bool:
        return self._available and self._running

    def _worker_loop(self) -> None:
        import numpy as np
        from PIL import Image

        hash_cache: dict[str, dict] = {}

        while self._running:
            try:
                item = self._request_q.get(timeout=2.0)
                if item is None:
                    break

                frame_bytes, frame_hash, monitor_id = item

                # Cache hit
                if frame_hash and frame_hash in hash_cache:
                    cached = hash_cache[frame_hash]
                    with self._lock:
                        self._latest[monitor_id] = {**cached, "from_cache": True, "ts": time.time()}
                    continue

                # Run OCR
                try:
                    img = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
                    img_array = np.array(img)
                    result = self._ocr(img_array)

                    blocks, text_parts = [], []
                    if result is not None and result.txts is not None:
                        for box, txt, score in zip(result.boxes, result.txts, result.scores):
                            if score < 0.3:
                                continue
                            xs = [p[0] for p in box]
                            ys = [p[1] for p in box]
                            blocks.append({
                                "text": txt,
                                "x": int(min(xs)), "y": int(min(ys)),
                                "w": int(max(xs)) - int(min(xs)),
                                "h": int(max(ys)) - int(min(ys)),
                                "confidence": round(float(score) * 100, 1),
                            })
                            text_parts.append(txt)

                    text = "\n".join(text_parts)
                    entry = {"blocks": blocks, "text": text, "ts": time.time(), "from_cache": False}

                    if frame_hash:
                        hash_cache[frame_hash] = {"blocks": blocks, "text": text}
                        if len(hash_cache) > 20:
                            del hash_cache[next(iter(hash_cache))]

                    with self._lock:
                        self._latest[monitor_id] = entry

                except Exception as e:
                    log.debug("OCR worker inference failed: %s", e)

            except queue.Empty:
                continue
            except Exception as e:
                log.debug("OCR worker loop error: %s", e)


# ── Singleton ──────────────────────────────────────────────────────────────────

_ocr_worker: Optional[OCRWorker] = None


def get_ocr_worker() -> Optional[OCRWorker]:
    return _ocr_worker


def init_ocr_worker() -> OCRWorker:
    global _ocr_worker
    if _ocr_worker is None or not _ocr_worker.available:
        _ocr_worker = OCRWorker()
        _ocr_worker.start()
    return _ocr_worker

