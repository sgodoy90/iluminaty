"""IPA v3 — Benchmark suite: validate encoder, compressor, stream, and full pipeline.

Usage:
    python -m ipa.benchmark                 # Run all benchmarks
    python -m ipa.benchmark --encoder       # Encoder latency + memory
    python -m ipa.benchmark --compressor    # Compression ratio + roundtrip
    python -m ipa.benchmark --stream        # Buffer memory + search latency
    python -m ipa.benchmark --e2e           # Full pipeline: screen → context
    python -m ipa.benchmark --motion        # Motion detection
"""
from __future__ import annotations
import argparse
import sys
import time
import os
import tracemalloc

import numpy as np
from PIL import Image


def _banner(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _result(name: str, value, unit: str = "", target: str = ""):
    status = ""
    if target:
        status = f"  (target: {target})"
    print(f"  {name:.<40} {value} {unit}{status}")


def bench_encoder(args):
    """Benchmark: SigLIP encoder latency, memory, output shape."""
    _banner("ENCODER BENCHMARK")
    from ipa.encoder import VisualEncoder

    enc = VisualEncoder(int8=not args.no_int8, device=args.device)
    _result("Model", enc.model_name)
    _result("Device", enc.device)
    _result("Int8", enc.int8)
    _result("Loaded before encode", enc.is_loaded)

    # Create test image
    img = Image.fromarray(np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8))

    # Warmup + load
    print("\n  Loading model (first encode)...")
    tracemalloc.start()
    t0 = time.perf_counter()
    patches = enc.encode_patches(img)
    load_time = (time.perf_counter() - t0) * 1000
    mem_current, mem_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    _result("Load + first encode", f"{load_time:.0f}", "ms")
    _result("Memory (peak)", f"{mem_peak / 1024 / 1024:.1f}", "MB")
    _result("Patch shape", str(patches.shape), "", "(196, 768)")
    _result("Patch dtype", str(patches.dtype))

    # CLS
    cls = enc.encode_cls(img)
    _result("CLS shape", str(cls.shape), "", "(768,)")

    # Latency benchmark
    n_frames = args.n_frames or 50
    print(f"\n  Encoding {n_frames} frames...")
    latencies = []
    for _ in range(n_frames):
        t0 = time.perf_counter()
        enc.encode_patches(img)
        latencies.append((time.perf_counter() - t0) * 1000)

    lat = sorted(latencies)
    _result("Mean latency", f"{np.mean(lat):.1f}", "ms", "<50ms CPU")
    _result("Median latency", f"{np.median(lat):.1f}", "ms")
    _result("P95 latency", f"{lat[int(len(lat)*0.95)]:.1f}", "ms")
    _result("Min/Max", f"{min(lat):.1f} / {max(lat):.1f}", "ms")

    stats = enc.stats()
    _result("Encoder status", "OK" if stats["loaded"] else "FAIL")
    print()


def bench_compressor(args):
    """Benchmark: compression ratio, roundtrip accuracy, I/P frame sizes."""
    _banner("COMPRESSOR BENCHMARK")
    from ipa.compressor import DeltaCompressor

    comp = DeltaCompressor(dim=768, bits=3)
    _result("Backend", comp.backend)

    # Generate synthetic patches
    np.random.seed(42)
    patches_a = np.random.randn(196, 768).astype(np.float32)
    patches_b = patches_a.copy()
    # Modify ~20 patches (simulate typical screen change)
    changed_idx = np.random.choice(196, 20, replace=False)
    patches_b[changed_idx] += np.random.randn(20, 768).astype(np.float32) * 0.5

    # I-frame compression
    t0 = time.perf_counter()
    iframe_bytes = comp.compress_keyframe(patches_a)
    iframe_time = (time.perf_counter() - t0) * 1000
    raw_size = patches_a.nbytes  # 196 * 768 * 4 = 601 KB
    _result("I-frame raw size", f"{raw_size/1024:.1f}", "KB")
    _result("I-frame compressed", f"{len(iframe_bytes)/1024:.1f}", "KB", "<60 KB")
    _result("I-frame ratio", f"{raw_size/len(iframe_bytes):.1f}x")
    _result("I-frame compress time", f"{iframe_time:.1f}", "ms")

    # I-frame roundtrip
    t0 = time.perf_counter()
    reconstructed = comp.decompress_keyframe(iframe_bytes)
    decomp_time = (time.perf_counter() - t0) * 1000
    mse = float(np.mean((patches_a - reconstructed) ** 2))
    cosine_sim = float(np.mean(
        np.sum(patches_a * reconstructed, axis=1) /
        (np.linalg.norm(patches_a, axis=1) * np.linalg.norm(reconstructed, axis=1) + 1e-8)
    ))
    _result("I-frame roundtrip MSE", f"{mse:.6f}", "", "<0.01")
    _result("I-frame cosine similarity", f"{cosine_sim:.6f}", "", ">0.99")
    _result("I-frame decompress time", f"{decomp_time:.1f}", "ms")

    # P-frame compression
    t0 = time.perf_counter()
    change_mask, delta_bytes, motion_bytes = comp.compress_delta(patches_b, patches_a)
    pframe_time = (time.perf_counter() - t0) * 1000
    pframe_total = len(change_mask) + len(delta_bytes) + len(motion_bytes)
    _result("P-frame change mask", f"{len(change_mask)}", "bytes (25)")
    _result("P-frame delta", f"{len(delta_bytes)/1024:.1f}", "KB", "<8 KB")
    _result("P-frame motion", f"{len(motion_bytes)}", "bytes")
    _result("P-frame total", f"{pframe_total/1024:.1f}", "KB")
    _result("P-frame compress time", f"{pframe_time:.1f}", "ms")

    # P-frame roundtrip
    t0 = time.perf_counter()
    reconstructed_b = comp.decompress_delta(patches_a, change_mask, delta_bytes)
    pdecomp_time = (time.perf_counter() - t0) * 1000
    mse_p = float(np.mean((patches_b - reconstructed_b) ** 2))
    _result("P-frame roundtrip MSE", f"{mse_p:.6f}")
    _result("P-frame decompress time", f"{pdecomp_time:.1f}", "ms")

    # Unchanged frame (should be near-zero)
    change_mask_same, delta_same, motion_same = comp.compress_delta(patches_a, patches_a)
    _result("Identical frame delta size", f"{len(delta_same)}", "bytes (should be 0)")

    print()


def bench_stream(args):
    """Benchmark: buffer memory, search latency, timeline."""
    _banner("STREAM BENCHMARK")
    from ipa.compressor import DeltaCompressor, _pack_bitmask
    from ipa.stream import VisualStream
    from ipa.types import PatchFrame

    comp = DeltaCompressor(dim=768, bits=3)
    stream = VisualStream(max_frames=10000, compressor=comp)

    np.random.seed(42)
    n_frames = args.n_frames or 1000
    print(f"  Pushing {n_frames} frames...")

    t0 = time.perf_counter()
    base_patches = np.random.randn(196, 768).astype(np.float32)
    iframe_data = comp.compress_keyframe(base_patches)
    cls_data = comp.compress_vectors(base_patches[:1])

    for i in range(n_frames):
        is_key = i % 30 == 0
        if is_key:
            grid = iframe_data
            ftype = "I"
            mask = _pack_bitmask(np.ones(196, dtype=bool))
            n_ch = 196
        else:
            n_changed = np.random.randint(5, 40)
            changed = np.zeros(196, dtype=bool)
            changed[np.random.choice(196, n_changed, replace=False)] = True
            mask = _pack_bitmask(changed)
            delta = np.random.randn(n_changed, 768).astype(np.float32) * 0.1
            grid = comp.compress_vectors(delta)
            ftype = "P"
            n_ch = n_changed

        frame = PatchFrame(
            timestamp=time.time() - (n_frames - i) * 0.33,
            frame_type=ftype,
            patch_grid=grid,
            change_mask=mask,
            motion_vectors=b"\x07\x07\x80" * min(n_ch, 30),
            cls_embedding=cls_data,
            n_changed=n_ch,
            metadata={"monitor_id": 1, "window_name": f"App-{i%5}", "scene_hint": "editing"},
        )
        stream.push(frame)

    push_time = (time.perf_counter() - t0) * 1000
    stats = stream.stats()
    _result("Frames pushed", stats["frames"])
    _result("Push time total", f"{push_time:.0f}", "ms")
    _result("Push time per frame", f"{push_time/n_frames:.2f}", "ms")
    _result("Buffer memory", f"{stats['memory_kb']:.1f}", "KB")
    _result("Buffer memory", f"{stats['memory_mb']:.2f}", "MB")
    _result("I-frames", stats["i_frames"])
    _result("P-frames", stats["p_frames"])
    _result("Span", f"{stats['span_seconds']:.0f}", "seconds")

    # Motion
    t0 = time.perf_counter()
    motion = stream.get_motion(seconds=5.0)
    motion_time = (time.perf_counter() - t0) * 1000
    _result("Motion detection time", f"{motion_time:.1f}", "ms")
    _result("Motion type", motion.motion_type)

    # Context
    t0 = time.perf_counter()
    ctx = stream.get_context(seconds=30.0)
    ctx_time = (time.perf_counter() - t0) * 1000
    _result("Context generation time", f"{ctx_time:.1f}", "ms", "<5ms")
    _result("Context token estimate", ctx.token_estimate)

    # Timeline
    t0 = time.perf_counter()
    tl = stream.get_timeline(seconds=60.0)
    tl_time = (time.perf_counter() - t0) * 1000
    _result("Timeline generation time", f"{tl_time:.1f}", "ms")
    _result("Timeline entries", len(tl))

    # Search
    query = np.random.randn(768).astype(np.float32)
    t0 = time.perf_counter()
    results = stream.search(query, top_k=5)
    search_time = (time.perf_counter() - t0) * 1000
    _result("Search time (top-5)", f"{search_time:.1f}", "ms", "<100ms")
    _result("Search results", len(results))

    print()


def bench_e2e(args):
    """End-to-end benchmark: capture screen → feed → get context."""
    _banner("END-TO-END BENCHMARK")
    from ipa.engine import IPAEngine

    engine = IPAEngine(config={"device": args.device, "int8": not args.no_int8})

    # Try to capture real screen
    img = None
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            _result("Source", "live screen capture")
            _result("Screen size", f"{img.size[0]}x{img.size[1]}")
    except ImportError:
        _result("Source", "synthetic (mss not installed)")

    if img is None:
        img = Image.fromarray(np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8))

    # Feed frames
    n_frames = args.n_frames or 20
    print(f"\n  Feeding {n_frames} frames through full pipeline...")

    latencies = []
    for i in range(n_frames):
        meta = {"monitor_id": 1, "window_name": "Test App", "scene_hint": "testing"}
        if i == 5:
            meta["window_changed"] = True
        t0 = time.perf_counter()
        frame = engine.feed(img, metadata=meta)
        latencies.append((time.perf_counter() - t0) * 1000)
        _result(f"Frame {i} ({frame.frame_type})", f"{latencies[-1]:.0f}ms, {frame.size_bytes} bytes, {frame.n_changed} changed")

    lat = sorted(latencies)
    print()
    _result("Mean pipeline latency", f"{np.mean(lat):.1f}", "ms")
    _result("P95 pipeline latency", f"{lat[int(len(lat)*0.95)]:.1f}", "ms")

    # Get context
    t0 = time.perf_counter()
    ctx = engine.context(seconds=30)
    ctx_time = (time.perf_counter() - t0) * 1000
    _result("Context generation", f"{ctx_time:.1f}", "ms")
    _result("Scene state", ctx.scene_state)
    _result("Token estimate", ctx.token_estimate)
    print(f"\n  Context text output:\n{ctx.to_text()}")

    # Status
    status = engine.status()
    _result("Buffer frames", status["stream"]["frames"])
    _result("Buffer memory", f"{status['stream']['memory_kb']:.1f}", "KB")
    _result("Encoder loaded", status["encoder"]["loaded"])

    print()


def bench_motion(args):
    """Motion detection benchmark."""
    _banner("MOTION DETECTION BENCHMARK")
    from ipa.compressor import DeltaCompressor, _pack_bitmask
    from ipa.stream import VisualStream
    from ipa.types import PatchFrame

    comp = DeltaCompressor(dim=768, bits=3)
    stream = VisualStream(max_frames=200, compressor=comp)

    cls_data = comp.compress_vectors(np.random.randn(1, 768).astype(np.float32))
    iframe_data = comp.compress_vectors(np.random.randn(196, 768).astype(np.float32))

    def push_pattern(name, changed_patches_list):
        stream2 = VisualStream(max_frames=200, compressor=comp)
        for i, changed_set in enumerate(changed_patches_list):
            mask = np.zeros(196, dtype=bool)
            mask[changed_set] = True
            delta = comp.compress_vectors(np.random.randn(len(changed_set), 768).astype(np.float32) * 0.1)
            frame = PatchFrame(
                timestamp=time.time() - (len(changed_patches_list) - i) * 0.33,
                frame_type="P",
                patch_grid=delta,
                change_mask=_pack_bitmask(mask),
                motion_vectors=b"",
                cls_embedding=cls_data,
                n_changed=len(changed_set),
                metadata={"monitor_id": 1, "window_name": "Test"},
            )
            stream2.push(frame)
        motion = stream2.get_motion(seconds=10)
        _result(f"Pattern: {name}", motion.motion_type, f"({motion.n_active_patches} patches)")

    # Static (no changes)
    push_pattern("static", [np.array([], dtype=int)] * 10)

    # Cursor (small area)
    push_pattern("cursor", [np.array([50+i, 51+i]) for i in range(10)])

    # Typing (small sparse area)
    push_pattern("typing", [np.random.choice(range(56, 70), 4, replace=False) for _ in range(10)])

    # Scrolling (large vertical band)
    push_pattern("scroll_down", [np.arange(28*i//10, 28*i//10+56) % 196 for i in range(10)])

    # Video (massive area)
    push_pattern("video", [np.random.choice(196, 140, replace=False) for _ in range(10)])

    print()


def main():
    parser = argparse.ArgumentParser(description="IPA v3 Benchmark Suite")
    parser.add_argument("--encoder", action="store_true", help="Run encoder benchmark")
    parser.add_argument("--compressor", action="store_true", help="Run compressor benchmark")
    parser.add_argument("--stream", action="store_true", help="Run stream benchmark")
    parser.add_argument("--e2e", action="store_true", help="Run end-to-end benchmark")
    parser.add_argument("--motion", action="store_true", help="Run motion detection benchmark")
    parser.add_argument("--device", default="cpu", help="Device for encoder (cpu/cuda/auto)")
    parser.add_argument("--no-int8", action="store_true", help="Disable int8 quantization")
    parser.add_argument("--n-frames", type=int, default=None, help="Number of frames for benchmark")
    args = parser.parse_args()

    run_all = not any([args.encoder, args.compressor, args.stream, args.e2e, args.motion])

    print(f"\n  IPA v3 — Real Eyes Benchmark Suite")
    print(f"  Device: {args.device} | Int8: {not args.no_int8}")

    if run_all or args.compressor:
        bench_compressor(args)
    if run_all or args.stream:
        bench_stream(args)
    if run_all or args.motion:
        bench_motion(args)
    if run_all or args.encoder:
        bench_encoder(args)
    if run_all or args.e2e:
        bench_e2e(args)

    _banner("DONE")


if __name__ == "__main__":
    main()
