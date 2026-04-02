"""
ILUMINATY - Host Telemetry
==========================
Low-cost host telemetry used by precheck/runtime policy:
- CPU and memory pressure
- Optional temperatures
- Optional NVIDIA GPU metrics (if nvidia-smi exists)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None  # type: ignore


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return max(float(minimum), min(float(maximum), value))


class HostTelemetry:
    """Collects lightweight host telemetry and emits policy hints."""

    def __init__(self):
        self._cpu_warn = _env_float("ILUMINATY_TELEMETRY_CPU_WARN_PCT", 85.0, 1.0, 100.0)
        self._cpu_critical = _env_float("ILUMINATY_TELEMETRY_CPU_CRITICAL_PCT", 96.0, 1.0, 100.0)
        self._mem_warn = _env_float("ILUMINATY_TELEMETRY_MEM_WARN_PCT", 90.0, 1.0, 100.0)
        self._mem_critical = _env_float("ILUMINATY_TELEMETRY_MEM_CRITICAL_PCT", 97.0, 1.0, 100.0)
        self._temp_warn = _env_float("ILUMINATY_TELEMETRY_TEMP_WARN_C", 82.0, 20.0, 130.0)
        self._temp_critical = _env_float("ILUMINATY_TELEMETRY_TEMP_CRITICAL_C", 90.0, 20.0, 130.0)
        self._gpu_warn = _env_float("ILUMINATY_TELEMETRY_GPU_WARN_PCT", 95.0, 1.0, 100.0)
        self._gpu_critical = _env_float("ILUMINATY_TELEMETRY_GPU_CRITICAL_PCT", 99.0, 1.0, 100.0)
        self._gpu_cache_ttl_s = _env_float("ILUMINATY_TELEMETRY_GPU_CACHE_S", 1.0, 0.1, 10.0)
        self._gpu_cache_ts = 0.0
        self._gpu_cache: Optional[dict] = None
        self._nvidia_smi = shutil.which("nvidia-smi")

    @property
    def available(self) -> bool:
        return bool(psutil is not None or self._nvidia_smi)

    def _safe_float(self, value, fallback: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return fallback
            return float(value)
        except Exception:
            return fallback

    def _collect_temperatures(self) -> dict:
        if psutil is None:
            return {"available": False, "max_c": None, "sources": []}
        try:
            sensors = psutil.sensors_temperatures() or {}
        except Exception:
            return {"available": False, "max_c": None, "sources": []}
        values = []
        sources = []
        for name, entries in sensors.items():
            for entry in entries:
                current = self._safe_float(getattr(entry, "current", None))
                if current is None:
                    continue
                values.append(current)
                if name not in sources:
                    sources.append(name)
        if not values:
            return {"available": False, "max_c": None, "sources": []}
        return {
            "available": True,
            "max_c": round(max(values), 1),
            "sources": sources[:8],
        }

    def _collect_gpu(self) -> dict:
        now = time.time()
        if self._gpu_cache and (now - self._gpu_cache_ts) < self._gpu_cache_ttl_s:
            return dict(self._gpu_cache)
        data = {
            "available": False,
            "utilization_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_percent": None,
            "temperature_c": None,
            "power_w": None,
            "source": "none",
        }
        if not self._nvidia_smi:
            self._gpu_cache = dict(data)
            self._gpu_cache_ts = now
            return data
        cmd = [
            self._nvidia_smi,
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1.2,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "nvidia_smi_failed")
            first = ""
            for line in (proc.stdout or "").splitlines():
                if line.strip():
                    first = line.strip()
                    break
            if not first:
                raise RuntimeError("nvidia_smi_empty")
            parts = [p.strip() for p in first.split(",")]
            if len(parts) < 5:
                raise RuntimeError("nvidia_smi_parse")
            util = self._safe_float(parts[0])
            mem_used = self._safe_float(parts[1])
            mem_total = self._safe_float(parts[2])
            temp = self._safe_float(parts[3])
            power = self._safe_float(parts[4])
            mem_pct = None
            if mem_used is not None and mem_total and mem_total > 0:
                mem_pct = round((mem_used / mem_total) * 100.0, 1)
            data = {
                "available": True,
                "utilization_percent": round(util, 1) if util is not None else None,
                "memory_used_mb": round(mem_used, 1) if mem_used is not None else None,
                "memory_total_mb": round(mem_total, 1) if mem_total is not None else None,
                "memory_percent": mem_pct,
                "temperature_c": round(temp, 1) if temp is not None else None,
                "power_w": round(power, 1) if power is not None else None,
                "source": "nvidia_smi",
            }
        except Exception as e:
            logger.debug("GPU telemetry unavailable: %s", e)
        self._gpu_cache = dict(data)
        self._gpu_cache_ts = now
        return data

    def snapshot(self) -> dict:
        timestamp_ms = int(time.time() * 1000)
        cpu_pct = None
        mem_pct = None
        swap_pct = None
        disk_pct = None
        load_avg = None

        if psutil is not None:
            try:
                cpu_pct = float(psutil.cpu_percent(interval=None))
            except Exception:
                cpu_pct = None
            try:
                mem_pct = float(psutil.virtual_memory().percent)
            except Exception:
                mem_pct = None
            try:
                swap_pct = float(psutil.swap_memory().percent)
            except Exception:
                swap_pct = None
            try:
                disk_pct = float(psutil.disk_usage(os.getcwd()).percent)
            except Exception:
                disk_pct = None
            try:
                if hasattr(os, "getloadavg"):
                    la = os.getloadavg()
                    load_avg = [round(float(la[0]), 2), round(float(la[1]), 2), round(float(la[2]), 2)]
            except Exception:
                load_avg = None

        temperatures = self._collect_temperatures()
        gpu = self._collect_gpu()

        reasons = []
        if cpu_pct is not None and cpu_pct >= self._cpu_warn:
            reasons.append(f"cpu>{self._cpu_warn:.0f}%")
        if mem_pct is not None and mem_pct >= self._mem_warn:
            reasons.append(f"mem>{self._mem_warn:.0f}%")
        temp_max = temperatures.get("max_c")
        if temp_max is not None and float(temp_max) >= self._temp_warn:
            reasons.append(f"temp>{self._temp_warn:.0f}C")
        gpu_util = gpu.get("utilization_percent")
        if gpu_util is not None and float(gpu_util) >= self._gpu_warn:
            reasons.append(f"gpu>{self._gpu_warn:.0f}%")

        return {
            "timestamp_ms": timestamp_ms,
            "available": self.available,
            "cpu_percent": round(cpu_pct, 1) if cpu_pct is not None else None,
            "memory_percent": round(mem_pct, 1) if mem_pct is not None else None,
            "swap_percent": round(swap_pct, 1) if swap_pct is not None else None,
            "disk_percent": round(disk_pct, 1) if disk_pct is not None else None,
            "load_avg": load_avg,
            "temperatures": temperatures,
            "gpu": gpu,
            "overloaded": bool(reasons),
            "overload_reasons": reasons[:8],
            "thresholds": {
                "cpu_warn_pct": self._cpu_warn,
                "cpu_critical_pct": self._cpu_critical,
                "mem_warn_pct": self._mem_warn,
                "mem_critical_pct": self._mem_critical,
                "temp_warn_c": self._temp_warn,
                "temp_critical_c": self._temp_critical,
                "gpu_warn_pct": self._gpu_warn,
                "gpu_critical_pct": self._gpu_critical,
            },
        }

    def policy_check(self, *, action_category: Optional[str], mode: str) -> dict:
        snap = self.snapshot()
        category = str(action_category or "normal").strip().lower()
        mode_norm = str(mode or "SAFE").strip().upper()

        severe_reasons = []
        cpu_pct = snap.get("cpu_percent")
        mem_pct = snap.get("memory_percent")
        temp_max = (snap.get("temperatures") or {}).get("max_c")
        gpu_util = (snap.get("gpu") or {}).get("utilization_percent")

        if cpu_pct is not None and float(cpu_pct) >= self._cpu_critical:
            severe_reasons.append(f"cpu>{self._cpu_critical:.0f}%")
        if mem_pct is not None and float(mem_pct) >= self._mem_critical:
            severe_reasons.append(f"mem>{self._mem_critical:.0f}%")
        if temp_max is not None and float(temp_max) >= self._temp_critical:
            severe_reasons.append(f"temp>{self._temp_critical:.0f}C")
        if gpu_util is not None and float(gpu_util) >= self._gpu_critical:
            severe_reasons.append(f"gpu>{self._gpu_critical:.0f}%")

        critical_category = category in {"destructive", "system"}
        if severe_reasons and mode_norm != "RAW" and critical_category:
            return {
                "allowed": False,
                "reason": "host_overloaded",
                "severity": "critical",
                "signals": severe_reasons[:8],
                "snapshot": snap,
            }
        if severe_reasons:
            return {
                "allowed": True,
                "reason": "host_overloaded_tolerated",
                "severity": "critical",
                "signals": severe_reasons[:8],
                "snapshot": snap,
            }
        return {
            "allowed": True,
            "reason": "host_ok",
            "severity": "normal",
            "signals": [],
            "snapshot": snap,
        }

