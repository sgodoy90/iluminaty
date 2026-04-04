# Contributing to ILUMINATY

## Ways to Contribute

- **Report bugs** — open an issue with steps to reproduce
- **Create domain packs** — the fastest way to add value for everyone
- **Improve tests** — especially integration tests for new conditions
- **Fix bugs** — check issues labeled `good first issue`

---

## Domain Packs

Domain packs are the easiest contribution. A domain pack is a `.toml` file that adapts ILUMINATY to a specific app — no Python code needed.

### What a domain pack does

- Tells ILUMINATY which text/URL/window signals indicate your app is active
- Adds custom `watch_and_notify` conditions specific to that app
- Documents expected screen regions for faster OCR
- Describes the semantic layout so the AI understands the UI

### Creating a domain pack

**1. Copy an example**

```bash
cp domain_packs/vscode.toml.example domain_packs/myapp.toml
```

**2. Fill in the sections**

```toml
[pack]
name        = "myapp"          # lowercase, no spaces
version     = "1.0.0"
description = "My app workflow"
author      = "your-github-handle"

[detection]
# How ILUMINATY knows your app is active
window_keywords = ["my app title"]
app_keywords    = ["myapp.exe"]
text_keywords   = ["unique text that appears in your app"]
min_signals     = 2             # require at least 2 of the above

[context]
description = """
Brief description of the app layout for the AI.
Where are the main panels? What do the colors mean?
What text appears in key areas?
"""

[states]
# Text patterns that indicate specific app states
ready    = ["ready", "connected", "online"]
loading  = ["loading", "please wait"]
error    = ["error", "failed", "disconnected"]

[[actions]]
name        = "my_action"
description = "What this action does"
find        = "Button Label"   # text to find with smart_locate
role        = "button"

[[watch_conditions]]
name        = "task_done"
description = "Task completed"
type        = "text_appeared"
text_match  = ["done", "complete", "success"]
```

**3. Test your pack**

```python
# With server running:
# 1. Place your .toml in domain_packs/
# 2. Open your app
# 3. In Claude: call get_spatial_context
# 4. Verify your pack is detected
```

**4. Submit a PR**

```bash
git checkout -b domain-pack/myapp
git add domain_packs/myapp.toml
git commit -m "domain-pack: myapp — brief description"
git push origin domain-pack/myapp
```

### Domain pack schema

| Field | Required | Description |
|---|---|---|
| `[pack].name` | Yes | Unique identifier (lowercase, hyphens OK) |
| `[pack].version` | Yes | Semver |
| `[pack].description` | Yes | One line |
| `[pack].author` | Yes | GitHub handle |
| `[detection]` | Yes | At least one detection signal |
| `[detection].min_signals` | No | Default: 2 |
| `[context].description` | Recommended | Layout description for AI |
| `[states]` | No | Text patterns for app states |
| `[[actions]]` | No | Named actions |
| `[[watch_conditions]]` | No | Custom wait conditions |

### Watch condition types

| Type | Description | Required fields |
|---|---|---|
| `text_appeared` | Text appeared on screen | `text_match` (list) |
| `text_disappeared` | Text left the screen | `text_match` (list) |
| `ocr_number_above` | Number on screen > threshold | `ocr_region` [x1,y1,x2,y2] |
| `ocr_number_below` | Number on screen < threshold | `ocr_region` [x1,y1,x2,y2] |
| `element_visible` | UI element with this text is visible | `element_text` |

---

## Reporting Bugs

Open an issue with:

1. **What happened** — exact error message or unexpected behavior
2. **Steps to reproduce** — minimal reproducible case
3. **Environment** — Windows version, Python version, number of monitors
4. **Logs** — server output with the error

For segfaults or crashes, run with:
```bash
python main.py start --port 8420 --fps 1 --api-key ILUM-dev-local
```
and include the full output.

---

## Running Tests

```bash
# Unit tests (no server needed)
pytest

# Integration tests (requires server running on :8420)
pytest tests/test_watch_memory_integration.py -v

# Stability test (60s stress test)
python tests/test_server_stability.py --quick
```

---

## Code Style

- Python 3.10+ type hints where practical
- Docstrings on public functions
- No new dependencies in `ipa/` beyond `numpy + pillow + imagehash`
- New MCP tools need: handler function, TOOLS entry, HANDLERS entry, licensing entry

---

## License

By contributing, you agree your contributions are licensed under MIT.
