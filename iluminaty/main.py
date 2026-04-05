"""
ILUMINATY - Main Entry Point
==============================
    iluminaty start                          → balanced profile (recommended)
    iluminaty start --profile low_power      → for CPUs without GPU / 4+ monitors
    iluminaty start --profile performance    → maximum responsiveness (GPU recommended)
    iluminaty start --fps 2 --port 8100      → custom config
    iluminaty start --api-key mi_key         → with auth

Resource profiles:
    low_power   : 1fps active, 0.1fps inactive, fast_loop 3Hz, OCR 60s
                  ~5% CPU idle, ~80MB RAM. For laptops/no-GPU/5 monitors.
    balanced    : 2fps active, 0.3fps inactive, fast_loop 5Hz, OCR 30s  [DEFAULT]
                  ~15% CPU idle, ~120MB RAM. For most desktops.
    performance : 5fps active, 0.5fps inactive, fast_loop 10Hz, OCR 10s
                  ~30% CPU idle, ~200MB RAM. GPU recommended.

Todo en RAM. Cero disco. Cuando el proceso muere, todo desaparece.
"""

import argparse
import os
import sys
import signal
from collections import deque
import uvicorn


def _apply_runtime_limits() -> None:
    """
    Apply conservative CPU defaults before heavy deps load.
    Can be overridden explicitly via environment variables.
    """
    if os.environ.get("ILUMINATY_CPU_FRIENDLY", "1") != "1":
        return

    thread_budget = os.environ.get("ILUMINATY_CPU_THREADS", "").strip() or "2"
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "ORT_NUM_THREADS",
    ):
        os.environ.setdefault(key, thread_budget)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    os.environ.setdefault("KMP_BLOCKTIME", "0")


_apply_runtime_limits()

from iluminaty.ring_buffer import RingBuffer
from iluminaty.capture import ScreenCapture, CaptureConfig
from iluminaty.server import app, init_server


BANNER = """
  =============================================
   ILUMINATY v1.0.0
   Real-time visual perception + action for AI
   7 Layers - 42+ Actions - Zero-disk
  =============================================
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ILUMINATY - Real-time visual perception for AI"
    )
    parser.add_argument(
        "command", nargs="?", default="start",
        choices=["start", "version", "mcp-config"],
        help="Command to run"
    )
    parser.add_argument("--port", type=int, default=8420, help="API port (default: 8420)")
    parser.add_argument("--host", default="127.0.0.1", help="API host (default: 127.0.0.1)")
    parser.add_argument("--fps", type=float, default=5.0, help="Target FPS (default: 5.0)")
    parser.add_argument("--buffer-seconds", type=int, default=30, help="Ring buffer duration in seconds (default: 30)")
    parser.add_argument("--quality", type=int, default=80, help="Image quality 10-95 (default: 80)")
    parser.add_argument("--format", type=str, default="webp", choices=["jpeg", "webp", "png"], help="Image format (default: webp)")
    parser.add_argument("--max-width", type=int, default=1280, help="Max frame width (default: 1280)")
    parser.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="Monitor mode: 0=auto multi-monitor, N=single monitor N (default: 0)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("ILUMINATY_KEY"),  # env var fallback
        help="API key for auth. Falls back to ILUMINATY_KEY env var (optional)",
    )
    parser.add_argument("--no-adaptive", action="store_true", help="Disable adaptive FPS")
    parser.add_argument("--no-smart-quality", action="store_true", help="Disable smart quality adjustment")
    parser.add_argument(
        "--smart-quality-sample-every",
        type=int,
        default=4,
        help="Analyze contrast every N frames for smart-quality (default: 4)",
    )
    parser.add_argument(
        "--webp-method",
        type=int,
        default=4,
        help="WebP encode method 0-6 (lower=faster, default: 4)",
    )
    parser.add_argument("--audio", type=str, default="off", choices=["off", "mic", "system", "all"],
                        help="Audio capture mode (default: off)")
    parser.add_argument("--audio-buffer", type=int, default=60, help="Audio buffer seconds (default: 60)")

    # v1.0: Computer Use args
    parser.add_argument("--actions", action="store_true", help="Enable action bridge (mouse/keyboard control)")
    parser.add_argument("--autonomy", type=str, default="suggest", choices=["suggest", "confirm", "auto"],
                        help="Autonomy level (default: suggest)")
    parser.add_argument("--browser-debug-port", type=int, default=9222,
                        help="Chrome DevTools debug port (default: 9222)")
    parser.add_argument("--file-sandbox", type=str, nargs="*", default=None,
                        help="Allowed file system paths for sandbox (default: current dir)")
    parser.add_argument(
        "--vision-profile",
        type=str,
        default="core_ram",
        choices=["core_ram", "vision_plus"],
        help="Temporal vision profile (default: core_ram)",
    )
    parser.add_argument(
        "--vision-plus-disk",
        action="store_true",
        help="Enable encrypted rotating disk spool (vision_plus profile only)",
    )
    parser.add_argument("--deep-loop-hz", type=float, default=None, help="Deep visual loop frequency (0.5-2.0). Overrides --profile.")
    parser.add_argument("--fast-loop-hz", type=float, default=None, help="Fast semantic loop frequency (8-12 typical). Overrides --profile.")
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        choices=["low_power", "balanced", "performance"],
        help=(
            "Resource profile. low_power=1fps/3Hz (no-GPU/5 monitors), "
            "balanced=2fps/5Hz (default), performance=5fps/10Hz (GPU recommended). "
            "Individual flags (--fps, --fast-loop-hz, etc.) override the profile."
        ),
    )
    parser.add_argument(
        "--max-cpu-pct",
        type=float,
        default=float(os.environ.get("ILUMINATY_MAX_CPU_PCT", "80")),
        help="Auto-throttle capture/OCR when CPU exceeds this %% (default: 80)",
    )

    return parser


def _write_mcp_config(port: int = 8420):
    """Write MCP config for Claude Code and Claude Desktop."""
    import json
    import sys
    import shutil

    python = sys.executable
    mcp_module = "-m iluminaty.mcp_server"

    config = {
        "mcpServers": {
            "iluminaty": {
                "command": python,
                "args": ["-m", "iluminaty.mcp_server"],
                "env": {
                    "ILUMINATY_API_URL": f"http://127.0.0.1:{port}",
                },
            }
        }
    }

    written = []

    # 1. Project-local .mcp.json (Claude Code)
    local_path = os.path.join(os.getcwd(), ".mcp.json")
    try:
        existing = {}
        if os.path.exists(local_path):
            existing = json.loads(open(local_path).read())
        existing.setdefault("mcpServers", {})
        existing["mcpServers"]["iluminaty"] = config["mcpServers"]["iluminaty"]
        with open(local_path, "w") as f:
            json.dump(existing, f, indent=2)
        written.append(local_path)
    except Exception as e:
        print(f"  ⚠ Could not write {local_path}: {e}")

    # 2. Claude Desktop config
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        desktop_path = os.path.join(appdata, "Claude", "claude_desktop_config.json")
        try:
            existing = {}
            if os.path.exists(desktop_path):
                existing = json.loads(open(desktop_path).read())
            existing.setdefault("mcpServers", {})
            existing["mcpServers"]["iluminaty"] = config["mcpServers"]["iluminaty"]
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, "w") as f:
                json.dump(existing, f, indent=2)
            written.append(desktop_path)
        except Exception as e:
            print(f"  ⚠ Could not write {desktop_path}: {e}")

    # 3. User-level ~/.mcp.json (Claude Code global)
    home_mcp = os.path.join(os.path.expanduser("~"), ".mcp.json")
    try:
        existing = {}
        if os.path.exists(home_mcp):
            existing = json.loads(open(home_mcp).read())
        existing.setdefault("mcpServers", {})
        existing["mcpServers"]["iluminaty"] = config["mcpServers"]["iluminaty"]
        with open(home_mcp, "w") as f:
            json.dump(existing, f, indent=2)
        written.append(home_mcp)
    except Exception as e:
        print(f"  ⚠ Could not write {home_mcp}: {e}")

    print("\n✓ ILUMINATY MCP config written:")
    for p in written:
        print(f"  {p}")
    print("\nRestart Claude Code or Claude Desktop to load the server.")
    print(f"Make sure `iluminaty start` is running on port {port}.\n")


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    if args.command == "version":
        from iluminaty import __version__; print(f"iluminaty v{__version__}")
        return

    if args.command == "mcp-config":
        _write_mcp_config(args.port)
        return
    
    print(BANNER)

    # ─── Apply resource profile ───────────────────────────────────────────────
    # Profile sets defaults; individual flags always override.
    _PROFILES = {
        "low_power": {
            "fps": 1.0, "min_fps": 0.1, "max_fps": 1.0,
            "fast_loop_hz": 3.0, "deep_loop_hz": 0.5,
            "ocr_active_s": 60.0, "ocr_inactive_s": 180.0,
            "quality": 70, "max_width": 960,
            "buffer_seconds": 20,
        },
        "balanced": {
            "fps": 2.0, "min_fps": 0.3, "max_fps": 2.0,
            "fast_loop_hz": 5.0, "deep_loop_hz": 1.0,
            "ocr_active_s": 30.0, "ocr_inactive_s": 90.0,
            "quality": 75, "max_width": 1280,
            "buffer_seconds": 30,
        },
        "performance": {
            "fps": 5.0, "min_fps": 0.5, "max_fps": 5.0,
            "fast_loop_hz": 10.0, "deep_loop_hz": 1.0,
            "ocr_active_s": 10.0, "ocr_inactive_s": 30.0,
            "quality": 80, "max_width": 1280,
            "buffer_seconds": 30,
        },
    }

    # Auto-detect profile if not specified:
    # Check GPU availability — no GPU → default to balanced, not performance
    _auto_profile = "balanced"
    try:
        import subprocess as _sp
        _gpu = _sp.run(
            ["powershell", "-Command",
             "(Get-WmiObject Win32_VideoController).AdapterCompatibility"],
            capture_output=True, text=True, timeout=3,
        )
        _has_gpu = bool(_gpu.stdout.strip())
    except Exception:
        _has_gpu = False

    _profile_name = args.profile or os.environ.get("ILUMINATY_PROFILE", "") or _auto_profile
    if _profile_name not in _PROFILES:
        _profile_name = _auto_profile
    _prof = _PROFILES[_profile_name]

    # Apply profile defaults — individual CLI args override
    _fps            = args.fps if "--fps" in sys.argv else _prof["fps"]
    _fast_loop_hz   = args.fast_loop_hz if args.fast_loop_hz is not None else _prof["fast_loop_hz"]
    _deep_loop_hz   = args.deep_loop_hz if args.deep_loop_hz is not None else _prof["deep_loop_hz"]
    _quality        = args.quality if "--quality" in sys.argv else _prof["quality"]
    _max_width      = args.max_width if "--max-width" in sys.argv else _prof["max_width"]
    _buffer_seconds = args.buffer_seconds if "--buffer-seconds" in sys.argv else _prof["buffer_seconds"]

    # Set OCR intervals via env (perception.py reads them at init)
    if "ILUMINATY_OCR_ACTIVE_INTERVAL_S" not in os.environ:
        os.environ["ILUMINATY_OCR_ACTIVE_INTERVAL_S"] = str(_prof["ocr_active_s"])
    if "ILUMINATY_OCR_INACTIVE_INTERVAL_S" not in os.environ:
        os.environ["ILUMINATY_OCR_INACTIVE_INTERVAL_S"] = str(_prof["ocr_inactive_s"])

    # Set max CPU throttle env
    os.environ["ILUMINATY_MAX_CPU_PCT"] = str(args.max_cpu_pct)

    # ─── Crear ring buffer (RAM pura) ───
    buffer = RingBuffer(
        max_seconds=_buffer_seconds,
        target_fps=_fps,
    )

    # ─── Crear capturador (IPA v2: auto multi-monitor) ───
    config = CaptureConfig(
        fps=_fps,
        quality=_quality,
        image_format=args.format,
        max_width=_max_width,
        monitor=args.monitor,
        adaptive_fps=not args.no_adaptive,
        smart_quality=not args.no_smart_quality,
        smart_quality_sample_every=max(1, int(args.smart_quality_sample_every)),
        webp_method=max(0, min(6, int(args.webp_method))),
        min_fps=_prof.get("min_fps", 0.2),
        max_fps=_prof.get("max_fps", _fps),
    )

    # Multi-monitor orchestration is explicit:
    #   --monitor 0  => auto all monitors
    #   --monitor N  => pinned single monitor N
    from iluminaty.monitors import MonitorManager
    _mon_mgr = MonitorManager()
    _mon_mgr.refresh()
    if _mon_mgr.count > 1 and args.monitor == 0:
        # Sublinear buffer scaling: active monitor gets full FPS budget,
        # each additional monitor adds only 0.3fps worth of slots (mostly idle)
        _extra_mon_fps = 0.3  # conservative — inactive monitors rarely change
        _total_effective_fps = _fps + (_mon_mgr.count - 1) * _extra_mon_fps
        buffer.max_slots = int(_buffer_seconds * _total_effective_fps)
        buffer._buffer = deque(buffer._buffer, maxlen=buffer.max_slots)
        from iluminaty.multi_capture import MultiMonitorCapture
        capture = MultiMonitorCapture(buffer=buffer, monitor_mgr=_mon_mgr, base_config=config)
    else:
        capture = ScreenCapture(buffer=buffer, config=config)
    
    # ─── Audio (opcional) ───
    audio_buffer = None
    audio_capture = None
    if args.audio != "off":
        from iluminaty.audio import AudioRingBuffer, AudioCapture
        audio_buffer = AudioRingBuffer(max_seconds=args.audio_buffer)
        audio_capture = AudioCapture(buffer=audio_buffer, mode=args.audio)
    
    # ─── Inyectar al server ───
    init_server(
        buffer=buffer,
        capture=capture,
        api_key=args.api_key,
        iluminaty_key=args.api_key,
        audio_buffer=audio_buffer,
        audio_capture=audio_capture,
        enable_actions=args.actions,
        autonomy_level=args.autonomy,
        browser_debug_port=args.browser_debug_port,
        file_sandbox_paths=args.file_sandbox,
        visual_profile=args.vision_profile,
        vision_plus_disk=args.vision_plus_disk,
        deep_loop_hz=_deep_loop_hz,
        fast_loop_hz=_fast_loop_hz,
    )

    # ─── Info de arranque ───
    _gpu_label = "GPU detected" if _has_gpu else "CPU only"
    print(f"  Profile:   {_profile_name} ({_gpu_label})")
    print(f"  API:       http://{args.host}:{args.port}")
    print(f"  FPS:       {_fps} active | {_prof.get('min_fps',0.2)} inactive (adaptive: {not args.no_adaptive})")
    print(f"  Format:    {args.format} q{_quality} max_w={_max_width} (smart: {not args.no_smart_quality})")
    print(f"  Buffer:    {_buffer_seconds}s ({buffer.max_slots} slots)")
    from iluminaty.multi_capture import MultiMonitorCapture
    if isinstance(capture, MultiMonitorCapture):
        print(f"  Monitors:  AUTO ({_mon_mgr.count} monitors, per-monitor capture)")
    else:
        print(f"  Monitor:   {'all' if args.monitor == 0 else f'#{args.monitor}'}")
    print(f"  Audio:     {args.audio}" + (f" ({args.audio_buffer}s buffer)" if args.audio != "off" else ""))
    print(f"  Auth:      {'enabled' if args.api_key else 'disabled'}")
    print(f"  Disk:      ZERO (RAM-only ring buffer)")
    print(f"  Actions:   {'ENABLED' if args.actions else 'disabled'} (autonomy: {args.autonomy})")
    _actual_fast_hz = 1.0 / max(0.08, min(0.25, 1.0 / max(1.0, _fast_loop_hz)))
    _hz_note = f" (capped to {_actual_fast_hz:.1f}Hz)" if _fast_loop_hz > 12.5 else ""
    print(f"  Perception: fast_loop={_fast_loop_hz:.1f}Hz{_hz_note} | deep_loop={_deep_loop_hz:.1f}Hz")
    ocr_active_s  = float(os.environ.get("ILUMINATY_OCR_ACTIVE_INTERVAL_S", _prof["ocr_active_s"]))
    ocr_inact_s   = float(os.environ.get("ILUMINATY_OCR_INACTIVE_INTERVAL_S", _prof["ocr_inactive_s"]))
    print(f"  OCR:       every {ocr_active_s:.0f}s (active) / {ocr_inact_s:.0f}s (inactive)")
    print(f"  CPU guard: throttle at {args.max_cpu_pct:.0f}%")
    print(f"  Vision profile: {args.vision_profile}" + (" + disk spool" if args.vision_plus_disk else ""))
    print()
    print(f"  Core Endpoints:")
    print(f"    GET  /frame/latest        - last frame")
    print(f"    GET  /vision/snapshot     - AI-ready enriched frame")
    print(f"    WS   /ws/stream           - live stream")
    print(f"    GET  /system/overview     - full system status")
    print(f"  Action Endpoints (v1.0):")
    print(f"    POST /agent/do            - intent-based action")
    print(f"    POST /action/click        - mouse click")
    print(f"    POST /action/type         - keyboard input")
    print(f"    GET  /ui/elements         - accessibility tree")
    print(f"    GET  /windows/list        - window manager")
    print(f"    POST /terminal/exec       - run commands")
    print(f"    GET  /git/status          - git operations")
    print(f"    POST /browser/navigate    - browser control")
    print(f"    GET  /files/read          - file system sandbox")
    print(f"    POST /safety/kill         - emergency kill switch")
    print()
    
    # ─── Arrancar captura ───
    capture.start()
    if audio_capture and args.audio != "off":
        audio_capture.start()
        print(f"  Audio capture started ({args.audio} mode).")
    print("  Capture started. ILUMINATY is watching.\n")

    # ─── Cleanup en SIGINT ───
    def cleanup(sig, frame):
        print("\n  Shutting down... flushing buffers (RAM cleared)")
        capture.stop()
        if audio_capture:
            audio_capture.stop()
        buffer.flush()
        if audio_buffer:
            audio_buffer.clear()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    # ─── Arrancar API server ───
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
