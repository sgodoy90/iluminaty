"""
ILUMINATY - UI Semantics Engine (Phase B)
=========================================
Low-cost semantic checks for UI precision:
- OCR policy by task criticality/phase (adaptive zoom hints)
- Target interactability checks (UI tree first)
- Overlay/z-layer guard heuristics for click-like actions
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


INTERACTIVE_ROLES = {
    "button",
    "menuitem",
    "menu_item",
    "checkbox",
    "radiobutton",
    "radio",
    "combobox",
    "edit",
    "textfield",
    "text_field",
    "tab",
    "tabitem",
    "hyperlink",
    "link",
    "listitem",
    "treeitem",
    "slider",
    "spinner",
}

OVERLAY_ROLES = {
    "dialog",
    "window",
    "pane",
    "modal",
    "tooltip",
    "alert",
}

CLICK_LIKE = {"click", "double_click", "right_click", "click_screen"}

VISUAL_INTERACTIVE_KEYWORDS = {
    "ok",
    "accept",
    "confirm",
    "continue",
    "yes",
    "allow",
    "save",
    "retry",
    "open",
    "apply",
    "submit",
    "send",
    "cancel",
    "close",
    "next",
    "back",
    "search",
    "download",
    "upload",
}

VISUAL_BLOCKER_KEYWORDS = {
    "loading",
    "wait",
    "please wait",
    "error",
    "warning",
    "failed",
    "not responding",
    "disabled",
    "read-only",
}


@dataclass
class OCRPolicy:
    zoom_factor: float
    native_preferred: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "zoom_factor": round(float(self.zoom_factor), 2),
            "native_preferred": bool(self.native_preferred),
            "reason": str(self.reason),
        }


class UISemanticsEngine:
    """
    Lightweight semantic checks built on top of existing UI tree / vision stack.
    """

    def __init__(self):
        self._ui_tree = None
        self._vision = None
        self._monitor_mgr = None
        self._buffer = None

    def set_layers(self, *, ui_tree=None, vision=None, monitor_mgr=None, buffer=None) -> None:
        self._ui_tree = ui_tree
        self._vision = vision
        self._monitor_mgr = monitor_mgr
        self._buffer = buffer

    def ocr_policy(
        self,
        *,
        task_phase: Optional[str],
        criticality: Optional[str] = None,
        action: Optional[str] = None,
    ) -> OCRPolicy:
        phase = _norm(task_phase)
        crit = _norm(criticality)
        act = _norm(action)

        # Base policy.
        zoom = 1.0
        native = False
        reason = "default"

        if phase in {"loading", "interaction", "editing"}:
            zoom = 1.8
            native = True
            reason = "phase_precision"
        if phase in {"verification", "validation"}:
            zoom = 2.2
            native = True
            reason = "phase_verification"
        if crit in {"high", "critical"}:
            zoom = max(zoom, 2.5)
            native = True
            reason = "criticality_high"
        elif crit in {"medium", "normal"}:
            zoom = max(zoom, 1.6)
            reason = "criticality_normal"
        elif crit in {"low", "background"}:
            zoom = min(zoom, 1.3)
            native = False
            reason = "criticality_low"

        if act in {"click", "double_click", "right_click", "type_text", "click_screen"}:
            zoom = max(zoom, 1.8)
            native = True
            reason = "action_precision"

        return OCRPolicy(
            zoom_factor=_clamp(zoom, 1.0, 3.0),
            native_preferred=bool(native),
            reason=reason,
        )

    def evaluate_target(
        self,
        *,
        x: int,
        y: int,
        monitor_id: Optional[int],
        action: str,
        mode: str,
        task_phase: Optional[str] = None,
    ) -> dict:
        action_norm = _norm(action)
        mode_norm = _norm(mode) or "safe"
        if action_norm not in CLICK_LIKE:
            return {
                "allowed": True,
                "applies": False,
                "reason": "not_click_like",
            }

        if not self._ui_tree or not getattr(self._ui_tree, "available", False):
            fallback = self._evaluate_visual_fallback(
                x=int(x),
                y=int(y),
                monitor_id=monitor_id,
                mode=mode_norm,
                task_phase=task_phase,
            )
            if fallback is not None:
                return fallback
            return {
                "allowed": True,
                "applies": True,
                "reason": "ui_tree_unavailable",
                "interactable": None,
                "occluded": None,
                "target_role": None,
                "target_name": None,
                "containing_count": 0,
            }

        try:
            elements = self._ui_tree.get_elements(max_depth=5) or []
        except Exception:
            elements = []

        containing = []
        tx = int(x)
        ty = int(y)
        for el in elements[:400]:
            ex = int(el.get("x", 0))
            ey = int(el.get("y", 0))
            ew = max(1, int(el.get("width", 1)))
            eh = max(1, int(el.get("height", 1)))
            if ex <= tx <= (ex + ew) and ey <= ty <= (ey + eh):
                area = int(ew * eh)
                containing.append((area, el))

        if not containing:
            return {
                "allowed": True,
                "applies": True,
                "reason": "no_ui_elements_at_target",
                "interactable": None,
                "occluded": None,
                "target_role": None,
                "target_name": None,
                "containing_count": 0,
            }

        # Heuristic: smaller containing element is usually the actionable topmost target.
        containing.sort(key=lambda item: item[0])
        top = containing[0][1]
        top_role = _norm(top.get("role"))
        top_name = str(top.get("name", ""))[:140]
        top_enabled = bool(top.get("is_enabled", True))

        interactive_candidates = []
        overlay_candidates = []
        for _, el in containing[:32]:
            role = _norm(el.get("role"))
            if role in INTERACTIVE_ROLES and bool(el.get("is_enabled", True)):
                interactive_candidates.append(el)
            if role in OVERLAY_ROLES:
                overlay_candidates.append(el)

        interactable = bool(top_role in INTERACTIVE_ROLES and top_enabled)
        occluded = bool(
            (top_role in OVERLAY_ROLES and len(interactive_candidates) == 0)
            or (top_role in OVERLAY_ROLES and len(interactive_candidates) > 0 and _norm(interactive_candidates[0].get("role")) != top_role)
            or (len(overlay_candidates) > 0 and len(interactive_candidates) == 0 and top_role not in INTERACTIVE_ROLES)
        )

        allowed = True
        reason = "ok"
        if mode_norm != "raw":
            if occluded:
                allowed = False
                reason = "blocked_by_overlay"
            elif not interactable:
                # In SAFE/HYBRID we require a reasonable interactability signal.
                # Do not block hard on navigation phase to avoid over-filtering.
                phase = _norm(task_phase)
                if phase not in {"navigation", "idle"}:
                    allowed = False
                    reason = "target_not_interactable"
                else:
                    reason = "non_interactable_navigation_tolerated"

        return {
            "allowed": bool(allowed),
            "applies": True,
            "reason": reason,
            "interactable": bool(interactable),
            "occluded": bool(occluded),
            "target_role": top_role or None,
            "target_name": top_name or None,
            "monitor_id": int(monitor_id) if monitor_id is not None else None,
            "containing_count": len(containing),
            "interactive_candidates": min(8, len(interactive_candidates)),
            "overlay_candidates": min(8, len(overlay_candidates)),
        }

    def _latest_slot(self, monitor_id: Optional[int]):
        if self._buffer is None:
            return None
        if monitor_id is not None and hasattr(self._buffer, "get_latest_for_monitor"):
            try:
                slot = self._buffer.get_latest_for_monitor(int(monitor_id))
                if slot is not None:
                    return slot
            except Exception:
                pass
        try:
            return self._buffer.get_latest()
        except Exception:
            return None

    def _extract_visual_probe_text(self, slot, x: int, y: int) -> tuple[str, int]:
        if slot is None or self._vision is None:
            return "", 0
        ocr = getattr(self._vision, "ocr", None)
        if ocr is None or not getattr(ocr, "available", False):
            return "", 0

        width = max(120, min(int(getattr(slot, "width", 0) or 0), 260))
        height = max(60, min(int(getattr(slot, "height", 0) or 0), 140))
        half_w = width // 2
        half_h = height // 2
        rx = max(0, int(x) - half_w)
        ry = max(0, int(y) - half_h)
        max_x = max(0, int(getattr(slot, "width", 0) or 0) - width)
        max_y = max(0, int(getattr(slot, "height", 0) or 0) - height)
        rx = min(rx, max_x)
        ry = min(ry, max_y)

        try:
            result = ocr.extract_region(
                slot.frame_bytes,
                int(rx),
                int(ry),
                int(width),
                int(height),
                zoom_factor=2.0,
            )
        except Exception:
            return "", 0

        text = ""
        block_count = 0
        if isinstance(result, dict):
            text = str(result.get("text", "") or "")
            blocks = result.get("blocks") or []
            try:
                block_count = int(len(blocks))
            except Exception:
                block_count = 0
        return text, block_count

    def _evaluate_visual_fallback(
        self,
        *,
        x: int,
        y: int,
        monitor_id: Optional[int],
        mode: str,
        task_phase: Optional[str],
    ) -> Optional[dict]:
        slot = self._latest_slot(monitor_id)
        if slot is None:
            return None

        probe_text, block_count = self._extract_visual_probe_text(slot, x, y)
        text_norm = _norm(probe_text)
        if not text_norm:
            return {
                "allowed": True,
                "applies": True,
                "reason": "ui_visual_fallback_no_text",
                "interactable": None,
                "occluded": None,
                "target_role": None,
                "target_name": None,
                "monitor_id": int(monitor_id) if monitor_id is not None else None,
                "containing_count": 0,
                "interactive_candidates": 0,
                "overlay_candidates": 0,
                "fallback_source": "visual_ocr",
                "fallback_confidence": 0.0,
            }

        # Tokenize while preserving common UI words.
        tokens = set(re.findall(r"[a-zA-Z0-9\-\_]+", text_norm))
        interactive_hits = sorted([k for k in VISUAL_INTERACTIVE_KEYWORDS if k in tokens or k in text_norm])
        blocker_hits = sorted([k for k in VISUAL_BLOCKER_KEYWORDS if k in text_norm])

        interactable = bool(interactive_hits)
        occluded = bool(blocker_hits and not interactive_hits)
        confidence = 0.35
        if interactable:
            confidence += 0.35
        if occluded:
            confidence += 0.25
        if block_count > 0:
            confidence += 0.05
        confidence = _clamp(confidence, 0.0, 1.0)

        phase = _norm(task_phase)
        allowed = True
        reason = "ui_visual_fallback_uncertain"
        if interactable:
            reason = "ui_visual_fallback_interactable"
        elif occluded:
            reason = "ui_visual_fallback_blocker"

        # Conservative blocking policy: only block on strong blocker signal in SAFE/HYBRID.
        if mode != "raw" and occluded and confidence >= 0.75 and phase not in {"navigation", "idle"}:
            allowed = False

        return {
            "allowed": bool(allowed),
            "applies": True,
            "reason": reason,
            "interactable": bool(interactable),
            "occluded": bool(occluded),
            "target_role": None,
            "target_name": None,
            "monitor_id": int(monitor_id) if monitor_id is not None else None,
            "containing_count": 0,
            "interactive_candidates": len(interactive_hits),
            "overlay_candidates": len(blocker_hits),
            "fallback_source": "visual_ocr",
            "fallback_confidence": round(float(confidence), 3),
            "fallback_text_preview": probe_text[:200],
            "fallback_interactive_hits": interactive_hits[:8],
            "fallback_blocker_hits": blocker_hits[:8],
        }
