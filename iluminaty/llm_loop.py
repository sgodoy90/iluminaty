"""
ILUMINATY - LLM Loop
======================
Closed-loop between ILUMINATY perception and a local/cloud LLM.

Flow (every tick):
  1. Read WorldState from ILUMINATY API (~200 tokens)
  2. Build compact prompt (no image needed — already pre-digested)
  3. Send to LLM → parse action JSON
  4. Execute action via ILUMINATY API
  5. Wait for screen to settle (ActionCompletionWatcher)
  6. Repeat

The LLM only needs to decide WHAT to do — ILUMINATY handles
seeing, grounding, executing, verifying, and recovering.

Usage (via main.py):
  python main.py start --actions --llm ollama --llm-model qwen3-vl:4b \\
    --goal "organiza mis downloads" --autonomy auto

Usage (programmatic):
  loop = LLMLoop.from_config(provider="ollama", model="qwen3-vl:4b",
                              goal="help me", api_url="http://localhost:8420")
  loop.start()
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Prompt templates ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are IluminatyBrain — a precise desktop automation agent.

You receive a JSON world state describing what is on screen.
You MUST respond with EXACTLY one JSON action. No explanation, no markdown, just JSON.

AVAILABLE ACTIONS:
  {"action": "click",        "x": 450, "y": 320, "button": "left"}
  {"action": "double_click", "x": 450, "y": 320}
  {"action": "type_text",    "text": "hello world"}
  {"action": "hotkey",       "keys": "ctrl+s"}
  {"action": "scroll",       "amount": 3, "direction": "down"}
  {"action": "run_command",  "cmd": "python script.py"}
  {"action": "browser_navigate", "url": "https://..."}
  {"action": "focus_window", "title": "window title"}
  {"action": "wait",         "ms": 1000, "reason": "loading"}
  {"action": "done",         "reason": "goal achieved"}
  {"action": "ask",          "question": "need clarification"}

RULES: Respond ONLY with valid JSON. One action. No text before or after."""

def _world_to_prompt(world: dict, goal: str, history: list[dict]) -> str:
    """Convert WorldState dict to compact LLM prompt (~200 tokens)."""
    surface = world.get("active_surface") or world.get("task_phase", "unknown")
    phase = world.get("task_phase", "unknown")
    ready = world.get("readiness", False)
    uncertainty = world.get("uncertainty", 1.0)
    affordances = world.get("affordances", [])
    domain = world.get("domain_pack", "general")
    staleness = world.get("staleness_ms", 0)

    # Extract visible text from visual_facts
    texts = []
    for fact in (world.get("visual_facts") or [])[:5]:
        t = str(fact.get("text") or fact.get("content") or "")
        if t:
            texts.append(t[:120])

    # Cursor position from world state
    cursor = world.get("cursor") or {}
    cursor_str = f"({cursor.get('x', '?')},{cursor.get('y', '?')})" if cursor else "unknown"

    # Recent history summary (last 3 actions)
    hist_lines = []
    for h in history[-3:]:
        status = "OK" if h.get("success") else "FAIL"
        hist_lines.append(f"  [{status}] {h.get('action')} — {h.get('reason','')[:60]}")

    prompt = f"""GOAL: {goal}

WORLD STATE:
  surface: {surface}
  phase: {phase}
  ready: {ready}
  uncertainty: {uncertainty:.2f}
  domain: {domain}
  cursor: {cursor_str}
  context_age_ms: {staleness}
  affordances: {affordances}
  visible_text: {texts}

{"RECENT ACTIONS:" + chr(10) + chr(10).join(hist_lines) if hist_lines else ""}

What is the next single action?"""
    return prompt


# ─── Action parser ────────────────────────────────────────────────────────────

def _parse_action(text: str) -> Optional[dict]:
    """Extract JSON action from LLM response. Handles markdown code blocks."""
    if not text:
        return None
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = text.rstrip("`").strip()
    # Find first {...} block
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# ─── Action executor ─────────────────────────────────────────────────────────

def _execute_action(action: dict, api_url: str, api_key: str = "") -> dict:
    """Send action to ILUMINATY API and return result."""
    act = action.get("action", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    def _post(path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{api_url}{path}", data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _get(path: str) -> dict:
        req = urllib.request.Request(f"{api_url}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    try:
        if act == "wait":
            ms = int(action.get("ms", 500))
            time.sleep(min(ms, 5000) / 1000.0)
            return {"success": True, "action": "wait", "reason": action.get("reason", "")}

        if act == "done":
            return {"success": True, "action": "done", "reason": action.get("reason", "goal_complete"), "done": True}

        if act == "ask":
            logger.info("[IluminatyBrain] LLM asks: %s", action.get("question"))
            return {"success": True, "action": "ask", "question": action.get("question", "")}

        if act == "click":
            return _post("/action/click", {
                "x": int(action.get("x", 0)),
                "y": int(action.get("y", 0)),
                "button": action.get("button", "left"),
            })

        if act == "double_click":
            return _post("/action/double_click", {
                "x": int(action.get("x", 0)),
                "y": int(action.get("y", 0)),
            })

        if act == "type_text":
            return _post("/action/type", {"text": str(action.get("text", ""))})

        if act == "hotkey":
            keys = action.get("keys", "")
            return _post(f"/action/hotkey?keys={urllib.parse.quote(str(keys))}", {})

        if act == "scroll":
            direction = action.get("direction", "down")
            amount = int(action.get("amount", 3))
            sign = amount if direction == "up" else -amount
            return _post("/action/scroll", {"amount": sign})

        if act == "run_command":
            return _post("/terminal/exec", {"command": str(action.get("cmd", ""))})

        if act == "browser_navigate":
            return _post("/browser/navigate", {"url": str(action.get("url", ""))})

        if act == "focus_window":
            title = action.get("title", "")
            return _post(f"/windows/focus?title={urllib.parse.quote(str(title))}", {})

        # Fallback: use generic do_action endpoint
        return _post("/action/execute", {"instruction": json.dumps(action)})

    except Exception as e:
        return {"success": False, "error": str(e), "action": act}


# ─── Main LLM Loop ───────────────────────────────────────────────────────────

import urllib.parse  # needed for _execute_action


class LLMLoop:
    """
    Autonomous closed-loop: WorldState -> LLM -> Action -> repeat.

    Thread-safe. Runs in a dedicated background thread.
    Call start() to begin, stop() to halt gracefully.
    """

    def __init__(
        self,
        adapter,
        goal: str = "Help the user with what you see on screen",
        api_url: str = "http://127.0.0.1:8420",
        api_key: str = "",
        tick_interval_s: float = 1.5,
        max_ticks: int = 500,
        autonomy: str = "confirm",   # suggest | confirm | auto
    ):
        self._adapter = adapter
        self._goal = goal
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._tick_interval = max(0.5, float(tick_interval_s))
        self._max_ticks = max_ticks
        self._autonomy = autonomy
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._history: list[dict] = []
        self._tick_count = 0
        self._last_action: Optional[dict] = None
        self._stats = {
            "ticks": 0, "actions": 0, "errors": 0,
            "done": False, "start_time": 0.0,
        }

    @classmethod
    def from_config(
        cls,
        provider: str = "ollama",
        model: str = "qwen3-vl:4b",
        goal: str = "Help me",
        api_url: str = "http://127.0.0.1:8420",
        api_key: str = "",
        llm_key: str = "",
        ollama_url: str = "http://localhost:11434",
        autonomy: str = "confirm",
    ) -> "LLMLoop":
        from .adapters import ADAPTERS
        AdapterClass = ADAPTERS.get(provider)
        if not AdapterClass:
            raise ValueError(f"Unknown provider: {provider}. Available: {list(ADAPTERS.keys())}")
        if provider == "ollama":
            adapter = AdapterClass(api_key="", model=model, base_url=ollama_url)
        else:
            adapter = AdapterClass(api_key=llm_key or api_key, model=model)
        adapter.connect()
        return cls(adapter=adapter, goal=goal, api_url=api_url,
                   api_key=api_key, autonomy=autonomy)

    def _get_world(self) -> Optional[dict]:
        try:
            headers = {}
            if self._api_key:
                headers["x-api-key"] = self._api_key
            req = urllib.request.Request(
                f"{self._api_url}/perception/world", headers=headers
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception as e:
            logger.debug("LLMLoop: get_world failed: %s", e)
            return None

    def _tick(self) -> bool:
        """One loop iteration. Returns False if loop should stop."""
        world = self._get_world()
        if not world:
            logger.warning("LLMLoop: no world state — is ILUMINATY running?")
            return True

        prompt = _world_to_prompt(world, self._goal, self._history)
        response = self._adapter.ask(prompt, system=SYSTEM_PROMPT)

        if not response:
            self._stats["errors"] += 1
            logger.warning("LLMLoop: LLM returned empty response")
            return True

        action = _parse_action(response)
        if not action:
            self._stats["errors"] += 1
            logger.warning("LLMLoop: could not parse action from: %s", response[:100])
            return True

        logger.info("[IluminatyBrain] tick=%d action=%s", self._tick_count, json.dumps(action))
        self._last_action = action

        # Autonomy gate
        if self._autonomy == "suggest":
            print(f"\n[IluminatyBrain] SUGGEST: {json.dumps(action)}")
            print(f"[IluminatyBrain] (suggest mode — not executing)")
            result = {"success": True, "action": action.get("action"), "reason": "suggested_only"}

        elif self._autonomy == "confirm":
            print(f"\n[IluminatyBrain] ACTION: {json.dumps(action)}")
            try:
                confirm = input("[IluminatyBrain] Execute? [y/N/stop]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm == "stop":
                return False
            if confirm != "y":
                result = {"success": True, "action": action.get("action"), "reason": "skipped_by_user"}
            else:
                result = _execute_action(action, self._api_url, self._api_key)

        else:  # auto
            result = _execute_action(action, self._api_url, self._api_key)

        # Record in history
        self._history.append({
            "tick": self._tick_count,
            "action": action.get("action"),
            "params": {k: v for k, v in action.items() if k != "action"},
            "success": bool(result.get("success")),
            "reason": str(result.get("reason") or result.get("error") or ""),
        })
        # Keep history bounded
        if len(self._history) > 50:
            self._history = self._history[-50:]

        self._stats["actions"] += 1

        # Done signal
        if result.get("done") or action.get("action") == "done":
            logger.info("[IluminatyBrain] Goal achieved: %s", action.get("reason", ""))
            self._stats["done"] = True
            return False

        return True

    def _loop(self):
        self._stats["start_time"] = time.time()
        logger.info("[IluminatyBrain] Starting loop | goal=%s | autonomy=%s", self._goal, self._autonomy)
        print(f"\n[IluminatyBrain] Running | goal: {self._goal} | model: {getattr(self._adapter, '_model', '?')} | autonomy: {self._autonomy}")
        print(f"[IluminatyBrain] Press Ctrl+C or type 'stop' to halt\n")

        while self._running and not self._stop_event.is_set():
            self._tick_count += 1
            self._stats["ticks"] = self._tick_count

            if self._tick_count > self._max_ticks:
                logger.info("[IluminatyBrain] Max ticks reached (%d)", self._max_ticks)
                break
            try:
                should_continue = self._tick()
                if not should_continue:
                    break
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._stats["errors"] += 1
                logger.error("[IluminatyBrain] tick error: %s", e)

            self._stop_event.wait(timeout=self._tick_interval)

        elapsed = time.time() - self._stats["start_time"]
        logger.info(
            "[IluminatyBrain] Loop ended | ticks=%d actions=%d errors=%d elapsed=%.1fs",
            self._stats["ticks"], self._stats["actions"],
            self._stats["errors"], elapsed,
        )
        print(f"\n[IluminatyBrain] Stopped | {self._stats['ticks']} ticks | {self._stats['actions']} actions | {elapsed:.1f}s")
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="llm-loop")
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def status(self) -> dict:
        return {
            **self._stats,
            "running": self._running,
            "goal": self._goal,
            "autonomy": self._autonomy,
            "model": getattr(self._adapter, "_model", getattr(self._adapter, "model", "?")),
            "last_action": self._last_action,
            "history_len": len(self._history),
        }
