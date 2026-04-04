"""
ILUMINATY - OCR Worker (subprocess isolation)
===============================================
Runs RapidOCR in a completely separate Python process.

Why subprocess instead of thread:
- Thread: RapidOCR/ONNX CPU inference blocks the GIL → FastAPI event loop
  stalls → /health takes 2000ms instead of <10ms. Unacceptable.
- Subprocess (spawn): totally isolated GIL. Main process never blocks.
  Communication via multiprocessing.Queue. Works on Windows with uvicorn.

Architecture:
  Main process (FastAPI/uvicorn)
      ↓ enqueue(frame_bytes, hash, monitor_id) → mp.Queue (non-blocking)
  OCR subprocess (isolated Python)
      ↓ RapidOCR inference (may take 50-300ms, blocks its own GIL)
      ↓ result → mp.Queue back to main
  Main process
      ↓ get_result(monitor_id) → latest cached result (instant)

If subprocess crashes or is unavailable: graceful degradation,
OCREngine falls back to Tesseract or returns ocr_pending.
"""
from __future__ import annotations

import io
import logging
import multiprocessing as mp
import os
import queue
import time
from typing import Optional

log = logging.getLogger("iluminaty.ocr_worker")

# Max frames queued — drop oldest if subprocess is slow
_QUEUE_MAXSIZE = 4


# ── Subprocess entry point ─────────────────────────────────────────────────────

def _ocr_subprocess_main(req_q: mp.Queue, res_q: mp.Queue) -> None:
    """Runs in isolated subprocess. Never imported in the main process."""
    import io as _io
    import time as _time

    try:
        from rapidocr import RapidOCR
        ocr = RapidOCR()
    except Exception as e:
        res_q.put({"type": "init_error", "error": str(e)})
        return

    res_q.put({"type": "ready"})

    import numpy as np
    from PIL import Image

    hash_cache: dict[str, dict] = {}

    while True:
        try:
            item = req_q.get(timeout=5.0)
        except Exception:
            continue

        if item is None:
            break   # shutdown signal

        frame_bytes, frame_hash, monitor_id = item

        # Cache hit — no inference needed
        if frame_hash and frame_hash in hash_cache:
            cached = hash_cache[frame_hash]
            res_q.put({
                "type": "result",
                "monitor_id": monitor_id,
                "text": cached["text"],
                "blocks": cached["blocks"],
                "from_cache": True,
                "ts": _time.time(),
            })
            continue

        # Run inference
        try:
            img = Image.open(_io.BytesIO(frame_bytes)).convert("RGB")
            arr = np.array(img)
            result = ocr(arr)

            blocks, parts = [], []
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
                    parts.append(txt)

            text = "\n".join(parts)

            if frame_hash:
                hash_cache[frame_hash] = {"text": text, "blocks": blocks}
                if len(hash_cache) > 30:
                    del hash_cache[next(iter(hash_cache))]

            res_q.put({
                "type": "result",
                "monitor_id": monitor_id,
                "text": text,
                "blocks": blocks,
                "from_cache": False,
                "ts": _time.time(),
            })

        except Exception as e:
            res_q.put({"type": "error", "monitor_id": monitor_id, "error": str(e)})


# ── OCRWorker — main process side ──────────────────────────────────────────────

class OCRWorker:
    """Manages OCR inference in an isolated subprocess.

    Main process only enqueues frames and reads cached results.
    All RapidOCR/ONNX code lives in the subprocess — GIL never blocks here.
    """

    def __init__(self):
        self._req_q:  Optional[mp.Queue] = None
        self._res_q:  Optional[mp.Queue] = None
        self._proc:   Optional[mp.Process] = None
        self._latest: dict[int, dict] = {}
        self._available = False
        self._result_drain_running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Launch OCR subprocess. Returns True when subprocess is ready."""
        try:
            # Use 'spawn' explicitly — required on Windows, avoids fork issues
            ctx = mp.get_context("spawn")
            self._req_q = ctx.Queue(maxsize=_QUEUE_MAXSIZE)
            self._res_q = ctx.Queue(maxsize=_QUEUE_MAXSIZE * 2)

            self._proc = ctx.Process(
                target=_ocr_subprocess_main,
                args=(self._req_q, self._res_q),
                daemon=True,
                name="iluminaty-ocr",
            )
            self._proc.start()

            # Wait for ready signal (max 30s — model load time)
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    msg = self._res_q.get(timeout=1.0)
                    if msg.get("type") == "ready":
                        self._available = True
                        log.info("OCR subprocess ready (pid=%d)", self._proc.pid)
                        self._start_drain_thread()
                        return True
                    elif msg.get("type") == "init_error":
                        log.warning("OCR subprocess init failed: %s", msg.get("error"))
                        return False
                except Exception:
                    continue

            log.warning("OCR subprocess did not become ready in 30s")
            return False

        except Exception as e:
            log.warning("OCR subprocess start failed: %s", e)
            return False

    def stop(self) -> None:
        self._available = False
        try:
            if self._req_q:
                self._req_q.put_nowait(None)
        except Exception:
            pass
        try:
            if self._proc and self._proc.is_alive():
                self._proc.join(timeout=3)
                if self._proc.is_alive():
                    self._proc.kill()
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def enqueue(self, frame_bytes: bytes, frame_hash: Optional[str],
                monitor_id: int) -> bool:
        """Queue a frame for OCR. Non-blocking — drops frame if queue is full."""
        if not self._available or self._req_q is None:
            return False
        try:
            self._req_q.put_nowait((frame_bytes, frame_hash, monitor_id))
            return True
        except Exception:
            return False   # queue full — drop frame, not a problem

    def get_result(self, monitor_id: int) -> Optional[dict]:
        """Return latest OCR result for a monitor. Always instant."""
        self._drain_results()
        return self._latest.get(monitor_id)

    def get_all_results(self) -> dict[int, dict]:
        self._drain_results()
        return dict(self._latest)

    @property
    def available(self) -> bool:
        if not self._available:
            return False
        # Check subprocess health
        if self._proc and not self._proc.is_alive():
            log.warning("OCR subprocess died — marking unavailable")
            self._available = False
            return False
        return True

    # ── Internal ───────────────────────────────────────────────────────────────

    def _drain_results(self) -> None:
        """Pull all pending results from the subprocess queue. Non-blocking."""
        if self._res_q is None:
            return
        try:
            while True:
                try:
                    msg = self._res_q.get_nowait()
                except Exception:
                    break
                if msg.get("type") == "result":
                    mid = msg.get("monitor_id", 0)
                    self._latest[mid] = {
                        "text":       msg.get("text", ""),
                        "blocks":     msg.get("blocks", []),
                        "from_cache": msg.get("from_cache", False),
                        "ts":         msg.get("ts", time.time()),
                    }
        except Exception:
            pass

    def _start_drain_thread(self) -> None:
        """Background thread that continuously drains the result queue.
        Keeps _latest fresh without requiring callers to drain manually.
        """
        import threading

        def _drain_loop():
            while self._available:
                self._drain_results()
                time.sleep(0.1)   # 10Hz drain — fresh enough for 3fps capture

        t = threading.Thread(target=_drain_loop, daemon=True, name="ocr-drain")
        t.start()


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
