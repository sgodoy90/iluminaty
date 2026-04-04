"""
ILUMINATY - Local Visual Engine
================================
Provides deep visual inference via native heuristics (OCR + motion + UI context).
No local VLM/SmolVLM — visual understanding is handled by the external AI
(Claude/GPT-4o) via MCP tools that deliver real frames as images.

Architecture:
  VisualTask   → input descriptor (frame bytes + context)
  VisualFact   → one atomic visual observation
  VisualInference → collected facts for one frame
  LocalNativeVisionProvider → OCR + motion + window context (CPU-only, no torch)
  VisualEngine → async worker queue with RT-bias (drop oldest, keep newest)
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PIL import Image
except Exception:
    Image = None


# ── Data types ────────────────────────────────────────────────────────────────

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


# ── Provider ──────────────────────────────────────────────────────────────────

class BaseVisualProvider:
    name = "base"

    def analyze(self, task: VisualTask) -> VisualInference:
        raise NotImplementedError


class LocalNativeVisionProvider(BaseVisualProvider):
    """CPU-only provider. Combines OCR text, motion context, and window signals.

    No torch, no GPU, no external model downloads.
    Works on every machine that can run iluminaty.
    Visual understanding (what things look like) is delegated to the
    external AI (Claude/GPT-4o) via MCP see_screen/see_now image delivery.
    """

    name = "local_native_vision"

    # Domain keywords for activity hints
    _MEDIA_TOKENS   = {"youtube", "video", "player", "netflix", "twitch", "vlc", "mpv"}
    _TRADING_TOKENS = {"chart", "tradingview", "candlestick", "buy", "sell", "rsi", "macd",
                       "bid", "ask", "order", "position"}
    _CODING_TOKENS  = {"def ", "class ", "function", "import", "require", "git", "commit",
                       "vscode", "pycharm", "intellij", "vim", "nvim"}
    _BROWSER_TOKENS = {"http", "https", "chrome", "firefox", "edge", "brave", "safari"}

    def analyze(self, task: VisualTask) -> VisualInference:
        t0 = time.time()
        now_ms = int(time.time() * 1000)
        facts: list[VisualFact] = []

        app    = (task.app_name    or "unknown").strip()
        title  = (task.window_title or "unknown").strip()
        ocr    = (task.ocr_text    or "").strip()
        motion = (task.motion_summary or "").strip()

        # Fact: active surface
        facts.append(VisualFact(
            kind="surface",
            text=f"Active: {app} | {title[:120]}",
            confidence=0.85 if app != "unknown" else 0.5,
            monitor=task.monitor,
            timestamp_ms=now_ms,
            source=self.name,
            evidence_ref=task.ref_id,
            tick_id=task.tick_id,
        ))

        # Fact: visible text (from OCR)
        if ocr:
            facts.append(VisualFact(
                kind="text",
                text=f"Visible text: {' '.join(ocr.split())[:220]}",
                confidence=0.78,
                monitor=task.monitor,
                timestamp_ms=now_ms,
                source=self.name,
                evidence_ref=task.ref_id,
                tick_id=task.tick_id,
            ))

        # Fact: motion context (from IPA fast loop)
        if motion:
            facts.append(VisualFact(
                kind="motion",
                text=f"Motion: {motion[:180]}",
                confidence=0.65,
                monitor=task.monitor,
                timestamp_ms=now_ms,
                source=self.name,
                evidence_ref=task.ref_id,
                tick_id=task.tick_id,
            ))

        # Domain hints from keyword matching
        blob = f"{app} {title} {ocr}".lower()
        if self._MEDIA_TOKENS & set(blob.split()):
            facts.append(VisualFact(
                kind="activity",
                text="Media/video playback likely",
                confidence=0.65,
                monitor=task.monitor, timestamp_ms=now_ms,
                source=self.name, evidence_ref=task.ref_id, tick_id=task.tick_id,
            ))
        elif self._TRADING_TOKENS & set(blob.replace(",", " ").split()):
            facts.append(VisualFact(
                kind="domain_hint",
                text="Trading/chart context detected",
                confidence=0.62,
                monitor=task.monitor, timestamp_ms=now_ms,
                source=self.name, evidence_ref=task.ref_id, tick_id=task.tick_id,
            ))
        elif any(t in blob for t in self._CODING_TOKENS):
            facts.append(VisualFact(
                kind="domain_hint",
                text="Code/development context detected",
                confidence=0.68,
                monitor=task.monitor, timestamp_ms=now_ms,
                source=self.name, evidence_ref=task.ref_id, tick_id=task.tick_id,
            ))

        summary_parts = [f.text for f in facts[:3]]
        summary = " | ".join(summary_parts)[:400] if summary_parts else "No visual facts"
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


# ── Engine ────────────────────────────────────────────────────────────────────

class VisualEngine:
    """Async worker queue for visual inference.

    RT-bias: on backlog, keeps newest task and drops stale ones.
    Bounded queue (maxlen) prevents unbounded RAM growth.
    """

    def __init__(
        self,
        provider: Optional[BaseVisualProvider] = None,
        max_queue: int = 24,
        max_history: int = 600,
    ):
        self._provider = provider or LocalNativeVisionProvider()
        self._queue: deque[VisualTask] = deque(maxlen=max(4, max_queue))
        self._history: deque[VisualInference] = deque(maxlen=max(60, max_history))
        self._latest_by_monitor: dict[int, VisualInference] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._dropped    = 0
        self._processed  = 0
        self._failures   = 0
        self._processed_by_monitor: dict[int, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._worker, daemon=True, name="visual-worker"
            )
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
                # RT-bias: take newest, drop stale backlog
                task = self._queue.pop()
                self._dropped += len(self._queue)
                self._queue.clear()

            try:
                inference = self._provider.analyze(task)
                with self._lock:
                    self._latest_by_monitor[inference.monitor] = inference
                    self._history.append(inference)
                    self._processed += 1
                    self._processed_by_monitor[inference.monitor] = (
                        self._processed_by_monitor.get(inference.monitor, 0) + 1
                    )
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
        return latest.get("facts", []) if latest else []

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

    def describe(
        self,
        frame_bytes: bytes,
        monitor_id: int = 0,
        app_name: str = "",
        window_title: str = "",
        ocr_text: str = "",
    ) -> dict:
        """On-demand inference (bypasses queue — direct call)."""
        task = VisualTask(
            ref_id=f"ondemand_{int(time.time() * 1000)}",
            tick_id=0,
            timestamp_ms=int(time.time() * 1000),
            monitor=monitor_id,
            frame_bytes=frame_bytes,
            mime_type="image/webp",
            app_name=app_name or "unknown",
            window_title=window_title or "unknown",
            ocr_text=ocr_text,
            priority=1.0,
        )
        inference = self._provider.analyze(task)
        with self._lock:
            self._latest_by_monitor[inference.monitor] = inference
            self._history.append(inference)
            self._processed += 1
        return inference.to_dict()

    def query(
        self,
        question: str,
        *,
        at_ms: Optional[int] = None,
        window_seconds: float = 30,
        monitor_id: Optional[int] = None,
    ) -> dict:
        """Keyword-match query over inference history."""
        question = (question or "").strip()
        if not question:
            return {"answer": "question is required", "confidence": 0.0, "evidence_refs": []}

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
                "confidence": 0.0, "evidence_refs": [],
            }

        q_words = {w.lower() for w in question.split() if len(w) > 2}
        scored = []
        for inf in candidates:
            text = " ".join([inf.summary] + [f.text for f in inf.facts]).lower()
            overlap = sum(1 for w in q_words if w in text)
            age_s = max(0, (now_ms - inf.timestamp_ms) / 1000.0)
            recency = max(0.0, 1.0 - age_s * 0.01)
            scored.append((overlap + inf.confidence + recency, inf))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]

        return {
            "answer": best.summary,
            "confidence": round(max(0.1, min(0.95, float(best.confidence))), 3),
            "evidence_refs": [best.evidence_ref],
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
                "processed_by_monitor": dict(self._processed_by_monitor),
                "dropped": self._dropped,
                "failures": self._failures,
                "history_size": len(self._history),
                "latest_monitors": sorted(self._latest_by_monitor.keys()),
            }
