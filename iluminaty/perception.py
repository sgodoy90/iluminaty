"""
ILUMINATY - Perception Engine v2 (Robotic Vision Pipeline)
==========================================================
The "eyes + visual cortex" of ILUMINATY.

Inspired by how Tesla Optimus and Boston Dynamics robots process vision:
  - FAST layer (every frame): frame diff gate, perceptual hash → skip if nothing changed
  - MEDIUM layer (on change): optical flow → WHERE did things move?
  - SLOW layer (on scene change): OCR diff → WHAT text appeared/disappeared?

The AI never processes images. It reads a stream of semantic events:
    "Switched to Chrome: github.com/iluminaty"
    "Page content loading (motion in center region)"
    "Content stabilized — page loaded"
    "Video playing in bottom-right quadrant"

Token cost: ~200 tokens per call (text only)
CPU only: no GPU required
Latency: <20ms per frame analysis
"""

import io
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Vision imports (soft dependencies)
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PIL import Image
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

try:
    from .vision import OCREngine, get_active_window_info
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


@dataclass
class PerceptionEvent:
    """A single thing that happened on screen."""
    timestamp: float
    event_type: str      # window_change, title_change, scene_change, scrolling, video, loading, idle, content_ready, text_appeared, page_navigation
    description: str     # Human-readable
    monitor: int = 0
    importance: float = 0.5  # 0.0 = trivial, 1.0 = critical
    details: dict = field(default_factory=dict)


def _bytes_to_gray(frame_bytes: bytes, target_width: int = 480) -> Optional[np.ndarray]:
    """Convert frame bytes (JPEG/WebP) to grayscale numpy array at reduced resolution."""
    if not HAS_CV2:
        return None
    try:
        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        # Resize to target width for fast processing
        h, w = img.shape
        if w > target_width:
            scale = target_width / w
            img = cv2.resize(img, (target_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return img
    except Exception:
        return None


class PerceptionEngine:
    """
    Continuous vision processing — the AI's visual cortex.

    Pipeline per frame (inspired by robotics):
      Gate 1: change_score check → skip if <0.01 (no change)          [<0.1ms]
      Gate 2: perceptual hash → skip if hamming distance < 5           [<1ms]
      Gate 3: optical flow → classify WHAT kind of change              [5-15ms]
      Gate 4: OCR diff → detect text changes (only on scene changes)   [50-200ms, throttled]
    """

    def __init__(self, buffer=None, max_events: int = 200):
        self._buffer = buffer
        self._events: deque[PerceptionEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Frame state
        self._last_gray: Optional[np.ndarray] = None   # Previous frame (grayscale, resized)
        self._last_phash = None                          # Previous perceptual hash
        self._last_hash: Optional[str] = None

        # Window state
        self._last_window: str = ""
        self._last_title: str = ""

        # Temporal state
        self._last_change_time: float = time.time()
        self._last_ocr_time: float = 0.0
        self._last_ocr_text: str = ""
        self._idle_reported: bool = False
        self._loading_detected: bool = False
        self._scene_stable_since: float = time.time()
        self._consecutive_high: int = 0     # Frames with high change in a row
        self._motion_reported: bool = False
        self._video_reported: bool = False

        # Motion tracking
        self._motion_zones: dict = {}  # zone_name → consecutive motion count
        self._change_history: deque = deque(maxlen=20)

        # OCR engine
        self._ocr = None
        if HAS_VISION:
            try:
                self._ocr = OCREngine()
            except Exception:
                pass

    def start(self, buffer):
        """Start the perception loop."""
        self._buffer = buffer
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perception")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _add_event(self, event_type: str, description: str,
                   importance: float = 0.5, monitor: int = 0, **details):
        evt = PerceptionEvent(
            timestamp=time.time(),
            event_type=event_type,
            description=description,
            importance=importance,
            monitor=monitor,
            details=details,
        )
        with self._lock:
            self._events.append(evt)

    def _loop(self):
        """Main perception loop — 2-3 Hz analysis."""
        last_ts = 0.0
        while self._running:
            try:
                if not self._buffer:
                    time.sleep(1)
                    continue

                slot = self._buffer.get_latest()
                if not slot or slot.timestamp <= last_ts:
                    time.sleep(0.2)
                    continue

                last_ts = slot.timestamp
                self._analyze_frame(slot)

            except Exception:
                pass
            time.sleep(0.4)

    # ─── GATE 1: Window Change (free, most reliable signal) ───

    def _check_window(self):
        """Detect active window changes — zero-cost, highest value signal."""
        if not HAS_VISION:
            return
        try:
            win = get_active_window_info()
            name = win.get("name", "")
            title = win.get("title", "")

            if name != self._last_window:
                self._add_event(
                    "window_change",
                    f"Switched to {name}: {title[:60]}",
                    importance=0.8,
                    old_window=self._last_window,
                    new_window=name,
                )
                self._last_window = name
                self._last_title = title
                self._scene_stable_since = time.time()
                self._loading_detected = True
                self._video_reported = False
                self._motion_reported = False
            elif title != self._last_title and title:
                self._add_event(
                    "title_change",
                    f"{name} → {title[:80]}",
                    importance=0.5,
                )
                self._last_title = title
                self._loading_detected = True
        except Exception:
            pass

    # ─── GATE 2: Perceptual Hash (fast semantic gate) ───

    def _check_phash(self, frame_bytes: bytes) -> int:
        """
        Compute perceptual hash and return hamming distance from last frame.
        Distance 0-4: nearly identical (skip)
        Distance 5-15: minor change (typing, cursor)
        Distance 16+: significant change (new content, scene switch)
        """
        if not HAS_IMAGEHASH:
            return 99  # No imagehash → assume changed
        try:
            img = Image.open(io.BytesIO(frame_bytes))
            current_hash = imagehash.phash(img, hash_size=8)
            if self._last_phash is None:
                self._last_phash = current_hash
                return 99
            distance = current_hash - self._last_phash
            self._last_phash = current_hash
            return distance
        except Exception:
            return 99

    # ─── GATE 3: Optical Flow (where is motion happening?) ───

    def _analyze_motion(self, current_gray: np.ndarray) -> dict:
        """
        Use Farneback optical flow to detect WHERE motion is happening.
        Divides screen into 3x3 grid zones and reports which zones have motion.

        Returns: {
            "total_motion": float (0-1),
            "zones": {"top_left": 0.1, "center": 0.8, ...},
            "dominant_direction": "down" | "up" | "left" | "right" | "mixed",
            "motion_region": "full" | "partial" | "spot" | "none"
        }
        """
        if self._last_gray is None or not HAS_CV2:
            self._last_gray = current_gray
            return {"total_motion": 0, "zones": {}, "dominant_direction": "none", "motion_region": "none"}

        try:
            # Farneback dense optical flow at reduced resolution (~480p)
            flow = cv2.calcOpticalFlowFarneback(
                self._last_gray, current_gray,
                None,
                pyr_scale=0.5, levels=2, winsize=15,
                iterations=2, poly_n=5, poly_sigma=1.1,
                flags=0
            )
            self._last_gray = current_gray

            # Compute magnitude
            mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])

            # Normalize total motion (0-1 scale)
            total_motion = float(np.mean(mag))
            total_motion_norm = min(total_motion / 5.0, 1.0)

            # Divide into 3x3 zones
            h, w = mag.shape
            zone_h, zone_w = h // 3, w // 3
            zone_names = [
                "top_left", "top_center", "top_right",
                "mid_left", "center", "mid_right",
                "bot_left", "bot_center", "bot_right",
            ]
            zones = {}
            for i, name in enumerate(zone_names):
                ry, rx = divmod(i, 3)
                zone_mag = mag[ry*zone_h:(ry+1)*zone_h, rx*zone_w:(rx+1)*zone_w]
                zones[name] = float(np.mean(zone_mag))

            # Dominant direction
            mean_dx = float(np.mean(flow[..., 0]))
            mean_dy = float(np.mean(flow[..., 1]))
            if abs(mean_dy) > abs(mean_dx) and abs(mean_dy) > 0.5:
                direction = "down" if mean_dy > 0 else "up"
            elif abs(mean_dx) > 0.5:
                direction = "right" if mean_dx > 0 else "left"
            else:
                direction = "mixed" if total_motion_norm > 0.1 else "none"

            # Motion region classification
            active_zones = sum(1 for v in zones.values() if v > 1.0)
            if active_zones >= 7:
                region = "full"        # Video, page transition
            elif active_zones >= 3:
                region = "partial"     # Scrolling, large update
            elif active_zones >= 1:
                region = "spot"        # Button click, spinner, cursor
            else:
                region = "none"

            return {
                "total_motion": total_motion_norm,
                "zones": zones,
                "dominant_direction": direction,
                "motion_region": region,
                "active_zones": active_zones,
            }

        except Exception:
            self._last_gray = current_gray
            return {"total_motion": 0, "zones": {}, "dominant_direction": "none", "motion_region": "none"}

    # ─── MAIN ANALYSIS ───

    def _analyze_frame(self, slot):
        """
        Robotic perception pipeline:
        Gate 1 → Window check (free)
        Gate 2 → change_score + pHash (skip if no change)
        Gate 3 → Optical flow (classify what changed)
        Gate 4 → OCR diff (only on scene changes, throttled)
        """
        now = time.time()
        change = slot.change_score

        # ── Gate 1: Window change (always check, zero cost) ──
        self._check_window()

        # ── Gate 2: Skip if nothing changed ──
        self._change_history.append(change)

        if change < 0.01:
            # Nothing changed — check for idle/stabilization
            self._consecutive_high = 0
            if not self._idle_reported and (now - self._last_change_time) > 15:
                self._add_event("idle", f"Screen stable for {now - self._last_change_time:.0f}s", importance=0.1)
                self._idle_reported = True
            if self._loading_detected and (now - self._scene_stable_since) > 2.0:
                self._add_event("content_ready", "Content loaded and stable", importance=0.7)
                self._loading_detected = False
            # Update gray frame even when idle (for next comparison)
            gray = _bytes_to_gray(slot.frame_bytes)
            if gray is not None:
                self._last_gray = gray
            return

        self._last_change_time = now
        self._idle_reported = False

        # Perceptual hash distance
        phash_dist = self._check_phash(slot.frame_bytes)

        if change < 0.05 and phash_dist < 5:
            # Minimal change (cursor blink, minor UI) — skip deeper analysis
            gray = _bytes_to_gray(slot.frame_bytes)
            if gray is not None:
                self._last_gray = gray
            return

        # ── Gate 3: Optical Flow — WHERE is the motion? ──
        gray = _bytes_to_gray(slot.frame_bytes)
        if gray is not None:
            motion = self._analyze_motion(gray)
        else:
            motion = {"total_motion": change, "motion_region": "unknown", "dominant_direction": "none"}

        # ── Classify the event based on motion analysis ──
        region = motion.get("motion_region", "none")
        direction = motion.get("dominant_direction", "none")
        total = motion.get("total_motion", 0)

        if change > 0.4 or phash_dist > 20:
            # HIGH CHANGE
            self._consecutive_high += 1
            self._scene_stable_since = now

            if self._consecutive_high <= 2:
                # Scene switch / page navigation
                desc = f"Scene change in {self._last_window}"
                if region == "full":
                    desc += " (full screen update)"
                elif direction in ("down", "up"):
                    desc += f" (content moving {direction})"
                self._add_event("scene_change", desc, importance=0.6, change_score=change, phash_dist=phash_dist)
                self._motion_reported = False
                self._video_reported = False
                self._loading_detected = True
            elif self._consecutive_high >= 8 and not self._video_reported:
                # Sustained full-screen motion = video
                self._add_event(
                    "video_detected",
                    f"Video/animation playing in {self._last_window} ({region} region, {direction})",
                    importance=0.3,
                )
                self._video_reported = True

        elif change > 0.1:
            # MODERATE CHANGE
            self._consecutive_high = 0
            self._scene_stable_since = now

            if direction in ("down", "up") and not self._motion_reported:
                self._add_event(
                    "scrolling",
                    f"Scrolling {direction} in {self._last_window}",
                    importance=0.2,
                    direction=direction,
                )
                self._motion_reported = True
            elif region == "spot" and not self._motion_reported:
                # Small localized change — could be loading spinner, button animation
                # Find which zone has the motion
                zones = motion.get("zones", {})
                active = [k for k, v in zones.items() if v > 1.0]
                if active:
                    self._add_event(
                        "ui_activity",
                        f"UI activity in {', '.join(active[:2])} of {self._last_window}",
                        importance=0.2,
                        zones=active,
                    )
                    self._motion_reported = True

        else:
            # LOW CHANGE
            self._consecutive_high = 0
            self._motion_reported = False

        # ── Gate 4: OCR diff (expensive, only on scene changes, max every 3s) ──
        if (self._ocr and self._ocr.available
                and (change > 0.3 or phash_dist > 15)
                and (now - self._last_ocr_time) > 3.0):
            self._last_ocr_time = now
            try:
                ocr_result = self._ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)
                current_text = ocr_result.get("text", "")

                if current_text and current_text != self._last_ocr_text:
                    old_words = set(self._last_ocr_text.split())
                    new_words = set(current_text.split())
                    appeared = new_words - old_words
                    disappeared = old_words - new_words

                    if len(appeared) > 10:
                        sample = " ".join(list(appeared)[:12])
                        self._add_event(
                            "text_appeared",
                            f"New content: {sample}...",
                            importance=0.5,
                            new_word_count=len(appeared),
                        )

                    if len(disappeared) > 15 and len(appeared) > 15:
                        self._add_event(
                            "page_navigation",
                            f"Page content replaced in {self._last_window}",
                            importance=0.7,
                        )
                        self._loading_detected = True

                    self._last_ocr_text = current_text
            except Exception:
                pass

        self._last_hash = slot.phash

    # ─── Public API ───

    def get_events(self, last_seconds: float = 30, min_importance: float = 0.0) -> list[PerceptionEvent]:
        cutoff = time.time() - last_seconds
        with self._lock:
            return [
                e for e in self._events
                if e.timestamp >= cutoff and e.importance >= min_importance
            ]

    def get_summary(self, last_seconds: float = 30) -> str:
        """Text summary of recent events — perfect for AI consumption (~200 tokens)."""
        events = self.get_events(last_seconds, min_importance=0.2)

        if not events:
            elapsed = time.time() - self._last_change_time
            return f"Screen stable — no significant changes in {elapsed:.0f}s. Active: {self._last_window}"

        lines = [f"## Perception (last {last_seconds:.0f}s) — {len(events)} events"]
        for e in events[-15:]:
            age = time.time() - e.timestamp
            ago = f"{age:.0f}s ago" if age < 60 else f"{age/60:.0f}m ago"
            marker = "!" if e.importance >= 0.7 else "-"
            lines.append(f"  {marker} [{ago}] {e.description}")

        lines.append(f"\nCurrent: {self._last_window} — {self._last_title[:60]}")
        return "\n".join(lines)

    def get_event_count(self) -> int:
        return len(self._events)

    @property
    def is_running(self) -> bool:
        return self._running
