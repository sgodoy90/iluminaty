from iluminaty.workers import WorkersSystem


def _seed_monitor(workers: WorkersSystem, monitor_id: int, is_active: bool, change_score: float, title: str):
    workers.update_monitor_digest(
        monitor_id=monitor_id,
        tick_id=10 + monitor_id,
        scene_state="interaction",
        scene_confidence=0.8,
        change_score=change_score,
        dominant_direction="none",
        window_info={"name": "brave", "window_title": title},
        attention_targets=[{"row": 2, "col": 2, "intensity": 0.7}],
        world_snapshot={
            "tick_id": 10 + monitor_id,
            "task_phase": "interaction",
            "active_surface": f"brave :: {title}",
            "readiness": True,
            "uncertainty": 0.2,
            "risk_mode": "safe",
            "domain_pack": "general",
        },
        visual_facts=[],
        evidence_count=1,
        is_active=is_active,
    )


def test_workers_schedule_and_route_query():
    workers = WorkersSystem(enabled=True)
    _seed_monitor(workers, monitor_id=1, is_active=True, change_score=0.2, title="docs")
    _seed_monitor(workers, monitor_id=2, is_active=False, change_score=0.6, title="trade chart")

    schedule = workers.get_schedule()
    assert schedule["recommended_monitor_id"] in (1, 2)
    assert len(schedule["budgets"]) >= 2

    route = workers.route_query("trade chart")
    assert route["monitor_id"] == 2


def test_workers_subgoal_influences_schedule():
    workers = WorkersSystem(enabled=True)
    _seed_monitor(workers, monitor_id=1, is_active=True, change_score=0.2, title="editor")
    _seed_monitor(workers, monitor_id=2, is_active=False, change_score=0.2, title="browser")

    baseline = workers.get_schedule()
    sg = workers.set_subgoal(
        monitor_id=2,
        goal="watch trading entry",
        priority=1.0,
        risk="high",
    )
    assert sg["monitor_id"] == 2

    boosted = workers.get_schedule()
    assert boosted["recommended_monitor_id"] == 2

    cleared = workers.clear_subgoal(sg["subgoal_id"], completed=True)
    assert cleared["ok"] is True
    after = workers.get_schedule()
    assert after["recommended_monitor_id"] in (1, 2)
