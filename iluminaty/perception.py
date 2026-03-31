"""
ILUMINATY Perception Algorithm (IPA) — Proprietary Engine
==========================================================
The "eyes + visual cortex" of ILUMINATY.

4-Gate pipeline inspired by robotics (Tesla Optimus), video codecs (keyframes),
and the human visual cortex (selective attention + change blindness).

Gate 0: Window change detection (free, ctypes)              [<0.1ms]
Gate 1: Histogram-based continuous change_score (0.0-1.0)   [<0.5ms]
Gate 2: Perceptual hash (imagehash phash, hamming distance)  [<1ms]
Gate 3: Optical flow (Farneback 480p) + SmartDiff + AttentionMap [5-25ms]
Gate 4: OCR diff (RapidOCR, structural, throttled 3s)       [50-200ms]

Post-processing:
  SceneStateMachine → TemporalEventFuser → CapturePredictor

The AI never processes images. It reads a stream of semantic events (~200 tokens).

7 IPA Classes:
  1. SceneStateMachine — IDLE/TYPING/SCROLLING/LOADING/VIDEO/TRANSITION/INTERACTION
  2. AttentionMap — 8x6 spatial heatmap with temporal decay
  3. ROITracker — up to 6 regions of interest
  4. KeyframeDetector — scene boundary markers
  5. TemporalEventFuser — raw events → composite narratives
  6. CapturePredictor — autocorrelation + FPS advisory
  7. MonitorPerceptionState — independent state per monitor

Token cost: ~200 tokens per call (text only)
CPU only: no GPU required
New RAM: <20KB total
"""

import io
import logging
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
from .world_state import WorldStateEngine
from .temporal_store import TemporalVisualStore
from .visual_engine import VisualEngine, VisualTask

logger = logging.getLogger(__name__)

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


# ─── Data Types ───

@dataclass
class PerceptionEvent:
    """A single thing that happened on screen."""
    timestamp: float
    event_type: str      # window_change, title_change, scene_change, scrolling, video, loading, idle, content_ready, text_appeared, page_navigation, composite
    description: str
    monitor: int = 0
    importance: float = 0.5  # 0.0 = trivial, 1.0 = critical
    uncertainty: float = 0.5  # 0.0 = very certain, 1.0 = uncertain
    details: dict = field(default_factory=dict)


class SceneState(Enum):
    """Scene classification states with hysteresis."""
    IDLE = "idle"
    TYPING = "typing"
    SCROLLING = "scrolling"
    LOADING = "loading"
    VIDEO = "video"
    TRANSITION = "transition"
    INTERACTION = "interaction"


# ─── Helper ───

def _bytes_to_gray(frame_bytes: bytes, target_width: int = 480) -> Optional[np.ndarray]:
    """Convert frame bytes (JPEG/WebP) to grayscale numpy array at reduced resolution."""
    if not HAS_CV2:
        return None
    try:
        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        h, w = img.shape
        if w > target_width:
            scale = target_width / w
            img = cv2.resize(img, (target_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        return img
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 1: SceneStateMachine
# ═══════════════════════════════════════════════════════════════

class SceneStateMachine:
    """
    Evidence-accumulation state classifier with hysteresis.

    Instead of single-frame thresholds, accumulates evidence over a 5-frame
    sliding window. Min dwell times prevent flip-flopping between states.

    Input signals: change_score, motion_dict, phash_dist, window_changed
    Output: current SceneState + confidence
    """

    # Min dwell time (seconds) before allowing transition OUT of each state
    _DWELL_TIMES = {
        SceneState.IDLE: 0.5,
        SceneState.TYPING: 0.3,
        SceneState.SCROLLING: 0.5,
        SceneState.LOADING: 1.0,
        SceneState.VIDEO: 2.0,
        SceneState.TRANSITION: 0.3,
        SceneState.INTERACTION: 0.3,
    }

    def __init__(self):
        self.state = SceneState.IDLE
        self.state_since: float = time.time()
        self.confidence: float = 0.0
        self._evidence: deque = deque(maxlen=5)  # last 5 frames of signals

    def update(self, change_score: float, motion: dict,
               phash_dist: int, window_changed: bool) -> SceneState:
        """Feed signals from one frame, return updated state."""
        now = time.time()
        self._evidence.append({
            "change": change_score,
            "motion": motion.get("total_motion", 0),
            "direction": motion.get("dominant_direction", "none"),
            "region": motion.get("motion_region", "none"),
            "active_zones": motion.get("active_zones", 0),
            "phash": phash_dist,
            "window": window_changed,
        })

        if len(self._evidence) < 2:
            return self.state

        # Check dwell time
        dwell = self._DWELL_TIMES.get(self.state, 0.3)
        if (now - self.state_since) < dwell:
            return self.state

        # Compute average signals over evidence window
        changes = [e["change"] for e in self._evidence]
        motions = [e["motion"] for e in self._evidence]
        directions = [e["direction"] for e in self._evidence]
        regions = [e["region"] for e in self._evidence]
        avg_change = sum(changes) / len(changes)
        avg_motion = sum(motions) / len(motions)

        # Determine candidate state
        candidate = self._classify(
            avg_change, avg_motion, directions, regions,
            phash_dist, window_changed
        )

        if candidate != self.state:
            self.state = candidate
            self.state_since = now
            self.confidence = min(avg_change * 2, 1.0)
        else:
            self.confidence = min(self.confidence + 0.1, 1.0)

        return self.state

    def _classify(self, avg_change: float, avg_motion: float,
                  directions: list, regions: list,
                  phash_dist: int, window_changed: bool) -> SceneState:
        """Classify scene from accumulated evidence."""
        if window_changed:
            return SceneState.TRANSITION

        # IDLE: very low change
        if avg_change < 0.005:
            return SceneState.IDLE

        # VIDEO: sustained full-screen motion
        full_count = sum(1 for r in regions if r == "full")
        if full_count >= 3 and avg_motion > 0.3:
            return SceneState.VIDEO

        # SCROLLING: directional motion (up/down)
        dir_count = sum(1 for d in directions if d in ("up", "down"))
        if dir_count >= 2 and avg_change > 0.05:
            return SceneState.SCROLLING

        # LOADING: moderate-to-high change with spot motion (spinners)
        spot_count = sum(1 for r in regions if r == "spot")
        if spot_count >= 2 and 0.03 < avg_change < 0.3:
            return SceneState.LOADING

        # TYPING: low change, spot region, phash close
        if avg_change < 0.08 and phash_dist < 10:
            partial_count = sum(1 for r in regions if r in ("spot", "partial"))
            if partial_count >= 1:
                return SceneState.TYPING

        # INTERACTION: moderate change, not directional
        if 0.03 < avg_change < 0.4:
            return SceneState.INTERACTION

        # Major scene change
        if avg_change > 0.4 or phash_dist > 20:
            return SceneState.TRANSITION

        return SceneState.IDLE


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 2: AttentionMap
# ═══════════════════════════════════════════════════════════════

class AttentionMap:
    """
    8x6 spatial heatmap tracking WHERE screen activity concentrates.

    Updated from SmartDiff changed regions.
    Decays 0.92/frame (~50% fade in 3 seconds at 3Hz).
    192 bytes total (float32[6,8]).
    """

    DECAY = 0.92
    COLS = 8
    ROWS = 6

    def __init__(self):
        self.grid = np.zeros((self.ROWS, self.COLS), dtype=np.float32)

    def update_from_diff(self, diff) -> None:
        """Update from a SmartDiff FrameDiff result."""
        # Decay all cells
        self.grid *= self.DECAY

        if diff is None or not hasattr(diff, 'changed_regions'):
            return

        for region in diff.changed_regions:
            r, c = region.grid_y, region.grid_x
            if 0 <= r < self.ROWS and 0 <= c < self.COLS:
                self.grid[r][c] = min(self.grid[r][c] + region.change_intensity, 1.0)

    def decay(self) -> None:
        """Manual decay step (for frames where no diff is computed)."""
        self.grid *= self.DECAY

    def get_hot_zones(self, threshold: float = 0.3) -> list[tuple[int, int, float]]:
        """Return (row, col, intensity) for cells above threshold."""
        hot = []
        for r in range(self.ROWS):
            for c in range(self.COLS):
                if self.grid[r][c] >= threshold:
                    hot.append((r, c, float(self.grid[r][c])))
        return sorted(hot, key=lambda x: x[2], reverse=True)

    def get_focus_region(self) -> Optional[str]:
        """Human-readable description of where attention is focused."""
        hot = self.get_hot_zones(0.3)
        if not hot:
            return None
        # Map to human-readable positions
        labels = []
        for r, c, intensity in hot[:3]:
            v = "top" if r < 2 else "bottom" if r >= 4 else "middle"
            h = "left" if c < 3 else "right" if c >= 5 else "center"
            labels.append(f"{v}-{h} ({intensity:.2f})")
        return ", ".join(labels)

    def should_focus_ocr(self, row: int, col: int) -> bool:
        """Should OCR focus on this cell? (high recent activity)"""
        if 0 <= row < self.ROWS and 0 <= col < self.COLS:
            return self.grid[row][col] > 0.4
        return False

    def summary(self) -> str:
        """Brief text for AI consumption."""
        focus = self.get_focus_region()
        if focus:
            return f"Attention: {focus}"
        return "Attention: dispersed"


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 3: ROITracker
# ═══════════════════════════════════════════════════════════════

@dataclass
class ROI:
    """A tracked region of interest."""
    row: int
    col: int
    roi_type: str  # spinner, text_front, animation, loading_bar
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    consecutive_frames: int = 1


class ROITracker:
    """
    Tracks up to 6 regions of interest across frames.

    Creates ROI after 3 consecutive active frames in same cell.
    Classifies type from motion pattern.
    Active ROIs lower skip threshold (keep watching that area).
    Expires after 10s inactivity.
    """

    MAX_ROIS = 6
    CREATE_THRESHOLD = 3       # consecutive active frames before creating ROI
    EXPIRE_SECONDS = 10.0

    def __init__(self):
        self.rois: list[ROI] = []
        self._candidates: dict[tuple[int, int], int] = {}  # (r,c) → consecutive count

    def update(self, hot_zones: list[tuple[int, int, float]], motion: dict) -> None:
        """Update ROIs from attention hot zones and motion data."""
        now = time.time()

        # Track candidate cells
        active_cells = {(r, c) for r, c, _ in hot_zones}
        new_candidates = {}
        for cell in active_cells:
            new_candidates[cell] = self._candidates.get(cell, 0) + 1
        self._candidates = new_candidates

        # Promote candidates to ROIs
        for (r, c), count in self._candidates.items():
            if count >= self.CREATE_THRESHOLD:
                if not any(roi.row == r and roi.col == c for roi in self.rois):
                    if len(self.rois) < self.MAX_ROIS:
                        roi_type = self._classify_type(r, c, motion)
                        self.rois.append(ROI(row=r, col=c, roi_type=roi_type))

        # Update existing ROIs
        for roi in self.rois:
            if (roi.row, roi.col) in active_cells:
                roi.last_active = now
                roi.consecutive_frames += 1

        # Expire old ROIs
        self.rois = [roi for roi in self.rois if (now - roi.last_active) < self.EXPIRE_SECONDS]

    def _classify_type(self, row: int, col: int, motion: dict) -> str:
        """Classify ROI type from motion pattern."""
        direction = motion.get("dominant_direction", "none")
        region = motion.get("motion_region", "none")

        if region == "spot" and direction == "none":
            return "spinner"
        if direction in ("up", "down") and region == "partial":
            return "text_front"
        if region == "full":
            return "animation"
        return "loading_bar"

    def has_active_roi(self, row: int, col: int) -> bool:
        """Check if there's an active ROI at this cell."""
        return any(roi.row == row and roi.col == col for roi in self.rois)

    def summary(self) -> str:
        if not self.rois:
            return ""
        parts = []
        for roi in self.rois[:3]:
            v = "top" if roi.row < 2 else "bot" if roi.row >= 4 else "mid"
            h = "left" if roi.col < 3 else "right" if roi.col >= 5 else "center"
            parts.append(f"{roi.roi_type}@{v}-{h}")
        return f"ROIs: {', '.join(parts)}"


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 4: KeyframeDetector
# ═══════════════════════════════════════════════════════════════

@dataclass
class Keyframe:
    """A scene boundary marker."""
    timestamp: float
    reason: str  # scene_change, window_change, loading_complete
    change_score: float
    phash_dist: int = 0
    monitor: int = 0


class KeyframeDetector:
    """
    Marks scene boundaries for efficient replay summaries.
    Max 50 markers (~10KB). Min 2s interval between keyframes.
    """

    MAX_KEYFRAMES = 50
    MIN_INTERVAL = 2.0

    def __init__(self):
        self.keyframes: deque[Keyframe] = deque(maxlen=self.MAX_KEYFRAMES)
        self._last_keyframe_time: float = 0.0

    def check(self, change_score: float, phash_dist: int,
              window_changed: bool, scene_state: SceneState,
              prev_state: SceneState, monitor: int = 0) -> Optional[Keyframe]:
        """Check if current frame should be a keyframe. Returns Keyframe or None."""
        now = time.time()
        if (now - self._last_keyframe_time) < self.MIN_INTERVAL:
            return None

        reason = None
        if change_score > 0.40 and phash_dist > 15:
            reason = "scene_change"
        elif window_changed:
            reason = "window_change"
        elif prev_state == SceneState.LOADING and scene_state == SceneState.IDLE:
            reason = "loading_complete"

        if reason:
            kf = Keyframe(
                timestamp=now, reason=reason,
                change_score=change_score, phash_dist=phash_dist,
                monitor=monitor,
            )
            self.keyframes.append(kf)
            self._last_keyframe_time = now
            return kf
        return None

    def get_recent(self, seconds: float = 60) -> list[Keyframe]:
        cutoff = time.time() - seconds
        return [kf for kf in self.keyframes if kf.timestamp >= cutoff]


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 5: TemporalEventFuser
# ═══════════════════════════════════════════════════════════════

class TemporalEventFuser:
    """
    Combines rapid atomic events into meaningful composite narratives.

    10-second sliding window. Pattern rules fuse related events.
    Unfused events emit after 3s timeout.
    Composites get importance 0.7.
    """

    WINDOW_SECONDS = 10.0
    FUSE_TIMEOUT = 3.0

    def __init__(self):
        self._raw_events: deque[PerceptionEvent] = deque(maxlen=50)
        self._fused: list[PerceptionEvent] = []
        self._last_fuse_time: float = 0.0

    def add_raw(self, event: PerceptionEvent) -> Optional[PerceptionEvent]:
        """Add a raw event. Returns a fused composite if pattern completes, else None."""
        self._raw_events.append(event)
        return self._try_fuse()

    def _try_fuse(self) -> Optional[PerceptionEvent]:
        """Check if recent raw events form a fusable pattern."""
        now = time.time()
        if (now - self._last_fuse_time) < 1.0:
            return None

        cutoff = now - self.WINDOW_SECONDS
        recent = [e for e in self._raw_events if e.timestamp >= cutoff]
        if len(recent) < 2:
            return None

        types = [e.event_type for e in recent[-5:]]
        descs = [e.description for e in recent[-5:]]

        # Pattern: window_change + scene_change + content_ready → "Navigated to {app}"
        if "window_change" in types and "content_ready" in types:
            app = ""
            for e in recent:
                if e.event_type == "window_change":
                    app = e.details.get("new_window", "app")
                    break
            self._last_fuse_time = now
            return PerceptionEvent(
                timestamp=now,
                event_type="composite",
                description=f"Navigated to {app} (loaded)",
                importance=0.7,
                details={"pattern": "navigation_complete", "fused_count": len(types)},
            )

        # Pattern: 3+ scene_changes in 2s → "Rapid switching"
        scene_times = [e.timestamp for e in recent if e.event_type == "scene_change"]
        if len(scene_times) >= 3:
            span = scene_times[-1] - scene_times[0]
            if span < 2.0:
                self._last_fuse_time = now
                return PerceptionEvent(
                    timestamp=now,
                    event_type="composite",
                    description=f"Rapid switching ({len(scene_times)} changes in {span:.1f}s)",
                    importance=0.7,
                    details={"pattern": "rapid_switching", "count": len(scene_times)},
                )

        # Pattern: scrolling + content_ready → "Scrolled and settled"
        if "scrolling" in types and "content_ready" in types:
            self._last_fuse_time = now
            return PerceptionEvent(
                timestamp=now,
                event_type="composite",
                description="Scrolled and settled on new content",
                importance=0.5,
                details={"pattern": "scroll_settle"},
            )

        # Pattern: typing + text_appeared → "Editing in {app}"
        if "typing" in types or ("text_appeared" in types and any("typing" in str(d) for d in descs)):
            for e in recent:
                if e.event_type in ("window_change", "title_change"):
                    app = e.details.get("new_window", e.description.split(":")[0] if ":" in e.description else "app")
                    self._last_fuse_time = now
                    return PerceptionEvent(
                        timestamp=now,
                        event_type="composite",
                        description=f"Editing in {app}",
                        importance=0.5,
                        details={"pattern": "editing"},
                    )

        return None

    def get_composites(self, seconds: float = 30) -> list[PerceptionEvent]:
        """Get recent composite events."""
        cutoff = time.time() - seconds
        return [e for e in self._fused if e.timestamp >= cutoff]


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 6: CapturePredictor
# ═══════════════════════════════════════════════════════════════

class CapturePredictor:
    """
    Tracks change_score time series for periodicity detection + FPS advisory.

    Simple autocorrelation for periodic patterns (0.8-6s periods).
    FPS suggestion: max during LOADING, min during IDLE, sync to pattern frequency.
    """

    def __init__(self, history_size: int = 60):
        self._scores: deque[float] = deque(maxlen=history_size)
        self._timestamps: deque[float] = deque(maxlen=history_size)
        self._period: Optional[float] = None
        self._fps_advice: float = 2.0

    def update(self, change_score: float, scene_state: SceneState) -> None:
        """Feed latest change_score and scene state."""
        self._scores.append(change_score)
        self._timestamps.append(time.time())

        # Update FPS advice based on scene state
        if scene_state == SceneState.IDLE:
            self._fps_advice = max(self._fps_advice * 0.9, 0.5)
        elif scene_state in (SceneState.LOADING, SceneState.TRANSITION):
            self._fps_advice = min(self._fps_advice * 1.3, 5.0)
        elif scene_state == SceneState.VIDEO:
            self._fps_advice = 3.0
        else:
            self._fps_advice = 2.0

        # Detect periodicity every 10th update
        if len(self._scores) >= 20 and len(self._scores) % 10 == 0:
            self._detect_period()

    def _detect_period(self) -> None:
        """Simple autocorrelation for periodicity detection."""
        scores = np.array(self._scores, dtype=np.float32)
        if scores.std() < 0.01:
            self._period = None
            return

        # Normalize
        scores = (scores - scores.mean()) / (scores.std() + 1e-8)
        n = len(scores)

        # Check lags corresponding to 0.8-6s periods (at ~2-3Hz)
        best_corr = 0.0
        best_lag = 0
        for lag in range(2, min(n // 2, 18)):  # 2-18 frames at 3Hz = 0.7-6s
            if lag >= n:
                break
            corr = float(np.dot(scores[:n - lag], scores[lag:]) / (n - lag))
            if corr > best_corr:
                best_corr = corr
                best_lag = lag

        if best_corr > 0.3 and best_lag > 0 and len(self._timestamps) > best_lag:
            # Estimate period in seconds
            dt = (self._timestamps[-1] - self._timestamps[-best_lag]) / best_lag
            self._period = round(dt * best_lag, 2)

    @property
    def fps_advice(self) -> float:
        return round(self._fps_advice, 1)

    @property
    def detected_period(self) -> Optional[float]:
        return self._period

    def summary(self) -> str:
        parts = [f"FPS advice: {self.fps_advice}"]
        if self._period:
            parts.append(f"periodic pattern: ~{self._period:.1f}s")
        return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════
# IPA CLASS 7: MonitorPerceptionState
# ═══════════════════════════════════════════════════════════════

@dataclass
class MonitorPerceptionState:
    """Independent perception state per monitor."""
    monitor_id: int
    scene: SceneStateMachine = field(default_factory=SceneStateMachine)
    attention: AttentionMap = field(default_factory=AttentionMap)
    predictor: CapturePredictor = field(default_factory=CapturePredictor)
    # Per-monitor temporal/motion state (IPA v2 stabilization)
    last_gray: Optional[np.ndarray] = None
    last_phash: Optional[object] = None
    last_hash: Optional[str] = None
    last_change_time: float = field(default_factory=time.time)
    last_ocr_time: float = 0.0
    last_ocr_text: str = ""
    idle_reported: bool = False
    loading_detected: bool = False
    scene_stable_since: float = field(default_factory=time.time)
    consecutive_high: int = 0
    motion_reported: bool = False
    video_reported: bool = False
    change_history: deque = field(default_factory=lambda: deque(maxlen=20))
    last_analyzed: float = 0.0
    is_active: bool = False
    frame_skip_counter: int = 0

    def should_analyze(self, is_active_monitor: bool) -> bool:
        """Active monitors: every frame. Inactive: every 3rd frame."""
        self.is_active = is_active_monitor
        if is_active_monitor:
            return True
        self.frame_skip_counter += 1
        if self.frame_skip_counter >= 3:
            self.frame_skip_counter = 0
            return True
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════════════

class PerceptionEngine:
    """
    ILUMINATY Perception Algorithm (IPA) — the AI's visual cortex.

    Continuous vision processing with 4-gate pipeline.
    Generates semantic event stream consumed by AI (~200 tokens).
    """

    def __init__(
        self,
        buffer=None,
        max_events: int = 200,
        monitor_mgr=None,
        smart_diff=None,
        context=None,
        visual_profile: str = "core_ram",
        enable_disk_spool: bool = False,
        deep_loop_hz: float = 1.0,
        fast_loop_hz: float = 10.0,
    ):
        self._buffer = buffer
        self._events: deque[PerceptionEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._deep_thread: Optional[threading.Thread] = None
        self._fast_loop_interval = max(0.08, min(0.25, 1.0 / max(1.0, fast_loop_hz)))
        self._deep_loop_interval = max(0.5, min(2.0, 1.0 / max(0.5, deep_loop_hz)))

        # External references (IPA Phase 1.3)
        self._monitor_mgr = monitor_mgr
        self._smart_diff = smart_diff
        self._context = context
        self._agent_coordinator = None  # IPA v2: set by server.py for multi-agent fan-out

        # Global window/context state (visual state itself is per-monitor)
        self._last_window: str = "unknown"
        self._last_title: str = "unknown"
        self._last_window_info: dict = {
            "name": "unknown",
            "app_name": "unknown",
            "window_title": "unknown",
            "title": "unknown",
            "bounds": {},
            "pid": 0,
        }

        # IPA components
        self._scene_machine = SceneStateMachine()
        self._attention = AttentionMap()
        self._roi_tracker = ROITracker()
        self._keyframe_detector = KeyframeDetector()
        self._event_fuser = TemporalEventFuser()
        self._predictor = CapturePredictor()
        self._monitor_states: dict[int, MonitorPerceptionState] = {}
        self._world = WorldStateEngine(horizon_seconds=90)
        self._temporal = TemporalVisualStore(
            horizon_seconds=90,
            profile=visual_profile,
            disk_enabled=enable_disk_spool,
            disk_ttl_minutes=30,
            sample_interval_ms=1200,
        )
        self._visual = VisualEngine(max_queue=24, max_history=900)

        # Semantic pacing
        self._last_world_update = 0.0
        self._world_update_interval = 0.12
        self._last_visual_delta_ms = int(time.time() * 1000)

        # OCR engine
        self._ocr = None
        if HAS_VISION:
            try:
                self._ocr = OCREngine()
            except Exception as e:
                logger.warning("OCR engine unavailable in perception startup: %s", e)

    def _get_monitor_state(self, monitor_id: int) -> MonitorPerceptionState:
        """Get or create per-monitor state."""
        if monitor_id not in self._monitor_states:
            self._monitor_states[monitor_id] = MonitorPerceptionState(monitor_id=monitor_id)
        return self._monitor_states[monitor_id]

    def start(self, buffer):
        """Start the perception loop."""
        self._buffer = buffer
        self._running = True
        self._visual.start()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perception-fast-loop")
        self._deep_thread = threading.Thread(target=self._deep_loop, daemon=True, name="perception-deep-loop")
        self._thread.start()
        self._deep_thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._deep_thread:
            self._deep_thread.join(timeout=3)
        self._visual.stop()

    def _add_event(self, event_type: str, description: str,
                   importance: float = 0.5, monitor: int = 0,
                   uncertainty: Optional[float] = None, **details):
        if uncertainty is None:
            uncertainty = max(0.0, min(1.0, 1.0 - importance))
        evt = PerceptionEvent(
            timestamp=time.time(),
            event_type=event_type,
            description=description,
            importance=importance,
            uncertainty=round(float(uncertainty), 3),
            monitor=monitor,
            details=details,
        )
        with self._lock:
            self._events.append(evt)
        try:
            self._temporal.add_semantic_transition(
                tick_id=self._world.tick_id,
                kind=event_type,
                summary=description,
                confidence=importance,
                monitor=monitor,
                evidence_refs=[],
            )
        except Exception:
            pass

        # IPA v2: fan-out to connected agents
        if self._agent_coordinator:
            try:
                self._agent_coordinator.push_perception_event(evt)
            except Exception as e:
                logger.debug("Failed to push perception event to coordinator: %s", e)

        # Feed to event fuser
        composite = self._event_fuser.add_raw(evt)
        if composite:
            with self._lock:
                self._events.append(composite)
            if self._agent_coordinator:
                try:
                    self._agent_coordinator.push_perception_event(composite)
                except Exception as e:
                    logger.debug("Failed to push composite perception event: %s", e)

    def _loop(self):
        """Fast loop (8-12Hz): low-latency semantic updates, never waits for VLM."""
        last_ts_per_monitor: dict[int, float] = {}
        while self._running:
            loop_start = time.time()
            try:
                if not self._buffer:
                    time.sleep(0.3)
                    continue

                # IPA v2: get latest frame per monitor (each compared to its own previous)
                per_monitor = self._buffer.get_latest_per_monitor()
                if not per_monitor:
                    time.sleep(self._fast_loop_interval)
                    continue

                analyzed_any = False
                for monitor_id, slot in per_monitor.items():
                    prev_ts = last_ts_per_monitor.get(monitor_id, 0.0)
                    if slot.timestamp <= prev_ts:
                        continue
                    last_ts_per_monitor[monitor_id] = slot.timestamp
                    self._analyze_frame(slot)
                    analyzed_any = True

                if not analyzed_any:
                    time.sleep(self._fast_loop_interval)

            except Exception as e:
                logger.warning("Perception loop iteration failed: %s", e)
            elapsed = time.time() - loop_start
            rest = self._fast_loop_interval - elapsed
            if rest > 0:
                time.sleep(rest)

    def _deep_loop(self):
        """
        Deep loop (0.5-2Hz): enqueues prioritized visual tasks for local VLM engine.
        This path is asynchronous and never blocks the fast semantic loop.
        """
        last_enqueued_ts: dict[int, float] = {}
        while self._running:
            started = time.time()
            try:
                if not self._buffer:
                    time.sleep(self._deep_loop_interval)
                    continue
                per_monitor = self._buffer.get_latest_per_monitor()
                now_ms = int(time.time() * 1000)
                for monitor_id, slot in per_monitor.items():
                    prev = last_enqueued_ts.get(monitor_id, 0.0)
                    if slot.timestamp <= prev:
                        continue
                    mon_state = self._get_monitor_state(monitor_id)

                    # Prioritize high-change or unstable scenes.
                    priority = min(1.0, max(0.1, float(getattr(slot, "change_score", 0.0)) + (1.0 - mon_state.scene.confidence)))
                    if priority < 0.2 and (now_ms - int(slot.timestamp * 1000)) > 1200:
                        continue

                    predicted_tick = self._world.tick_id + 1
                    frame_ref = self._temporal.add_frame_ref(
                        slot,
                        tick_id=predicted_tick,
                        boundary_reason="deep_loop",
                        force=True,
                    )
                    ref_id = frame_ref["ref_id"] if frame_ref else f"tmp_{int(slot.timestamp * 1000)}_{monitor_id}"
                    motion_desc = (
                        f"state={mon_state.scene.state.value} "
                        f"conf={mon_state.scene.confidence:.2f} "
                        f"change={getattr(slot, 'change_score', 0.0):.3f}"
                    )
                    task = VisualTask(
                        ref_id=ref_id,
                        tick_id=predicted_tick,
                        timestamp_ms=int(slot.timestamp * 1000),
                        monitor=monitor_id,
                        frame_bytes=slot.frame_bytes,
                        mime_type=getattr(slot, "mime_type", "image/webp"),
                        app_name=self._last_window_info.get("app_name", self._last_window),
                        window_title=self._last_window_info.get("window_title", self._last_title),
                        ocr_text=mon_state.last_ocr_text,
                        motion_summary=motion_desc,
                        priority=priority,
                    )
                    self._visual.enqueue(task)
                    last_enqueued_ts[monitor_id] = slot.timestamp
            except Exception as e:
                logger.debug("Deep loop iteration failed: %s", e)

            elapsed = time.time() - started
            rest = self._deep_loop_interval - elapsed
            if rest > 0:
                time.sleep(rest)

    # ─── GATE 0: Window Change (free, most reliable signal) ───

    def _check_window(self, monitor: int = 0) -> tuple[bool, dict]:
        """Detect active window changes. Returns (changed, window_info)."""
        if not HAS_VISION:
            return False, self._last_window_info
        try:
            win = get_active_window_info() or {}
            name = win.get("name") or win.get("app_name") or "unknown"
            title = win.get("window_title") or win.get("title") or "unknown"

            # Keep context engine warm continuously (not only via endpoint pulls)
            if self._context:
                try:
                    self._context.update(name, title)
                except Exception as e:
                    logger.debug("Context engine update failed for window change: %s", e)

            self._last_window_info = {
                "name": name,
                "app_name": name,
                "window_title": title,
                "title": win.get("title", title),
                "bounds": win.get("bounds", {}),
                "pid": win.get("pid", 0),
            }

            changed = False
            if name != self._last_window:
                self._add_event(
                    "window_change",
                    f"Switched to {name}: {title[:60]}",
                    importance=0.8,
                    monitor=monitor,
                    old_window=self._last_window,
                    new_window=name,
                )
                self._last_window = name
                self._last_title = title
                changed = True
            elif title != self._last_title and title:
                self._add_event(
                    "title_change",
                    f"{name} → {title[:80]}",
                    importance=0.5,
                    monitor=monitor,
                )
                self._last_title = title
                changed = True
            return changed, self._last_window_info
        except Exception:
            return False, self._last_window_info

    # ─── GATE 2: Perceptual Hash (fast semantic gate) ───

    def _check_phash(self, mon_state: MonitorPerceptionState, frame_bytes: bytes) -> int:
        """Compute perceptual hash and return hamming distance from last frame."""
        if not HAS_IMAGEHASH:
            return 99
        try:
            img = Image.open(io.BytesIO(frame_bytes))
            current_hash = imagehash.phash(img, hash_size=8)
            if mon_state.last_phash is None:
                mon_state.last_phash = current_hash
                return 99
            distance = current_hash - mon_state.last_phash
            mon_state.last_phash = current_hash
            return distance
        except Exception:
            return 99

    # ─── GATE 3: Optical Flow (where is motion happening?) ───

    def _analyze_motion(self, mon_state: MonitorPerceptionState, current_gray: np.ndarray) -> dict:
        """Farneback optical flow → 3x3 zone motion analysis."""
        if mon_state.last_gray is None or not HAS_CV2:
            mon_state.last_gray = current_gray
            return {"total_motion": 0, "zones": {}, "dominant_direction": "none",
                    "motion_region": "none", "active_zones": 0}

        try:
            flow = cv2.calcOpticalFlowFarneback(
                mon_state.last_gray, current_gray, None,
                pyr_scale=0.5, levels=2, winsize=15,
                iterations=2, poly_n=5, poly_sigma=1.1, flags=0
            )
            mon_state.last_gray = current_gray

            mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            total_motion = min(float(np.mean(mag)) / 5.0, 1.0)

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
                zone_mag = mag[ry * zone_h:(ry + 1) * zone_h, rx * zone_w:(rx + 1) * zone_w]
                zones[name] = float(np.mean(zone_mag))

            mean_dx = float(np.mean(flow[..., 0]))
            mean_dy = float(np.mean(flow[..., 1]))
            if abs(mean_dy) > abs(mean_dx) and abs(mean_dy) > 0.5:
                direction = "down" if mean_dy > 0 else "up"
            elif abs(mean_dx) > 0.5:
                direction = "right" if mean_dx > 0 else "left"
            else:
                direction = "mixed" if total_motion > 0.1 else "none"

            active_zones = sum(1 for v in zones.values() if v > 1.0)
            if active_zones >= 7:
                region = "full"
            elif active_zones >= 3:
                region = "partial"
            elif active_zones >= 1:
                region = "spot"
            else:
                region = "none"

            return {
                "total_motion": total_motion,
                "zones": zones,
                "dominant_direction": direction,
                "motion_region": region,
                "active_zones": active_zones,
            }

        except Exception:
            mon_state.last_gray = current_gray
            return {"total_motion": 0, "zones": {}, "dominant_direction": "none",
                    "motion_region": "none", "active_zones": 0}

    # ─── MAIN ANALYSIS (IPA 4-Gate Pipeline) ───

    def _analyze_frame(self, slot):
        """
        IPA 4-Gate Pipeline:
        Gate 0 → Window check (free)
        Gate 1 → change_score thresholds (continuous 0.0-1.0)
        Gate 2 → pHash (skip if semantically identical)
        Gate 3 → Optical flow + SmartDiff + AttentionMap
        Gate 4 → OCR diff (throttled, only on major changes)

        Post: SceneStateMachine → KeyframeDetector → CapturePredictor
        """
        now = time.time()
        change = slot.change_score
        monitor_id = getattr(slot, 'monitor_id', 0)

        # Get per-monitor state
        mon_state = self._get_monitor_state(monitor_id)

        # Determine active monitor
        active_monitor_id = 0
        if self._monitor_mgr:
            try:
                active = self._monitor_mgr.get_active_monitor()
                if active:
                    active_monitor_id = getattr(active, 'id', 0)
            except Exception as e:
                logger.debug("Failed to resolve active monitor, defaulting to 0: %s", e)

        if not mon_state.should_analyze(monitor_id == active_monitor_id):
            self._attention.decay()
            return

        mon_state.last_analyzed = now

        # ── Gate 0: Window change (always, zero cost) ──
        window_changed, window_info = self._check_window(monitor=monitor_id)
        if window_changed:
            mon_state.scene_stable_since = now
            mon_state.loading_detected = True
            mon_state.video_reported = False
            mon_state.motion_reported = False

        # ── Gate 1: Change score thresholds ──
        mon_state.change_history.append(change)
        prev_scene_state = mon_state.scene.state

        if change < 0.005:
            # Truly identical — skip everything
            mon_state.consecutive_high = 0
            self._attention.decay()
            if not mon_state.idle_reported and (now - mon_state.last_change_time) > 15:
                self._add_event("idle", f"Screen stable for {now - mon_state.last_change_time:.0f}s",
                                importance=0.1, monitor=monitor_id)
                mon_state.idle_reported = True
            if mon_state.loading_detected and (now - mon_state.scene_stable_since) > 2.0:
                self._add_event("content_ready", "Content loaded and stable",
                                importance=0.7, monitor=monitor_id)
                mon_state.loading_detected = False

            # Update state machine with zero signals
            motion = {"total_motion": 0, "dominant_direction": "none",
                      "motion_region": "none", "active_zones": 0}
            mon_state.scene.update(change, motion, 0, window_changed)
            self._scene_machine.update(change, motion, 0, window_changed)
            self._predictor.update(change, mon_state.scene.state)
            mon_state.predictor.update(change, mon_state.scene.state)

            gray = _bytes_to_gray(slot.frame_bytes)
            if gray is not None:
                mon_state.last_gray = gray
            self._update_world_state(
                mon_state,
                monitor_id,
                motion,
                window_info,
                slot=slot,
                boundary_reason="window_change" if window_changed else "",
            )
            return

        mon_state.last_change_time = now
        mon_state.idle_reported = False

        if change < 0.03:
            # Cursor/blink level — only process if ROI is watching this area
            has_roi = bool(self._roi_tracker.rois)
            if not has_roi:
                self._attention.decay()
                motion = {"total_motion": 0, "dominant_direction": "none",
                          "motion_region": "none", "active_zones": 0}
                mon_state.scene.update(change, motion, 0, window_changed)
                self._scene_machine.update(change, motion, 0, window_changed)
                self._predictor.update(change, mon_state.scene.state)
                mon_state.predictor.update(change, mon_state.scene.state)
                gray = _bytes_to_gray(slot.frame_bytes)
                if gray is not None:
                    mon_state.last_gray = gray
                self._update_world_state(
                    mon_state,
                    monitor_id,
                    motion,
                    window_info,
                    slot=slot,
                    boundary_reason="window_change" if window_changed else "",
                )
                return

        # ── Gate 2: Perceptual hash ──
        phash_dist = self._check_phash(mon_state, slot.frame_bytes)

        if change < 0.05 and phash_dist < 5:
            # Minimal change — skip deeper analysis
            self._attention.decay()
            motion = {"total_motion": change, "dominant_direction": "none",
                      "motion_region": "none", "active_zones": 0}
            mon_state.scene.update(change, motion, phash_dist, window_changed)
            self._scene_machine.update(change, motion, phash_dist, window_changed)
            self._predictor.update(change, mon_state.scene.state)
            mon_state.predictor.update(change, mon_state.scene.state)
            gray = _bytes_to_gray(slot.frame_bytes)
            if gray is not None:
                mon_state.last_gray = gray
            self._update_world_state(
                mon_state,
                monitor_id,
                motion,
                window_info,
                slot=slot,
                boundary_reason="window_change" if window_changed else "",
            )
            return

        # ── Gate 3: Optical Flow + SmartDiff + AttentionMap ──
        gray = _bytes_to_gray(slot.frame_bytes)
        if gray is not None:
            motion = self._analyze_motion(mon_state, gray)

            # SmartDiff fast comparison (IPA Phase 1.2)
            diff = None
            if self._smart_diff is not None:
                try:
                    diff = self._smart_diff.compare_fast(gray)
                except Exception as e:
                    logger.debug("SmartDiff compare_fast failed, continuing without diff: %s", e)

            # Update AttentionMap from SmartDiff
            self._attention.update_from_diff(diff)
            mon_state.attention.update_from_diff(diff)

            # Update ROI tracker
            hot_zones = mon_state.attention.get_hot_zones()
            self._roi_tracker.update(hot_zones, motion)
        else:
            motion = {"total_motion": change, "motion_region": "unknown",
                      "dominant_direction": "none", "active_zones": 0}

        # ── Update SceneStateMachine ──
        new_state = mon_state.scene.update(change, motion, phash_dist, window_changed)
        self._scene_machine.update(change, motion, phash_dist, window_changed)

        # ── KeyframeDetector ──
        self._keyframe_detector.check(
            change, phash_dist, window_changed,
            new_state, prev_scene_state, monitor_id
        )

        # ── CapturePredictor ──
        self._predictor.update(change, new_state)
        mon_state.predictor.update(change, new_state)

        # ── Classify & emit events ──
        region = motion.get("motion_region", "none")
        direction = motion.get("dominant_direction", "none")
        event_uncertainty = max(0.0, min(1.0, 1.0 - mon_state.scene.confidence))

        if change > 0.4 or phash_dist > 20:
            # HIGH CHANGE
            mon_state.consecutive_high += 1
            mon_state.scene_stable_since = now

            if mon_state.consecutive_high <= 2:
                desc = f"Scene change in {self._last_window}"
                if region == "full":
                    desc += " (full screen update)"
                elif direction in ("down", "up"):
                    desc += f" (content moving {direction})"
                self._add_event("scene_change", desc, importance=0.6,
                                monitor=monitor_id, uncertainty=event_uncertainty,
                                change_score=change, phash_dist=phash_dist)
                mon_state.motion_reported = False
                mon_state.video_reported = False
                mon_state.loading_detected = True
            elif mon_state.consecutive_high >= 8 and not mon_state.video_reported:
                self._add_event(
                    "video_detected",
                    f"Video/animation playing in {self._last_window} ({region} region, {direction})",
                    importance=0.3, monitor=monitor_id, uncertainty=event_uncertainty,
                )
                mon_state.video_reported = True

        elif change > 0.15:
            # MODERATE CHANGE — medium path + attention
            mon_state.consecutive_high = 0
            mon_state.scene_stable_since = now

            if direction in ("down", "up") and not mon_state.motion_reported:
                self._add_event("scrolling", f"Scrolling {direction} in {self._last_window}",
                                importance=0.2, monitor=monitor_id,
                                uncertainty=event_uncertainty, direction=direction)
                mon_state.motion_reported = True
            elif region == "spot" and not mon_state.motion_reported:
                zones = motion.get("zones", {})
                active = [k for k, v in zones.items() if v > 1.0]
                if active:
                    self._add_event("ui_activity",
                                    f"UI activity in {', '.join(active[:2])} of {self._last_window}",
                                    importance=0.2, monitor=monitor_id,
                                    uncertainty=event_uncertainty, zones=active)
                    mon_state.motion_reported = True

        elif change > 0.03:
            # MINOR CHANGE — Gate 1 threshold, update attention only
            mon_state.consecutive_high = 0
            mon_state.motion_reported = False

        else:
            mon_state.consecutive_high = 0
            mon_state.motion_reported = False

        # ── Gate 4: OCR diff (expensive, only on major changes, max every 3s) ──
        if (self._ocr and self._ocr.available
                and (change > 0.3 or phash_dist > 15)
                and (now - mon_state.last_ocr_time) > 3.0):
            # Focus OCR on hot attention zones if possible
            mon_state.last_ocr_time = now
            try:
                ocr_result = self._ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)
                current_text = ocr_result.get("text", "")

                if current_text and current_text != mon_state.last_ocr_text:
                    old_words = set(mon_state.last_ocr_text.split())
                    new_words = set(current_text.split())
                    appeared = new_words - old_words
                    disappeared = old_words - new_words

                    if len(appeared) > 10:
                        sample = " ".join(list(appeared)[:12])
                        self._add_event("text_appeared", f"New content: {sample}...",
                                        importance=0.5, monitor=monitor_id,
                                        uncertainty=event_uncertainty,
                                        new_word_count=len(appeared))

                    if len(disappeared) > 15 and len(appeared) > 15:
                        self._add_event("page_navigation",
                                        f"Page content replaced in {self._last_window}",
                                        importance=0.7, monitor=monitor_id,
                                        uncertainty=event_uncertainty)
                        mon_state.loading_detected = True

                    mon_state.last_ocr_text = current_text
            except Exception as e:
                logger.debug("OCR diff step failed on monitor %s: %s", monitor_id, e)

        mon_state.last_hash = slot.phash
        boundary_reason = ""
        if new_state != prev_scene_state:
            boundary_reason = "scene_transition"
        elif change > 0.4:
            boundary_reason = "high_change"
        self._update_world_state(
            mon_state,
            monitor_id,
            motion,
            window_info,
            slot=slot,
            boundary_reason=boundary_reason,
        )

    def _get_primary_monitor_state(self) -> Optional[MonitorPerceptionState]:
        if not self._monitor_states:
            return None
        active = [ms for ms in self._monitor_states.values() if ms.is_active]
        if active:
            return max(active, key=lambda ms: ms.last_analyzed)
        return max(self._monitor_states.values(), key=lambda ms: ms.last_analyzed)

    def _recent_event_dicts(self, seconds: float = 20) -> list[dict]:
        events = self.get_events(last_seconds=seconds, min_importance=0.0)
        return [
            {
                "timestamp": e.timestamp,
                "type": e.event_type,
                "importance": e.importance,
                "monitor": e.monitor,
            }
            for e in events[-20:]
        ]

    def _update_world_state(
        self,
        mon_state: MonitorPerceptionState,
        monitor_id: int,
        motion: dict,
        window_info: Optional[dict] = None,
        slot=None,
        boundary_reason: str = "",
    ) -> None:
        now = time.time()
        if (now - self._last_world_update) < self._world_update_interval:
            return

        workflow = "unknown"
        if self._context:
            try:
                workflow = self._context.get_state().current_workflow
            except Exception:
                workflow = "unknown"

        info = window_info or self._last_window_info
        app_name = info.get("name") or info.get("app_name") or self._last_window
        title = info.get("window_title") or info.get("title") or self._last_title
        hot = [
            {"row": r, "col": c, "intensity": round(i, 3)}
            for r, c, i in mon_state.attention.get_hot_zones()
        ]
        recent_events = self._recent_event_dicts(seconds=25)
        visual_facts = self._visual.get_latest_facts(monitor_id=monitor_id)
        evidence = []
        for e in recent_events[-8:]:
            evidence.append(
                {
                    "id": f"evt_{int(e.get('timestamp', 0) * 1000)}_{e.get('type', 'event')}",
                    "type": "event",
                    "summary": e.get("type", "event"),
                    "confidence": e.get("importance", 0.3),
                    "timestamp_ms": int(e.get("timestamp", now) * 1000),
                    "monitor": e.get("monitor", monitor_id),
                }
            )

        # Reserve tick for frame ref so trace and world stay aligned.
        predicted_tick = self._world.tick_id + 1
        frame_refs = []
        if slot is not None:
            try:
                ref = self._temporal.add_frame_ref(
                    slot,
                    tick_id=predicted_tick,
                    boundary_reason=boundary_reason,
                    force=bool(boundary_reason),
                )
                if ref:
                    frame_refs.append(ref)
            except Exception as e:
                logger.debug("Temporal frame_ref failed: %s", e)

        snapshot = self._world.update(
            scene_state=mon_state.scene.state.value,
            scene_confidence=mon_state.scene.confidence,
            window_title=title,
            app_name=app_name,
            workflow=workflow,
            monitor_id=monitor_id,
            attention_hot_zones=hot,
            recent_events=recent_events,
            dominant_direction=motion.get("dominant_direction", "none"),
            visual_facts=visual_facts,
            evidence=evidence,
            frame_refs=frame_refs,
        )
        try:
            self._temporal.add_semantic_transition(
                tick_id=snapshot.get("tick_id", predicted_tick),
                kind="world_update",
                summary=f"{snapshot.get('task_phase')} | {snapshot.get('active_surface', 'unknown')}",
                confidence=max(0.0, 1.0 - float(snapshot.get("uncertainty", 1.0))),
                monitor=monitor_id,
                evidence_refs=[f.get("ref_id", "") for f in frame_refs if f.get("ref_id")],
            )
        except Exception as e:
            logger.debug("Temporal semantic transition failed: %s", e)
        self._last_world_update = now

    # ─── Public API ───

    def get_events(self, last_seconds: float = 30, min_importance: float = 0.0) -> list[PerceptionEvent]:
        cutoff = time.time() - last_seconds
        with self._lock:
            return [
                e for e in self._events
                if e.timestamp >= cutoff and e.importance >= min_importance
            ]

    def get_summary(self, last_seconds: float = 30) -> str:
        """
        IPA-enhanced text summary — perfect for AI consumption (~200 tokens).
        Includes: events, scene state, attention, ROIs, predictor advice.
        """
        events = self.get_events(last_seconds, min_importance=0.2)
        primary = self._get_primary_monitor_state()
        scene_state = primary.scene.state.value.upper() if primary else self._scene_machine.state.value.upper()
        scene_conf = primary.scene.confidence if primary else self._scene_machine.confidence

        lines = [f"## Perception — {scene_state} (conf {scene_conf:.1f})"]

        if not events:
            elapsed = time.time() - (primary.last_change_time if primary else time.time())
            lines.append(f"No significant changes in {elapsed:.0f}s")
        else:
            lines.append(f"{len(events)} events in last {last_seconds:.0f}s:")
            for e in events[-12:]:
                age = time.time() - e.timestamp
                ago = f"{age:.0f}s ago" if age < 60 else f"{age / 60:.0f}m ago"
                marker = "!" if e.importance >= 0.7 else "-"
                lines.append(f"  {marker} [{ago}] {e.description}")

        attention_text = primary.attention.summary() if primary else self._attention.summary()
        if attention_text:
            lines.append(attention_text)

        roi_text = self._roi_tracker.summary()
        if roi_text:
            lines.append(roi_text)

        if len(self._monitor_states) > 1:
            mon_parts = []
            for mid, ms in sorted(self._monitor_states.items()):
                active = " (active)" if ms.is_active else ""
                mon_parts.append(f"Mon {mid}: {ms.scene.state.value}{active}")
            lines.append(" | ".join(mon_parts))

        predictor_summary = primary.predictor.summary() if primary else self._predictor.summary()
        lines.append(predictor_summary)

        world = self.get_world_state()
        lines.append(
            f"World: phase={world.get('task_phase')} "
            f"readiness={world.get('readiness')} "
            f"uncertainty={world.get('uncertainty')}"
        )
        lines.append(f"Window: {self._last_window} — {self._last_title[:60]}")

        return "\n".join(lines)

    def get_event_count(self) -> int:
        return len(self._events)

    def get_state(self) -> dict:
        """Full IPA introspection for /perception/state endpoint."""
        primary = self._get_primary_monitor_state()
        attention = primary.attention if primary else self._attention
        predictor = primary.predictor if primary else self._predictor
        scene = primary.scene if primary else self._scene_machine

        return {
            "scene_state": scene.state.value,
            "scene_confidence": round(scene.confidence, 2),
            "attention_hot_zones": [
                {"row": r, "col": c, "intensity": round(i, 3)}
                for r, c, i in attention.get_hot_zones()
            ],
            "attention_focus": attention.get_focus_region(),
            "rois": [
                {"row": roi.row, "col": roi.col, "type": roi.roi_type,
                 "age_s": round(time.time() - roi.created, 1)}
                for roi in self._roi_tracker.rois
            ],
            "keyframes_recent": len(self._keyframe_detector.get_recent(60)),
            "fps_advice": predictor.fps_advice,
            "detected_period": predictor.detected_period,
            "monitors": {
                mid: {
                    "state": ms.scene.state.value,
                    "scene_confidence": round(ms.scene.confidence, 2),
                    "is_active": ms.is_active,
                    "last_analyzed": round(ms.last_analyzed, 1),
                }
                for mid, ms in self._monitor_states.items()
            },
            "world": self.get_world_state(),
            "visual": self._visual.stats(),
            "temporal": self._temporal.stats(),
            "event_count": len(self._events),
            "running": self._running,
        }

    def get_world_state(self) -> dict:
        return self._world.get_world()

    def get_world_trace(self, seconds: float = 90) -> list[dict]:
        world_trace = self._world.get_trace(seconds=seconds)
        temporal = self._temporal.get_trace(seconds=seconds)
        trace = list(world_trace)
        for s in temporal.get("semantic", []):
            trace.append(
                {
                    "timestamp_ms": s.get("timestamp_ms"),
                    "tick_id": s.get("tick_id"),
                    "summary": s.get("summary"),
                    "boundary_reason": f"semantic:{s.get('kind', 'event')}",
                    "task_phase": "semantic",
                    "active_surface": "temporal",
                    "readiness": None,
                    "uncertainty": round(1.0 - float(s.get("confidence", 0.0)), 3),
                    "evidence_refs": s.get("evidence_refs", []),
                    "frame_refs": [],
                }
            )
        trace.sort(key=lambda x: int(x.get("timestamp_ms", 0)))
        return trace

    def get_readiness(self) -> dict:
        return self._world.get_readiness()

    def get_world_trace_bundle(self, seconds: float = 90) -> dict:
        return {
            "trace": self.get_world_trace(seconds=seconds),
            "temporal": self._temporal.get_trace(seconds=seconds),
        }

    def set_risk_mode(self, mode: str) -> None:
        self._world.set_risk_mode(mode)

    def record_action_feedback(self, action: str, success: bool, message: str = "") -> None:
        self._world.note_action(action=action, success=success, message=message)
        try:
            self._temporal.add_semantic_transition(
                tick_id=self._world.tick_id,
                kind="action_feedback",
                summary=f"{action}: {'ok' if success else 'failed'} {message[:160]}",
                confidence=1.0 if success else 0.3,
                monitor=0,
                evidence_refs=[],
            )
        except Exception:
            pass

    def check_context_freshness(self, context_tick_id: Optional[int], max_staleness_ms: int) -> dict:
        return self._world.check_context_freshness(context_tick_id, max_staleness_ms)

    def get_visual_facts_delta(self, since_ms: int, monitor_id: Optional[int] = None) -> list[dict]:
        return self._visual.get_facts_delta(since_ms=since_ms, monitor_id=monitor_id)

    def query_visual(
        self,
        question: str,
        at_ms: Optional[int] = None,
        window_seconds: float = 30,
        monitor_id: Optional[int] = None,
    ) -> dict:
        result = self._visual.query(
            question,
            at_ms=at_ms,
            window_seconds=window_seconds,
            monitor_id=monitor_id,
        )
        frame_refs = self._temporal.query_frame_refs(
            at_ms=at_ms,
            window_seconds=window_seconds,
            monitor_id=monitor_id,
            limit=8,
        )
        result["frame_refs"] = frame_refs
        return result

    def get_attention_heatmap(self) -> list[list[float]]:
        """Raw attention grid for dashboard visualization."""
        primary = self._get_primary_monitor_state()
        if primary:
            return primary.attention.grid.tolist()
        return self._attention.grid.tolist()

    @property
    def is_running(self) -> bool:
        return self._running
