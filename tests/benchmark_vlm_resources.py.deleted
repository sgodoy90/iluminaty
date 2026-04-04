"""
VLM Resource Benchmark (CPU vs CUDA)
====================================

Measures real runtime resources while ILUMINATY is running with LocalSmolVLM:
- CPU total usage (process tree) as % sum across cores
- RAM RSS (MB) process tree
- GPU utilization / memory (via nvidia-smi) when available
- Visual engine queue/processed/drop/failures + provider runtime status

Outputs:
- JSON raw report
- Markdown executive report

Usage:
  py tests/benchmark_vlm_resources.py --duration 30 --sample 3 --profiles cpu,cuda
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

try:
    import psutil
except Exception as e:  # pragma: no cover
    raise SystemExit(f"psutil is required for this benchmark: {e}")


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    arr = sorted(values)
    idx = max(0, min(len(arr) - 1, math.ceil((q / 100.0) * len(arr)) - 1))
    return arr[idx]


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.fmean(values))


def _tree_procs(root_pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(root_pid)
    except Exception:
        return []
    procs: list[psutil.Process] = [root]
    try:
        procs.extend(root.children(recursive=True))
    except Exception:
        pass
    alive: list[psutil.Process] = []
    for p in procs:
        try:
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                alive.append(p)
        except Exception:
            pass
    return alive


def _cpu_time_seconds(procs: list[psutil.Process]) -> float:
    total = 0.0
    for p in procs:
        try:
            t = p.cpu_times()
            total += float(t.user) + float(t.system)
        except Exception:
            pass
    return total


def _rss_mb(procs: list[psutil.Process]) -> float:
    rss = 0
    for p in procs:
        try:
            rss += p.memory_info().rss
        except Exception:
            pass
    return rss / (1024 * 1024)


def _gpu_stats() -> dict:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=3,
        ).strip()
        if not out:
            return {"gpu_available": False, "gpu_util_pct": 0.0, "gpu_mem_used_mb": 0.0, "gpu_mem_total_mb": 0.0}
        line = out.splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return {"gpu_available": False, "gpu_util_pct": 0.0, "gpu_mem_used_mb": 0.0, "gpu_mem_total_mb": 0.0}
        return {
            "gpu_available": True,
            "gpu_util_pct": float(parts[0]),
            "gpu_mem_used_mb": float(parts[1]),
            "gpu_mem_total_mb": float(parts[2]),
        }
    except Exception:
        return {"gpu_available": False, "gpu_util_pct": 0.0, "gpu_mem_used_mb": 0.0, "gpu_mem_total_mb": 0.0}


def _http_json(port: int, path: str, timeout: float = 4.0) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.getcode(), json.loads(body)


def _wait_ready(port: int, timeout_s: float = 90.0) -> bool:
    start = time.time()
    while (time.time() - start) < timeout_s:
        try:
            code, _ = _http_json(port, "/health", timeout=1.5)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _spawn_server(port: int, args, device_policy: str) -> subprocess.Popen:
    env = dict(**subprocess.os.environ)
    env.update(
        {
            "ILUMINATY_VLM_CAPTION": "1",
            "ILUMINATY_VLM_BACKEND": "smol",
            "ILUMINATY_VLM_MODEL": args.model,
            "ILUMINATY_VLM_INT8": "1" if args.int8 else "0",
            "ILUMINATY_VLM_IMAGE_SIZE": str(args.image_size),
            "ILUMINATY_VLM_MAX_TOKENS": str(args.max_tokens),
            "ILUMINATY_VLM_MIN_INTERVAL_MS": str(args.vlm_min_interval_ms),
            "ILUMINATY_VLM_KEEPALIVE_MS": str(args.vlm_keepalive_ms),
            "ILUMINATY_VLM_PRIORITY_THRESHOLD": str(args.vlm_priority_threshold),
            "ILUMINATY_VLM_SECONDARY_HEARTBEAT_S": str(args.secondary_heartbeat_s),
            "ILUMINATY_VLM_DEVICE": device_policy,
            "ILUMINATY_VLM_DTYPE": args.vlm_dtype,
            "ILUMINATY_FAST_INACTIVE_SKIP": str(args.fast_inactive_skip),
            "ILUMINATY_FAST_INACTIVE_FORCE_S": str(args.fast_inactive_force_s),
            "ILUMINATY_OCR_ACTIVE_INTERVAL_S": str(args.ocr_active_interval_s),
            "ILUMINATY_OCR_INACTIVE_INTERVAL_S": str(args.ocr_inactive_interval_s),
            "ILUMINATY_OCR_CHANGE_THRESHOLD": str(args.ocr_change_threshold),
            "ILUMINATY_OCR_PHASH_THRESHOLD": str(args.ocr_phash_threshold),
            "ILUMINATY_CPU_THREADS": str(args.cpu_threads),
        }
    )
    cmd = [
        "py",
        "-3.13",
        "-m",
        "iluminaty.main",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--monitor",
        str(args.monitor),
        "--fps",
        str(args.fps),
        "--fast-loop-hz",
        str(args.fast_loop_hz),
        "--deep-loop-hz",
        str(args.deep_loop_hz),
        "--vision-profile",
        str(args.vision_profile),
    ]
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_proc_tree(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            time.sleep(2)
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
    except Exception:
        pass


def _run_profile(profile: str, port: int, args) -> dict:
    proc = _spawn_server(port=port, args=args, device_policy=profile)
    try:
        if not _wait_ready(port):
            return {"profile": profile, "error": "server_not_ready"}

        samples: list[dict] = []
        visual_samples: list[dict] = []

        prev_procs = _tree_procs(proc.pid)
        prev_cpu = _cpu_time_seconds(prev_procs)
        prev_ts = time.time()
        start = prev_ts

        while (time.time() - start) < args.duration:
            time.sleep(args.sample)
            now = time.time()
            procs = _tree_procs(proc.pid)
            cpu_now = _cpu_time_seconds(procs)
            dt = max(0.0001, now - prev_ts)
            dcpu = max(0.0, cpu_now - prev_cpu)
            cpu_pct_sum = (dcpu / dt) * 100.0
            rss_mb = _rss_mb(procs)
            gpu = _gpu_stats()

            point = {
                "t": round(now - start, 3),
                "cpu_pct_sum": round(cpu_pct_sum, 3),
                "rss_mb": round(rss_mb, 3),
                "gpu_available": bool(gpu["gpu_available"]),
                "gpu_util_pct": round(float(gpu["gpu_util_pct"]), 3),
                "gpu_mem_used_mb": round(float(gpu["gpu_mem_used_mb"]), 3),
                "gpu_mem_total_mb": round(float(gpu["gpu_mem_total_mb"]), 3),
            }
            samples.append(point)

            try:
                _, state = _http_json(port, "/perception/state", timeout=2.5)
                visual = state.get("visual") or {}
                deep = state.get("deep_loop") or {}
                visual_samples.append(
                    {
                        "t": point["t"],
                        "provider": visual.get("provider"),
                        "queue_size": visual.get("queue_size"),
                        "processed": visual.get("processed"),
                        "dropped": visual.get("dropped"),
                        "failures": visual.get("failures"),
                        "provider_status": visual.get("provider_status") or {},
                        "enqueued_active": deep.get("enqueued_active"),
                        "enqueued_secondary": deep.get("enqueued_secondary"),
                        "skipped_inactive": deep.get("skipped_inactive"),
                    }
                )
            except Exception:
                pass

            prev_cpu = cpu_now
            prev_ts = now

        cpu_vals = [s["cpu_pct_sum"] for s in samples]
        rss_vals = [s["rss_mb"] for s in samples]
        gpu_util_vals = [s["gpu_util_pct"] for s in samples]
        gpu_mem_vals = [s["gpu_mem_used_mb"] for s in samples]

        summary = {
            "profile": profile,
            "samples": len(samples),
            "duration_s": float(args.duration),
            "cpu_avg_pct_sum": round(_safe_mean(cpu_vals), 3),
            "cpu_p50_pct_sum": round(_pct(cpu_vals, 50), 3),
            "cpu_p95_pct_sum": round(_pct(cpu_vals, 95), 3),
            "cpu_peak_pct_sum": round(max(cpu_vals) if cpu_vals else 0.0, 3),
            "rss_avg_mb": round(_safe_mean(rss_vals), 3),
            "rss_p95_mb": round(_pct(rss_vals, 95), 3),
            "rss_peak_mb": round(max(rss_vals) if rss_vals else 0.0, 3),
            "gpu_util_avg_pct": round(_safe_mean(gpu_util_vals), 3),
            "gpu_util_p95_pct": round(_pct(gpu_util_vals, 95), 3),
            "gpu_util_peak_pct": round(max(gpu_util_vals) if gpu_util_vals else 0.0, 3),
            "gpu_mem_avg_mb": round(_safe_mean(gpu_mem_vals), 3),
            "gpu_mem_p95_mb": round(_pct(gpu_mem_vals, 95), 3),
            "gpu_mem_peak_mb": round(max(gpu_mem_vals) if gpu_mem_vals else 0.0, 3),
            "last_visual": visual_samples[-1] if visual_samples else {},
        }
        return {"summary": summary, "samples": samples, "visual_samples": visual_samples}
    finally:
        _stop_proc_tree(proc)


def _render_markdown(report: dict, args) -> str:
    lines: list[str] = []
    lines.append("# Benchmark — VLM Recursos (CPU vs CUDA)")
    lines.append("")
    lines.append(f"Generated (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}")
    lines.append("")
    lines.append("## Config")
    lines.append("")
    lines.append(f"- Profiles: `{','.join(report.get('profiles', []))}`")
    lines.append(f"- Duration/profile: `{args.duration}s`")
    lines.append(f"- Sample interval: `{args.sample}s`")
    lines.append(f"- Capture: monitor=`{args.monitor}` fps=`{args.fps}` fast_loop=`{args.fast_loop_hz}` deep_loop=`{args.deep_loop_hz}`")
    lines.append(f"- VLM: model=`{args.model}` device=`by profile` dtype=`{args.vlm_dtype}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Profile | CPU avg %sum | CPU p95 %sum | RAM avg MB | RAM p95 MB | GPU util avg % | GPU mem avg MB | Processed | Dropped | Queue | Runtime |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for p in report.get("profiles", []):
        entry = report.get("results", {}).get(p, {})
        s = entry.get("summary", {})
        lv = s.get("last_visual", {})
        ps = lv.get("provider_status", {})
        runtime = ps.get("runtime_device", "n/a")
        lines.append(
            f"| `{p}` | {s.get('cpu_avg_pct_sum', 0)} | {s.get('cpu_p95_pct_sum', 0)} | "
            f"{s.get('rss_avg_mb', 0)} | {s.get('rss_p95_mb', 0)} | {s.get('gpu_util_avg_pct', 0)} | "
            f"{s.get('gpu_mem_avg_mb', 0)} | {lv.get('processed', 0)} | {lv.get('dropped', 0)} | "
            f"{lv.get('queue_size', 0)} | `{runtime}` |"
        )
    lines.append("")

    if "cpu" in report.get("results", {}) and "cuda" in report.get("results", {}):
        c = report["results"]["cpu"]["summary"]
        g = report["results"]["cuda"]["summary"]
        cpu_delta = (g.get("cpu_avg_pct_sum", 0.0) - c.get("cpu_avg_pct_sum", 0.0))
        ram_delta = (g.get("rss_avg_mb", 0.0) - c.get("rss_avg_mb", 0.0))
        lines.append("## Delta (`cuda` vs `cpu`)")
        lines.append("")
        lines.append(f"- CPU avg delta: `{cpu_delta:+.3f}` (%sum)")
        lines.append(f"- RAM avg delta: `{ram_delta:+.3f}` (MB)")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- `%sum` means aggregate CPU across logical cores (can exceed 100%).")
    lines.append("- GPU metrics come from `nvidia-smi`; if unavailable they remain `0`.")
    lines.append("- This benchmark measures end-to-end runtime process cost, not only model inference.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark VLM runtime resources (CPU vs CUDA).")
    parser.add_argument("--duration", type=float, default=30.0, help="Seconds per profile")
    parser.add_argument("--sample", type=float, default=3.0, help="Sampling interval seconds")
    parser.add_argument("--profiles", type=str, default="cpu,cuda", help="Comma-separated profiles: cpu,cuda")
    parser.add_argument("--base-port", type=int, default=8500, help="Base port for profile runs")

    parser.add_argument("--monitor", type=int, default=0)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--fast-loop-hz", type=float, default=8.0)
    parser.add_argument("--deep-loop-hz", type=float, default=1.0)
    parser.add_argument("--vision-profile", type=str, default="core_ram")

    parser.add_argument("--model", type=str, default="HuggingFaceTB/SmolVLM2-500M-Instruct")
    parser.add_argument("--int8", action="store_true", help="Enable CPU int8 quantization path")
    parser.add_argument("--image-size", type=int, default=384)
    parser.add_argument("--max-tokens", type=int, default=48)
    parser.add_argument("--vlm-min-interval-ms", type=int, default=2000)
    parser.add_argument("--vlm-keepalive-ms", type=int, default=12000)
    parser.add_argument("--vlm-priority-threshold", type=float, default=0.85)
    parser.add_argument("--secondary-heartbeat-s", type=float, default=20.0)
    parser.add_argument("--vlm-dtype", type=str, default="auto", choices=["auto", "fp16", "bf16", "fp32"])

    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--fast-inactive-skip", type=int, default=6)
    parser.add_argument("--fast-inactive-force-s", type=float, default=3.0)
    parser.add_argument("--ocr-active-interval-s", type=float, default=6.0)
    parser.add_argument("--ocr-inactive-interval-s", type=float, default=20.0)
    parser.add_argument("--ocr-change-threshold", type=float, default=0.45)
    parser.add_argument("--ocr-phash-threshold", type=int, default=22)

    parser.add_argument(
        "--out-json",
        type=pathlib.Path,
        default=ROOT / "BENCHMARK-RESOURCES-VLM.json",
        help="Output JSON report path",
    )
    parser.add_argument(
        "--out-md",
        type=pathlib.Path,
        default=ROOT / "BENCHMARK-RESOURCES-VLM.md",
        help="Output Markdown report path",
    )
    args = parser.parse_args()

    profiles = [p.strip().lower() for p in args.profiles.split(",") if p.strip()]
    profiles = [p for p in profiles if p in {"cpu", "cuda"}]
    if not profiles:
        raise SystemExit("No valid profiles provided. Use cpu,cuda")

    config_json = {}
    for key, value in vars(args).items():
        if isinstance(value, pathlib.Path):
            config_json[key] = str(value)
        else:
            config_json[key] = value

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
        "config": config_json,
        "results": {},
    }

    for i, profile in enumerate(profiles):
        port = args.base_port + i
        print(f"[bench] running profile={profile} port={port} ...")
        result = _run_profile(profile=profile, port=port, args=args)
        report["results"][profile] = result
        summary = result.get("summary", {})
        if summary:
            print(
                f"[bench] {profile}: cpu_avg={summary.get('cpu_avg_pct_sum')} "
                f"rss_avg={summary.get('rss_avg_mb')} "
                f"gpu_avg={summary.get('gpu_util_avg_pct')}"
            )
        else:
            print(f"[bench] {profile}: failed -> {result.get('error')}")

    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.out_md.write_text(_render_markdown(report, args), encoding="utf-8")
    print(f"[bench] json -> {args.out_json}")
    print(f"[bench] md   -> {args.out_md}")


if __name__ == "__main__":
    main()
