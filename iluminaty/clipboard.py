"""
ILUMINATY - Capa 1: Clipboard Avanzado
========================================
Leer/escribir clipboard. Texto, imagenes, historial.
Cross-platform: Windows (win32), macOS (pbcopy), Linux (xclip).
"""

import time
import sys
import subprocess
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClipboardEntry:
    """Una entrada en el historial de clipboard."""
    content: str
    content_type: str  # "text", "image", "file"
    timestamp: float
    source: str  # "user", "agent", "unknown"

    def to_dict(self) -> dict:
        return {
            "content": self.content[:200] + ("..." if len(self.content) > 200 else ""),
            "content_type": self.content_type,
            "timestamp": self.timestamp,
            "time_iso": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "source": self.source,
            "length": len(self.content),
        }


class ClipboardManager:
    """
    Clipboard cross-platform con historial.
    Permite leer, escribir, y trackear cambios en el clipboard.
    """

    def __init__(self, history_size: int = 50):
        self._platform = sys.platform
        self._history: deque[ClipboardEntry] = deque(maxlen=history_size)
        self._last_content: str = ""

    @property
    def available(self) -> bool:
        """Verifica si el clipboard esta disponible."""
        try:
            self.read()
            return True
        except Exception:
            return False

    def read(self) -> str:
        """Lee el contenido actual del clipboard (texto)."""
        if self._platform == "win32":
            return self._read_win()
        elif self._platform == "darwin":
            return self._read_mac()
        return self._read_linux()

    def write(self, text: str, source: str = "agent") -> bool:
        """Escribe texto al clipboard."""
        success = False
        if self._platform == "win32":
            success = self._write_win(text)
        elif self._platform == "darwin":
            success = self._write_mac(text)
        else:
            success = self._write_linux(text)

        if success:
            self._history.append(ClipboardEntry(
                content=text,
                content_type="text",
                timestamp=time.time(),
                source=source,
            ))
            self._last_content = text

        return success

    def check_changed(self) -> Optional[ClipboardEntry]:
        """Verifica si el clipboard cambio desde la ultima vez. Retorna la nueva entrada o None."""
        try:
            current = self.read()
            if current and current != self._last_content:
                entry = ClipboardEntry(
                    content=current,
                    content_type="text",
                    timestamp=time.time(),
                    source="user",
                )
                self._history.append(entry)
                self._last_content = current
                return entry
        except Exception:
            pass
        return None

    def get_history(self, count: int = 20) -> list[dict]:
        """Retorna las ultimas N entradas del historial."""
        items = list(self._history)[-count:]
        return [e.to_dict() for e in reversed(items)]

    def clear_history(self):
        """Limpia el historial."""
        self._history.clear()

    # ─── Platform implementations ───

    def _read_win(self) -> str:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard(0)
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if not handle:
                    return ""
                data = ctypes.c_wchar_p(handle)
                return data.value or ""
            finally:
                user32.CloseClipboard()
        except Exception:
            # Fallback: usar powershell
            try:
                result = subprocess.run(
                    ["powershell", "-command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=3
                )
                return result.stdout.strip()
            except Exception:
                return ""

    def _write_win(self, text: str) -> bool:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard(0)
            try:
                user32.EmptyClipboard()
                data = text.encode("utf-16-le") + b"\x00\x00"
                h_mem = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
                ptr = kernel32.GlobalLock(h_mem)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(13, h_mem)  # CF_UNICODETEXT
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            # Fallback: usar powershell (pass text via stdin to avoid injection)
            try:
                subprocess.run(
                    ["powershell", "-Command", "Set-Clipboard"],
                    input=text, text=True,
                    capture_output=True, timeout=3
                )
                return True
            except Exception:
                return False

    def _read_mac(self) -> str:
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
            return result.stdout
        except Exception:
            return ""

    def _write_mac(self, text: str) -> bool:
        try:
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            return process.returncode == 0
        except Exception:
            return False

    def _read_linux(self) -> str:
        for cmd in [["xclip", "-selection", "clipboard", "-o"],
                    ["xsel", "--clipboard", "--output"]]:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    return result.stdout
            except FileNotFoundError:
                continue
        return ""

    def _write_linux(self, text: str) -> bool:
        for cmd in [["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"]]:
            try:
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                process.communicate(text.encode("utf-8"))
                if process.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
        return False

    @property
    def stats(self) -> dict:
        return {
            "platform": self._platform,
            "available": self.available,
            "history_size": len(self._history),
            "last_content_length": len(self._last_content),
        }
