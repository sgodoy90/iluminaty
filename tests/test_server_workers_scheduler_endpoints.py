from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionWorkersSchedulerStub:
    def __init__(self):
        self.subgoals = {}

    def get_workers_schedule(self):
        return {
            "timestamp_ms": 1,
            "active_monitor_id": 1,
            "recommended_monitor_id": 2,
            "budgets": [{"monitor_id": 2, "share": 0.7, "score": 1.2}],
            "reason": "test",
        }

    def list_worker_subgoals(self, include_completed: bool = False):
        _ = include_completed
        return list(self.subgoals.values())

    def set_worker_subgoal(self, **kwargs):
        item = {
            "subgoal_id": "sg_test_1",
            "monitor_id": int(kwargs.get("monitor_id", 1)),
            "goal": str(kwargs.get("goal", "")),
            "priority": float(kwargs.get("priority", 0.5)),
        }
        self.subgoals[item["subgoal_id"]] = item
        return item

    def clear_worker_subgoal(self, subgoal_id: str, completed: bool = True):
        if subgoal_id not in self.subgoals:
            return {"ok": False, "reason": "subgoal_not_found"}
        item = self.subgoals.pop(subgoal_id)
        return {"ok": True, "subgoal": {**item, "completed": bool(completed)}}

    def route_worker_query(self, query: str, preferred_monitor_id=None):
        _ = preferred_monitor_id
        return {"query": query, "monitor_id": 2, "score": 0.91}


def test_workers_scheduler_endpoints():
    original_api_key = server._state.api_key
    original_perception = server._state.perception
    try:
        server._state.api_key = None
        server._state.perception = _PerceptionWorkersSchedulerStub()
        client = TestClient(server.app)

        schedule = client.get("/workers/schedule")
        assert schedule.status_code == 200
        assert schedule.json()["recommended_monitor_id"] == 2

        set_resp = client.post("/workers/subgoals", json={"monitor_id": 2, "goal": "watch trade"})
        assert set_resp.status_code == 200
        assert set_resp.json()["monitor_id"] == 2

        listed = client.get("/workers/subgoals")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        routed = client.post("/workers/route", json={"query": "trade"})
        assert routed.status_code == 200
        assert routed.json()["monitor_id"] == 2

        cleared = client.delete("/workers/subgoals/sg_test_1")
        assert cleared.status_code == 200
        assert cleared.json()["ok"] is True
    finally:
        server._state.api_key = original_api_key
        server._state.perception = original_perception
