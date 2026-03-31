from types import SimpleNamespace

from iluminaty.grounding import GroundingEngine


class _UITreeStub:
    available = True

    def find_all(self, name=None, role=None):
        if (name or "").lower() == "save":
            return [
                {"name": "Save", "role": "button", "x": 100, "y": 200, "width": 80, "height": 30},
            ]
        return []


class _OCRStub:
    def extract_text(self, frame_bytes, frame_hash=None):
        return {
            "text": "Save Cancel",
            "blocks": [
                {"text": "Save", "x": 102, "y": 201, "w": 76, "h": 28, "confidence": 96},
            ],
            "confidence": 96.0,
        }


class _VisionStub:
    def __init__(self):
        self.ocr = _OCRStub()


class _PerceptionStub:
    def get_world_state(self):
        return {
            "tick_id": 7,
            "task_phase": "interaction",
            "active_surface": "editor",
            "staleness_ms": 5,
            "attention_targets": ["middle-center:0.8"],
            "visual_facts": [{"text": "Save button visible", "evidence_ref": "vf_1"}],
        }

    def check_context_freshness(self, context_tick_id, max_staleness_ms):
        return {"allowed": True, "reason": "fresh", "latest_tick_id": 7, "staleness_ms": 5}


class _BufferStub:
    def get_latest_for_monitor(self, monitor_id):
        return self.get_latest()

    def get_latest(self):
        return SimpleNamespace(
            frame_bytes=b"x",
            phash="h1",
            width=1920,
            height=1080,
            monitor_id=1,
        )


def test_grounding_engine_resolve_success_hybrid_fusion():
    engine = GroundingEngine()
    engine.set_layers(
        ui_tree=_UITreeStub(),
        vision=_VisionStub(),
        perception=_PerceptionStub(),
        buffer=_BufferStub(),
    )
    data = engine.resolve(query="save", role="button", mode="SAFE", category="normal")

    assert data["success"] is True
    assert data["blocked"] is False
    assert data["target"] is not None
    assert data["target"]["confidence"] >= 0.72
    assert data["target"]["center_xy"][0] > 0
    assert len(data["candidates"]) >= 1


def test_grounding_engine_blocks_when_query_missing():
    engine = GroundingEngine()
    engine.set_layers(
        ui_tree=_UITreeStub(),
        vision=_VisionStub(),
        perception=_PerceptionStub(),
        buffer=_BufferStub(),
    )
    data = engine.resolve(query="", mode="SAFE", category="normal")

    assert data["success"] is False
    assert data["blocked"] is True
    assert data["reason"] == "query_required"
