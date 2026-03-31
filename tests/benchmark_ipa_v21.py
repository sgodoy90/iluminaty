"""
IPA v2.1 benchmark runner (local, deterministic).

Purpose:
- Measure p50/p95 latency for key endpoints under synthetic load.
- Validate target envelope for semantic loop and action control path.

Usage:
  py tests/benchmark_ipa_v21.py --iterations 500 --warmup 50 --workers 4
"""

from __future__ import annotations

import argparse
import math
import pathlib
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

# Ensure local package import works when executed as a script.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iluminaty import server
from iluminaty.intent import Intent
from iluminaty.resolver import ResolutionResult


class _PerceptionBenchStub:
    def __init__(self):
        self._lock = threading.Lock()
        self._tick = 0

    def _next_world(self) -> dict:
        with self._lock:
            self._tick += 1
            tick = self._tick
        now_ms = int(time.time() * 1000)
        return {
            "timestamp_ms": now_ms,
            "tick_id": tick,
            "task_phase": "interaction",
            "active_surface": "code :: benchmark",
            "entities": ["app:code", "monitor:1"],
            "affordances": ["click", "type_text", "do_action"],
            "attention_targets": ["middle-center:0.77"],
            "uncertainty": 0.08,
            "readiness": True,
            "readiness_reasons": ["ready_for_action"],
            "risk_mode": "safe",
            "visual_facts": [{"kind": "surface", "text": "editor visible", "confidence": 0.8}],
            "evidence": [{"id": f"evt_{tick}", "type": "event", "summary": "ui_activity", "confidence": 0.7}],
            "staleness_ms": 5,
        }

    def get_world_state(self):
        return self._next_world()

    def get_readiness(self):
        world = self._next_world()
        return {
            "timestamp_ms": world["timestamp_ms"],
            "tick_id": world["tick_id"],
            "readiness": True,
            "uncertainty": world["uncertainty"],
            "reasons": ["ready_for_action"],
            "task_phase": world["task_phase"],
            "active_surface": world["active_surface"],
            "risk_mode": "safe",
            "staleness_ms": 5,
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        staleness = 5
        if int(max_staleness_ms) < staleness:
            return {"allowed": False, "reason": "context_stale", "latest_tick_id": self._tick, "staleness_ms": staleness}
        if context_tick_id is not None and int(context_tick_id) != self._tick:
            return {"allowed": False, "reason": "context_tick_mismatch", "latest_tick_id": self._tick, "staleness_ms": staleness}
        return {"allowed": True, "reason": "fresh", "latest_tick_id": self._tick, "staleness_ms": staleness}

    def get_events(self, last_seconds: float = 3, min_importance: float = 0.1):
        return []

    def get_visual_facts_delta(self, since_ms: int, monitor_id=None):
        now_ms = int(time.time() * 1000)
        return [{
            "kind": "surface",
            "text": "editor visible",
            "confidence": 0.8,
            "monitor": 1,
            "timestamp_ms": now_ms,
            "source": "bench",
            "evidence_ref": f"fr_{now_ms}",
        }]

    def get_world_trace_bundle(self, seconds: float = 90):
        return {"trace": [], "temporal": {"semantic": [], "frame_refs": []}}

    def query_visual(self, question: str, at_ms=None, window_seconds: float = 30, monitor_id=None):
        now_ms = int(time.time() * 1000)
        return {
            "answer": f"bench_answer:{question[:40]}",
            "confidence": 0.81,
            "evidence_refs": [f"fr_{now_ms}"],
            "frame_refs": [{"ref_id": f"fr_{now_ms}", "timestamp_ms": now_ms, "monitor": monitor_id or 1}],
            "source": "bench",
            "timestamp_ms": now_ms,
            "tick_id": self._tick,
            "monitor": monitor_id or 1,
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
            params={"x": 1, "y": 1},
            confidence=0.95,
            raw_input=instruction,
            category="normal",
        )


class _VerificationResult:
    def __init__(self, ok: bool = True):
        self.ok = ok

    def to_dict(self):
        return {"verified": self.ok, "method": "bench", "message": "ok" if self.ok else "failed"}


class _VerifierStub:
    def capture_pre_state(self, action: str, params: dict):
        return {"action": action, "params": params}

    def verify(self, action: str, params: dict, pre_state=None):
        return _VerificationResult(True)


class _ResolverStub:
    def resolve(self, action: str, params: dict):
        # Tiny jitter to avoid unrealistically flat latencies.
        time.sleep(0.001)
        return ResolutionResult(
            action=action,
            method_used="bench",
            success=True,
            message="ok",
            attempts=[],
            total_ms=1.3,
        )


def _setup_server_state() -> None:
    server._state.api_key = None
    server._state.perception = _PerceptionBenchStub()
    server._state.safety = _SafetyStub()
    server._state.intent = _IntentStub()
    server._state.resolver = _ResolverStub()
    server._state.verifier = _VerifierStub()
    server._state.recovery = None
    server._state.audit = None
    server._state.autonomy = None


def _pct(values_ms: list[float], q: float) -> float:
    if not values_ms:
        return 0.0
    if len(values_ms) == 1:
        return values_ms[0]
    values = sorted(values_ms)
    idx = max(0, min(len(values) - 1, math.ceil((q / 100.0) * len(values)) - 1))
    return values[idx]


def _bench_sync(name: str, fn, iterations: int, warmup: int) -> dict:
    for _ in range(max(0, warmup)):
        fn()
    samples = []
    t0 = time.perf_counter()
    for _ in range(iterations):
        s = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - s) * 1000.0)
    total = time.perf_counter() - t0
    return {
        "name": name,
        "count": iterations,
        "p50_ms": round(_pct(samples, 50), 3),
        "p95_ms": round(_pct(samples, 95), 3),
        "p99_ms": round(_pct(samples, 99), 3),
        "avg_ms": round(statistics.fmean(samples), 3),
        "rps": round(iterations / max(0.001, total), 2),
    }


def _worker_endpoint(path: str, method: str, iterations: int, warmup: int) -> list[float]:
    client = TestClient(server.app)
    payload_execute = {"instruction": "click save", "mode": "SAFE", "verify": True, "max_staleness_ms": 2000}
    payload_precheck = {"instruction": "click save", "mode": "SAFE", "max_staleness_ms": 2000}
    payload_query = {"question": "que hay en pantalla", "window_seconds": 30}

    def _call():
        if method == "GET":
            resp = client.get(path)
        else:
            body = {}
            if path == "/action/execute":
                body = payload_execute
            elif path == "/action/precheck":
                body = payload_precheck
            elif path == "/perception/query":
                body = payload_query
            resp = client.post(path, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"{path} -> status {resp.status_code}")

    for _ in range(max(0, warmup)):
        _call()
    out = []
    for _ in range(iterations):
        s = time.perf_counter()
        _call()
        out.append((time.perf_counter() - s) * 1000.0)
    return out


def _bench_concurrent(name: str, path: str, method: str, total_iterations: int, warmup: int, workers: int) -> dict:
    per_worker = max(1, total_iterations // max(1, workers))
    all_samples: list[float] = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(_worker_endpoint, path, method, per_worker, warmup // max(1, workers))
            for _ in range(workers)
        ]
        for fut in futures:
            all_samples.extend(fut.result())
    total = time.perf_counter() - t0
    count = len(all_samples)
    return {
        "name": name,
        "count": count,
        "p50_ms": round(_pct(all_samples, 50), 3),
        "p95_ms": round(_pct(all_samples, 95), 3),
        "p99_ms": round(_pct(all_samples, 99), 3),
        "avg_ms": round(statistics.fmean(all_samples), 3),
        "rps": round(count / max(0.001, total), 2),
    }


def run_benchmarks(iterations: int, warmup: int, workers: int) -> list[dict]:
    _setup_server_state()
    client = TestClient(server.app)
    results = []

    results.append(
        _bench_sync(
            "GET /perception/world (single)",
            lambda: _assert_200(client.get("/perception/world")),
            iterations,
            warmup,
        )
    )
    results.append(
        _bench_sync(
            "POST /action/precheck (single)",
            lambda: _assert_200(client.post("/action/precheck", json={"instruction": "click save", "mode": "SAFE"})),
            iterations,
            warmup,
        )
    )
    results.append(
        _bench_sync(
            "POST /action/execute (single)",
            lambda: _assert_200(client.post("/action/execute", json={"instruction": "click save", "mode": "SAFE", "verify": True, "max_staleness_ms": 2000})),
            iterations,
            warmup,
        )
    )
    results.append(
        _bench_sync(
            "POST /perception/query (single)",
            lambda: _assert_200(client.post("/perception/query", json={"question": "que hay en pantalla", "window_seconds": 30})),
            iterations,
            warmup,
        )
    )

    # Concurrent load profile.
    results.append(
        _bench_concurrent(
            "GET /perception/world (concurrent)",
            "/perception/world",
            "GET",
            total_iterations=iterations,
            warmup=warmup,
            workers=workers,
        )
    )
    results.append(
        _bench_concurrent(
            "POST /action/execute (concurrent)",
            "/action/execute",
            "POST",
            total_iterations=iterations,
            warmup=warmup,
            workers=workers,
        )
    )
    return results


def _assert_200(resp):
    if resp.status_code != 200:
        raise RuntimeError(f"status={resp.status_code} body={resp.text}")
    return resp


def print_report(results: list[dict], target_p95_ms: float = 300.0) -> int:
    print("\nIPA v2.1 Benchmark Report")
    print("=" * 78)
    print(f"{'Case':44} {'p50':>8} {'p95':>8} {'p99':>8} {'avg':>8} {'RPS':>10} {'Target':>9}")
    print("-" * 78)
    failed = 0
    for r in results:
        p95 = r["p95_ms"]
        target_ok = p95 <= target_p95_ms
        if not target_ok:
            failed += 1
        marker = "PASS" if target_ok else "FAIL"
        print(
            f"{r['name'][:44]:44} "
            f"{r['p50_ms']:8.3f} {r['p95_ms']:8.3f} {r['p99_ms']:8.3f} "
            f"{r['avg_ms']:8.3f} {r['rps']:10.2f} {marker:>9}"
        )
    print("-" * 78)
    print(f"Target p95 <= {target_p95_ms:.0f}ms | failures: {failed}/{len(results)}")
    return failed


def main():
    parser = argparse.ArgumentParser(description="IPA v2.1 benchmark runner")
    parser.add_argument("--iterations", type=int, default=600, help="Total requests per benchmark case")
    parser.add_argument("--warmup", type=int, default=60, help="Warmup requests per case")
    parser.add_argument("--workers", type=int, default=4, help="Workers for concurrent profile")
    parser.add_argument("--target-p95-ms", type=float, default=300.0, help="Target p95 threshold")
    args = parser.parse_args()

    results = run_benchmarks(args.iterations, args.warmup, args.workers)
    failed = print_report(results, target_p95_ms=args.target_p95_ms)
    raise SystemExit(0 if failed == 0 else 2)


if __name__ == "__main__":
    main()
