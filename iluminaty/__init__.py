# ILUMINATY - Real-time visual perception for AI
# Zero-disk, RAM-only ring buffer architecture

from .ring_buffer import RingBuffer
from .capture import ScreenCapture, CaptureConfig
from .vision import VisionIntelligence
from .server import app

__version__ = "0.3.0"
__all__ = ["RingBuffer", "ScreenCapture", "CaptureConfig", "VisionIntelligence", "app"]
