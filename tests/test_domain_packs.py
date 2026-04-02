import json

from iluminaty.domain_packs import DomainPackRegistry
from iluminaty.world_state import WorldStateEngine


def test_builtin_domain_pack_selection_trading():
    engine = WorldStateEngine(horizon_seconds=90)
    snapshot = engine.update(
        scene_state="interaction",
        scene_confidence=0.92,
        window_title="BTCUSDT Chart - TradingView",
        app_name="TradingView",
        workflow="finance",
        monitor_id=1,
        attention_hot_zones=[{"row": 2, "col": 4, "intensity": 0.8}],
        recent_events=[{"type": "page_navigation"}],
        dominant_direction="none",
        visual_facts=[{"kind": "caption", "text": "order panel and candlestick chart", "confidence": 0.8}],
    )
    assert snapshot["domain_pack"] == "trading"
    assert snapshot["domain_confidence"] >= 0.34
    assert "do_action" in snapshot["affordances"]
    assert snapshot["domain_policy"]["max_staleness_ms"]["safe"] <= 700


def test_custom_domain_pack_is_loaded_and_selected(tmp_path):
    custom_dir = tmp_path / "domain_packs"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "medical_ops.json").write_text(
        json.dumps(
            {
                "name": "medical_ops",
                "description": "Hospital dashboard workflows",
                "priority": 98,
                "match": {
                    "workflows": ["medical_support"],
                    "apps": ["medidesk"],
                    "titles": ["patient", "triage"],
                },
                "affordances": ["do_action", "find_ui_element"],
                "attention_hints": ["vitals_panel", "patient_queue"],
                "staleness_policy": {"safe": 800, "hybrid": 700, "raw": 3000},
            }
        ),
        encoding="utf-8",
    )

    registry = DomainPackRegistry(custom_dir=str(custom_dir))
    packs = registry.list_packs()
    assert any(p.get("name") == "medical_ops" and p.get("source") == "custom" for p in packs)

    engine = WorldStateEngine(horizon_seconds=90, domain_registry=registry)
    snapshot = engine.update(
        scene_state="interaction",
        scene_confidence=0.95,
        window_title="Patient triage queue",
        app_name="MediDesk",
        workflow="medical_support",
        monitor_id=1,
        attention_hot_zones=[],
        recent_events=[{"type": "text_appeared"}],
        dominant_direction="none",
    )
    assert snapshot["domain_pack"] == "medical_ops"
    assert snapshot["domain_policy"]["max_staleness_ms"]["safe"] == 800


def test_domain_override_forces_selection():
    engine = WorldStateEngine(horizon_seconds=90)
    forced = engine.set_domain_override("coding")
    assert forced["ok"] is True

    snapshot = engine.update(
        scene_state="consuming",
        scene_confidence=0.6,
        window_title="Random browser page",
        app_name="Brave",
        workflow="unknown",
        monitor_id=1,
        attention_hot_zones=[],
        recent_events=[{"type": "window_change"}],
        dominant_direction="none",
    )
    assert snapshot["domain_pack"] == "coding"
    assert snapshot["domain_confidence"] >= 0.99

    auto = engine.set_domain_override("auto")
    assert auto["ok"] is True
    assert auto["override"] is None
