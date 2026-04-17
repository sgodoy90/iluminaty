"""
IPA v2.1 Release Gate Stress Runner
===================================
Runs a realistic synthetic stress battery over the FastAPI app:
- Concurrent mixed HTTP load
- Stale-context gate validation (SAFE mode)
- Recovery loop stress (fail -> recover -> success)
- WebSocket semantic stream soak

Output:
- Console summary
- Markdown report written to STRESS-REPORT-IPA-v2.1.md

Usage:
  py tests/stress_ipa_v21_release_gate.py --duration 90 --workers 8
"""

from __future__ import annotations

import argparse
import math
import pathlib
import random
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

# Ensure local package import works when executed as a script.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iluminaty import server  # noqa: E402
from iluminaty.intent import Intent  # noqa: E402
from iluminaty.resolver import ResolutionResult  # noqa: E402

_API_KEY = "test-key"


@dataclass
class _Event:
    timestamp: float
    event_type: str
    description: str
    importance: float
    uncertainty: float
    monitor: int


class _PerceptionStressStub:
    def __init__(self, monitors: int = 3):
        self._monitors = max(1, monitors)
        self._lock = threading.Lock()
        self._tick = 0
        self._last_world_ts_ms = int(time.time() * 1000)
        self._enable_loading_cycles = True

    def set_loading_cycles(self, enabled: bool) -> None:
        self._enable_loading_cycles = bool(enabled)

    def _build_world(self) -> dict:
        with self._lock:
            self._tick += 1
            tick = self._tick
            self._last_world_ts_ms = int(time.time() * 1000)
            now_ms = self._last_world_ts_ms
        monitor = (tick % self._monitors) + 1
        phase = "interaction"
        if self._enable_loading_cycles and tick % 31 == 0:
            phase = "loading"
        elif self._enable_loading_cycles and tick % 17 == 0:
            phase = "navigation"
        return {
            "timestamp_ms": now_ms,
            "tick_id": tick,
            "task_phase": phase,
            "active_surface": f"monitor-{monitor}::editor",
            "entities": [f"monitor:{monitor}", "app:code", "workflow:coding"],
            "affordances": ["click", "type_text", "hotkey", "do_action"],
            "attention_targets": ["middle-center:0.82", "top-left:0.41"],
            "uncertainty": 0.09 if phase != "loading" else 0.35,
            "readiness": phase != "loading",
            "readiness_reasons": ["ready_for_action"] if phase != "loading" else ["scene_not_stable"],
            "risk_mode": "safe",
            "visual_facts": [
                {
                    "kind": "surface",
                    "text": f"Editor active on monitor {monitor}",
                    "confidence": 0.82,
                    "monitor": monitor,
                    "timestamp_ms": now_ms,
                    "source": "stress",
                    "evidence_ref": f"fr_{tick}",
                }
            ],
            "evidence": [
                {
                    "id": f"evt_{tick}",
                    "type": "event",
                    "summary": "ui_activity",
                    "confidence": 0.73,
                    "timestamp_ms": now_ms,
                    "monitor": monitor,
                }
            ],
            "staleness_ms": 6,
        }

    def get_world_state(self):
        return self._build_world()

    def get_readiness(self):
        world = self._build_world()
        return {
            "timestamp_ms": world["timestamp_ms"],
            "tick_id": world["tick_id"],
            "readiness": world["readiness"],
            "uncertainty": world["uncertainty"],
            "reasons": world["readiness_reasons"],
            "task_phase": world["task_phase"],
            "active_surface": world["active_surface"],
            "risk_mode": world["risk_mode"],
            "staleness_ms": world["staleness_ms"],
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        with self._lock:
            latest_tick = self._tick
            staleness = max(0, int(time.time() * 1000) - self._last_world_ts_ms)
        if int(max_staleness_ms) < staleness:
            return {"allowed": False, "reason": "context_stale", "latest_tick_id": latest_tick, "staleness_ms": staleness}
        if context_tick_id is not None and int(context_tick_id) != latest_tick:
            return {
                "allowed": False,
                "reason": "context_tick_mismatch",
                "latest_tick_id": latest_tick,
                "staleness_ms": staleness,
            }
        return {"allowed": True, "reason": "fresh", "latest_tick_id": latest_tick, "staleness_ms": staleness}

    def get_events(self, last_seconds: float = 3, min_importance: float = 0.1):
        now = time.time()
        out = []
        for i in range(3):
            ts = now - (i * 0.4)
            ev = _Event(
                timestamp=ts,
                event_type="ui_activity" if i % 2 == 0 else "scrolling",
                description=f"event_{i}",
                importance=0.3 + (i * 0.1),
                uncertainty=0.2,
                monitor=((self._tick + i) % self._monitors) + 1,
            )
            if ev.importance >= min_importance:
                out.append(ev)
        return out

    def get_visual_facts_delta(self, since_ms: int, monitor_id=None):
        now_ms = int(time.time() * 1000)
        monitor = int(monitor_id) if monitor_id is not None else ((self._tick % self._monitors) + 1)
        if now_ms <= since_ms:
            return []
        return [
            {
                "kind": "surface",
                "text": f"monitor {monitor} active",
                "confidence": 0.8,
                "monitor": monitor,
                "timestamp_ms": now_ms,
                "source": "stress",
                "evidence_ref": f"fr_{now_ms}",
            }
        ]

    def get_world_trace_bundle(self, seconds: float = 90):
        now_ms = int(time.time() * 1000)
        return {
            "trace": [
                {
                    "timestamp_ms": now_ms - 1000,
                    "tick_id": max(1, self._tick - 1),
                    "summary": "interaction | editor | ready",
                    "boundary_reason": "state_transition",
                    "task_phase": "interaction",
                    "active_surface": "editor",
                    "readiness": True,
                    "uncertainty": 0.12,
                    "evidence_refs": [],
                    "frame_refs": [],
                }
            ],
            "temporal": {"semantic": [], "frame_refs": []},
        }

    def query_visual(self, question: str, at_ms=None, window_seconds: float = 30, monitor_id=None):
        now_ms = int(time.time() * 1000)
        mon = int(monitor_id) if monitor_id is not None else ((self._tick % self._monitors) + 1)
        return {
            "answer": f"answer[{mon}]: {question[:60]}",
            "confidence": 0.84,
            "evidence_refs": [f"fr_{now_ms}"],
            "frame_refs": [{"ref_id": f"fr_{now_ms}", "timestamp_ms": now_ms, "monitor": mon}],
            "source": "stress",
            "timestamp_ms": now_ms,
            "tick_id": self._tick,
            "monitor": mon,
        }

    def record_action_feedback(self, action: str, success: bool, message: str = ""):
        return None


class _SafetyStub:
    is_killed = False

    def check_action(self, action: str, category: str):
        return {"allowed": True, "reason": "ok"}


class _IntentStub:
    def classify_or_default(self, instruction: str):
        return Intent(
            action="click",
            params={"x": 100, "y": 200},
            confidence=0.95,
            raw_input=instruction,
            category="normal",
        )


class _VerificationResult:
    def __init__(self, verified: bool = True):
        self.verified = verified

    def to_dict(self):
        return {"verified": self.verified, "method": "stress", "message": "ok" if self.verified else "failed"}


class _VerifierStub:
    def capture_pre_state(self, action: str, params: dict):
        return {"action": action, "params": params}

    def verify(self, action: str, params: dict, pre_state=None):
        return _VerificationResult(True)


class _RecoveryResult:
    def __init__(self, recovered: bool = True, strategy: str = "retry"):
        self.recovered = recovered
        self.strategy = strategy

    def to_dict(self):
        return {"recovered": self.recovered, "strategy": self.strategy}


class _RecoveryStub:
    def recover(self, action: str, params: dict, message: str):
        return _RecoveryResult(True, "retry")


class _ResolverStressStub:
    """
    Fails once per request_id when force_fail_once=true, then succeeds.
    Also injects tiny jitter for realistic latency profiles.
    """

    def __init__(self):
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def resolve(self, action: str, params: dict):
        time.sleep(0.001 + random.random() * 0.0015)
        req_id = str(params.get("request_id", ""))
        fail_once = bool(params.get("force_fail_once", False))
        should_fail = False
        if fail_once and req_id:
            with self._lock:
                if req_id not in self._seen:
                    self._seen.add(req_id)
                    should_fail = True
        if should_fail:
            return ResolutionResult(
                action=action,
                method_used="stress",
                success=False,
                message="synthetic first-attempt failure",
                attempts=[{"method": "stress", "success": False, "duration_ms": 1.8}],
                total_ms=1.8,
            )
        return ResolutionResult(
            action=action,
            method_used="stress",
            success=True,
            message="ok",
            attempts=[{"method": "stress", "success": True, "duration_ms": 1.6}],
            total_ms=1.6,
        )


def _setup_server_state() -> None:
    server._state.api_key = _API_KEY
    server._state.perception = _PerceptionStressStub(monitors=3)
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverStressStub()
    server._state.verifier = _VerifierStub()
    server._state.recovery = _RecoveryStub()
    server._state.audit = None
    server._state.autonomy = None
    server._state.operating_mode = "SAFE"


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    idx = max(0, min(len(arr) - 1, math.ceil((q / 100.0) * len(arr)) - 1))
    return arr[idx]


def _summarize(latencies: list[float]) -> dict:
    if not latencies:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "avg_ms": 0.0}
    return {
        "count": len(latencies),
        "p50_ms": round(_pct(latencies, 50), 3),
        "p95_ms": round(_pct(latencies, 95), 3),
        "p99_ms": round(_pct(latencies, 99), 3),
        "avg_ms": round(statistics.fmean(latencies), 3),
    }


def _run_with_timeout(fn: Callable[[], None], timeout_s: float = 2.0) -> None:
    """
    Execute fn with a hard timeout using a daemon thread to avoid permanent stalls
    inside in-process TestClient calls under heavy concurrency.
    """
    done = threading.Event()
    error: list[BaseException] = []

    def _target():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - benchmark harness should capture all failures
            error.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    finished = done.wait(timeout=timeout_s)
    if not finished:
        raise TimeoutError(f"request timed out after {timeout_s}s")
    if error:
        raise error[0]


def _hard_timeout_enabled() -> bool:
    raw = str(__import__("os").environ.get("ILUMINATY_STRESS_HARD_TIMEOUT", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def run_concurrent_http_load(duration_s: int, workers: int) -> dict:
    """
    Mixed endpoint load profile:
      40% /perception/world
      20% /action/precheck
      20% /action/execute
      20% /perception/query
    """
    end_time = time.time() + duration_s
    lock = threading.Lock()
    latencies: dict[str, list[float]] = {
        "GET /perception/world": [],
        "POST /action/precheck": [],
        "POST /action/execute": [],
        "POST /perception/query": [],
    }
    counts = {k: 0 for k in latencies}
    errors = 0

    choices: list[tuple[str, float, Callable[[TestClient], None]]] = [
        (
            "GET /perception/world",
            0.40,
            lambda c: _assert_200(c.get("/perception/world", timeout=2.0)),
        ),
        (
            "POST /action/precheck",
            0.20,
            lambda c: _assert_200(
                c.post(
                    "/action/precheck",
                    json={"instruction": "click save", "mode": "SAFE", "max_staleness_ms": 2000},
                    timeout=2.0,
                )
            ),
        ),
        (
            "POST /action/execute",
            0.20,
            lambda c: _assert_200(
                c.post(
                    "/action/execute",
                    json={
                        "action": "click",
                        "params": {"x": 100, "y": 200},
                        "mode": "SAFE",
                        "verify": True,
                        "max_staleness_ms": 2000,
                    },
                    timeout=2.0,
                )
            ),
        ),
        (
            "POST /perception/query",
            0.20,
            lambda c: _assert_200(
                c.post(
                    "/perception/query",
                    json={"question": "what is visible now", "window_seconds": 30},
                    timeout=2.0,
                )
            ),
        ),
    ]

    weighted = []
    for name, weight, fn in choices:
        weighted.extend([(name, fn)] * int(weight * 100))

    def _worker():
        nonlocal errors
        client = TestClient(server.app, headers={"x-api-key": _API_KEY})
        use_hard_timeout = _hard_timeout_enabled()
        while time.time() < end_time:
            name, fn = random.choice(weighted)
            t0 = time.perf_counter()
            try:
                if use_hard_timeout:
                    _run_with_timeout(lambda: fn(client), timeout_s=2.0)
                else:
                    fn(client)
                dt = (time.perf_counter() - t0) * 1000.0
                with lock:
                    latencies[name].append(dt)
                    counts[name] += 1
            except Exception:
                with lock:
                    errors += 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker) for _ in range(workers)]
        for fut in futures:
            fut.result()
    elapsed = time.perf_counter() - started
    total_calls = sum(counts.values())
    total_rps = total_calls / max(0.001, elapsed)
    return {
        "duration_s": duration_s,
        "workers": workers,
        "counts": counts,
        "errors": errors,
        "error_rate_pct": round((errors / max(1, total_calls)) * 100.0, 3),
        "total_calls": total_calls,
        "total_rps": round(total_rps, 2),
        "latency": {name: _summarize(values) for name, values in latencies.items()},
    }


def run_stale_context_gate(iterations: int = 500) -> dict:
    client = TestClient(server.app, headers={"x-api-key": _API_KEY})
    blocked = 0
    reasons: dict[str, int] = {}
    latencies = []
    for i in range(iterations):
        t0 = time.perf_counter()
        resp = client.post(
            "/action/execute",
            json={
                "action": "click",
                "params": {"x": 1, "y": 1},
                "mode": "SAFE",
                "verify": True,
                "context_tick_id": -9999 - i,
                "max_staleness_ms": 1,
            },
        )
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if resp.status_code != 200:
            continue
        payload = resp.json()
        ok = payload.get("result", {}).get("success", False)
        if not ok:
            blocked += 1
            reason = payload.get("precheck", {}).get("context_check", {}).get("reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "iterations": iterations,
        "blocked": blocked,
        "blocked_rate_pct": round((blocked / max(1, iterations)) * 100.0, 3),
        "reasons": reasons,
        "latency": _summarize(latencies),
    }


def run_recovery_stress(iterations: int = 700) -> dict:
    client = TestClient(server.app, headers={"x-api-key": _API_KEY})
    perception = getattr(server._state, "perception", None)
    prev_loading_mode = None
    if hasattr(perception, "_enable_loading_cycles"):
        prev_loading_mode = bool(getattr(perception, "_enable_loading_cycles"))
    if hasattr(perception, "set_loading_cycles"):
        # Isolate recovery behavior from synthetic "scene_not_stable" cadence.
        perception.set_loading_cycles(False)

    successes = 0
    failures = 0
    recovered_success = 0
    skipped_not_stable = 0
    latencies = []
    completed = 0
    attempts = 0
    max_attempts = max(iterations * 3, iterations + 50)
    try:
        while completed < iterations and attempts < max_attempts:
            attempts += 1
            req_id = f"req_{attempts}"
            t0 = time.perf_counter()
            resp = client.post(
                "/action/execute",
                json={
                    "action": "click",
                    "params": {"x": 10, "y": 20, "request_id": req_id, "force_fail_once": True},
                    "mode": "SAFE",
                    "verify": True,
                    "max_staleness_ms": 2500,
                },
                timeout=2.0,
            )
            latencies.append((time.perf_counter() - t0) * 1000.0)
            if resp.status_code != 200:
                failures += 1
                completed += 1
                continue
            payload = resp.json()
            result = payload.get("result", {})
            ok = bool(result.get("success"))
            if ok:
                successes += 1
                attempts_meta = result.get("attempts", [])
                if payload.get("recovery") is not None or (
                    attempts_meta and any(not a.get("success", True) for a in attempts_meta)
                ):
                    recovered_success += 1
                completed += 1
                continue
            message = str(result.get("message", "")).strip().lower()
            if message == "scene_not_stable":
                skipped_not_stable += 1
                continue
            failures += 1
            completed += 1
    finally:
        if prev_loading_mode is not None and hasattr(perception, "set_loading_cycles"):
            perception.set_loading_cycles(prev_loading_mode)

    denominator = max(1, completed)
    return {
        "iterations": iterations,
        "completed_iterations": completed,
        "raw_attempts": attempts,
        "skipped_scene_not_stable": skipped_not_stable,
        "successes": successes,
        "failures": failures,
        "success_rate_pct": round((successes / denominator) * 100.0, 3),
        "recovered_successes": recovered_success,
        "recovered_rate_pct": round((recovered_success / denominator) * 100.0, 3),
        "latency": _summarize(latencies),
    }


def run_ws_soak(seconds: int = 45) -> dict:
    client = TestClient(server.app, headers={"x-api-key": _API_KEY})
    t_end = time.time() + seconds
    messages = 0
    malformed = 0
    intervals = []
    last_t = None
    with client.websocket_connect(
        f"/perception/stream?interval_ms=120&include_events=true&token={_API_KEY}"
    ) as ws:
        while time.time() < t_end:
            payload = ws.receive_json()
            now = time.time()
            messages += 1
            if last_t is not None:
                intervals.append((now - last_t) * 1000.0)
            last_t = now
            required = {"type", "world", "readiness", "tick_id"}
            if not required.issubset(set(payload.keys())):
                malformed += 1
    avg_interval = statistics.fmean(intervals) if intervals else 0.0
    mps = messages / max(0.001, seconds)
    return {
        "duration_s": seconds,
        "messages": messages,
        "messages_per_sec": round(mps, 3),
        "malformed": malformed,
        "malformed_rate_pct": round((malformed / max(1, messages)) * 100.0, 4),
        "avg_interval_ms": round(avg_interval, 3),
        "p95_interval_ms": round(_pct(intervals, 95), 3) if intervals else 0.0,
    }


def _assert_200(resp):
    if resp.status_code != 200:
        raise RuntimeError(f"status={resp.status_code} body={resp.text[:200]}")
    return resp


def evaluate_release_gate(http_load: dict, stale_gate: dict, recovery: dict, ws_soak: dict) -> dict:
    failures: list[str] = []
    # Performance gates
    for endpoint in ("GET /perception/world", "POST /action/execute"):
        p95 = http_load["latency"][endpoint]["p95_ms"]
        if p95 > 300.0:
            failures.append(f"{endpoint} p95 {p95}ms > 300ms")
    if http_load["error_rate_pct"] > 1.0:
        failures.append(f"HTTP error_rate {http_load['error_rate_pct']}% > 1.0%")

    # Context safety gate
    if stale_gate["blocked_rate_pct"] < 95.0:
        failures.append(f"stale blocked_rate {stale_gate['blocked_rate_pct']}% < 95%")

    # Recovery gate
    if recovery["success_rate_pct"] < 98.0:
        failures.append(f"recovery success_rate {recovery['success_rate_pct']}% < 98%")
    if recovery["recovered_rate_pct"] < 95.0:
        failures.append(f"recovered_rate {recovery['recovered_rate_pct']}% < 95%")

    # WS stability gate
    if ws_soak["malformed"] > 0:
        failures.append(f"ws malformed messages {ws_soak['malformed']} > 0")
    if ws_soak["messages_per_sec"] < 2.0:
        failures.append(f"ws messages_per_sec {ws_soak['messages_per_sec']} < 2.0")

    return {
        "pass": len(failures) == 0,
        "failures": failures,
    }


def write_markdown_report(
    path: pathlib.Path,
    http_load: dict,
    stale_gate: dict,
    recovery: dict,
    ws_soak: dict,
    gate: dict,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = []
    lines.append("# Stress Report — IPA v2.1 Release Gate")
    lines.append("")
    lines.append(f"Generated (UTC): {ts}")
    lines.append("")
    lines.append("## 1) Concurrent HTTP Mixed Load")
    lines.append("")
    lines.append(
        f"- Duration: {http_load['duration_s']}s | Workers: {http_load['workers']} | Total calls: {http_load['total_calls']} | Total RPS: {http_load['total_rps']}"
    )
    lines.append(f"- Errors: {http_load['errors']} | Error rate: {http_load['error_rate_pct']}%")
    lines.append("")
    lines.append("| Endpoint | Count | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name, stats in http_load["latency"].items():
        lines.append(
            f"| `{name}` | {http_load['counts'][name]} | {stats['p50_ms']} | {stats['p95_ms']} | {stats['p99_ms']} | {stats['avg_ms']} |"
        )
    lines.append("")

    lines.append("## 2) Stale Context Gate (SAFE)")
    lines.append("")
    lines.append(
        f"- Iterations: {stale_gate['iterations']} | Blocked: {stale_gate['blocked']} | Blocked rate: {stale_gate['blocked_rate_pct']}%"
    )
    lines.append(f"- Reasons: {stale_gate['reasons']}")
    lines.append(
        f"- Latency: p50={stale_gate['latency']['p50_ms']}ms | p95={stale_gate['latency']['p95_ms']}ms | p99={stale_gate['latency']['p99_ms']}ms"
    )
    lines.append("")

    lines.append("## 3) Recovery Stress (Fail Once -> Recover)")
    lines.append("")
    lines.append(
        f"- Iterations target: {recovery['iterations']} | Completed: {recovery['completed_iterations']} | Raw attempts: {recovery['raw_attempts']}"
    )
    lines.append(
        f"- Successes: {recovery['successes']} | Failures: {recovery['failures']} | Skipped(scene_not_stable): {recovery['skipped_scene_not_stable']}"
    )
    lines.append(
        f"- Success rate: {recovery['success_rate_pct']}% | Recovered successes: {recovery['recovered_successes']} ({recovery['recovered_rate_pct']}%)"
    )
    lines.append(
        f"- Latency: p50={recovery['latency']['p50_ms']}ms | p95={recovery['latency']['p95_ms']}ms | p99={recovery['latency']['p99_ms']}ms"
    )
    lines.append("")

    lines.append("## 4) WebSocket Soak (`/perception/stream`)")
    lines.append("")
    lines.append(
        f"- Duration: {ws_soak['duration_s']}s | Messages: {ws_soak['messages']} | Msg/s: {ws_soak['messages_per_sec']}"
    )
    lines.append(
        f"- Malformed: {ws_soak['malformed']} ({ws_soak['malformed_rate_pct']}%) | avg interval: {ws_soak['avg_interval_ms']}ms | p95 interval: {ws_soak['p95_interval_ms']}ms"
    )
    lines.append("")

    lines.append("## 5) Release Gate Verdict")
    lines.append("")
    if gate["pass"]:
        lines.append("**PASS** — all gates satisfied.")
    else:
        lines.append("**FAIL** — one or more gates failed:")
        for f in gate["failures"]:
            lines.append(f"- {f}")
    lines.append("")
    lines.append("## Gate Thresholds")
    lines.append("")
    lines.append("- `p95 <= 300ms` for `GET /perception/world` and `POST /action/execute`")
    lines.append("- HTTP mixed-load error rate `<= 1.0%`")
    lines.append("- SAFE stale-context blocked rate `>= 95%`")
    lines.append("- Recovery success rate `>= 98%` and recovered-rate `>= 95%`")
    lines.append("- WebSocket malformed messages `= 0` and throughput `>= 2 msg/s`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def print_console_summary(http_load: dict, stale_gate: dict, recovery: dict, ws_soak: dict, gate: dict) -> None:
    print("\nIPA v2.1 Release Gate Stress Summary")
    print("=" * 82)
    print(
        f"HTTP mixed load: calls={http_load['total_calls']} rps={http_load['total_rps']} "
        f"errors={http_load['errors']} ({http_load['error_rate_pct']}%)"
    )
    for name, stats in http_load["latency"].items():
        print(
            f"  {name:28} p50={stats['p50_ms']:7.3f}ms "
            f"p95={stats['p95_ms']:7.3f}ms p99={stats['p99_ms']:7.3f}ms"
        )
    print(
        f"Stale gate: blocked={stale_gate['blocked']}/{stale_gate['iterations']} "
        f"({stale_gate['blocked_rate_pct']}%) reasons={stale_gate['reasons']}"
    )
    print(
        f"Recovery: completed={recovery['completed_iterations']}/{recovery['iterations']} "
        f"raw_attempts={recovery['raw_attempts']} skipped_not_stable={recovery['skipped_scene_not_stable']} "
        f"success={recovery['success_rate_pct']}% recovered={recovery['recovered_rate_pct']}%"
    )
    print(
        f"WS soak: messages={ws_soak['messages']} msg/s={ws_soak['messages_per_sec']} "
        f"malformed={ws_soak['malformed']} avg_interval={ws_soak['avg_interval_ms']}ms"
    )
    print("-" * 82)
    print(f"VERDICT: {'PASS' if gate['pass'] else 'FAIL'}")
    if gate["failures"]:
        for item in gate["failures"]:
            print(f"  - {item}")


def main():
    parser = argparse.ArgumentParser(description="IPA v2.1 release gate stress battery")
    parser.add_argument("--duration", type=int, default=90, help="Duration for mixed HTTP load (seconds)")
    parser.add_argument("--workers", type=int, default=8, help="Workers for mixed HTTP load")
    parser.add_argument("--ws-soak-seconds", type=int, default=45, help="WebSocket soak duration")
    parser.add_argument("--stale-iterations", type=int, default=500, help="SAFE stale-gate iterations")
    parser.add_argument("--recovery-iterations", type=int, default=700, help="Recovery stress iterations")
    parser.add_argument(
        "--report",
        type=str,
        default="STRESS-REPORT-IPA-v2.1.md",
        help="Markdown report output path",
    )
    args = parser.parse_args()

    _setup_server_state()
    http_load = run_concurrent_http_load(duration_s=args.duration, workers=args.workers)
    stale_gate = run_stale_context_gate(iterations=args.stale_iterations)
    recovery = run_recovery_stress(iterations=args.recovery_iterations)
    ws_soak = run_ws_soak(seconds=args.ws_soak_seconds)
    gate = evaluate_release_gate(http_load, stale_gate, recovery, ws_soak)

    report_path = ROOT / args.report
    write_markdown_report(report_path, http_load, stale_gate, recovery, ws_soak, gate)
    print_console_summary(http_load, stale_gate, recovery, ws_soak, gate)
    print(f"\nReport written to: {report_path}")
    raise SystemExit(0 if gate["pass"] else 2)


if __name__ == "__main__":
    main()
