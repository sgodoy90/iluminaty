# ILUMINATY - Real-time visual perception for AI
# Zero-disk, RAM-only ring buffer architecture

__version__ = "0.5.0"

# Lazy imports - only load when accessed
def __getattr__(name):
    if name == "RingBuffer":
        from .ring_buffer import RingBuffer
        return RingBuffer
    elif name == "ScreenCapture":
        from .capture import ScreenCapture
        return ScreenCapture
    elif name == "CaptureConfig":
        from .capture import CaptureConfig
        return CaptureConfig
    elif name == "VisionIntelligence":
        from .vision import VisionIntelligence
        return VisionIntelligence
    elif name == "app":
        from .server import app
        return app
    raise AttributeError(f"module 'iluminaty' has no attribute {name!r}")

__all__ = ["RingBuffer", "ScreenCapture", "CaptureConfig", "VisionIntelligence", "app", "__version__"]
