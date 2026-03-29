"""
ILUMINATY - Main Entry Point
==============================
    iluminaty start                     → arranca con defaults
    iluminaty start --fps 2 --port 8100 → custom config
    iluminaty start --api-key mi_key    → con autenticación
    
Todo en RAM. Cero disco. Cuando el proceso muere, todo desaparece.
"""

import argparse
import sys
import signal
import uvicorn

from iluminaty.ring_buffer import RingBuffer
from iluminaty.capture import ScreenCapture, CaptureConfig
from iluminaty.server import app, init_server


BANNER = """
  =============================================
   ILUMINATY v0.1.0
   Real-time visual perception for AI
   Zero-disk - RAM-only - Universal API
  =============================================
"""


def main():
    parser = argparse.ArgumentParser(
        description="ILUMINATY - Real-time visual perception for AI"
    )
    parser.add_argument(
        "command", nargs="?", default="start", choices=["start", "version"],
        help="Command to run"
    )
    parser.add_argument("--port", type=int, default=8420, help="API port (default: 8420)")
    parser.add_argument("--host", default="127.0.0.1", help="API host (default: 127.0.0.1)")
    parser.add_argument("--fps", type=float, default=1.0, help="Target FPS (default: 1.0)")
    parser.add_argument("--buffer-seconds", type=int, default=30, help="Ring buffer duration in seconds (default: 30)")
    parser.add_argument("--quality", type=int, default=80, help="Image quality 10-95 (default: 80)")
    parser.add_argument("--format", type=str, default="webp", choices=["jpeg", "webp", "png"], help="Image format (default: webp)")
    parser.add_argument("--max-width", type=int, default=1280, help="Max frame width (default: 1280)")
    parser.add_argument("--monitor", type=int, default=1, help="Monitor number: 0=all, 1=primary (default: 1)")
    parser.add_argument("--api-key", type=str, default=None, help="API key for auth (optional)")
    parser.add_argument("--no-adaptive", action="store_true", help="Disable adaptive FPS")
    parser.add_argument("--no-smart-quality", action="store_true", help="Disable smart quality adjustment")
    
    args = parser.parse_args()
    
    if args.command == "version":
        print("iluminaty v0.1.0")
        return
    
    print(BANNER)
    
    # ─── Crear ring buffer (RAM pura) ───
    buffer = RingBuffer(
        max_seconds=args.buffer_seconds,
        target_fps=args.fps,
    )
    
    # ─── Crear capturador ───
    config = CaptureConfig(
        fps=args.fps,
        quality=args.quality,
        image_format=args.format,
        max_width=args.max_width,
        monitor=args.monitor,
        adaptive_fps=not args.no_adaptive,
        smart_quality=not args.no_smart_quality,
    )
    capture = ScreenCapture(buffer=buffer, config=config)
    
    # ─── Inyectar al server ───
    init_server(buffer=buffer, capture=capture, api_key=args.api_key)
    
    # ─── Info de arranque ───
    print(f"  API:       http://{args.host}:{args.port}")
    print(f"  FPS:       {args.fps} (adaptive: {not args.no_adaptive})")
    print(f"  Format:    {args.format} q{args.quality} (smart: {not args.no_smart_quality})")
    print(f"  Buffer:    {args.buffer_seconds}s ({buffer.max_slots} slots)")
    print(f"  Max width: {args.max_width}px")
    print(f"  Monitor:   {'all' if args.monitor == 0 else f'#{args.monitor}'}")
    print(f"  Auth:      {'enabled' if args.api_key else 'disabled'}")
    print(f"  Disk:      ZERO (RAM-only ring buffer)")
    print()
    print(f"  Endpoints:")
    print(f"    GET  /frame/latest        - last frame (JPEG)")
    print(f"    GET  /frame/latest?base64 - last frame (base64 JSON)")
    print(f"    GET  /frames?last=5       - last N frames")
    print(f"    GET  /frames?seconds=10   - recent frames")
    print(f"    GET  /buffer/stats        - stats")
    print(f"    WS   /ws/stream           - live stream")
    print(f"    POST /config              - change config live")
    print(f"    POST /buffer/flush        - destroy buffer")
    print()
    
    # ─── Arrancar captura ───
    capture.start()
    print("  Capture started. ILUMINATY is watching.\n")
    
    # ─── Cleanup en SIGINT ───
    def cleanup(sig, frame):
        print("\n  Shutting down... flushing buffer (RAM cleared)")
        capture.stop()
        buffer.flush()
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
