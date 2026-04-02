import iluminaty.mcp_server as mcp


def test_handle_workers_status_formats_output(monkeypatch):
    def _fake_get(path: str):
        assert path == "/workers/status"
        return {
            "enabled": True,
            "active_monitor_id": 2,
            "monitor_count": 2,
            "workers": {
                "monitor": {"processed": 12, "errors": 0, "avg_latency_ms": 0.7, "staleness_ms": 50},
            },
            "arbiter": {"owner": "executor-a", "denied_count": 1, "lease_remaining_ms": 400},
            "monitors": [
                {"monitor_id": 2, "scene_state": "interaction", "task_phase": "interaction", "readiness": True, "staleness_ms": 80},
            ],
        }

    monkeypatch.setattr(mcp, "_api_get", _fake_get)
    out = mcp.handle_workers_status({})
    assert isinstance(out, list)
    assert out
    text = out[0]["text"]
    assert "Workers Status" in text
    assert "Active monitor: 2" in text
    assert "Worker[monitor]" in text


def test_handle_workers_monitor_requires_monitor():
    out = mcp.handle_workers_monitor({})
    assert "monitor is required" in out[0]["text"]


def test_handle_workers_monitor_calls_api(monkeypatch):
    def _fake_get(path: str):
        assert path == "/workers/monitor/3"
        return {
            "monitor_id": 3,
            "tick_id": 44,
            "scene_state": "loading",
            "scene_confidence": 0.66,
            "change_score": 0.3,
            "dominant_direction": "down",
            "task_phase": "loading",
            "active_surface": "brave :: page",
            "readiness": False,
            "uncertainty": 0.4,
            "staleness_ms": 123,
            "attention_targets": ["r2c3:0.9"],
            "visual_facts": [{"kind": "text"}],
        }

    monkeypatch.setattr(mcp, "_api_get", _fake_get)
    out = mcp.handle_workers_monitor({"monitor": 3})
    text = out[0]["text"]
    assert "Worker Monitor 3" in text
    assert "Scene: loading" in text
    assert "Staleness: 123ms" in text


def test_handle_workers_claim_release_call_api(monkeypatch):
    calls = []

    def _fake_post(path: str, body=None):
        calls.append((path, body))
        if path == "/workers/action/claim":
            return {"granted": True, "held_by": "mcp-executor", "ttl_ms": 2000, "reason": "acquired"}
        if path == "/workers/action/release":
            return {"released": True, "success": True, "message": "ok"}
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(mcp, "_api_post", _fake_post)
    claim = mcp.handle_workers_claim_action({"owner": "tester", "ttl_ms": 2000, "force": False})
    release = mcp.handle_workers_release_action({"owner": "tester", "success": True, "message": "ok"})

    assert "GRANTED" in claim[0]["text"]
    assert "OK" in release[0]["text"]
    assert calls[0][0] == "/workers/action/claim"
    assert calls[0][1]["owner"] == "tester"
    assert calls[1][0] == "/workers/action/release"
    assert calls[1][1]["owner"] == "tester"


def test_handle_workers_schedule(monkeypatch):
    def _fake_get(path: str):
        assert path == "/workers/schedule"
        return {
            "active_monitor_id": 1,
            "recommended_monitor_id": 2,
            "reason": "test",
            "budgets": [{"monitor_id": 2, "share": 0.7, "score": 1.3}],
        }

    monkeypatch.setattr(mcp, "_api_get", _fake_get)
    out = mcp.handle_workers_schedule({})
    text = out[0]["text"]
    assert "Workers Schedule" in text
    assert "Recommended monitor: 2" in text


def test_handle_workers_set_subgoal_and_route(monkeypatch):
    calls = []

    def _fake_post(path: str, body=None):
        calls.append((path, body))
        if path == "/workers/subgoals":
            return {"subgoal_id": "sg_1", "monitor_id": body["monitor_id"], "goal": body["goal"]}
        if path == "/workers/route":
            return {"monitor_id": 2, "score": 0.9}
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(mcp, "_api_post", _fake_post)
    set_out = mcp.handle_workers_set_subgoal({"monitor_id": 2, "goal": "watch trade"})
    route_out = mcp.handle_workers_route({"query": "trade"})

    assert "sg_1" in set_out[0]["text"]
    assert "monitor 2" in route_out[0]["text"]
    assert calls[0][0] == "/workers/subgoals"
    assert calls[1][0] == "/workers/route"
