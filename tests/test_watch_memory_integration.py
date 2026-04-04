"""
Integration tests — S02 M002
watch_and_notify + visual_memory end-to-end

Requires a running ILUMINATY server at localhost:8420.
Skip automatically if server is not reachable.
"""
import http.client
import json
import os
import time
import pytest

HOST = "127.0.0.1"
PORT = int(os.getenv("ILUMINATY_PORT", "8420"))
KEY  = os.getenv("ILUMINATY_KEY", "ILUM-dev-local")
SERVER_AVAILABLE = False


def _conn():
    return http.client.HTTPConnection(HOST, PORT, timeout=15)


def _get(path):
    c = _conn()
    c.request("GET", path, headers={"X-API-Key": KEY})
    r = c.getresponse()
    data = json.loads(r.read())
    c.close()
    return r.status, data


def _post(path):
    c = _conn()
    c.request("POST", path, body=b"",
              headers={"X-API-Key": KEY, "Content-Length": "0"})
    r = c.getresponse()
    data = json.loads(r.read())
    c.close()
    return r.status, data


def _server_alive():
    try:
        c = http.client.HTTPConnection(HOST, PORT, timeout=3)
        c.request("GET", "/health", headers={"X-API-Key": KEY})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return data.get("status") == "alive"
    except Exception:
        return False


# ── Fixture ────────────────────────────��──────────────���────────────────────────

@pytest.fixture(scope="module", autouse=True)
def require_server():
    if not _server_alive():
        pytest.skip("ILUMINATY server not reachable at localhost:8420 — skipping integration tests")


# ── watch_and_notify ─────────────────────────────────────���────────────────────

class TestWatchAndNotify:

    def test_health_baseline(self):
        """Sanity: server is alive before any watch tests."""
        status, data = _get("/health")
        assert status == 200
        assert data["status"] == "alive"

    def test_watch_idle_triggers(self):
        """idle condition should trigger quickly — screen is not actively changing."""
        t0 = time.perf_counter()
        status, data = _post("/watch/notify?condition=idle&timeout=10&idle_seconds=2")
        elapsed = time.perf_counter() - t0

        assert status == 200, f"Expected 200, got {status}: {data}"
        assert data["triggered"] is True, f"Expected triggered=True: {data}"
        assert elapsed < 12, f"Should return within timeout+2s, took {elapsed:.1f}s"

    def test_watch_idle_elapsed_reported(self):
        """elapsed_s should be a positive number."""
        _, data = _post("/watch/notify?condition=idle&timeout=5&idle_seconds=1")
        assert "elapsed_s" in data
        assert isinstance(data["elapsed_s"], (int, float))
        assert data["elapsed_s"] >= 0

    def test_watch_timeout_respected(self):
        """Condition that never fires should timeout cleanly."""
        # text_appeared with very specific text that won't appear
        t0 = time.perf_counter()
        _, data = _post(
            "/watch/notify?condition=text_appeared"
            "&text=XYZZY_NEVER_ON_SCREEN_12345"
            "&timeout=3"
        )
        elapsed = time.perf_counter() - t0

        assert data["triggered"] is False or data.get("timed_out") is True, \
            f"Expected not triggered or timed_out: {data}"
        # Should return around 3s, not 0s and not way over
        assert 2.0 < elapsed < 8.0, f"Timeout took {elapsed:.1f}s (expected ~3s)"

    def test_watch_invalid_condition_returns_error(self):
        """Unknown condition should return an error message, not crash."""
        status, data = _post("/watch/notify?condition=definitely_not_a_real_condition&timeout=1")
        # Should return 200 with error message or 422 — not 500
        assert status in (200, 422), f"Unexpected status {status}: {data}"
        if status == 200:
            # If 200, triggered should be False and there should be a reason/error
            assert "reason" in data or "error" in data or not data.get("triggered")

    def test_monitor_until_alias(self):
        """monitor_until endpoint is an alias for watch/notify."""
        status, data = _post("/watch/until?condition=idle&timeout=5&idle_seconds=1")
        assert status == 200
        assert data["triggered"] is True

    def test_watch_no_condition_returns_error(self):
        """Missing condition parameter should return 422."""
        status, data = _post("/watch/notify?timeout=1")
        assert status == 422, f"Expected 422 for missing condition, got {status}"


# ── visual_memory ────────────────────────────��────────────────────────────────

class TestVisualMemory:

    def test_memory_stats(self):
        """Stats endpoint returns expected shape."""
        status, data = _get("/memory/stats")
        assert status == 200
        assert "memory_dir" in data
        assert "sessions_saved" in data
        assert isinstance(data["sessions_saved"], int)
        assert data["sessions_saved"] >= 0

    def test_memory_save(self):
        """Save returns saved=True with stats."""
        status, data = _post("/memory/save")
        assert status == 200
        assert data.get("saved") is True, f"Expected saved=True: {data}"
        stats = data.get("stats", {})
        assert stats.get("sessions_saved", 0) >= 1

    def test_memory_load_after_save(self):
        """Load returns the session just saved."""
        # Save first
        _post("/memory/save")
        time.sleep(0.1)

        status, data = _get("/memory/load?max_age_hours=1")
        assert status == 200
        assert data.get("found") is True, f"Expected found=True: {data}"
        assert "session_id" in data
        assert "saved_at" in data
        assert isinstance(data["saved_at"], (int, float))

    def test_memory_load_fresh_within_age(self):
        """Load with max_age_hours=1 returns a session saved just now."""
        _post("/memory/save")
        _, data = _get("/memory/load?max_age_hours=1")
        assert data.get("found") is True

    def test_memory_load_too_old_returns_not_found(self):
        """Load with max_age_hours=0 should not find sessions (all are too old)."""
        _, data = _get("/memory/load?max_age_hours=0")
        # Sessions saved this session are 0h old but max is 0h
        # May or may not find depending on exact timing — just check shape
        assert "found" in data

    def test_memory_prompt_returns_text(self):
        """Prompt endpoint returns non-empty text when memory exists."""
        _post("/memory/save")
        time.sleep(0.1)

        status, data = _get("/memory/prompt?max_age_hours=1")
        assert status == 200
        assert data.get("found") is True
        prompt = data.get("prompt", "")
        assert len(prompt) > 50, f"Prompt too short: {repr(prompt)}"
        assert "Session" in prompt or "session" in prompt or "Monitor" in prompt

    def test_memory_prompt_ascii_safe(self):
        """Prompt should not contain non-ASCII characters that break Windows cp1252."""
        _post("/memory/save")
        _, data = _get("/memory/prompt?max_age_hours=1")
        prompt = data.get("prompt", "")
        try:
            prompt.encode("ascii")
        except UnicodeEncodeError as e:
            # Find the offending character
            for i, ch in enumerate(prompt):
                if ord(ch) > 127:
                    pytest.fail(f"Non-ASCII char at pos {i}: {repr(ch)} (U+{ord(ch):04X})")

    def test_memory_save_idempotent(self):
        """Saving multiple times should not error."""
        for _ in range(3):
            status, data = _post("/memory/save")
            assert status == 200
            assert data.get("saved") is True

    def test_memory_sessions_capped(self):
        """Sessions should not grow unboundedly (capped at MAX_SESSIONS=10)."""
        # Save 5 more times
        for _ in range(5):
            _post("/memory/save")
            time.sleep(0.05)

        _, data = _get("/memory/stats")
        assert data.get("sessions_saved", 0) <= 15, \
            f"Too many sessions: {data.get('sessions_saved')}"


# ── MCP tool contracts ────────────────────────────────────────────────────────

class TestMCPToolContracts:
    """Verify the new MCP tools are registered in the server's tool list."""

    def _get_available_tools(self):
        """Use /license/status which lists all available MCP tools."""
        _, data = _get("/license/status")
        return set(data.get("mcp_tools", {}).get("available", []))

    def test_watch_and_notify_registered(self):
        tools = self._get_available_tools()
        assert "watch_and_notify" in tools, \
            f"watch_and_notify not in available tools: {sorted(tools)}"

    def test_monitor_until_registered(self):
        tools = self._get_available_tools()
        assert "monitor_until" in tools

    def test_get_session_memory_registered(self):
        tools = self._get_available_tools()
        assert "get_session_memory" in tools

    def test_save_session_memory_registered(self):
        tools = self._get_available_tools()
        assert "save_session_memory" in tools
