"""
ILUMINATY - Single Executable Entry Point
============================================
Este archivo es lo que PyInstaller empaqueta.
El usuario hace doble click en ILUMINATY.exe y:
1. Arranca el daemon de captura
2. Arranca el API server
3. Abre el dashboard en el browser
4. Muestra icono en system tray (Windows)

CERO instalacion. CERO dependencias. CERO terminal.
"""

import sys
import os
import time
import signal
import threading
import webbrowser

# Fix para PyInstaller
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))


def main():
    PORT = 8420
    HOST = "127.0.0.1"

    # Import ILUMINATY components
    from iluminaty.ring_buffer import RingBuffer
    from iluminaty.capture import ScreenCapture, CaptureConfig
    from iluminaty.server import app, init_server

    # ─── Create components ───
    buffer = RingBuffer(max_seconds=30, target_fps=1.0)
    config = CaptureConfig(
        fps=1.0,
        quality=80,
        image_format="webp",
        max_width=1280,
        monitor=1,
        adaptive_fps=True,
        smart_quality=True,
    )
    capture = ScreenCapture(buffer=buffer, config=config)

    # ─── Init server ───
    init_server(buffer=buffer, capture=capture)

    # ─── Start capture ───
    capture.start()

    # ─── Open browser after short delay ───
    def open_browser():
        time.sleep(2)
        webbrowser.open(f"http://{HOST}:{PORT}")

    threading.Thread(target=open_browser, daemon=True).start()

    # ─── Cleanup ───
    def cleanup(sig=None, frame=None):
        capture.stop()
        buffer.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # ─── Run server (blocks) ───
    import uvicorn
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
