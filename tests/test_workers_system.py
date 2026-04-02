import time

from iluminaty.workers import WorkersSystem


def test_workers_monitor_digest_and_status():
    workers = WorkersSystem(enabled=True)
    world = {
        "tick_id": 12,
        "task_phase": "interaction",
        "active_surface": "brave :: chatgpt",
        "readiness": True,
        "uncertainty": 0.12,
        "risk_mode": "safe",
        "domain_pack": "general",
        "attention_targets": ["middle-center:0.8"],
    }

    workers.update_monitor_digest(
        monitor_id=2,
        tick_id=12,
        scene_state="interaction",
        scene_confidence=0.88,
        change_score=0.22,
        dominant_direction="none",
        window_info={"name": "brave", "window_title": "chat"},
        attention_targets=[{"row": 2, "col": 3, "intensity": 0.8}],
        world_snapshot=world,
        visual_facts=[{"kind": "text", "text": "hello"}],
        evidence_count=3,
        is_active=True,
    )
    workers.update_spatial_state(active_monitor_id=2, monitor_ids=[1, 2, 3])
    workers.update_fusion_world(world)

    status = workers.status()
    assert status["enabled"] is True
    assert status["active_monitor_id"] == 2
    assert status["monitor_count"] == 1
    assert status["workers"]["monitor"]["processed"] >= 1
    assert status["workers"]["fusion"]["processed"] >= 1
    assert status["workers"]["spatial"]["processed"] >= 1
    assert status["monitors"][0]["monitor_id"] == 2


def test_workers_action_arbiter_single_writer():
    workers = WorkersSystem(enabled=True, default_action_ttl_ms=400)
    claim_a = workers.claim_action(owner="a")
    claim_b = workers.claim_action(owner="b")

    assert claim_a["granted"] is True
    assert claim_b["granted"] is False
    assert claim_b["reason"] == "arbiter_busy"

    release_a = workers.release_action(owner="a", success=True, message="ok")
    claim_b2 = workers.claim_action(owner="b")
    assert release_a["released"] is True
    assert claim_b2["granted"] is True


def test_workers_arbiter_expires_stale_lease():
    workers = WorkersSystem(enabled=True, default_action_ttl_ms=250)
    claim_a = workers.claim_action(owner="a", ttl_ms=250)
    assert claim_a["granted"] is True
    time.sleep(0.30)
    claim_b = workers.claim_action(owner="b")
    assert claim_b["granted"] is True
