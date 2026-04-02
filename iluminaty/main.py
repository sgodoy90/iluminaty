"""
ILUMINATY - Main Entry Point
==============================
    iluminaty start                     → arranca con defaults
    iluminaty start --fps 2 --port 8100 → custom config
    iluminaty start --api-key mi_key    → con autenticación
    
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
        "command", nargs="?", default="start", choices=["start", "version"],
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
    parser.add_argument("--api-key", type=str, default=None, help="API key for auth (optional)")
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
    parser.add_argument("--deep-loop-hz", type=float, default=1.0, help="Deep visual loop frequency (0.5-2.0)")
    parser.add_argument("--fast-loop-hz", type=float, default=10.0, help="Fast semantic loop frequency (8-12 typical)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    if args.command == "version":
        from iluminaty import __version__; print(f"iluminaty v{__version__}")
        return
    
    print(BANNER)
    
    # ─── Crear ring buffer (RAM pura) ───
    buffer = RingBuffer(
        max_seconds=args.buffer_seconds,
        target_fps=args.fps,
    )
    
    # ─── Crear capturador (IPA v2: auto multi-monitor) ───
    config = CaptureConfig(
        fps=args.fps,
        quality=args.quality,
        image_format=args.format,
        max_width=args.max_width,
        monitor=args.monitor,
        adaptive_fps=not args.no_adaptive,
        smart_quality=not args.no_smart_quality,
        smart_quality_sample_every=max(1, int(args.smart_quality_sample_every)),
        webp_method=max(0, min(6, int(args.webp_method))),
    )

    # Multi-monitor orchestration is explicit:
    #   --monitor 0  => auto all monitors
    #   --monitor N  => pinned single monitor N
    from iluminaty.monitors import MonitorManager
    _mon_mgr = MonitorManager()
    _mon_mgr.refresh()
    if _mon_mgr.count > 1 and args.monitor == 0:
        # Multi-monitor: scale buffer for N monitors
        buffer.max_slots = int(args.buffer_seconds * (args.fps * 2 + (_mon_mgr.count - 1) * 0.5))
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
        audio_buffer=audio_buffer,
        audio_capture=audio_capture,
        enable_actions=args.actions,
        autonomy_level=args.autonomy,
        browser_debug_port=args.browser_debug_port,
        file_sandbox_paths=args.file_sandbox,
        visual_profile=args.vision_profile,
        vision_plus_disk=args.vision_plus_disk,
        deep_loop_hz=args.deep_loop_hz,
        fast_loop_hz=args.fast_loop_hz,
    )
    
    # ─── Info de arranque ───
    print(f"  API:       http://{args.host}:{args.port}")
    print(f"  FPS:       {args.fps} (adaptive: {not args.no_adaptive})")
    print(f"  Format:    {args.format} q{args.quality} (smart: {not args.no_smart_quality})")
    print(f"  Buffer:    {args.buffer_seconds}s ({buffer.max_slots} slots)")
    print(f"  Max width: {args.max_width}px")
    from iluminaty.multi_capture import MultiMonitorCapture
    if isinstance(capture, MultiMonitorCapture):
        print(f"  Monitor:   AUTO ({_mon_mgr.count} monitors, per-monitor capture)")
    else:
        print(f"  Monitor:   {'all' if args.monitor == 0 else f'#{args.monitor}'}")
    print(f"  Audio:     {args.audio}" + (f" ({args.audio_buffer}s buffer)" if args.audio != "off" else ""))
    print(f"  Auth:      {'enabled' if args.api_key else 'disabled'}")
    print(f"  Disk:      ZERO (RAM-only ring buffer)")
    print(f"  Actions:   {'ENABLED' if args.actions else 'disabled'} (autonomy: {args.autonomy})")
    print(f"  Browser:   debug port {args.browser_debug_port}")
    # Warn if fast-loop-hz exceeds the hard cap (max(0.08s interval) = 12.5 Hz)
    _actual_fast_hz = 1.0 / max(0.08, min(0.25, 1.0 / max(1.0, args.fast_loop_hz)))
    _hz_note = f" (capped to {_actual_fast_hz:.1f}Hz — use <=12 to avoid silent cap)" if args.fast_loop_hz > 12.5 else ""
    print(f"  Perception: fast_loop={args.fast_loop_hz:.1f}Hz{_hz_note} | deep_loop={args.deep_loop_hz:.1f}Hz")
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
