"""
ILUMINATY - Local Visual Engine (IPA v2.1)
===========================================
Deep visual loop provider abstraction.

Default provider is fully local, dependency-free, and CPU-safe.
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

try:
    from PIL import Image
except Exception:  # pragma: no cover - pillow is expected in runtime but keep soft-fail
    Image = None


@dataclass
class VisualTask:
    ref_id: str
    tick_id: int
    timestamp_ms: int
    monitor: int
    frame_bytes: bytes
    mime_type: str
    app_name: str = "unknown"
    window_title: str = "unknown"
    ocr_text: str = ""
    motion_summary: str = ""
    priority: float = 0.5


@dataclass
class VisualFact:
    kind: str
    text: str
    confidence: float
    monitor: int
    timestamp_ms: int
    source: str
    evidence_ref: str
    tick_id: int


@dataclass
class VisualInference:
    timestamp_ms: int
    tick_id: int
    monitor: int
    summary: str
    confidence: float
    source: str
    evidence_ref: str
    facts: list[VisualFact] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["latency_ms"] = round(self.latency_ms, 2)
        data["confidence"] = round(self.confidence, 3)
        return data


class BaseVisualProvider:
    name = "base"

    def analyze(self, task: VisualTask) -> VisualInference:  # pragma: no cover - interface
        raise NotImplementedError


class LocalNativeVisionProvider(BaseVisualProvider):
    """
    Local default provider.
    Uses native heuristics (OCR + motion + UI/window context) only.
    """

    name = "local_native_vision"

    def analyze(self, task: VisualTask) -> VisualInference:
        t0 = time.time()
        now_ms = int(time.time() * 1000)
        facts: list[VisualFact] = []

        app = (task.app_name or "unknown").strip()
        title = (task.window_title or "unknown").strip()
        ocr = (task.ocr_text or "").strip()
        motion = (task.motion_summary or "").strip()

        facts.append(
            VisualFact(
                kind="surface",
                text=f"Active surface {app} | {title[:120]}",
                confidence=0.85 if app != "unknown" else 0.5,
                monitor=task.monitor,
                timestamp_ms=now_ms,
                source=self.name,
                evidence_ref=task.ref_id,
                tick_id=task.tick_id,
            )
        )

        if ocr:
            short_ocr = " ".join(ocr.split())[:220]
            facts.append(
                VisualFact(
                    kind="text",
                    text=f"Visible text: {short_ocr}",
                    confidence=0.75,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        if motion:
            facts.append(
                VisualFact(
                    kind="motion",
                    text=f"Motion context: {motion[:180]}",
                    confidence=0.62,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        low_blob = f"{app} {title} {ocr} {motion}".lower()
        if ("youtube" in low_blob or "video" in low_blob or "player" in low_blob) and motion:
            facts.append(
                VisualFact(
                    kind="activity",
                    text="Likely video/media playback on active surface",
                    confidence=0.64,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        if any(token in low_blob for token in ("chart", "tradingview", "candlestick", "buy", "sell", "rsi", "macd")):
            facts.append(
                VisualFact(
                    kind="domain_hint",
                    text="Possible trading/chart context detected",
                    confidence=0.61,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        summary_parts = [f.text for f in facts[:3]]
        summary = " | ".join(summary_parts)[:360] if summary_parts else "No visual facts"
        confidence = sum(f.confidence for f in facts) / max(1, len(facts))

        return VisualInference(
            timestamp_ms=now_ms,
            tick_id=task.tick_id,
            monitor=task.monitor,
            summary=summary,
            confidence=confidence,
            source=self.name,
            evidence_ref=task.ref_id,
            facts=facts,
            latency_ms=(time.time() - t0) * 1000.0,
        )


class LocalSmolVLMProvider(BaseVisualProvider):
    """
    Optional local caption augmentation provider.
    Default remains native/local heuristics; this provider is opt-in.
    """

    name = "local_smolvlm"

    def __init__(self, caption_enabled: Optional[bool] = None):
        self._base = LocalNativeVisionProvider()
        if caption_enabled is None:
            caption_enabled = os.environ.get("ILUMINATY_VLM_CAPTION", "0") == "1"
        self._caption_enabled = bool(caption_enabled)
        self._caption_backend = None
        if self._caption_enabled:
            self._try_init_caption_backend()

    def _try_init_caption_backend(self) -> None:
        try:
            from transformers import pipeline  # type: ignore

            self._caption_backend = pipeline(
                "image-to-text",
                model=os.environ.get("ILUMINATY_VLM_MODEL", "Salesforce/blip-image-captioning-base"),
                device=-1,
            )
        except Exception:
            self._caption_backend = None

    def _caption(self, image_bytes: bytes) -> str:
        if not self._caption_backend or Image is None:
            return ""
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            out = self._caption_backend(img, max_new_tokens=40)
            if not out:
                return ""
            text = out[0].get("generated_text", "")
            return str(text).strip()[:180]
        except Exception:
            return ""

    def _coerce_base(self, base: VisualInference, source: str, facts: list[VisualFact], latency_ms: float) -> VisualInference:
        summary_parts = [f.text for f in facts[:3]]
        summary = " | ".join(summary_parts)[:360] if summary_parts else "No visual facts"
        confidence = sum(f.confidence for f in facts) / max(1, len(facts))
        return VisualInference(
            timestamp_ms=base.timestamp_ms,
            tick_id=base.tick_id,
            monitor=base.monitor,
            summary=summary,
            confidence=confidence,
            source=source,
            evidence_ref=base.evidence_ref,
            facts=facts,
            latency_ms=latency_ms,
        )

    def analyze(self, task: VisualTask) -> VisualInference:
        t0 = time.time()
        base = self._base.analyze(task)
        facts = list(base.facts)

        if self._caption_enabled:
            caption = self._caption(task.frame_bytes)
            if caption:
                facts.append(
                    VisualFact(
                        kind="image_caption",
                        text=f"Frame caption: {caption}",
                        confidence=0.55,
                        monitor=task.monitor,
                        timestamp_ms=base.timestamp_ms,
                        source=f"{self.name}:caption",
                        evidence_ref=task.ref_id,
                        tick_id=task.tick_id,
                    )
                )
        latency_ms = max(base.latency_ms, (time.time() - t0) * 1000.0)
        return self._coerce_base(base=base, source=self.name, facts=facts, latency_ms=latency_ms)


def _build_default_provider() -> BaseVisualProvider:
    """
    Provider strategy:
    - default: fully local/native heuristics (`native`)
    - optional: caption-augmented local provider (`smolvlm` / env flag)
    """
    mode = os.environ.get("ILUMINATY_VISION_PROVIDER", "native").strip().lower()
    caption_flag = os.environ.get("ILUMINATY_VLM_CAPTION", "0") == "1"
    if mode in ("smolvlm", "caption", "hybrid") or caption_flag:
        return LocalSmolVLMProvider(caption_enabled=True)
    return LocalNativeVisionProvider()


class VisualEngine:
    """
    Dedicated worker for deep visual inference.
    Queue is bounded with drop-oldest policy to avoid backlog.
    """

    def __init__(
        self,
        provider: Optional[BaseVisualProvider] = None,
        max_queue: int = 24,
        max_history: int = 600,
    ):
        self._provider = provider or _build_default_provider()
        self._queue: deque[VisualTask] = deque(maxlen=max(4, max_queue))
        self._history: deque[VisualInference] = deque(maxlen=max(60, max_history))
        self._latest_by_monitor: dict[int, VisualInference] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._dropped = 0
        self._processed = 0
        self._failures = 0

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker, daemon=True, name="ipa-visual-worker")
            self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    def enqueue(self, task: VisualTask) -> bool:
        with self._cond:
            if not self._running:
                return False
            if len(self._queue) >= self._queue.maxlen:
                self._queue.popleft()
                self._dropped += 1
            self._queue.append(task)
            self._cond.notify()
            return True

    def _worker(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(timeout=0.5)
                if not self._running:
                    return
                # RT bias: consume newest task, drop stale backlog.
                task = self._queue.pop()
                self._dropped += len(self._queue)
                self._queue.clear()

            try:
                inference = self._provider.analyze(task)
                with self._lock:
                    self._latest_by_monitor[inference.monitor] = inference
                    self._history.append(inference)
                    self._processed += 1
            except Exception:
                with self._lock:
                    self._failures += 1

    def get_latest(self, monitor_id: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            if monitor_id is None:
                if not self._latest_by_monitor:
                    return None
                latest = max(self._latest_by_monitor.values(), key=lambda x: x.timestamp_ms)
                return latest.to_dict()
            item = self._latest_by_monitor.get(int(monitor_id))
            return item.to_dict() if item else None

    def get_latest_facts(self, monitor_id: Optional[int] = None) -> list[dict]:
        latest = self.get_latest(monitor_id)
        if not latest:
            return []
        return latest.get("facts", [])

    def get_facts_delta(self, since_ms: int, monitor_id: Optional[int] = None) -> list[dict]:
        with self._lock:
            out = []
            for inf in self._history:
                if inf.timestamp_ms <= since_ms:
                    continue
                if monitor_id is not None and inf.monitor != int(monitor_id):
                    continue
                out.extend(asdict(f) for f in inf.facts)
            return out[-40:]

    def query(
        self,
        question: str,
        *,
        at_ms: Optional[int] = None,
        window_seconds: float = 30,
        monitor_id: Optional[int] = None,
    ) -> dict:
        question = (question or "").strip()
        if not question:
            return {
                "answer": "question is required",
                "confidence": 0.0,
                "evidence_refs": [],
                "source": self._provider.name,
            }
        now_ms = int(time.time() * 1000)
        window_cutoff = now_ms - int(max(1.0, float(window_seconds)) * 1000)
        target_ms = int(at_ms) if at_ms is not None else None

        with self._lock:
            candidates = list(self._history)
        if monitor_id is not None:
            candidates = [c for c in candidates if c.monitor == int(monitor_id)]
        if target_ms is not None:
            candidates.sort(key=lambda c: abs(c.timestamp_ms - target_ms))
            candidates = candidates[:8]
        else:
            candidates = [c for c in candidates if c.timestamp_ms >= window_cutoff][-12:]

        if not candidates:
            return {
                "answer": "No visual evidence in the requested time window.",
                "confidence": 0.0,
                "evidence_refs": [],
                "source": self._provider.name,
            }

        q_words = {w.lower() for w in question.split() if len(w) > 2}
        scored = []
        for inf in candidates:
            text = " ".join([inf.summary] + [f.text for f in inf.facts]).lower()
            overlap = len([w for w in q_words if w in text])
            score = overlap + inf.confidence
            scored.append((score, inf))
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0][1]

        answer = best.summary
        evidence_refs = [best.evidence_ref]
        confidence = max(0.1, min(0.95, float(best.confidence)))

        return {
            "answer": answer,
            "confidence": round(confidence, 3),
            "evidence_refs": evidence_refs,
            "source": self._provider.name,
            "timestamp_ms": best.timestamp_ms,
            "tick_id": best.tick_id,
            "monitor": best.monitor,
        }

    def stats(self) -> dict:
        with self._lock:
            return {
                "provider": self._provider.name,
                "running": self._running,
                "queue_size": len(self._queue),
                "processed": self._processed,
                "dropped": self._dropped,
                "failures": self._failures,
                "history_size": len(self._history),
            }
