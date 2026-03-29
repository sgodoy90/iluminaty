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

# ILUMINATY API base URL
API_BASE = "http://127.0.0.1:8420"


def _api_get(path: str) -> dict:
    """GET request to ILUMINATY API."""
    url = API_BASE + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _api_post(path: str) -> dict:
    """POST request to ILUMINATY API."""
    url = API_BASE + path
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
            "memory usage, FPS, active window."
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


HANDLERS = {
    "see_screen": handle_see_screen,
    "see_changes": handle_see_changes,
    "annotate_screen": handle_annotate,
    "read_screen_text": handle_read_text,
    "screen_status": handle_status,
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
        sys.stdout.write(f"Content-Length: {len(data)}\r\n\r\n{data}")
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
                            "version": "0.2.0",
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
