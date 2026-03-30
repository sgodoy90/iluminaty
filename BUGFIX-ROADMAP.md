# ILUMINATY - Bug Fix Roadmap
## Based on ANALISIS-ILUMINATY.docx (Score: 7.5/10)

---

## Priority 0: CRITICAL (3 bugs)

### BUG-001: relay.py - XOR encryption without nonce/IV
- **File**: `relay.py:68-72`
- **Problem**: XOR encryption reuses key. Same key+plaintext = same ciphertext. Vulnerable to pattern analysis.
- **Fix**: Replace XOR with AES-256-GCM using `cryptography` library. Add nonce per message.
- **Impact**: Anyone sniffing the relay traffic can analyze patterns.

### BUG-002: profile.py - Auto-save never executes
- **File**: `profile.py:176`
- **Problem**: Compares `now - last_updated` but `last_updated` is set on every `observe()` call. The condition `now - self.profile.last_updated > 300` is never true because it was just updated.
- **Fix**: Add separate `_last_save_time` field.
- **Impact**: User preferences lost on crash.

### BUG-003: dashboard.py - No authentication on dashboard
- **File**: `dashboard.py` + `server.py`
- **Problem**: Anyone on localhost:8420 can control capture, flush buffer, access screen.
- **Fix**: Add optional auth token check for dashboard access.
- **Impact**: Local apps can spy on screen capture.

---

## Priority 1: HIGH (6 bugs)

### BUG-004: security.py - Memory leak in rate limiter
- **File**: `security.py:121`
- **Problem**: `_windows` dict grows unbounded, then shrinks. Uses list per client.
- **Fix**: Use `deque(maxlen=max_rpm)` instead of list.

### BUG-005: server.py - Race condition in globals
- **File**: `server.py:46-61`
- **Problem**: 16 `Optional[T] = None` globals without synchronization during init.
- **Fix**: Use a single `ServerState` dataclass with threading.Lock.

### BUG-006: server.py - Config update without lock
- **File**: `server.py:220-238`
- **Problem**: Modifies `_capture.config` while capture thread reads it.
- **Fix**: Add lock to config updates.

### BUG-007: capture.py - Event creation in loop
- **File**: `capture.py:201-203`
- **Problem**: Creates `threading.Event()` on every iteration.
- **Fix**: Reuse single Event or use `time.sleep()`.

### BUG-008: main.py - Version hardcoded inconsistency
- **File**: `main.py:56` vs `__init__.py`
- **Problem**: main.py says '0.3.0', __init__.py says '0.5.0'.
- **Fix**: Single source of truth from `__init__.__version__`.

### BUG-009: adapters.py - Deprecated Anthropic SDK import
- **File**: `adapters.py:319`
- **Problem**: Uses old import path.
- **Fix**: Update to current SDK.

---

## Priority 2: MEDIUM (10 bugs)

### BUG-010: vision.py - OCR cache ignores region
### BUG-011: vision.py - OCR text silently truncated to 2000 chars
### BUG-012: smart_diff.py - img.crop() without bounds check
### BUG-013: watchdog.py - _last_fired mutated in dataclass, not thread-safe
### BUG-014: router.py - AI model costs hardcoded and outdated
### BUG-015: context.py - Workflow detection by simple keywords, fragile
### BUG-016: fusion.py - Base64 encoding every cycle, CPU waste
### BUG-017: mcp_server.py - API_BASE hardcoded, not configurable
### BUG-018: server.py - CORS allow_origins=['*'] too permissive
### BUG-019: security.py - Missing detection for connection strings, OAuth, private keys

---

## Execution Order

```
NOW:     BUG-001 → BUG-002 → BUG-003 → BUG-008 (criticals + version)
THEN:    BUG-004 → BUG-005 → BUG-006 → BUG-007 (memory + race conditions)
AFTER:   BUG-009 → BUG-010 through BUG-019 (medium priority)
```
