"""
ILUMINATY - Capa 1: Window Manager
====================================
Control directo de ventanas del SO: mover, resize, focus,
minimize, maximize, close, listar.

Windows: ctypes user32.dll
macOS: AppleScript via subprocess
Linux: wmctrl + xdotool via subprocess
"""

import sys
import logging
import time
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """Informacion de una ventana."""
    handle: int
    title: str
    pid: int
    x: int
    y: int
    width: int
    height: int
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    app_name: str = ""

    def to_dict(self) -> dict:
        return {
            "handle": self.handle,
            "title": self.title,
            "pid": self.pid,
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
            "app_name": self.app_name,
        }


class WindowManager:
    """
    Gestion de ventanas cross-platform.
    Acceso directo via APIs del SO, no screenshots.
    """

    def __init__(self):
        self._platform = sys.platform
        self._user32 = None
        self._psapi = None

        if self._platform == "win32":
            try:
                import ctypes
                self._user32 = ctypes.windll.user32
                self._psapi = ctypes.windll.psapi
                self._ctypes = ctypes
            except Exception as e:
                logger.debug("Windows API bridge init failed: %s", e)

    @property
    def available(self) -> bool:
        if self._platform == "win32":
            return self._user32 is not None
        return True  # macOS/Linux use subprocess

    # ─── List Windows ───

    def list_windows(self) -> list[WindowInfo]:
        """Lista todas las ventanas visibles."""
        if self._platform == "win32":
            return self._list_windows_win()
        elif self._platform == "darwin":
            return self._list_windows_mac()
        return self._list_windows_linux()

    def _list_windows_win(self) -> list[WindowInfo]:
        windows = []
        ctypes = self._ctypes

        def callback(hwnd, _):
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if not title:
                return True

            rect = ctypes.wintypes.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))

            pid = ctypes.wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            # Define WINDOWPLACEMENT struct (not in ctypes.wintypes)
            class WINDOWPLACEMENT(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_uint),
                    ("flags", ctypes.c_uint),
                    ("showCmd", ctypes.c_uint),
                    ("ptMinPosition", ctypes.wintypes.POINT),
                    ("ptMaxPosition", ctypes.wintypes.POINT),
                    ("rcNormalPosition", ctypes.wintypes.RECT),
                ]

            placement = WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(placement)
            self._user32.GetWindowPlacement(hwnd, ctypes.byref(placement))

            is_minimized = placement.showCmd == 2  # SW_SHOWMINIMIZED
            is_maximized = placement.showCmd == 3  # SW_SHOWMAXIMIZED

            windows.append(WindowInfo(
                handle=hwnd,
                title=title,
                pid=pid.value,
                x=rect.left, y=rect.top,
                width=rect.right - rect.left,
                height=rect.bottom - rect.top,
                is_visible=True,
                is_minimized=is_minimized,
                is_maximized=is_maximized,
            ))
            return True

        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            self._user32.EnumWindows(WNDENUMPROC(callback), 0)
        except Exception as e:
            logger.debug("EnumWindows failed: %s", e)
        return windows

    def _list_windows_mac(self) -> list[WindowInfo]:
        try:
            script = '''
            tell application "System Events"
                set windowList to {}
                repeat with proc in (every process whose visible is true)
                    repeat with win in (every window of proc)
                        set end of windowList to {name of win, name of proc, position of win, size of win}
                    end repeat
                end repeat
                return windowList
            end tell
            '''
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=5)
            # Parse AppleScript output (simplified)
            return []  # TODO: parse AppleScript list output
        except Exception:
            return []

    def _list_windows_linux(self) -> list[WindowInfo]:
        try:
            result = subprocess.run(["wmctrl", "-l", "-G", "-p"],
                                    capture_output=True, text=True, timeout=3)
            windows = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(None, 8)
                if len(parts) >= 9:
                    windows.append(WindowInfo(
                        handle=int(parts[0], 16),
                        title=parts[8],
                        pid=int(parts[2]),
                        x=int(parts[3]), y=int(parts[4]),
                        width=int(parts[5]), height=int(parts[6]),
                        is_visible=True,
                        is_minimized=False,
                        is_maximized=False,
                    ))
            return windows
        except Exception:
            return []

    # ─── Window Actions ───

    def focus_window(self, title: Optional[str] = None, handle: Optional[int] = None) -> bool:
        """Trae una ventana al frente."""
        if handle is None and title:
            handle = self._find_by_title(title)
        if handle is None:
            return False

        if self._platform == "win32":
            self._user32.ShowWindow(handle, 9)  # SW_RESTORE
            return bool(self._user32.SetForegroundWindow(handle))
        elif self._platform == "darwin":
            return self._applescript_window_action(title, "set index to 1")
        else:
            return self._run_cmd(["wmctrl", "-i", "-a", hex(handle)])

    def minimize_window(self, title: Optional[str] = None, handle: Optional[int] = None) -> bool:
        """Minimiza una ventana."""
        if handle is None and title:
            handle = self._find_by_title(title)
        if handle is None:
            return False

        if self._platform == "win32":
            return bool(self._user32.ShowWindow(handle, 6))  # SW_MINIMIZE
        elif self._platform == "darwin":
            return self._applescript_window_action(title, "set miniaturized to true")
        else:
            return self._run_cmd(["xdotool", "windowminimize", str(handle)])

    def maximize_window(self, title: Optional[str] = None, handle: Optional[int] = None) -> bool:
        """Maximiza una ventana."""
        if handle is None and title:
            handle = self._find_by_title(title)
        if handle is None:
            return False

        if self._platform == "win32":
            return bool(self._user32.ShowWindow(handle, 3))  # SW_MAXIMIZE
        elif self._platform == "darwin":
            return self._applescript_window_action(title, "set zoomed to true")
        else:
            return self._run_cmd(["wmctrl", "-i", "-r", hex(handle), "-b", "add,maximized_vert,maximized_horz"])

    def close_window(self, title: Optional[str] = None, handle: Optional[int] = None) -> bool:
        """Cierra una ventana (DESTRUCTIVE - requiere confirmacion)."""
        if handle is None and title:
            handle = self._find_by_title(title)
        if handle is None:
            return False

        if self._platform == "win32":
            WM_CLOSE = 0x0010
            return bool(self._user32.PostMessageW(handle, WM_CLOSE, 0, 0))
        elif self._platform == "darwin":
            return self._applescript_window_action(title, "close")
        else:
            return self._run_cmd(["wmctrl", "-i", "-c", hex(handle)])

    def move_window(self, x: int, y: int, width: int = -1, height: int = -1,
                    title: Optional[str] = None, handle: Optional[int] = None) -> bool:
        """Mueve y/o redimensiona una ventana."""
        if handle is None and title:
            handle = self._find_by_title(title)
        if handle is None:
            return False

        if self._platform == "win32":
            if width < 0 or height < 0:
                rect = self._ctypes.wintypes.RECT()
                self._user32.GetWindowRect(handle, self._ctypes.byref(rect))
                if width < 0:
                    width = rect.right - rect.left
                if height < 0:
                    height = rect.bottom - rect.top
            return bool(self._user32.MoveWindow(handle, x, y, width, height, True))
        elif self._platform == "darwin":
            return self._applescript_window_action(
                title, f"set bounds to {{{x}, {y}, {x + width}, {y + height}}}")
        else:
            return self._run_cmd(["wmctrl", "-i", "-r", hex(handle), "-e",
                                  f"0,{x},{y},{width},{height}"])

    def get_active_window(self) -> Optional[WindowInfo]:
        """Retorna la ventana activa."""
        if self._platform == "win32":
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return None
            for win in self._list_windows_win():
                if win.handle == hwnd:
                    return win
        return None

    # ─── Helpers ───

    def _find_by_title(self, title: str) -> Optional[int]:
        """Busca una ventana por titulo (match parcial case-insensitive)."""
        title_lower = title.lower()
        for win in self.list_windows():
            if title_lower in win.title.lower():
                return win.handle
        return None

    def _applescript_window_action(self, title: str, action: str) -> bool:
        if not title:
            return False
        try:
            # Sanitize title to prevent AppleScript injection
            safe_title = title.replace('"', '').replace('\\', '')
            script = f'''
            tell application "System Events"
                set targetWindow to first window of (first process whose name contains "{safe_title}")
                {action}
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
            return True
        except Exception:
            return False

    def _run_cmd(self, cmd: list[str]) -> bool:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=3)
            return result.returncode == 0
        except Exception:
            return False

    @property
    def stats(self) -> dict:
        windows = self.list_windows()
        return {
            "platform": self._platform,
            "available": self.available,
            "visible_windows": len(windows),
        }
