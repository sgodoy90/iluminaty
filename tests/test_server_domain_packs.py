from fastapi.testclient import TestClient

from iluminaty import server


class _PerceptionDomainStub:
    def list_domain_packs(self):
        return {
            "packs": [{"name": "coding", "source": "builtin", "priority": 90}],
            "active": {"domain_pack": "coding", "domain_confidence": 0.8, "domain_source": "builtin"},
            "override": None,
        }

    def reload_domain_packs(self):
        return {"loaded": 0, "errors": [], "total": 1, "override": None}

    def set_domain_override(self, name):
        if str(name or "").strip().lower() == "unknown":
            return {"ok": False, "reason": "unknown_domain_pack"}
        if str(name or "").strip().lower() in {"auto", "", "none"}:
            return {"ok": True, "override": None, "reason": "auto"}
        return {"ok": True, "override": str(name).strip().lower(), "reason": "forced"}


def test_domain_pack_endpoints_list_reload_and_override():
    server._state.api_key = "test-key"
    server._state.perception = _PerceptionDomainStub()
    client = TestClient(server.app, headers={"x-api-key": "test-key"})

    listed = client.get("/domain-packs")
    assert listed.status_code == 200
    assert listed.json()["active"]["domain_pack"] == "coding"

    reloaded = client.post("/domain-packs/reload")
    assert reloaded.status_code == 200
    assert reloaded.json()["total"] == 1

    forced = client.post("/domain-packs/override", json={"name": "coding"})
    assert forced.status_code == 200
    assert forced.json()["override"] == "coding"

    bad = client.post("/domain-packs/override", json={"name": "unknown"})
    assert bad.status_code == 400
