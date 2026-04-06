"""
uia_backend.py — Cross-platform UI Automation backend for ILUMINATY.

Platform dispatch:
  Windows → UI Automation (UIA) via comtypes + UIAutomationCore.dll
  macOS   → Accessibility API (AXUIElement) via pyobjc
  Linux   → AT-SPI2 via pyatspi

Public API (used by mcp_server.py):
  get_focused_element()  → ElementInfo | None
  get_element_at(gx, gy) → ElementInfo | None
  find_all_interactive(hwnd_or_title) → list[ElementInfo]

ElementInfo is a plain dict:
  {
    "name":         str,
    "control_type": str,   # human-readable: "Edit/Input", "Button", etc.
    "control_type_id": int,
    "bounding_rect": {"left", "top", "right", "bottom"},
    "center_global": {"x", "y"},
  }
Monitor-relative coords are resolved in mcp_server.py (needs mss which is
always available), not here, to keep this module dependency-free.
"""

from __future__ import annotations
import platform
import sys
from typing import Optional

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

CT_NAMES_WIN = {
    50000: "Button",        50001: "Calendar",      50002: "CheckBox",
    50003: "ComboBox",      50004: "Edit/Input",    50005: "Hyperlink",
    50006: "Image",         50007: "ListItem",      50008: "List",
    50009: "RadioButton",   50010: "ScrollBar",     50011: "Slider",
    50012: "Spinner",       50013: "RadioButton",   50014: "StatusBar",
    50015: "Tab",           50016: "Spinner",       50017: "Text",
    50018: "ToolBar",       50019: "ToolTip",       50020: "Tree/ListItem",
    50021: "TreeItem",      50022: "Custom",        50023: "Group",
    50024: "Thumb",         50025: "DataGrid",      50026: "DataItem",
    50027: "Document/TextArea", 50028: "SplitButton", 50029: "Window",
    50030: "Pane",          50031: "Header",        50032: "HeaderItem",
    50033: "Table",         50034: "TitleBar",      50035: "Separator",
}

INTERACTIVE_WIN = {
    50000, 50002, 50003, 50004, 50005,
    50009, 50011, 50012, 50013, 50016, 50028,
    50027,  # Document (RichEdit, code editors)
    50030,  # Pane — included only when it has a non-empty name (e.g. 'Editor de texto')
}

def _make_elem(name, ct_name, ct_id, left, top, right, bottom) -> dict:
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    return {
        "name":            name or "",
        "control_type":    ct_name,
        "control_type_id": ct_id,
        "bounding_rect":   {"left": left, "top": top, "right": right, "bottom": bottom},
        "center_global":   {"x": cx, "y": cy},
    }


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------

_win_uia  = None   # cached IUIAutomation COM object
_win_UIA  = None   # cached UIAutomationClient module

def _win_init():
    global _win_uia, _win_UIA
    if _win_uia is not None:
        return _win_uia, _win_UIA
    import comtypes.client as cc
    try:
        from comtypes.gen import UIAutomationClient as UIA
    except ImportError:
        cc.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
    _win_uia = cc.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    _win_UIA = UIA
    return _win_uia, _win_UIA


def _win_elem_to_dict(elem) -> Optional[dict]:
    try:
        name = elem.CurrentName or ""
        ct   = elem.CurrentControlType
        r    = elem.CurrentBoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return _make_elem(name, CT_NAMES_WIN.get(ct, f"Unknown({ct})"), ct,
                          r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _win_focused() -> Optional[dict]:
    uia, _ = _win_init()
    elem = uia.GetFocusedElement()
    return _win_elem_to_dict(elem) if elem else None


def _win_element_at(gx: int, gy: int) -> Optional[dict]:
    import ctypes.wintypes as wt
    uia, _ = _win_init()
    pt   = wt.POINT(gx, gy)
    elem = uia.ElementFromPoint(pt)
    return _win_elem_to_dict(elem) if elem else None


def _win_find_all(window_title: str = "") -> list[dict]:
    import ctypes
    import ctypes.wintypes as _cwt
    _u32 = ctypes.windll.user32
    uia, UIA = _win_init()

    def _best_hwnd_for_pid(seed_hwnd: int) -> int:
        """Given any hwnd, find the hwnd in the same process with most UIA descendants."""
        pid_buf = ctypes.c_ulong()
        _u32.GetWindowThreadProcessId(seed_hwnd, ctypes.byref(pid_buf))
        target_pid = pid_buf.value
        proc_hwnds: list = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        def _enum(hwnd, _):
            p = ctypes.c_ulong()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == target_pid:
                proc_hwnds.append(hwnd)
            return True
        _u32.EnumWindows(_enum, 0)

        best, best_n = seed_hwnd, -1
        cond = uia.CreateTrueCondition()
        for h in proc_hwnds:
            try:
                e = uia.ElementFromHandle(h)
                n = e.FindAll(UIA.TreeScope_Descendants, cond).Length
                if n > best_n:
                    best_n, best = n, h
            except Exception:
                continue
        return best

    if window_title:
        tl = window_title.lower()
        candidates: list = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        def _enum_title(hwnd, _):
            if not _u32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(512)
            _u32.GetWindowTextW(hwnd, buf, 512)
            if tl in buf.value.lower():
                candidates.append(hwnd)
            return True
        _u32.EnumWindows(_enum_title, 0)

        # Fallback: search by window class name (e.g. 'Brave' → Chrome_WidgetWin_1)
        CLASS_ALIASES = {
            "brave": "Chrome_WidgetWin_1",
            "chrome": "Chrome_WidgetWin_1",
            "edge": "Chrome_WidgetWin_1",
            "firefox": "MozillaWindowClass",
        }
        if not candidates:
            cls_name = CLASS_ALIASES.get(tl)
            if cls_name:
                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                def _enum_cls(hwnd, _):
                    if not _u32.IsWindowVisible(hwnd):
                        return True
                    cls_buf = ctypes.create_unicode_buffer(256)
                    _u32.GetClassNameW(hwnd, cls_buf, 256)
                    if cls_buf.value == cls_name:
                        candidates.append(hwnd)
                    return True
                _u32.EnumWindows(_enum_cls, 0)

        if not candidates:
            return []

        target = uia.ElementFromHandle(_best_hwnd_for_pid(candidates[0]))
    else:
        fg = _u32.GetForegroundWindow()
        if not fg:
            return []
        target = uia.ElementFromHandle(_best_hwnd_for_pid(fg))

    cond_all = uia.CreateTrueCondition()
    all_e    = target.FindAll(UIA.TreeScope_Descendants, cond_all)

    results, seen = [], set()
    for i in range(all_e.Length):
        try:
            e  = all_e.GetElement(i)
            ct = e.CurrentControlType
            if ct not in INTERACTIVE_WIN:
                continue
            # Skip unnamed Panes — too generic, causes false positives
            if ct == 50030 and not (e.CurrentName or "").strip():
                continue
            d = _win_elem_to_dict(e)
            if d is None:
                continue
            key = (d["bounding_rect"]["left"], d["bounding_rect"]["top"],
                   d["bounding_rect"]["right"], d["bounding_rect"]["bottom"])
            if key in seen:
                continue
            seen.add(key)
            results.append(d)
        except Exception:
            continue  # element destroyed mid-scan (dynamic DOM update)
    return results


# ---------------------------------------------------------------------------
# macOS backend  (requires: pip install pyobjc-framework-ApplicationServices)
# ---------------------------------------------------------------------------

AX_ROLE_MAP = {
    "AXButton":        "Button",
    "AXCheckBox":      "CheckBox",
    "AXRadioButton":   "RadioButton",
    "AXTextField":     "Edit/Input",
    "AXTextArea":      "Edit/Input",
    "AXSearchField":   "Edit/Input",
    "AXComboBox":      "ComboBox",
    "AXPopUpButton":   "ComboBox",
    "AXSlider":        "Slider",
    "AXLink":          "Hyperlink",
    "AXMenuButton":    "SplitButton",
    "AXIncrementor":   "Spinner",
}
AX_INTERACTIVE = set(AX_ROLE_MAP.keys())


def _mac_elem_to_dict(elem) -> Optional[dict]:
    """Convert a pyobjc AXUIElement to ElementInfo dict."""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            kAXRoleAttribute, kAXTitleAttribute, kAXValueAttribute,
            kAXPositionAttribute, kAXSizeAttribute,
        )
        import ctypes

        def _attr(el, key):
            err, val = AXUIElementCopyAttributeValue(el, key, None)
            return val if err == 0 else None

        role  = _attr(elem, kAXRoleAttribute) or ""
        title = _attr(elem, kAXTitleAttribute) or _attr(elem, kAXValueAttribute) or ""
        pos   = _attr(elem, kAXPositionAttribute)
        size  = _attr(elem, kAXSizeAttribute)

        if pos is None or size is None:
            return None

        # pos/size are NSPoint/NSSize — access via .x .y .width .height
        x, y = int(pos.x), int(pos.y)
        w, h = int(size.width), int(size.height)
        if w <= 0 or h <= 0:
            return None

        ct_name = AX_ROLE_MAP.get(role, role or "Unknown")
        return _make_elem(str(title), ct_name, hash(role) & 0xFFFF,
                          x, y, x + w, y + h)
    except Exception:
        return None


def _mac_focused() -> Optional[dict]:
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
            kAXFocusedUIElementAttribute,
        )
        sys_el = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(
            sys_el, kAXFocusedUIElementAttribute, None
        )
        if err != 0 or focused is None:
            return None
        return _mac_elem_to_dict(focused)
    except Exception:
        return None


def _mac_element_at(gx: int, gy: int) -> Optional[dict]:
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyElementAtPosition,
        )
        sys_el = AXUIElementCreateSystemWide()
        err, elem = AXUIElementCopyElementAtPosition(sys_el, float(gx), float(gy), None)
        if err != 0 or elem is None:
            return None
        return _mac_elem_to_dict(elem)
    except Exception:
        return None


def _mac_find_all(window_title: str = "") -> list[dict]:
    """Walk AX tree of the frontmost app (or app matching window_title)."""
    try:
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            AXUIElementCopyAttributeNames,
            kAXWindowsAttribute, kAXTitleAttribute,
            kAXChildrenAttribute, kAXRoleAttribute,
            kAXFocusedApplicationAttribute,
        )
        import subprocess, json

        # Get frontmost app PID
        script = (
            'tell application "System Events" to get unix id of first process '
            'whose frontmost is true'
        )
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=3)
        pid = int(r.stdout.strip())
        app_elem = AXUIElementCreateApplication(pid)

        def _attr(el, key):
            err, val = AXUIElementCopyAttributeValue(el, key, None)
            return val if err == 0 else None

        # Find target window
        wins = _attr(app_elem, kAXWindowsAttribute) or []
        target = None
        for w in wins:
            t = _attr(w, kAXTitleAttribute) or ""
            if not window_title or window_title.lower() in t.lower():
                target = w
                break
        if target is None:
            return []

        # BFS walk
        results, seen = [], set()
        queue = [target]
        while queue:
            el = queue.pop(0)
            role = _attr(el, kAXRoleAttribute) or ""
            if role in AX_INTERACTIVE:
                d = _mac_elem_to_dict(el)
                if d:
                    key = (d["bounding_rect"]["left"], d["bounding_rect"]["top"],
                           d["bounding_rect"]["right"], d["bounding_rect"]["bottom"])
                    if key not in seen:
                        seen.add(key)
                        results.append(d)
            children = _attr(el, kAXChildrenAttribute) or []
            queue.extend(children)
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Linux backend  (requires: pip install pyatspi)
# ---------------------------------------------------------------------------

ATSPI_ROLE_MAP = {
    "push button":    "Button",
    "check box":      "CheckBox",
    "radio button":   "RadioButton",
    "text":           "Edit/Input",
    "entry":          "Edit/Input",
    "password text":  "Edit/Input",
    "combo box":      "ComboBox",
    "link":           "Hyperlink",
    "slider":         "Slider",
    "spin button":    "Spinner",
    "menu item":      "Button",
    "toggle button":  "CheckBox",
}
ATSPI_INTERACTIVE = set(ATSPI_ROLE_MAP.keys())


def _linux_elem_to_dict(acc) -> Optional[dict]:
    try:
        import pyatspi
        role_name = acc.getRoleName().lower()
        ct_name   = ATSPI_ROLE_MAP.get(role_name, role_name or "Unknown")
        name      = acc.name or ""
        ext       = acc.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        x, y, w, h = ext.x, ext.y, ext.width, ext.height
        if w <= 0 or h <= 0:
            return None
        return _make_elem(name, ct_name, hash(role_name) & 0xFFFF,
                          x, y, x + w, y + h)
    except Exception:
        return None


def _linux_focused() -> Optional[dict]:
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            if app is None:
                continue
            try:
                focused = app.queryComponent().getAccessibleAtPoint(0, 0,
                              pyatspi.DESKTOP_COORDS)
            except Exception:
                continue
        # Correct approach: use focus tracker
        focused = pyatspi.findAncestor(
            pyatspi.Registry.getDesktop(0),
            lambda x: x.getState().contains(pyatspi.STATE_FOCUSED)
        )
        return _linux_elem_to_dict(focused) if focused else None
    except Exception:
        return None


def _linux_element_at(gx: int, gy: int) -> Optional[dict]:
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            if app is None:
                continue
            for i in range(app.childCount):
                try:
                    win = app.getChildAtIndex(i)
                    comp = win.queryComponent()
                    elem = comp.getAccessibleAtPoint(gx, gy, pyatspi.DESKTOP_COORDS)
                    if elem:
                        return _linux_elem_to_dict(elem)
                except Exception:
                    continue
        return None
    except Exception:
        return None


def _linux_find_all(window_title: str = "") -> list[dict]:
    try:
        import pyatspi

        def _walk(node, results, seen, depth=0):
            if depth > 30:
                return
            try:
                role = node.getRoleName().lower()
                if role in ATSPI_INTERACTIVE:
                    d = _linux_elem_to_dict(node)
                    if d:
                        key = (d["bounding_rect"]["left"], d["bounding_rect"]["top"],
                               d["bounding_rect"]["right"], d["bounding_rect"]["bottom"])
                        if key not in seen:
                            seen.add(key)
                            results.append(d)
                for i in range(node.childCount):
                    child = node.getChildAtIndex(i)
                    if child:
                        _walk(child, results, seen, depth + 1)
            except Exception:
                pass

        desktop = pyatspi.Registry.getDesktop(0)
        results, seen = [], set()
        for app in desktop:
            if app is None:
                continue
            for i in range(app.childCount):
                try:
                    win = app.getChildAtIndex(i)
                    title = win.name or ""
                    if not window_title or window_title.lower() in title.lower():
                        _walk(win, results, seen)
                except Exception:
                    continue
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API — OS dispatch
# ---------------------------------------------------------------------------

def get_focused_element() -> Optional[dict]:
    if _OS == "Windows":
        return _win_focused()
    elif _OS == "Darwin":
        return _mac_focused()
    else:
        return _linux_focused()


def get_element_at(gx: int, gy: int) -> Optional[dict]:
    if _OS == "Windows":
        return _win_element_at(gx, gy)
    elif _OS == "Darwin":
        return _mac_element_at(gx, gy)
    else:
        return _linux_element_at(gx, gy)


def find_all_interactive(window_title: str = "") -> list[dict]:
    if _OS == "Windows":
        return _win_find_all(window_title)
    elif _OS == "Darwin":
        return _mac_find_all(window_title)
    else:
        return _linux_find_all(window_title)


def os_name() -> str:
    return _OS
