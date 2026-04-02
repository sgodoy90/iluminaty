import importlib
import http.client
import io

import iluminaty.mcp_server as mcp


class _DummyHTTPResponse:
    """Minimal http.client.HTTPResponse stub."""

    def __init__(self, payload: bytes = b"{}"):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _DummyHTTPConnection:
    """Stub for http.client.HTTPConnection that records the last request."""

    def __init__(self, *args, **kwargs):
        self.last_method: str = ""
        self.last_path: str = ""
        self.last_headers: dict = {}
        self._resp = _DummyHTTPResponse()

    def request(self, method, path, body=None, headers=None):
        self.last_method = method
        self.last_path = path
        self.last_headers = dict(headers or {})

    def getresponse(self) -> _DummyHTTPResponse:
        return self._resp


def test_mcp_forwards_api_key_header(monkeypatch):
    monkeypatch.setenv("ILUMINATY_API_KEY", "abc123")
    module = importlib.reload(mcp)

    dummy_conn = _DummyHTTPConnection()

    # Reset the cached connection so _get_conn() creates a fresh one
    module._CONN["conn"] = None
    module._CONN["ts"] = 0.0

    monkeypatch.setattr(module.http.client, "HTTPConnection", lambda *a, **kw: dummy_conn)

    module._api_get("/health")
    assert dummy_conn.last_headers.get("x-api-key") == "abc123", (
        f"Expected x-api-key header, got: {dummy_conn.last_headers}"
    )

    module._api_post("/tokens/reset")
    assert dummy_conn.last_headers.get("x-api-key") == "abc123"


def test_mcp_registers_v21_tools():
    tool_names = {t["name"] for t in mcp.TOOLS}
    for name in {
        "vision_query",
        "window_minimize",
        "window_maximize",
        "window_close",
        "workers_status",
        "workers_monitor",
        "workers_claim_action",
        "workers_release_action",
        "workers_schedule",
        "workers_set_subgoal",
        "workers_clear_subgoal",
        "workers_route",
        "behavior_stats",
        "behavior_recent",
        "behavior_suggest",
        "runtime_profile",
        "audio_interrupt_status",
        "audio_interrupt_ack",
    }:
        assert name in tool_names
        assert name in mcp.ALL_MCP_TOOLS
        assert name in mcp.HANDLERS
