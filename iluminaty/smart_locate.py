"""
ILUMINATY - Smart Locate Engine
=================================
Resolves "where is X?" without asking the AI to guess coordinates.

Resolution hierarchy (fastest/most precise first):

  1. UIAutomation tree  — exact bbox from OS accessibility (apps nativas, VS Code, dialogs)
  2. OCR text blocks    — exact bbox from RapidOCR (<5ms, works in any app)
  3. Visual fallback    — Set-of-Marks overlay sent to the calling AI (last resort)

The engine returns a LocateResult with:
  - x, y        : center coordinates ready for click
  - w, h        : element size
  - source      : "ui_tree" | "ocr" | "visual_fallback"
  - confidence  : 0.0-1.0
  - label       : human-readable description of what was found

Usage from mcp_server.py:
    from .smart_locate import SmartLocateEngine
    engine = SmartLocateEngine(ui_tree, ocr_fn)
    result = engine.locate("Save button", monitor_id=2)
    if result:
        act(click, result.x, result.y)
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger("iluminaty.smart_locate")


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LocateResult:
    """A resolved UI target with exact coordinates."""
    x: int                          # center x (global desktop coords)
    y: int                          # center y (global desktop coords)
    w: int = 0                      # element width
    h: int = 0                      # element height
    source: str = "unknown"         # ui_tree | ocr | visual_fallback
    confidence: float = 1.0         # 0.0 - 1.0
    label: str = ""                 # what was found
    monitor_id: int = 0
    method_detail: str = ""         # debug: which strategy matched

    def to_dict(self) -> dict:
        return {
            "x": self.x, "y": self.y,
            "w": self.w, "h": self.h,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "label": self.label,
            "monitor_id": self.monitor_id,
            "method_detail": self.method_detail,
        }


# ── Text normalization helpers ─────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Normalize for fuzzy matching: lowercase, strip punctuation."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    """Sequence similarity 0.0-1.0 between two normalized strings."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Substring match — only meaningful if the shorter string has real length
    shorter = min(len(na), len(nb))
    longer  = max(len(na), len(nb))
    if shorter >= 3 and (na in nb or nb in na):
        # Boost only if the shorter is at least 40% of the longer (avoids "c" in "facebook")
        if shorter / longer >= 0.4:
            return 0.85 + 0.15 * (shorter / longer)
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _token_match(query: str, text: str) -> float:
    """Score based on how many query tokens appear in text."""
    q_tokens = _norm(query).split()
    t_text   = _norm(text)
    if not q_tokens:
        return 0.0
    matches = sum(1 for t in q_tokens if t in t_text)
    return matches / len(q_tokens)


def _best_score(query: str, text: str) -> float:
    """Combined score: max of similarity and token match."""
    return max(_similarity(query, text), _token_match(query, text))


# ── Role / hint extraction ────────────────────────────────────────────────────

_ROLE_HINTS = {
    "button": ("button", "btn", "boton", "click", "press", "submit", "send",
                "enviar", "guardar", "save", "cancel", "ok", "accept", "close"),
    "edit":   ("field", "input", "campo", "texto", "text", "email", "password",
                "buscar", "search", "type", "escribir", "enter"),
    "link":   ("link", "enlace", "href", "url"),
    "checkbox": ("checkbox", "check", "toggle", "casilla"),
    "combobox": ("dropdown", "select", "lista", "combo"),
    "tab":    ("tab", "pestaña"),
    "menuitem": ("menu", "option", "opcion"),
}


def _infer_role(query: str) -> Optional[str]:
    """Guess the UI role from the query description."""
    q = _norm(query)
    for role, hints in _ROLE_HINTS.items():
        if any(h in q for h in hints):
            return role
    return None


# ── Smart Locate Engine ───────────────────────────────────────────────────────

class SmartLocateEngine:
    """
    Resolves a natural-language target description to exact (x, y) coordinates.

    Tries three strategies in order — stops at the first success:
      1. UIAutomation tree (fastest for native apps)
      2. OCR text blocks  (fastest for web/Electron/any rendered text)
      3. Returns None     (caller should fall back to visual estimate)

    Thread-safe: each call is independent, no shared mutable state.
    """

    # Minimum similarity threshold to accept a match
    SCORE_THRESHOLD_TREE = 0.55
    SCORE_THRESHOLD_OCR  = 0.65
    _OCR_CACHE_TTL  = 2.0
    _TREE_CACHE_TTL = 0.8

    def __init__(
        self,
        ui_tree=None,
        ocr_fn: Optional[Callable] = None,  # kept for API compatibility, no longer called
        monitor_bounds: Optional[dict] = None,
    ):
        self._tree = ui_tree
        self._monitor_bounds = monitor_bounds or {}
        # Instance-level caches (survive across requests when engine lives in _state)
        self._ocr_cache: dict = {}
        self._tree_cache: dict = {}

    def locate(
        self,
        query: str,
        monitor_id: Optional[int] = None,
        prefer_role: Optional[str] = None,
        active_window_pid: Optional[int] = None,
    ) -> Optional[LocateResult]:
        """
        Main entry point. Returns LocateResult or None if not found.

        Args:
            query:              Natural language description ("Save button", "email field")
            monitor_id:         Preferred monitor (None = any)
            prefer_role:        Role hint if known ("button", "edit", etc.)
            active_window_pid:  PID of the target window for UITree filtering
        """
        if not query:
            return None

        inferred_role = prefer_role or _infer_role(query)

        t0 = time.perf_counter()

        # ── Strategy 1: OCR text blocks (uses perception cache — 0ms when warm) ──
        result = self._locate_via_ocr(query, inferred_role, monitor_id)
        if result:
            log.debug("locate(%r) -> ocr in %.1fms: %s",
                      query, (time.perf_counter()-t0)*1000, result.label)
            return result

        # ── Strategy 2: UIAutomation tree (slower — only if COM native, not PowerShell) ──
        # Skip PowerShell-based UITree as it blocks for 400-800ms
        # COM native (comtypes) is fast (<5ms) and safe to use here
        tree_backend = getattr(self._tree, '_uia', None)
        is_com_native = tree_backend is not None and tree_backend != "powershell"
        if is_com_native and active_window_pid != "skip_tree":
            result = self._locate_via_tree(query, inferred_role, active_window_pid, monitor_id)
            if result:
                log.debug("locate(%r) -> ui_tree in %.1fms: %s",
                          query, (time.perf_counter()-t0)*1000, result.label)
                return result

        log.debug("locate(%r) -> not found (%.1fms)", query, (time.perf_counter()-t0)*1000)
        return None

    # ── UIAutomation tree strategy ────────────────────────────────────────────

    def _locate_via_tree(
        self,
        query: str,
        role: Optional[str],
        pid: Optional[int],
        monitor_id: Optional[int],
    ) -> Optional[LocateResult]:
        if self._tree is None:
            return None

        # Use cached elements if PID matches and cache is fresh
        now = time.perf_counter()
        cached = self._tree_cache
        if (cached.get("elements")
                and (now - cached.get("ts", 0)) < self._TREE_CACHE_TTL
                and cached.get("pid") == pid):
            elements = cached["elements"]
        else:
            try:
                # Limit walk to active window PID when known — 10x faster
                elements = self._tree.get_elements(pid=pid, max_depth=5)
            except Exception as e:
                log.debug("ui_tree.get_elements failed: %s", e)
                return None
            self._tree_cache = {"ts": now, "elements": elements, "pid": pid}

        if not elements:
            return None

        # Score each element
        best_score = 0.0
        best_el = None

        for el in elements:
            name = str(el.get("name") or "")
            el_role = str(el.get("role") or "")
            value = str(el.get("value") or "")
            automation_id = str(el.get("automation_id") or "")

            # Skip elements with no position or zero size
            w = int(el.get("width") or 0)
            h = int(el.get("height") or 0)
            if w <= 0 or h <= 0:
                continue

            # Skip disabled elements
            if el.get("is_enabled") is False:
                continue

            # Score against name, value, automation_id
            score = max(
                _best_score(query, name),
                _best_score(query, value),
                _best_score(query, automation_id) * 0.8,  # lower weight for automation_id
            )

            # Role bonus: if we inferred a role and it matches, boost
            if role and role.lower() in el_role.lower():
                score = min(1.0, score + 0.1)

            # Penalize if role is clearly wrong (e.g. query says "button" but el is "text")
            if role and el_role and role.lower() not in el_role.lower():
                # Only penalize if role inference is high-confidence
                if _token_match(query, role) > 0.8:
                    score *= 0.7

            if score > best_score:
                best_score = score
                best_el = el

        if best_score < self.SCORE_THRESHOLD_TREE or best_el is None:
            return None

        # Convert to center coords
        x = int(best_el.get("x") or 0)
        y = int(best_el.get("y") or 0)
        w = int(best_el.get("width") or 1)
        h = int(best_el.get("height") or 1)
        cx = x + w // 2
        cy = y + h // 2

        # Validate coords are on a real monitor
        if monitor_id is not None and not self._on_monitor(cx, cy, monitor_id):
            return None

        return LocateResult(
            x=cx, y=cy, w=w, h=h,
            source="ui_tree",
            confidence=min(1.0, best_score),
            label=f"{best_el.get('role','?')} '{best_el.get('name','?')}'",
            monitor_id=monitor_id or self._detect_monitor(cx, cy),
            method_detail=f"ui_tree score={best_score:.2f} name='{best_el.get('name')}'",
        )

    # ── OCR strategy ──────────────────────────────────────────────────────────

    def _locate_via_ocr(
        self,
        query: str,
        role: Optional[str],
        monitor_id: Optional[int],
    ) -> Optional[LocateResult]:
        # Only use pre-populated cache — never call ocr_fn directly from here
        # (calling OCR from within locate would block the caller's thread)
        cache_key = monitor_id or 0
        cached = self._ocr_cache.get(cache_key)
        if cached:
            blocks = cached.get("blocks", [])
        elif monitor_id is None:
            # Try any monitor
            if self._ocr_cache:
                cached = next(iter(self._ocr_cache.values()))
                blocks = cached.get("blocks", [])
            else:
                return None  # no OCR data available yet
        else:
            return None  # no OCR data for this monitor yet

        if not blocks:
            return None

        best_score = 0.0
        best_block = None

        for block in blocks:
            text = str(block.get("text") or "")
            if not text.strip():
                continue

            # Exact match gets maximum score
            if _norm(query) == _norm(text):
                score = 1.0
            else:
                score = _best_score(query, text)
                # Penalize long OCR blocks — they're likely sentences, not labels
                # A button label "Google" should beat "...click on Google..."
                text_len = len(_norm(text).split())
                query_len = max(1, len(_norm(query).split()))
                if text_len > query_len * 3:
                    score *= max(0.4, query_len / text_len)

            # For OCR: if the role suggests a button and the text is short
            if role == "button" and len(text) < 25:
                score = min(1.0, score + 0.05)

            if score > best_score:
                best_score = score
                best_block = block

        if best_score < self.SCORE_THRESHOLD_OCR or best_block is None:
            return None

        bx = int(best_block.get("x") or 0)
        by = int(best_block.get("y") or 0)
        bw = int(best_block.get("w") or 1)
        bh = int(best_block.get("h") or 1)

        cx = bx + bw // 2
        cy = by + bh // 2

        return LocateResult(
            x=cx, y=cy, w=bw, h=bh,
            source="ocr",
            confidence=min(1.0, best_score),
            label=f"text '{best_block.get('text')}'",
            monitor_id=monitor_id or self._detect_monitor(cx, cy),
            method_detail=f"ocr score={best_score:.2f} text='{best_block.get('text')}'",
        )

    # ── Monitor helpers ───────────────────────────────────────────────────────

    def _on_monitor(self, x: int, y: int, monitor_id: int) -> bool:
        """Check if (x,y) falls within the given monitor's bounds."""
        bounds = self._monitor_bounds.get(monitor_id)
        if not bounds:
            return True  # can't validate, assume OK
        left = int(bounds.get("left", 0))
        top  = int(bounds.get("top", 0))
        w    = int(bounds.get("width", 1920))
        h    = int(bounds.get("height", 1080))
        return left <= x < left + w and top <= y < top + h

    def _detect_monitor(self, x: int, y: int) -> int:
        """Detect which monitor contains (x, y)."""
        for mid, bounds in self._monitor_bounds.items():
            if self._on_monitor(x, y, mid):
                return mid
        return 1  # fallback

    def update_monitor_bounds(self, bounds: dict) -> None:
        """Update monitor bounds (call after spatial_state refresh)."""
        self._monitor_bounds = bounds
