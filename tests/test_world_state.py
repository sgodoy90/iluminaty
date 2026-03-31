from iluminaty.world_state import WorldStateEngine


def test_world_state_readiness_and_risk_mode():
    engine = WorldStateEngine(horizon_seconds=90)
    engine.set_risk_mode("raw")

    snapshot = engine.update(
        scene_state="interaction",
        scene_confidence=0.95,
        window_title="main.py - VS Code",
        app_name="Code",
        workflow="coding",
        monitor_id=1,
        attention_hot_zones=[{"row": 1, "col": 2, "intensity": 0.9}],
        recent_events=[{"type": "window_change"}],
        dominant_direction="none",
    )

    assert snapshot["readiness"] is True
    assert snapshot["risk_mode"] == "raw"
    assert snapshot["task_phase"] in {"interaction", "editing", "navigation", "unknown"}
    assert snapshot["attention_targets"]


def test_world_state_trace_and_action_feedback():
    engine = WorldStateEngine(horizon_seconds=90)

    engine.update(
        scene_state="loading",
        scene_confidence=0.2,
        window_title="Loading...",
        app_name="Browser",
        workflow="browsing",
        monitor_id=1,
        attention_hot_zones=[],
        recent_events=[],
        dominant_direction="down",
    )
    engine.note_action("click", success=False, message="button not ready")

    trace = engine.get_trace(seconds=90)
    assert len(trace) >= 2
    assert any(item["boundary_reason"] == "action_feedback" for item in trace)
    assert any("button not ready" in item["summary"] for item in trace)
