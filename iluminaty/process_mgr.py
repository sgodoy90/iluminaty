"""
ILUMINATY - Capa 1: Process Manager
=====================================
Listar, abrir, cerrar, matar procesos del SO.
Cross-platform usando psutil (fallback a subprocess).

"Abre Chrome" → launch
"Cierra Spotify" → terminate
"Que esta corriendo?" → list
"""

import sys
import time
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProcessInfo:
    """Informacion de un proceso."""
    pid: int
    name: str
    status: str
    cpu_percent: float
    memory_mb: float
    create_time: float

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "status": self.status,
            "cpu_percent": self.cpu_percent,
            "memory_mb": round(self.memory_mb, 1),
            "uptime_seconds": round(time.time() - self.create_time),
        }


class ProcessManager:
    """
    Gestion de procesos cross-platform.
    Usa psutil si disponible, subprocess como fallback.
    """

    def __init__(self):
        self._psutil = None
        self._platform = sys.platform
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._psutil is not None

    def list_processes(self, sort_by: str = "memory") -> list[dict]:
        """Lista procesos activos, ordenados por CPU o memoria."""
        if self._psutil:
            return self._list_psutil(sort_by)
        return self._list_fallback()

    def _list_psutil(self, sort_by: str) -> list[dict]:
        procs = []
        for proc in self._psutil.process_iter(['pid', 'name', 'status', 'cpu_percent', 'memory_info', 'create_time']):
            try:
                info = proc.info
                procs.append(ProcessInfo(
                    pid=info['pid'],
                    name=info['name'] or "unknown",
                    status=info['status'] or "unknown",
                    cpu_percent=info['cpu_percent'] or 0.0,
                    memory_mb=(info['memory_info'].rss / 1024 / 1024) if info['memory_info'] else 0.0,
                    create_time=info['create_time'] or 0.0,
                ).to_dict())
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue

        key = "memory_mb" if sort_by == "memory" else "cpu_percent"
        procs.sort(key=lambda p: p.get(key, 0), reverse=True)
        return procs[:50]  # Top 50

    def _list_fallback(self) -> list[dict]:
        try:
            if self._platform == "win32":
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5
                )
                procs = []
                for line in result.stdout.strip().split("\n"):
                    parts = line.strip('"').split('","')
                    if len(parts) >= 5:
                        procs.append({
                            "name": parts[0],
                            "pid": int(parts[1]),
                            "memory_mb": round(int(parts[4].replace(",", "").replace(" K", "").replace("K", "")) / 1024, 1) if parts[4].strip() else 0,
                        })
                return procs[:50]
            else:
                result = subprocess.run(
                    ["ps", "aux", "--sort=-rss"],
                    capture_output=True, text=True, timeout=5
                )
                procs = []
                for line in result.stdout.strip().split("\n")[1:51]:
                    parts = line.split(None, 10)
                    if len(parts) >= 11:
                        procs.append({
                            "pid": int(parts[1]),
                            "name": parts[10][:50],
                            "cpu_percent": float(parts[2]),
                            "memory_mb": round(float(parts[5]) / 1024, 1),
                        })
                return procs
        except Exception:
            return []

    def find_process(self, name: str) -> list[dict]:
        """Busca procesos por nombre (match parcial)."""
        name_lower = name.lower()
        return [p for p in self.list_processes() if name_lower in p.get("name", "").lower()]

    def launch(self, command: str, args: Optional[list[str]] = None) -> dict:
        """Lanza un proceso/aplicacion."""
        cmd = [command] + (args or [])
        try:
            if self._platform == "win32":
                # Windows: usar start para apps, subprocess para commands
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            return {"success": True, "pid": process.pid, "command": command}
        except Exception as e:
            # Fallback: intentar con platform launcher (no shell=True)
            try:
                if self._platform == "win32":
                    subprocess.Popen(["cmd", "/c", "start", "", command],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif self._platform == "darwin":
                    subprocess.Popen(["open", command],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(["xdg-open", command],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"success": True, "pid": -1, "command": command}
            except Exception as e2:
                return {"success": False, "error": str(e2), "command": command}

    def terminate(self, pid: Optional[int] = None, name: Optional[str] = None) -> dict:
        """Termina un proceso (graceful). DESTRUCTIVE."""
        if pid is None and name:
            matches = self.find_process(name)
            if not matches:
                return {"success": False, "error": f"No process found: {name}"}
            pid = matches[0]["pid"]

        if pid is None:
            return {"success": False, "error": "No PID specified"}

        try:
            if self._psutil:
                proc = self._psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=5)
            else:
                if self._platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=5)
                else:
                    subprocess.run(["kill", str(pid)], capture_output=True, timeout=5)
            return {"success": True, "pid": pid}
        except Exception as e:
            return {"success": False, "error": str(e), "pid": pid}

    def kill(self, pid: int) -> dict:
        """Mata un proceso (force). DESTRUCTIVE."""
        try:
            if self._psutil:
                proc = self._psutil.Process(pid)
                proc.kill()
            else:
                if self._platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                else:
                    subprocess.run(["kill", "-9", str(pid)], capture_output=True, timeout=5)
            return {"success": True, "pid": pid}
        except Exception as e:
            return {"success": False, "error": str(e), "pid": pid}

    @property
    def stats(self) -> dict:
        return {
            "platform": self._platform,
            "available": self.available,
            "psutil_installed": self._psutil is not None,
        }
