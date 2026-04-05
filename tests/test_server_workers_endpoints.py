from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionWorkersStub:
    def __init__(self):
        self.claims = 0
        self.releases = 0

    def get_workers_status(self):
        return {"enabled": True, "monitor_count": 2, "active_monitor_id": 1}

    def get_worker_monitor(self, monitor_id: int):
        if int(monitor_id) == 1:
            return {"monitor_id": 1, "scene_state": "interaction"}
        return None

    def claim_action_lease(self, owner: str, ttl_ms=None, force: bool = False):
        self.claims += 1
        return {"granted": True, "owner": owner, "ttl_ms": ttl_ms or 2500, "force": bool(force)}

    def release_action_lease(self, owner: str, success: bool, message: str = ""):
        self.releases += 1
        return {"released": True, "owner": owner, "success": bool(success), "message": message}


def test_workers_status_and_monitor_endpoints():
    original_perception = server._state.perception
    original_api_key = server._state.api_key
    try:
        server._state.api_key = "test-key"
        server._state.perception = _PerceptionWorkersStub()
        client = TestClient(server.app, headers={"x-api-key": "test-key"})

        status = client.get("/workers/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is True

        monitor_ok = client.get("/workers/monitor/1")
        assert monitor_ok.status_code == 200
        assert monitor_ok.json()["monitor_id"] == 1

        monitor_missing = client.get("/workers/monitor/9")
        assert monitor_missing.status_code == 404
    finally:
        server._state.perception = original_perception
        server._state.api_key = original_api_key


def test_workers_action_claim_release_endpoints():
    original_perception = server._state.perception
    original_api_key = server._state.api_key
    try:
        stub = _PerceptionWorkersStub()
        server._state.api_key = "test-key"
        server._state.perception = stub
        client = TestClient(server.app, headers={"x-api-key": "test-key"})

        claim = client.post("/workers/action/claim", json={"owner": "tester", "ttl_ms": 1200})
        assert claim.status_code == 200
        assert claim.json()["granted"] is True

        release = client.post("/workers/action/release", json={"owner": "tester", "success": True})
        assert release.status_code == 200
        assert release.json()["released"] is True
        assert stub.claims == 1
        assert stub.releases == 1
    finally:
        server._state.perception = original_perception
        server._state.api_key = original_api_key
