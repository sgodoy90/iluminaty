"""
ILUMINATY - Multi-Monitor Capture Orchestrator
================================================
IPA v2: Auto-detects all connected monitors and captures each independently.

- Active monitor (where cursor/window is): full FPS (3-5 Hz)
- Inactive monitors: reduced FPS (0.3-0.5 Hz) — passive scan
- All frames go to shared RingBuffer, tagged by monitor_id
- Activity detector thread polls active window every 1s
- Single monitor: zero overhead (wraps 1 ScreenCapture)
"""

import time
import logging
import threading
import os
from typing import Optional, Callable

import mss

from .ring_buffer import RingBuffer
from .capture import ScreenCapture, CaptureConfig
from .monitors import MonitorManager

logger = logging.getLogger(__name__)

# Soft dependency for active window detection
try:
    from .vision import get_active_window_info
    _HAS_VISION = True
except ImportError:
    _HAS_VISION = False


class MultiMonitorCapture:
    """
    Wraps N ScreenCapture instances — one per physical monitor.

    Duck-types the same interface as ScreenCapture for backward compatibility:
    is_running, current_fps, config, start(), stop(), on_frame()
    """

    def __init__(
        self,
        buffer: RingBuffer,
        monitor_mgr: MonitorManager,
        base_config: CaptureConfig,
    ):
        self.buffer = buffer
        self.monitor_mgr = monitor_mgr
        self.config = base_config  # base config (used as template)
        self._captures: dict[int, ScreenCapture] = {}
        self._running = False
        self._activity_thread: Optional[threading.Thread] = None
        self._on_frame: Optional[Callable] = None
        self._active_monitor_id: int = 1
        try:
            poll_s = float(os.environ.get("ILUMINATY_ACTIVITY_POLL_S", "0.25"))
        except Exception:
            poll_s = 0.25
        self._activity_poll_s = max(0.05, min(2.0, poll_s))

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_fps(self) -> float:
        """FPS of the active monitor's capture."""
        active_cap = self._captures.get(self._active_monitor_id)
        if active_cap:
            return active_cap.current_fps
        return self.config.fps

    @property
    def captures(self) -> dict[int, ScreenCapture]:
        return self._captures

    def start(self):
        """Detect monitors, create per-monitor captures, start all."""
        if self._running:
            return

        self.monitor_mgr.refresh()
        monitors = self.monitor_mgr.monitors

        if not monitors:
            # Fallback: single capture on monitor 1
            monitors = [type('M', (), {'id': 1})()]
        else:
            try:
                active = self.monitor_mgr.get_active_monitor()
                if active:
                    self._active_monitor_id = int(active.id)
                else:
                    self._active_monitor_id = int(monitors[0].id)
            except Exception:
                self._active_monitor_id = int(monitors[0].id)

        for mon in monitors:
            config = CaptureConfig(
                fps=self.config.fps,
                quality=self.config.quality,
                image_format=self.config.image_format,
                max_width=self.config.max_width,
                monitor=mon.id,
                skip_unchanged=self.config.skip_unchanged,
                adaptive_fps=self.config.adaptive_fps,
                min_fps=self.config.min_fps,
                max_fps=self.config.max_fps,
                smart_quality=self.config.smart_quality,
                smart_quality_sample_every=self.config.smart_quality_sample_every,
                webp_method=self.config.webp_method,
            )
            capture = ScreenCapture(buffer=self.buffer, config=config)
            if self._on_frame:
                capture.on_frame(self._on_frame)
            self._captures[mon.id] = capture

        # Start all captures
        self._running = True
        for capture in self._captures.values():
            capture.start()

        # Start activity detection thread
        self._activity_thread = threading.Thread(
            target=self._activity_loop, daemon=True, name="monitor-activity"
        )
        self._activity_thread.start()

        # Set initial active monitor FPS
        self._update_fps_for_active(self._active_monitor_id)

        print(f"  [IPA] Multi-monitor capture: {len(self._captures)} monitors")
        for mid, cap in self._captures.items():
            print(f"    Monitor {mid}: {cap.config.fps} fps")

    def stop(self):
        """Stop all captures."""
        self._running = False
        for capture in self._captures.values():
            capture.stop()
        if self._activity_thread:
            self._activity_thread.join(timeout=3)
        self._captures.clear()

    def on_frame(self, callback: Callable):
        """Register callback on all captures."""
        self._on_frame = callback
        for capture in self._captures.values():
            capture.on_frame(callback)

    def _activity_loop(self):
        """Poll active window to detect which monitor is active."""
        while self._running:
            try:
                if _HAS_VISION:
                    win_info = get_active_window_info()
                    bounds = win_info.get("bounds", {})
                    if bounds:
                        new_active = self.monitor_mgr.detect_active_from_window(bounds)
                        if new_active != self._active_monitor_id:
                            self._active_monitor_id = new_active
                            self._update_fps_for_active(new_active)
            except Exception as e:
                logger.debug("Multi-monitor activity probe failed: %s", e)
            time.sleep(self._activity_poll_s)

    def _update_fps_for_active(self, active_id: int):
        """Active monitor gets max FPS, inactive get min FPS."""
        for mid, capture in self._captures.items():
            if mid == active_id:
                capture._current_fps = self.config.max_fps
            else:
                capture._current_fps = self.config.min_fps

    def get_capture(self, monitor_id: int) -> Optional[ScreenCapture]:
        return self._captures.get(monitor_id)

    @property
    def active_monitor_id(self) -> int:
        return self._active_monitor_id
