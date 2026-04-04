"""IPA v3 — Real-World Benchmark System

7 tests that prove the AI has real eyes:
  1. --device        CPU vs GPU head-to-head
  2. --compression   Quality vs size (3-bit, 4-bit, int8)
  3. --memory        RAM scaling (100 → 10K frames)
  4. --realtime      Live video perception (THE BIG ONE)
  5. --motion-accuracy  Guided motion classification
  6. --tokens        Token efficiency comparison
  7. --sustained     5-minute endurance test

Usage:
    python -m ipa.benchmark_real                     # All tests
    python -m ipa.benchmark_real --realtime --fps 3  # Live perception
    python -m ipa.benchmark_real --device            # CPU vs GPU
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import sys
import time
import tracemalloc
import base64
import io
from collections import Counter
from datetime import datetime

import numpy as np
from PIL import Image

PY = sys.executable
REPORT: dict = {"timestamp": datetime.now().isoformat(), "system": {}, "results": {}}

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Helpers ─────────────────────────────────────────────────────────────────

def _header(title: str):
    print(f"\n\033[1;36m{'━'*60}\033[0m")
    print(f"\033[1;37m  {title}\033[0m")
    print(f"\033[1;36m{'━'*60}\033[0m\n")

def _row(label: str, value, unit: str = "", target: str = ""):
    t = f"  \033[90m(target: {target})\033[0m" if target else ""
    print(f"  \033[37m{label:.<42}\033[0m \033[1;33m{value}\033[0m {unit}{t}")

def _ok(msg: str):
    print(f"  \033[1;32m✓\033[0m {msg}")

def _fail(msg: str):
    print(f"  \033[1;31m✗\033[0m {msg}")

def _info(msg: str):
    print(f"  \033[90m{msg}\033[0m")

def _grab_screen():
    """Capture primary monitor screenshot as PIL Image."""
    import mss
    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.rgb)

def _system_info() -> dict:
    info = {
        "platform": platform.platform(),
        "cpu": platform.processor() or "unknown",
        "ram_total_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3), 1) if hasattr(os, "sysconf") else "N/A",
        "python": platform.python_version(),
    }
    try:
        import psutil
        info["ram_total_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
        info["cpu_count"] = psutil.cpu_count()
    except ImportError:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["gpu_vram_mb"] = round(torch.cuda.get_device_properties(0).total_memory / (1024**2))
            info["cuda_version"] = torch.version.cuda
        else:
            info["gpu"] = "None (CUDA not available)"
    except ImportError:
        info["gpu"] = "N/A (torch not installed)"
    REPORT["system"] = info
    return info

def _latency_stats(latencies: list[float]) -> dict:
    lat = sorted(latencies)
    n = len(lat)
    return {
        "mean_ms": round(np.mean(lat), 1),
        "median_ms": round(np.median(lat), 1),
        "p95_ms": round(lat[int(n * 0.95)] if n >= 20 else max(lat), 1),
        "min_ms": round(min(lat), 1),
        "max_ms": round(max(lat), 1),
        "count": n,
    }


# ── Test 1: Device Comparison ───────────────────────────────────────────────

def test_device(args):
    _header("TEST 1: DEVICE COMPARISON — CPU vs GPU")
    from ipa.engine import IPAEngine

    img = _grab_screen()
    _row("Screen", f"{img.size[0]}x{img.size[1]}")
    n = args.n_frames or 30

    devices = []
    if args.target_device in ("auto", "cpu"):
        devices.append("cpu")
    if args.target_device in ("auto", "cuda"):
        import torch
        if torch.cuda.is_available():
            devices.append("cuda")
        else:
            _info("CUDA not available — skipping GPU test")

    results = {}
    for dev in devices:
        print(f"\n  \033[1;35m▸ {dev.upper()}\033[0m")
        dtype = "fp16" if dev == "cuda" else "fp32"
        engine = IPAEngine(config={"device": dev, "int8": False})

        # Warmup (loads model)
        engine.feed(img, metadata={"window_name": "benchmark"})

        import torch
        if dev == "cuda":
            torch.cuda.reset_peak_memory_stats()

        latencies = []
        encode_times = []
        for i in range(n):
            t0 = time.perf_counter()
            frame = engine.feed(img, metadata={"window_name": "benchmark"})
            total = (time.perf_counter() - t0) * 1000
            latencies.append(total)
            # Approximate encode time from encoder stats
            if i == 0:
                encode_times.append(total)
            else:
                encode_times.append(total)

        stats = _latency_stats(latencies)
        max_fps = round(1000 / stats["mean_ms"], 1) if stats["mean_ms"] > 0 else 999

        mem = 0
        if dev == "cuda":
            mem = round(torch.cuda.max_memory_allocated() / (1024**2), 1)
        else:
            mem = round(engine.status()["stream"]["memory_kb"] / 1024, 1)

        _row(f"Pipeline latency (mean)", f"{stats['mean_ms']}", "ms")
        _row(f"Pipeline latency (p95)", f"{stats['p95_ms']}", "ms")
        _row(f"Pipeline latency (min)", f"{stats['min_ms']}", "ms")
        _row(f"Max sustainable FPS", f"{max_fps}", "fps")
        _row(f"Memory", f"{mem}", "MB")

        results[dev] = {**stats, "max_fps": max_fps, "memory_mb": mem, "frames": n, "dtype": dtype}

        # Cleanup
        engine.encoder.unload()
        if dev == "cuda":
            torch.cuda.empty_cache()

    if "cpu" in results and "cuda" in results:
        speedup = round(results["cpu"]["mean_ms"] / results["cuda"]["mean_ms"], 1)
        print(f"\n  \033[1;32m⚡ GPU Speedup: {speedup}x faster than CPU\033[0m")
        results["speedup"] = speedup

    REPORT["results"]["device_comparison"] = results


# ── Test 2: Compression Quality ─────────────────────────────────────────────

def test_compression(args):
    _header("TEST 2: COMPRESSION QUALITY — 3-bit vs 4-bit vs int8")
    from ipa.compressor import DeltaCompressor

    np.random.seed(42)
    patches = np.random.randn(196, 768).astype(np.float32)
    modified = patches.copy()
    idx = np.random.choice(196, 25, replace=False)
    modified[idx] += np.random.randn(25, 768).astype(np.float32) * 0.5

    raw_size = patches.nbytes
    results = {}

    for label, bits, use_tq in [("turboquant_3bit", 3, True), ("turboquant_4bit", 4, True), ("int8_fallback", 3, False)]:
        if not use_tq:
            # Force fallback by not importing turboquant
            comp = DeltaCompressor(dim=768, bits=8, similarity_threshold=0.92)
            comp._tq = None
            comp._backend = "fallback_int8"
        else:
            comp = DeltaCompressor(dim=768, bits=bits)

        print(f"\n  \033[1;35m▸ {label}\033[0m")

        # I-frame
        t0 = time.perf_counter()
        iframe = comp.compress_keyframe(patches)
        ct = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        recon = comp.decompress_keyframe(iframe)
        dt = (time.perf_counter() - t0) * 1000

        mse = float(np.mean((patches - recon)**2))
        cos = float(np.mean(np.sum(patches * recon, axis=1) / (np.linalg.norm(patches, axis=1) * np.linalg.norm(recon, axis=1) + 1e-8)))
        ratio = raw_size / len(iframe)

        _row("I-frame size", f"{len(iframe)/1024:.1f}", "KB")
        _row("Ratio vs raw", f"{ratio:.1f}x")
        _row("MSE", f"{mse:.6f}")
        _row("Cosine similarity", f"{cos:.6f}")
        _row("Compress time", f"{ct:.1f}", "ms")
        _row("Decompress time", f"{dt:.1f}", "ms")

        # P-frame
        mask, delta, motion = comp.compress_delta(modified, patches)
        psize = len(mask) + len(delta) + len(motion)
        _row("P-frame size (25 changed)", f"{psize/1024:.1f}", "KB")

        results[label] = {
            "iframe_kb": round(len(iframe)/1024, 1),
            "pframe_kb": round(psize/1024, 1),
            "ratio": round(ratio, 1),
            "mse": round(mse, 6),
            "cosine_sim": round(cos, 6),
            "compress_ms": round(ct, 1),
            "decompress_ms": round(dt, 1),
        }

    REPORT["results"]["compression"] = results


# ── Test 3: Memory Scaling ──────────────────────────────────────────────────

def test_memory(args):
    _header("TEST 3: MEMORY SCALING")
    from ipa.compressor import DeltaCompressor, _pack_bitmask
    from ipa.stream import VisualStream
    from ipa.types import PatchFrame

    comp = DeltaCompressor(dim=768, bits=3)
    np.random.seed(42)
    base = np.random.randn(196, 768).astype(np.float32)
    iframe_data = comp.compress_keyframe(base)
    cls_data = comp.compress_vectors(base[:1])

    scale_points = [100, 500, 1000, 5000, 10000]
    results = []

    for target in scale_points:
        stream = VisualStream(max_frames=target + 100, compressor=comp)
        tracemalloc.start()

        for i in range(target):
            is_key = i % 30 == 0
            if is_key:
                grid = iframe_data
                ftype, n_ch = "I", 196
                mask = _pack_bitmask(np.ones(196, dtype=bool))
            else:
                n_ch = np.random.randint(5, 40)
                changed = np.zeros(196, dtype=bool)
                changed[np.random.choice(196, n_ch, replace=False)] = True
                mask = _pack_bitmask(changed)
                grid = comp.compress_vectors(np.random.randn(n_ch, 768).astype(np.float32) * 0.1)
                ftype = "P"

            stream.push(PatchFrame(
                timestamp=time.time() - (target - i) * 0.33,
                frame_type=ftype, patch_grid=grid, change_mask=mask,
                motion_vectors=b"", cls_embedding=cls_data,
                n_changed=n_ch, metadata={"monitor_id": 1, "window_name": "bench"},
            ))

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        buf_mem = stream.memory_bytes
        per_frame = buf_mem / target
        proj_1hr = per_frame * 10800 / (1024**2)  # 1hr @ 3fps

        _row(f"{target:>6} frames", f"{buf_mem/1024:.0f} KB", f"({per_frame:.0f} B/frame, proj 1hr: {proj_1hr:.0f} MB)")
        results.append({
            "frames": target,
            "buffer_kb": round(buf_mem/1024, 1),
            "per_frame_bytes": round(per_frame),
            "projected_1hr_mb": round(proj_1hr),
            "tracemalloc_peak_mb": round(peak/(1024**2), 1),
        })

    REPORT["results"]["memory_scaling"] = results


# ── Test 4: Real-Time Video Perception ──────────────────────────────────────

def test_realtime(args):
    _header("TEST 4: REAL-TIME VIDEO PERCEPTION")
    from ipa.engine import IPAEngine
    import mss

    fps = args.fps or 3
    duration = args.duration or 30
    device = args.target_device if args.target_device != "auto" else "cpu"

    print(f"  \033[1;33mConfig: {fps} FPS, {duration}s, device={device}\033[0m")
    print(f"\n  \033[1;37m▶ Open a video in YouTube or VLC, then press ENTER...\033[0m", end="")
    input()

    engine = IPAEngine(config={"device": device, "int8": False})
    interval = 1.0 / fps
    start_time = time.time()
    frame_count = 0
    dropped = 0
    latencies = []
    motion_log = []
    last_print = 0

    print(f"\n  \033[1;32m● CAPTURING — {duration}s at {fps}fps\033[0m\n")

    with mss.mss() as sct:
        mon = sct.monitors[1]
        next_capture = time.time()

        while (time.time() - start_time) < duration:
            now = time.time()
            if now < next_capture:
                time.sleep(max(0, next_capture - now - 0.001))
                continue

            # Check if we missed frames
            while next_capture < now - interval:
                next_capture += interval
                dropped += 1
            next_capture += interval

            # Capture
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

            # Process
            t0 = time.perf_counter()
            frame = engine.feed(img, metadata={"window_name": "video_test"})
            pipeline_ms = (time.perf_counter() - t0) * 1000
            latencies.append(pipeline_ms)
            frame_count += 1

            # Get motion
            motion = engine.motion(seconds=3)
            elapsed = now - start_time

            # Print live perception every ~1 second
            if elapsed - last_print >= 1.0:
                last_print = elapsed
                mtype = motion.motion_type
                spd = motion.speed
                n_ch = frame.n_changed
                ftype = frame.frame_type

                color = "\033[32m" if mtype == "static" else "\033[33m" if mtype in ("typing", "cursor") else "\033[35m"
                print(f"  [{elapsed:05.1f}s] {color}{mtype:.<16}\033[0m "
                      f"speed={spd:.2f}  patches={n_ch:>3}  {ftype}-frame  {pipeline_ms:.0f}ms")
                motion_log.append({"t": round(elapsed, 1), "motion": mtype, "speed": round(spd, 2), "patches": n_ch})

    # Summary
    ctx = engine.context(seconds=duration)
    stats = _latency_stats(latencies)
    motion_hist = Counter(m["motion"] for m in motion_log)
    buf = engine.status()["stream"]

    print(f"\n\033[1;36m{'━'*60}\033[0m")
    print(f"  \033[1;37mSUMMARY\033[0m")
    _row("Frames processed", frame_count)
    _row("Frames dropped", dropped)
    _row("Actual FPS", f"{frame_count/duration:.1f}")
    _row("Avg pipeline latency", f"{stats['mean_ms']:.0f}", "ms")
    _row("P95 pipeline latency", f"{stats['p95_ms']:.0f}", "ms")
    _row("Buffer memory", f"{buf['memory_kb']:.0f}", "KB")
    _row("Motion histogram", str(dict(motion_hist)))
    _row("Scene transitions", len(ctx.timeline))
    print(f"\n  \033[90mContext output:\033[0m")
    print(f"  {ctx.to_text()}")

    REPORT["results"]["realtime"] = {
        "fps_target": fps, "fps_actual": round(frame_count/duration, 1),
        "duration_s": duration, "device": device,
        "frames_processed": frame_count, "frames_dropped": dropped,
        "latency": stats, "motion_histogram": dict(motion_hist),
        "scene_transitions": len(ctx.timeline),
        "buffer_memory_kb": round(buf["memory_kb"], 1),
        "motion_log": motion_log,
    }


# ── Test 5: Motion Accuracy ────────────────────────────────────────────────

def test_motion_accuracy(args):
    _header("TEST 5: MOTION ACCURACY — Guided Classification")
    from ipa.engine import IPAEngine
    import mss

    engine = IPAEngine(config={"device": "cpu", "int8": False})
    test_duration = 5

    scenarios = [
        ("idle", "static", "Keep your screen IDLE (don't touch anything)"),
        ("scroll", "scroll_down", "SCROLL a webpage up and down"),
        ("typing", "typing", "TYPE some text in any editor/notepad"),
        ("cursor", "cursor", "MOVE your mouse cursor around quickly"),
        ("video", "video", "PLAY a video (YouTube, any video)"),
    ]

    results = []
    print(f"  We'll test 5 motion patterns, {test_duration}s each.\n")

    for action, expected, instruction in scenarios:
        print(f"  \033[1;33m▶ {instruction} for {test_duration}s — press ENTER to start...\033[0m", end="")
        input()
        engine.reset()

        # Capture for test_duration seconds
        with mss.mss() as sct:
            mon = sct.monitors[1]
            start = time.time()
            while time.time() - start < test_duration:
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
                engine.feed(img, metadata={"window_name": action})
                time.sleep(0.33)  # ~3fps

        motion = engine.motion(seconds=test_duration)
        got = motion.motion_type
        # Accept related types as correct
        accept = {
            "static": {"static", "idle"},
            "scroll_down": {"scroll_down", "scroll_up", "scroll_horizontal", "loading"},
            "typing": {"typing", "interaction", "cursor"},
            "cursor": {"cursor", "typing", "interaction"},
            "video": {"video", "loading"},
        }
        passed = got in accept.get(expected, {expected})

        if passed:
            _ok(f"{action:.<15} expected={expected:.<16} got={got:.<16} ✓")
        else:
            _fail(f"{action:.<15} expected={expected:.<16} got={got:.<16} ✗")

        results.append({"action": action, "expected": expected, "got": got, "passed": passed})

    total = len(results)
    correct = sum(1 for r in results if r["passed"])
    print(f"\n  \033[1;{'32' if correct == total else '31'}m{correct}/{total} passed\033[0m")
    REPORT["results"]["motion_accuracy"] = {"tests": total, "passed": correct, "details": results}


# ── Test 6: Token Efficiency ───────────────────────────────────────────────

def test_tokens(args):
    _header("TEST 6: TOKEN EFFICIENCY — IPA vs Screenshots vs OCR")

    img = _grab_screen()

    # 1. Full screenshot (WebP base64)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    full_b64 = base64.b64encode(buf.getvalue()).decode()
    full_tokens = len(full_b64) // 4  # rough estimate: 4 chars ≈ 1 token

    # 2. Low-res screenshot (320px)
    ratio = 320 / max(img.size)
    low = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)), Image.LANCZOS)
    buf2 = io.BytesIO()
    low.save(buf2, format="WEBP", quality=60)
    low_b64 = base64.b64encode(buf2.getvalue()).decode()
    low_tokens = len(low_b64) // 4

    # 3. OCR text (simulated — count characters)
    ocr_tokens = 200  # typical OCR output

    # 4. IPA context
    from ipa.engine import IPAEngine
    engine = IPAEngine(config={"device": "cpu"})
    engine.feed(img, metadata={"window_name": "token_test"})
    for _ in range(5):
        engine.feed(img, metadata={"window_name": "token_test"})
    ctx = engine.context(seconds=10)
    ipa_text = ctx.to_text()
    ipa_tokens = len(ipa_text) // 4  # rough

    # 5. IPA + thumbnail
    ipa_plus_tokens = ipa_tokens + low_tokens

    print(f"  {'Mode':<25} {'Tokens':>8}  {'Motion?':>9}  {'Layout?':>9}  {'Text?':>7}")
    print(f"  {'─'*65}")
    modes = [
        ("Screenshot (full_res)", full_tokens, "No", "Yes (pixel)", "Yes"),
        ("Screenshot (low_res)", low_tokens, "No", "Blurry", "No"),
        ("OCR text_only", ocr_tokens, "No", "No", "Yes"),
        ("IPA context", ipa_tokens, "YES", "YES", "No"),
        ("IPA + thumbnail", ipa_plus_tokens, "YES", "YES", "Blurry"),
    ]
    for name, tokens, motion, layout, text in modes:
        color = "\033[32m" if "YES" in motion else "\033[37m"
        print(f"  {color}{name:<25}\033[0m {tokens:>8}  {motion:>9}  {layout:>9}  {text:>7}")

    savings = round((1 - ipa_tokens / full_tokens) * 100, 1)
    print(f"\n  \033[1;32m⚡ IPA context is {full_tokens // max(ipa_tokens,1)}x more efficient than full screenshot\033[0m")
    print(f"  \033[1;32m⚡ {savings}% token savings with motion + layout awareness\033[0m")

    REPORT["results"]["token_efficiency"] = {
        "screenshot_full": full_tokens, "screenshot_low": low_tokens,
        "ocr": ocr_tokens, "ipa_context": ipa_tokens,
        "ipa_plus_thumbnail": ipa_plus_tokens,
        "savings_pct": savings,
    }
    engine.encoder.unload()


# ── Test 7: Sustained Load ─────────────────────────────────────────────────

def test_sustained(args):
    _header("TEST 7: SUSTAINED LOAD — Endurance Test")
    from ipa.engine import IPAEngine
    import psutil
    import mss

    fps = args.fps or 3
    duration = args.duration or 300  # 5 min default
    device = args.target_device if args.target_device != "auto" else "cpu"
    sample_interval = 10  # seconds between metric samples

    print(f"  \033[1;33mConfig: {fps} FPS, {duration}s ({duration//60}m), device={device}\033[0m")
    print(f"  Running... (metrics every {sample_interval}s)\n")

    engine = IPAEngine(config={"device": device, "int8": False})
    interval = 1.0 / fps
    start_time = time.time()
    frame_count = 0
    dropped = 0
    latencies = []
    samples = []
    last_sample = 0
    proc = psutil.Process()

    import torch
    has_cuda = device == "cuda" and torch.cuda.is_available()
    if has_cuda:
        torch.cuda.reset_peak_memory_stats()

    print(f"  {'Time':>6}  {'CPU%':>5}  {'RAM':>7}  {'VRAM':>7}  {'Latency':>8}  {'Dropped':>7}  {'Frames':>7}")
    print(f"  {'─'*55}")

    with mss.mss() as sct:
        mon = sct.monitors[1]
        next_capture = time.time()

        while (time.time() - start_time) < duration:
            now = time.time()
            if now < next_capture:
                time.sleep(max(0, next_capture - now - 0.001))
                continue

            while next_capture < now - interval:
                next_capture += interval
                dropped += 1
            next_capture += interval

            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

            t0 = time.perf_counter()
            engine.feed(img, metadata={"window_name": "sustained_test"})
            pipeline_ms = (time.perf_counter() - t0) * 1000
            latencies.append(pipeline_ms)
            frame_count += 1

            elapsed = now - start_time
            if elapsed - last_sample >= sample_interval:
                last_sample = elapsed
                cpu_pct = proc.cpu_percent(interval=0)
                ram_mb = proc.memory_info().rss / (1024**2)
                vram_mb = torch.cuda.memory_allocated() / (1024**2) if has_cuda else 0
                avg_lat = np.mean(latencies[-int(fps*sample_interval):]) if latencies else 0

                minutes = int(elapsed) // 60
                seconds = int(elapsed) % 60
                print(f"  {minutes}:{seconds:02d}    {cpu_pct:>4.0f}%  {ram_mb:>5.0f}MB  "
                      f"{vram_mb:>5.0f}MB  {avg_lat:>6.0f}ms  {dropped:>7}  {frame_count:>7}")

                samples.append({
                    "elapsed_s": round(elapsed), "cpu_pct": round(cpu_pct, 1),
                    "ram_mb": round(ram_mb, 1), "vram_mb": round(vram_mb, 1),
                    "avg_latency_ms": round(avg_lat, 1), "dropped": dropped,
                    "frames": frame_count,
                })

    stats = _latency_stats(latencies)
    buf = engine.status()["stream"]

    print(f"\n  \033[1;37mSUMMARY\033[0m")
    _row("Duration", f"{duration}s ({duration//60}m)")
    _row("Total frames", frame_count)
    _row("Dropped frames", dropped)
    _row("Actual FPS", f"{frame_count/duration:.1f}")
    _row("Avg latency", f"{stats['mean_ms']:.0f}", "ms")
    _row("P95 latency", f"{stats['p95_ms']:.0f}", "ms")
    _row("Buffer memory", f"{buf['memory_kb']:.0f}", "KB")

    stable = all(
        abs(s["avg_latency_ms"] - stats["mean_ms"]) < stats["mean_ms"] * 0.3
        for s in samples if s["avg_latency_ms"] > 0
    ) if samples else True

    if stable and dropped < frame_count * 0.05:
        _ok("STABLE — no degradation detected")
    else:
        _fail(f"DEGRADATION — {dropped} dropped, latency variance high")

    REPORT["results"]["sustained"] = {
        "duration_s": duration, "device": device, "fps_target": fps,
        "fps_actual": round(frame_count/duration, 1),
        "frames_total": frame_count, "frames_dropped": dropped,
        "latency": stats, "buffer_memory_kb": round(buf["memory_kb"], 1),
        "stable": stable, "samples": samples,
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IPA v3 — Real-World Benchmark System")
    parser.add_argument("--device", dest="target_device", default="auto", help="cpu/cuda/auto")
    parser.add_argument("--fps", type=int, default=3, help="Target FPS for realtime/sustained")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--n-frames", type=int, default=None, help="Frame count for device test")
    parser.add_argument("--output", type=str, default="ipa_benchmark_report.json", help="JSON output")

    # Test selectors
    parser.add_argument("--all", action="store_true", help="Run all tests")
    g = parser.add_argument_group("tests")
    g.add_argument("--device-test", action="store_true", dest="t_device", help="CPU vs GPU")
    g.add_argument("--compression", action="store_true", dest="t_compression", help="Quality vs size")
    g.add_argument("--memory", action="store_true", dest="t_memory", help="RAM scaling")
    g.add_argument("--realtime", action="store_true", dest="t_realtime", help="Live video perception")
    g.add_argument("--motion-accuracy", action="store_true", dest="t_motion", help="Guided motion test")
    g.add_argument("--tokens", action="store_true", dest="t_tokens", help="Token comparison")
    g.add_argument("--sustained", action="store_true", dest="t_sustained", help="Endurance test")

    args = parser.parse_args()
    run_all = args.all or not any([
        args.t_device, args.t_compression, args.t_memory,
        args.t_realtime, args.t_motion, args.t_tokens, args.t_sustained
    ])

    _header("IPA v3 — REAL EYES BENCHMARK SYSTEM")
    info = _system_info()
    _row("Platform", info.get("platform", ""))
    _row("CPU", info.get("cpu", ""))
    _row("GPU", info.get("gpu", "N/A"))
    _row("RAM", f"{info.get('ram_total_gb', '?')} GB")
    _row("Python", info.get("python", ""))

    if run_all or args.t_compression:
        test_compression(args)
    if run_all or args.t_memory:
        test_memory(args)
    if run_all or args.t_device:
        test_device(args)
    if run_all or args.t_tokens:
        test_tokens(args)
    if run_all or args.t_realtime:
        test_realtime(args)
    if run_all or args.t_motion:
        test_motion_accuracy(args)
    if run_all or args.t_sustained:
        test_sustained(args)

    # Save JSON report
    output = args.output
    with open(output, "w") as f:
        json.dump(REPORT, f, indent=2, default=str)
    print(f"\n  \033[1;32m📄 Report saved: {output}\033[0m")

    _header("BENCHMARK COMPLETE")


if __name__ == "__main__":
    main()
