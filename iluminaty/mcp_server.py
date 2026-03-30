"""
ILUMINATY - MCP Server
========================
Model Context Protocol server para que coding agents
(Claude Code, Cursor, etc.) puedan "ver" la pantalla.

La IA llama a estas tools cuando necesita ver:
- see_screen    → snapshot enriquecido actual
- see_region    → zoom a una region especifica
- see_changes   → que cambio en los ultimos N segundos
- annotate      → marcar algo en la pantalla
- read_text     → OCR de la pantalla o region

Uso:
  claude mcp add iluminaty -- python -m iluminaty.mcp

O como servidor standalone:
  python -m iluminaty.mcp --port 8421
"""

import json
import sys
import base64
import time
import urllib.request
import urllib.parse

import os

# ILUMINATY API base URL - configurable via env var
API_BASE = os.environ.get("ILUMINATY_API_URL", "http://127.0.0.1:8420")


def _api_get(path: str) -> dict:
    """GET request to ILUMINATY API."""
    url = API_BASE + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _api_post(path: str, body: dict | None = None) -> dict:
    """POST request to ILUMINATY API."""
    url = API_BASE + path
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, method="POST", data=data,
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


# ─── MCP Tool Definitions ───

TOOLS = [
    {
        "name": "see_screen",
        "description": (
            "See what is currently on the user's screen in real-time. "
            "Returns an enriched snapshot with: the screen image, OCR text "
            "of visible content, active window info, and a structured prompt. "
            "Use this when you need to see what the user sees right now."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_image": {
                    "type": "boolean",
                    "description": "Include the screen image as base64 (default true)",
                    "default": True,
                },
                "ocr": {
                    "type": "boolean",
                    "description": "Run OCR to extract visible text (default false, slower)",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "see_changes",
        "description": (
            "See what changed on screen in the last N seconds. "
            "Returns multiple frames showing the progression. "
            "Use this when the user says 'what just happened' or "
            "'did you see that'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "How many seconds back to look (default 10)",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "annotate_screen",
        "description": (
            "Draw an annotation on the screen to mark an area for discussion. "
            "Types: rect (rectangle), circle, arrow, text. "
            "Use this when you want to point at something specific."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["rect", "circle", "arrow", "text"],
                    "description": "Annotation type",
                },
                "x": {"type": "integer", "description": "X position"},
                "y": {"type": "integer", "description": "Y position"},
                "width": {"type": "integer", "description": "Width", "default": 100},
                "height": {"type": "integer", "description": "Height", "default": 100},
                "color": {"type": "string", "description": "Color hex", "default": "#FF0000"},
                "text": {"type": "string", "description": "Text for text annotations", "default": ""},
            },
            "required": ["type", "x", "y"],
        },
    },
    {
        "name": "read_screen_text",
        "description": (
            "Read all visible text on the screen using OCR. "
            "Optionally read only a specific region. "
            "Use this when you need to read text that is on screen."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region_x": {"type": "integer", "description": "Region X (optional)"},
                "region_y": {"type": "integer", "description": "Region Y (optional)"},
                "region_w": {"type": "integer", "description": "Region width (optional)"},
                "region_h": {"type": "integer", "description": "Region height (optional)"},
            },
        },
    },
    {
        "name": "screen_status",
        "description": (
            "Get ILUMINATY system status: buffer stats, capture state, "
            "memory usage, FPS, active window, workflow, audio level."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_context",
        "description": (
            "Get the user's current context: what app they're using, "
            "what workflow they're in (coding, browsing, meeting, etc.), "
            "how focused they are, and how long they've been at it. "
            "Use this to understand what the user is doing before helping them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_audio_level",
        "description": (
            "Get the current audio level and whether speech is detected. "
            "Use this to know if the user is talking or in a call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # ─── v1.0: Computer Use Tools ───
    {
        "name": "do_action",
        "description": (
            "Execute an action on the user's computer using natural language. "
            "Examples: 'save the file', 'open Chrome', 'click the Submit button', "
            "'type hello world', 'scroll down', 'copy', 'paste'. "
            "The AI interprets the instruction, resolves the best method (API > keyboard > UI tree > vision), "
            "verifies the result, and auto-recovers on failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction (e.g. 'save the file', 'click Submit')",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "click_element",
        "description": (
            "Click on a UI element by name using the accessibility tree. "
            "No coordinates needed - finds the element automatically. "
            "Example: click_element('Save') clicks the Save button."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Element name or label"},
                "role": {"type": "string", "description": "Element role (button, textfield, etc)", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type text using the keyboard. Supports unicode. "
            "The text is typed at the current cursor position."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command and return the output. "
            "Example: 'npm test', 'python script.py', 'git status'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default 30)", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_windows",
        "description": (
            "List all visible windows on the desktop with their titles, positions, and sizes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "find_ui_element",
        "description": (
            "Find a UI element on screen using the accessibility tree. "
            "Returns element info including position, size, and state. "
            "Use this before clicking to verify the element exists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Element name to search for"},
                "role": {"type": "string", "description": "Element role filter (button, textfield, etc)", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file from the filesystem (sandboxed). "
            "Returns the file content as text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file (sandboxed, auto-backup). "
            "Creates parent directories if needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_clipboard",
        "description": "Read the current clipboard content.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "agent_status",
        "description": (
            "Get the full agent status: actions enabled, safety state, "
            "autonomy level, available capabilities, and recent action log."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ─── Tool Handlers ───

def handle_see_screen(args: dict) -> dict:
    include_image = args.get("include_image", True)
    ocr = args.get("ocr", False)
    data = _api_get(f"/vision/snapshot?ocr={str(ocr).lower()}&include_image={str(include_image).lower()}")

    result = {"type": "text", "text": data.get("ai_prompt", "")}

    # If image is included, add it as image content
    if include_image and "image_base64" in data:
        return [
            {"type": "text", "text": data["ai_prompt"]},
            {
                "type": "image",
                "data": data["image_base64"],
                "mimeType": "image/webp",
            },
        ]

    return [result]


def handle_see_changes(args: dict) -> dict:
    seconds = args.get("seconds", 10)
    data = _api_get(f"/frames?seconds={seconds}")
    count = data.get("count", 0)
    frames = data.get("frames", [])

    summary_lines = [
        f"## Screen Changes (last {seconds}s)",
        f"**Frames captured**: {count}",
        "",
    ]
    for i, f in enumerate(frames):
        summary_lines.append(
            f"- Frame {i+1}: {f['timestamp_iso']} | "
            f"{f['size_bytes']}B | change:{f['change_score']}"
        )

    return [{"type": "text", "text": "\n".join(summary_lines)}]


def handle_annotate(args: dict) -> dict:
    ann_type = args.get("type", "rect")
    x = args.get("x", 0)
    y = args.get("y", 0)
    w = args.get("width", 100)
    h = args.get("height", 100)
    color = args.get("color", "#FF0000")
    text = args.get("text", "")

    params = f"type={ann_type}&x={x}&y={y}&width={w}&height={h}&color={color}&text={text}"
    data = _api_post(f"/annotations/add?{params}")

    return [{"type": "text", "text": f"Annotation added: {ann_type} at ({x},{y}) id={data.get('id', '?')}"}]


def handle_read_text(args: dict) -> dict:
    params = []
    for key in ["region_x", "region_y", "region_w", "region_h"]:
        if key in args and args[key] is not None:
            params.append(f"{key}={args[key]}")

    query = "&".join(params) if params else ""
    path = f"/vision/ocr?{query}" if query else "/vision/ocr"
    data = _api_get(path)

    text = data.get("text", "")
    if not text:
        return [{"type": "text", "text": "No readable text found on screen. OCR may not be available (install tesseract)."}]

    return [{"type": "text", "text": f"## Screen Text (OCR)\n```\n{text[:3000]}\n```\nConfidence: {data.get('confidence', 0)}%"}]


def handle_status(args: dict) -> dict:
    stats = _api_get("/buffer/stats")
    window = _api_get("/vision/window")

    lines = [
        "## ILUMINATY Status",
        f"- Capture: {'running' if stats.get('capture_running') else 'stopped'}",
        f"- Buffer: {stats.get('slots_used')}/{stats.get('slots_max')} slots",
        f"- RAM: {stats.get('memory_mb')} MB",
        f"- FPS: {stats.get('current_fps')}",
        f"- Frames captured: {stats.get('total_frames_captured')}",
        f"- Frames dropped (no change): {stats.get('frames_dropped_no_change')}",
        f"- Active window: {window.get('title', 'unknown')}",
    ]
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_context(args: dict) -> dict:
    try:
        ctx = _api_get("/context/state")
        lines = [
            "## User Context",
            f"- Workflow: **{ctx.get('workflow', 'unknown')}** (confidence: {ctx.get('confidence', 0)})",
            f"- App: {ctx.get('app', 'unknown')}",
            f"- Window: {ctx.get('title', '')[:80]}",
            f"- Focus: {'HIGH' if ctx.get('is_focused') else 'LOW'} ({ctx.get('switches_5min', 0)} app switches in 5min)",
            f"- Time in workflow: {ctx.get('time_in_workflow_seconds', 0):.0f}s",
            "",
            ctx.get("summary", ""),
        ]
        return [{"type": "text", "text": "\n".join(lines)}]
    except Exception:
        return [{"type": "text", "text": "Context engine not available."}]


def handle_audio_level(args: dict) -> dict:
    try:
        level = _api_get("/audio/level")
        speech = "YES - user is speaking" if level.get("is_speech") else "No speech detected"
        return [{"type": "text", "text": f"Audio level: {level.get('level', 0):.3f} | Speech: {speech}"}]
    except Exception:
        return [{"type": "text", "text": "Audio not enabled. Start with --audio mic"}]


# ─── v1.0: Computer Use Handlers ───

def handle_do_action(args: dict) -> list:
    instruction = args.get("instruction", "")
    if not instruction:
        return [{"type": "text", "text": "Error: instruction is required"}]
    data = _api_post(f"/agent/do?instruction={urllib.parse.quote(instruction)}")
    intent = data.get("intent", {})
    result = data.get("result", {})
    verification = data.get("verification")
    recovery = data.get("recovery")

    lines = [
        f"## Action: {intent.get('action', 'unknown')}",
        f"**Intent**: {instruction} → {intent.get('action')} (confidence: {intent.get('confidence', 0)})",
        f"**Result**: {'SUCCESS' if result.get('success') else 'FAILED'} via {result.get('method_used', 'none')} ({result.get('total_ms', 0):.0f}ms)",
        f"**Message**: {result.get('message', '')}",
    ]
    if verification:
        lines.append(f"**Verified**: {'YES' if verification.get('verified') else 'NO'} ({verification.get('method', '')})")
    if recovery:
        lines.append(f"**Recovery**: {'Recovered' if recovery.get('recovered') else 'Failed'} - {recovery.get('final_message', '')}")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_click_element(args: dict) -> list:
    name = args.get("name", "")
    role = args.get("role", "")
    query = f"name={urllib.parse.quote(name)}"
    if role:
        query += f"&role={urllib.parse.quote(role)}"
    data = _api_post(f"/ui/click?{query}")
    return [{"type": "text", "text": f"Click element '{name}': {'SUCCESS' if data.get('success') else 'FAILED'} - {data.get('message', '')}"}]


def handle_type_text(args: dict) -> list:
    text = args.get("text", "")
    data = _api_post(f"/action/type?text={urllib.parse.quote(text)}")
    return [{"type": "text", "text": f"Typed {len(text)} chars: {'SUCCESS' if data.get('success') else 'FAILED'}"}]


def handle_run_command(args: dict) -> list:
    cmd = args.get("command", "")
    timeout = args.get("timeout", 30)
    data = _api_post(f"/terminal/exec?cmd={urllib.parse.quote(cmd)}&timeout={timeout}")
    lines = [
        f"## Command: `{cmd}`",
        f"**Status**: {'SUCCESS' if data.get('success') else 'FAILED'} (exit: {data.get('return_code', '?')}, {data.get('duration_ms', 0):.0f}ms)",
    ]
    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")
    if stdout:
        lines.append(f"\n**stdout**:\n```\n{stdout[:3000]}\n```")
    if stderr:
        lines.append(f"\n**stderr**:\n```\n{stderr[:1000]}\n```")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_list_windows(args: dict) -> list:
    data = _api_get("/windows/list")
    windows = data.get("windows", [])
    lines = [f"## Windows ({len(windows)})"]
    for w in windows[:30]:
        lines.append(f"- **{w.get('title', '?')[:60]}** (pid:{w.get('pid')}, {w.get('width')}x{w.get('height')})")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_find_ui_element(args: dict) -> list:
    name = args.get("name", "")
    role = args.get("role", "")
    query = f"name={urllib.parse.quote(name)}"
    if role:
        query += f"&role={urllib.parse.quote(role)}"
    data = _api_get(f"/ui/find?{query}")
    el = data.get("element")
    if el:
        return [{"type": "text", "text": f"Found: **{el.get('name')}** ({el.get('role')}) at ({el.get('x')},{el.get('y')}) {el.get('width')}x{el.get('height')} enabled={el.get('is_enabled')}"}]
    return [{"type": "text", "text": f"Element '{name}' not found on screen."}]


def handle_read_file(args: dict) -> list:
    path = args.get("path", "")
    data = _api_get(f"/files/read?path={urllib.parse.quote(path)}")
    if data.get("success"):
        content = data.get("content", "")
        return [{"type": "text", "text": f"## {path} ({data.get('lines', 0)} lines, {data.get('size', 0)}B)\n```\n{content[:5000]}\n```"}]
    return [{"type": "text", "text": f"Failed to read {path}: {data.get('error', 'unknown')}"}]


def handle_write_file(args: dict) -> list:
    path = args.get("path", "")
    content = args.get("content", "")
    data = _api_post(f"/files/write?path={urllib.parse.quote(path)}", body={"content": content})
    if data.get("success"):
        return [{"type": "text", "text": f"Written {data.get('size', 0)}B to {path}"}]
    return [{"type": "text", "text": f"Failed to write {path}: {data.get('error', 'unknown')}"}]


def handle_get_clipboard(args: dict) -> list:
    data = _api_get("/clipboard/read")
    text = data.get("text", "")
    return [{"type": "text", "text": f"Clipboard ({len(text)} chars):\n```\n{text[:2000]}\n```" if text else "Clipboard is empty."}]


def handle_agent_status(args: dict) -> list:
    data = _api_get("/agent/status")
    lines = ["## Agent Status"]
    for section, info in data.items():
        if isinstance(info, dict):
            lines.append(f"\n**{section}**:")
            for k, v in list(info.items())[:10]:
                lines.append(f"  - {k}: {v}")
    return [{"type": "text", "text": "\n".join(lines)}]


HANDLERS = {
    "see_screen": handle_see_screen,
    "see_changes": handle_see_changes,
    "annotate_screen": handle_annotate,
    "read_screen_text": handle_read_text,
    "screen_status": handle_status,
    "get_context": handle_context,
    "get_audio_level": handle_audio_level,
    # v1.0: Computer Use
    "do_action": handle_do_action,
    "click_element": handle_click_element,
    "type_text": handle_type_text,
    "run_command": handle_run_command,
    "list_windows": handle_list_windows,
    "find_ui_element": handle_find_ui_element,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "get_clipboard": handle_get_clipboard,
    "agent_status": handle_agent_status,
}


# ─── MCP Protocol (stdio) ───

def run_mcp_stdio():
    """
    Run as MCP server over stdio.
    Reads JSON-RPC from stdin, writes to stdout.
    """
    import sys

    def send(msg: dict):
        data = json.dumps(msg)
        sys.stdout.write(f"Content-Length: {len(data.encode('utf-8'))}\r\n\r\n{data}")
        sys.stdout.flush()

    def read_message() -> dict:
        # Read headers
        headers = {}
        while True:
            line = sys.stdin.readline()
            if line == "\r\n" or line == "\n" or line == "":
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip()] = val.strip()

        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return {}

        body = sys.stdin.read(content_length)
        return json.loads(body)

    # MCP handshake loop
    while True:
        try:
            msg = read_message()
            if not msg:
                continue

            method = msg.get("method", "")
            msg_id = msg.get("id")

            if method == "initialize":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "iluminaty",
                            "version": "1.0.0",
                        },
                    },
                })

            elif method == "notifications/initialized":
                pass  # No response needed

            elif method == "tools/list":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": TOOLS},
                })

            elif method == "tools/call":
                tool_name = msg.get("params", {}).get("name", "")
                tool_args = msg.get("params", {}).get("arguments", {})

                handler = HANDLERS.get(tool_name)
                if handler:
                    try:
                        content = handler(tool_args)
                        send({
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {"content": content},
                        })
                    except Exception as e:
                        send({
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "content": [{"type": "text", "text": f"Error: {e}"}],
                                "isError": True,
                            },
                        })
                else:
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "isError": True,
                        },
                    })

            elif method == "ping":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            sys.stderr.write(f"MCP error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    run_mcp_stdio()
