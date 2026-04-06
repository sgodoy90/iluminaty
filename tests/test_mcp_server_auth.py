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
    # Core 20 tools — M003 S03 final set
    required = {
        "see_now", "see_region", "what_changed", "verify_action",
        "get_spatial_context", "map_environment",
        "watch_and_notify",
        "act_on", "act",
        "uia_find_all", "uia_focused", "find_on_screen",
        "list_windows", "focus_window",
        "open_path", "run_command", "os_dialog_resolve",
        "read_file", "write_file",
        "screen_status",
    }
    for name in required:
        assert name in tool_names, f"Missing tool: {name}"
        assert name in mcp.ALL_MCP_TOOLS, f"Tool not in ALL_MCP_TOOLS: {name}"
        assert name in mcp.HANDLERS, f"Tool has no handler: {name}"
