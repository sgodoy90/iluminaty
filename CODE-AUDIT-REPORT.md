# ILUMINATY — Global Code Audit Report
**Date:** 2026-04-05 | **Auditors:** 3 parallel agents | **Scope:** Full codebase

---

## SECURITY FINDINGS

### CRITICAL

| ID | File:Line | Issue | Fix |
|----|-----------|-------|-----|
| C-1 | `server.py:186` | Auth bypass when no API key configured — all endpoints open | Reject if `_state.api_key` is None |
| C-2 | `server.py:2421` | WebSocket `/ws/stream` has zero authentication | Add token/key check before `ws.accept()` |
| C-3 | `start_dev.bat:3` | `ILUMINATY_NO_AUTH=1` hardcoded — kills all auth | Remove or restrict to loopback only |
| C-4 | `server.py:6107` | Terminal blocklist bypassable: `rmdir /s`, `powershell`, `wmic` not blocked | Switch to allowlist approach |

### HIGH

| ID | File:Line | Issue |
|----|-----------|-------|
| H-1 | `server.py` (50+ endpoints) | Endpoints missing `_check_auth()` even when key is set |
| H-2 | `server.py:2196` | API key exposed in `?token=` URL query param |
| H-3 | `mcp_server.py:3390` | Prompt injection scanner too narrow — unicode bypass, missing patterns |
| H-4 | `server.py:2146` | CORS missing DELETE method, dynamic origins |

### MEDIUM/LOW

| ID | Issue |
|----|-------|
| M-1 | Filesystem sandbox default `"."` allows reading entire project including `.env` |
| M-2 | `/files/write` content in URL query string (length limits + log exposure) |
| M-3 | `licensing.py` gate is no-op — misleads future devs |
| M-4 | Debug log captures OCR text that may contain passwords |
| M-5 | `cwd` in `/terminal/exec` not validated against sandbox |
| M-6 | No rate limiting on direct action/terminal endpoints |
| L-1 | `start.bat` echoes API key to stdout |
| L-3 | Injection warning is advisory only — not enforced |

---

## BUG FINDINGS

### CRITICAL

| ID | File:Line | Issue |
|----|-----------|-------|
| BUG-001 | `server.py:1884` | `result.success` accessed when `result` is None → AttributeError |
| BUG-002 | `mcp_server.py:2709` | `triple_click` always reports OK regardless of actual result |
| BUG-003 | `mcp_server.py:2752` | `hold_key` calls non-existent `/action/key_down` `/action/key_up` → always fake OK |
| BUG-004 | `mcp_server.py:2737` | `mouse_down`/`mouse_up` call non-existent endpoints |

### HIGH

| ID | File:Line | Issue |
|----|-----------|-------|
| BUG-005 | `perception.py:851` | `_get_monitor_state` dict mutation without lock → race condition |
| BUG-006 | `watch_engine.py:107` | Loop vars `seen_gate_ts`/`last_ocr` never updated → `text_disappeared` broken |
| BUG-007 | `server.py:4153` | `open_on_monitor` reuse logic inverted — steals window from wrong monitor |
| BUG-008 | `server.py:2444` | ZeroDivisionError when `fps=0` kills WebSocket silently |
| BUG-009 | `ocr_worker.py:294` | Old subprocess not stopped on respawn → zombie + leaked Queue |
| BUG-010 | `recording.py:198` | All frames buffered in RAM → OOM on long recordings |

### MEDIUM/LOW

| ID | File:Line | Issue |
|----|-----------|-------|
| BUG-011 | `perception.py:_check_window` | Multi-field read/write without lock |
| BUG-012 | `compressor.py:155` | No truncation check on corrupt IPA frame → ValueError crash |
| BUG-013 | `watch_engine.py:188` | `new_events` potentially undefined → NameError |
| BUG-014 | `visual_memory.py:206` | Two `time.time()` calls → duration inconsistency |
| BUG-015 | `server.py:4175` | `title_hint` ignored in window-reuse path |
| BUG-016 | `compressor.py:240` | Shape mismatch between bitmask and delta vectors |
| BUG-017 | `ocr_worker.py:78` | `result.boxes/scores` not guarded before zip |
| BUG-018 | `visual_memory.py:283` | Corrupt session files silently swallowed — no logging |
| BUG-019 | `perception.py:_cpu_throttle` | Throttle restore interval stale after second cycle |

---

## DEAD CODE / QUALITY FINDINGS

### Critical Dead Code

| Finding | Location | Action |
|---------|----------|--------|
| 23 unreachable MCP handlers (~426 lines, ~10% of mcp_server.py) | `mcp_server.py` | REMOVE |
| `iluminaty/spatial.py` — 230 lines, zero importers | `spatial.py` | REMOVE |
| `iluminaty/profile.py` — 219 lines, zero importers | `profile.py` | REMOVE |
| 5 tools in schema not in `ALL_MCP_TOOLS` | `licensing.py` | ADD |

### Redundancy

| Finding | Action |
|---------|--------|
| `see_screen` overlaps `see_now` (legacy, dead token logic) | REMOVE or document |
| Port `8420` hardcoded in 8 places across 5 files | Extract to `constants.py` |
| ~30 unused imports across 20 files | REMOVE |

### Quality

| Finding | Action |
|---------|--------|
| `_scan_prompt_injection` only in `see_now` — missing `see_region`, `what_changed` | ADD |
| `SyntaxWarning` in `grounding.py:304` — becomes error in Python 3.14+ | FIX |
| 12 functions >100 lines (worst: `init_server` 417, `_analyze_frame` 336) | REFACTOR |

### Test Gaps

| Missing | Action |
|---------|--------|
| Zero tests for `security.py` | ADD |
| Zero tests for `ring_buffer.py` | ADD |
| No test asserting `HANDLERS.keys() ⊆ ALL_MCP_TOOLS` | ADD |

---

## Priority Action Plan

**Fix now (Show HN blockers):**
1. BUG-003/004 — `hold_key`/`mouse_down`/`mouse_up` completely non-functional
2. BUG-002 — `triple_click` lies about success
3. C-2 — WebSocket auth
4. BUG-006 — `watch_engine` `text_disappeared` broken

**Fix before public launch:**
5. C-1 — Auth bypass
6. C-4 — Terminal command injection
7. BUG-005 — Race condition on monitor state
8. BUG-008 — WebSocket ZeroDivision
9. BUG-009 — OCR zombie subprocess

**Cleanup sprint:**
10. Remove dead modules (`spatial.py`, `profile.py`)
11. Remove 23 dead MCP handlers
12. Add missing tests
