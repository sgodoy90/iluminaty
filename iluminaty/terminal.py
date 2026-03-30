"""
ILUMINATY - Capa 3: Terminal PTY
=================================
Ejecuta comandos en una pseudo-terminal.
Lee output en tiempo real, detecta errores, timeout automatico.

"Ejecuta npm test" → run_command("npm test")
"Que dice el output?" → get_output()
"""

import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class CommandResult:
    """Resultado de un comando ejecutado."""
    command: str
    return_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "return_code": self.return_code,
            "stdout": self.stdout[-5000:] if len(self.stdout) > 5000 else self.stdout,
            "stderr": self.stderr[-2000:] if len(self.stderr) > 2000 else self.stderr,
            "duration_ms": round(self.duration_ms, 1),
            "timed_out": self.timed_out,
            "success": self.return_code == 0 and not self.timed_out,
        }


class TerminalManager:
    """
    Gestiona ejecucion de comandos en terminal.
    Soporta comandos sincronos y async con output streaming.
    """

    def __init__(self, default_timeout: float = 30.0, max_history: int = 50):
        self._platform = sys.platform
        self._default_timeout = default_timeout
        self._history: deque[CommandResult] = deque(maxlen=max_history)
        self._running: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return True  # Terminal siempre disponible

    def run_command(self, command: str, cwd: Optional[str] = None,
                    timeout: Optional[float] = None, env: Optional[dict] = None) -> CommandResult:
        """Ejecuta un comando sincrono. Retorna cuando termina."""
        timeout = timeout or self._default_timeout
        start = time.time()

        if self._platform == "win32":
            # Windows: use cmd.exe to handle built-in commands
            cmd = command
            shell = True
        else:
            cmd = shlex.split(command)
            shell = False

        try:
            result = subprocess.run(
                cmd, shell=shell, cwd=cwd,
                capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            elapsed = (time.time() - start) * 1000
            cmd_result = CommandResult(
                command=command,
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=elapsed,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            elapsed = (time.time() - start) * 1000
            cmd_result = CommandResult(
                command=command,
                return_code=None,
                stdout=e.stdout or "" if hasattr(e, 'stdout') else "",
                stderr=e.stderr or "" if hasattr(e, 'stderr') else "",
                duration_ms=elapsed,
                timed_out=True,
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            cmd_result = CommandResult(
                command=command,
                return_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=elapsed,
                timed_out=False,
            )

        self._history.append(cmd_result)
        return cmd_result

    def run_background(self, command: str, name: str, cwd: Optional[str] = None) -> dict:
        """Lanza un comando en background. Usa get_background_output() para leer."""
        if self._platform == "win32":
            cmd = command
            shell = True
        else:
            cmd = shlex.split(command)
            shell = False
        try:
            proc = subprocess.Popen(
                cmd, shell=shell, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            with self._lock:
                self._running[name] = proc
            return {"success": True, "name": name, "pid": proc.pid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_background_status(self, name: str) -> dict:
        """Estado de un comando background."""
        with self._lock:
            proc = self._running.get(name)
        if not proc:
            return {"error": f"No background command: {name}"}

        poll = proc.poll()
        if poll is None:
            return {"name": name, "status": "running", "pid": proc.pid}
        else:
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            with self._lock:
                del self._running[name]
            return {
                "name": name, "status": "finished",
                "return_code": poll,
                "stdout": stdout[-5000:],
                "stderr": stderr[-2000:],
            }

    def kill_background(self, name: str) -> dict:
        """Mata un comando background."""
        with self._lock:
            proc = self._running.get(name)
        if not proc:
            return {"error": f"No background command: {name}"}
        try:
            proc.kill()
            with self._lock:
                del self._running[name]
            return {"success": True, "name": name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_history(self, count: int = 20) -> list[dict]:
        """Historial de comandos ejecutados."""
        items = list(self._history)[-count:]
        return [r.to_dict() for r in reversed(items)]

    def get_running(self) -> list[dict]:
        """Lista comandos background activos."""
        with self._lock:
            return [
                {"name": name, "pid": proc.pid, "running": proc.poll() is None}
                for name, proc in self._running.items()
            ]

    @property
    def stats(self) -> dict:
        return {
            "available": True,
            "platform": self._platform,
            "history_size": len(self._history),
            "running_commands": len(self._running),
            "default_timeout": self._default_timeout,
        }
