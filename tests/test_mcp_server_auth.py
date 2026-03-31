import importlib

import iluminaty.mcp_server as mcp


class _DummyResponse:
    def __init__(self, payload: bytes = b"{}"):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_mcp_forwards_api_key_header(monkeypatch):
    monkeypatch.setenv("ILUMINATY_API_KEY", "abc123")
    module = importlib.reload(mcp)

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return _DummyResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    module._api_get("/health")
    assert captured["headers"].get("X-api-key") == "abc123"

    module._api_post("/tokens/reset")
    assert captured["headers"].get("X-api-key") == "abc123"


def test_mcp_registers_v21_tools():
    tool_names = {t["name"] for t in mcp.TOOLS}
    for name in {"vision_query", "window_minimize", "window_maximize", "window_close"}:
        assert name in tool_names
        assert name in mcp.ALL_MCP_TOOLS
        assert name in mcp.HANDLERS
