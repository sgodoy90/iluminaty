"""
ILUMINATY Brain Chat Terminal
==============================
Terminal interactivo para hablar con el modelo local de IluminatyBrain.
El modelo corre 100% local en tu GPU — sin Ollama, sin APIs externas.
Se descarga de HuggingFace la primera vez y queda cacheado.

Modos:
  --mode chat      Conversacion libre (el modelo responde en texto)
  --mode agent     El modelo decide acciones y las ejecuta
  --mode train     El modelo responde y TU calificas cada respuesta

Uso:
  # Default: Qwen2.5-3B en BF16 (requiere ~3GB VRAM)
  python -m iluminaty.brain_chat

  # Modelo mas chico en INT4 (requiere ~1.5GB VRAM)
  python -m iluminaty.brain_chat --4bit

  # Modelo mas grande
  python -m iluminaty.brain_chat --model Qwen/Qwen2.5-7B-Instruct --4bit

  # Desde GGUF local (sin descargar nada)
  python -m iluminaty.brain_chat --gguf C:/models/qwen2.5-7b.gguf

  # Modo agente
  python -m iluminaty.brain_chat --mode agent --autonomy confirm

  # Reanudar sesion anterior
  python -m iluminaty.brain_chat --session mi_sesion
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

ILUMINATY_URL = "http://127.0.0.1:8420"
SESSIONS_DIR = pathlib.Path.home() / ".iluminaty" / "brain_sessions"

# ─── Colors ──────────────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    GREEN   = "\033[92m"
    CYAN    = "\033[96m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    GRAY    = "\033[90m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    WHITE   = "\033[97m"
    BG_GRAY = "\033[100m"

_color_enabled: Optional[bool] = None

def _supports_color() -> bool:
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return _color_enabled

def _c(color: str, text: str) -> str:
    if _supports_color():
        return color + text + C.RESET
    return text


# ─── ILUMINATY API helpers ───────────────────────────────────────────────────

def _get_world(api_url: str = ILUMINATY_URL) -> Optional[dict]:
    try:
        req = urllib.request.Request(f"{api_url}/perception/world")
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _get_screen_text(api_url: str = ILUMINATY_URL) -> str:
    try:
        req = urllib.request.Request(f"{api_url}/vision/ocr")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
            return data.get("text", "")[:500]
    except Exception:
        return ""

def _execute(action: dict, api_url: str = ILUMINATY_URL) -> dict:
    """Execute an action via ILUMINATY API."""
    act = action.get("action", "")
    headers = {"Content-Type": "application/json"}

    def post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{api_url}{path}", data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    try:
        if act == "wait":
            time.sleep(min(int(action.get("ms", 500)), 5000) / 1000)
            return {"success": True}
        if act == "type_text":
            return post("/action/type", {"text": action.get("text", "")})
        if act == "hotkey":
            k = urllib.parse.quote(str(action.get("keys", "")))
            return post(f"/action/hotkey?keys={k}", {})
        if act == "click":
            return post("/action/click", {
                "x": action.get("x", 0), "y": action.get("y", 0),
            })
        if act == "double_click":
            return post("/action/click", {
                "x": action.get("x", 0), "y": action.get("y", 0),
                "double": True,
            })
        if act == "scroll":
            d = action.get("direction", "down")
            a = int(action.get("amount", 3))
            return post("/action/scroll", {"amount": a if d == "up" else -a})
        if act == "run_command":
            return post("/terminal/exec", {"command": action.get("cmd", "")})
        if act == "browser_navigate":
            return post("/browser/navigate", {"url": action.get("url", "")})
        if act == "focus_window":
            t = urllib.parse.quote(str(action.get("title", "")))
            return post(f"/windows/focus?title={t}", {})
        if act == "done":
            return {"success": True, "done": True}
        if act == "ask":
            return {"success": True, "question": action.get("text", "")}
        return post("/action/execute", {"instruction": json.dumps(action)})
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Chat template builder ──────────────────────────────────────────────────

def _build_chat_prompt(
    messages: list[dict],
    system: str,
    backend: str,
    screen_ctx: str = "",
) -> str:
    """
    Build a proper chat prompt using the model's expected format.
    Qwen models use ChatML: <|im_start|>role\ncontent<|im_end|>
    Generic fallback for other models.
    """
    full_system = system
    if screen_ctx:
        full_system += f"\n\n[Estado de pantalla actual]\n{screen_ctx}"

    # ChatML format (Qwen, many GGUF models)
    parts = [f"<|im_start|>system\n{full_system}<|im_end|>"]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _build_agent_prompt(
    world: dict,
    goal: str,
    history: list[dict],
) -> str:
    """Compact WorldState + goal prompt for agent mode."""
    surface = world.get("active_surface") or "unknown"
    phase = world.get("task_phase", "unknown")
    ready = world.get("readiness", False)
    affordances = world.get("affordances", [])[:6]
    texts = [
        str(f.get("text") or f.get("content") or "")[:80]
        for f in (world.get("visual_facts") or [])[:4]
        if f.get("text") or f.get("content")
    ]
    hist = [
        f"[{'OK' if h.get('success') else 'FAIL'}] {h.get('action')} "
        f"{h.get('reason', '')[:40]}"
        for h in history[-3:]
    ]
    parts = [
        f"GOAL: {goal}",
        f"surface={surface} phase={phase} ready={ready}",
        f"affordances={affordances}",
    ]
    if texts:
        parts.append(f"visible={texts}")
    if hist:
        parts.append("recent=" + " | ".join(hist))
    parts.append("Next action?")
    return "\n".join(parts)


# ─── Session persistence ────────────────────────────────────────────────────

def _ensure_sessions_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def _save_session(
    session_name: str,
    history: list[dict],
    model_name: str,
    mode: str,
    training_data: list[dict],
):
    """Save conversation session to disk."""
    _ensure_sessions_dir()
    data = {
        "name": session_name,
        "model": model_name,
        "mode": mode,
        "created": datetime.datetime.now().isoformat(),
        "messages": history,
        "training_data": training_data,
        "message_count": len(history),
    }
    path = SESSIONS_DIR / f"{session_name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

def _load_session(session_name: str) -> Optional[dict]:
    """Load a saved session."""
    path = SESSIONS_DIR / f"{session_name}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _list_sessions() -> list[dict]:
    """List all saved sessions."""
    _ensure_sessions_dir()
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            sessions.append({
                "name": data.get("name", f.stem),
                "model": data.get("model", "?"),
                "mode": data.get("mode", "?"),
                "messages": data.get("message_count", len(data.get("messages", []))),
                "created": data.get("created", "?"),
            })
        except Exception:
            continue
    return sessions


# ─── Streaming for transformers backend ──────────────────────────────────────

def _stream_transformers(brain, prompt: str, max_tokens: int = 512):
    """
    Token-by-token streaming for transformers backend using TextIteratorStreamer.
    Yields text chunks as they're generated.
    """
    import torch
    from threading import Thread

    try:
        from transformers import TextIteratorStreamer
    except ImportError:
        # Fallback: generate full then yield at once
        text = brain._infer_transformers(prompt)
        yield text
        return

    streamer = TextIteratorStreamer(
        brain._tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
    )

    device = next(brain._model.parameters()).device
    inputs = brain._tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=1536,
    ).to(device)

    gen_kwargs = {
        **inputs,
        "max_new_tokens": max_tokens,
        "temperature": 0.7,
        "do_sample": True,
        "top_p": 0.9,
        "pad_token_id": brain._tokenizer.eos_token_id,
        "streamer": streamer,
    }

    thread = Thread(target=brain._model.generate, kwargs=gen_kwargs)
    thread.start()

    for text_chunk in streamer:
        yield text_chunk

    thread.join()


# ─── UI helpers ──────────────────────────────────────────────────────────────

def _world_summary(world: Optional[dict]) -> str:
    if not world:
        return _c(C.GRAY, "  [pantalla: no disponible]")
    surface = world.get("active_surface", "?")
    phase = world.get("task_phase", "?")
    ready = world.get("readiness", False)
    texts = [
        f.get("text", "") for f in (world.get("visual_facts") or [])[:3]
        if f.get("text")
    ]
    s = f"  {_c(C.GRAY, 'pantalla:')} {surface} | {phase} | listo={ready}"
    if texts:
        s += f"\n  {_c(C.GRAY, 'visible:')} {' | '.join(t[:40] for t in texts)}"
    return s


def _status_line(
    world: Optional[dict],
    model_name: str,
    mode: str,
    msg_count: int,
) -> str:
    """Compact status bar shown above the input prompt."""
    connected = _c(C.GREEN, "ON") if world else _c(C.RED, "OFF")
    surface = world.get("active_surface", "?")[:12] if world else "?"
    return (
        _c(C.GRAY, "[") +
        f"{_c(C.CYAN, mode)} | "
        f"{_c(C.MAGENTA, model_name[:20])} | "
        f"pantalla:{connected} {surface} | "
        f"msgs:{msg_count}" +
        _c(C.GRAY, "]")
    )


def _print_banner(model_name: str, mode: str, autonomy: str, backend: str):
    """Print the startup banner."""
    print()
    w = 58
    print(_c(C.CYAN, "+" + "=" * w + "+"))
    title = "ILUMINATY Brain Chat"
    print(_c(C.CYAN, "|") + _c(C.BOLD + C.WHITE, title.center(w)) + _c(C.CYAN, "|"))
    print(_c(C.CYAN, "+" + "-" * w + "+"))
    print(_c(C.CYAN, "|") + f"  Modelo  : {_c(C.GREEN, str(model_name)[:40])}" .ljust(w + 13) + _c(C.CYAN, "|"))
    print(_c(C.CYAN, "|") + f"  Backend : {_c(C.YELLOW, backend)}" .ljust(w + 13) + _c(C.CYAN, "|"))
    print(_c(C.CYAN, "|") + f"  Modo    : {_c(C.YELLOW, mode)}" .ljust(w + 13) + _c(C.CYAN, "|"))
    if mode == "agent":
        print(_c(C.CYAN, "|") + f"  Control : {_c(C.YELLOW, autonomy)}" .ljust(w + 13) + _c(C.CYAN, "|"))
    print(_c(C.CYAN, "+" + "-" * w + "+"))
    print(_c(C.CYAN, "|") + _c(C.GRAY, "  Comandos:").ljust(w + 10) + _c(C.CYAN, "|"))
    cmds = [
        ("/pantalla",  "ver estado de pantalla"),
        ("/historia",  "historial de mensajes"),
        ("/stats",     "estadisticas del modelo"),
        ("/sesiones",  "listar sesiones guardadas"),
        ("/guardar",   "guardar sesion actual"),
        ("/cargar",    "cargar una sesion anterior"),
        ("/limpiar",   "borrar historial"),
        ("/sistema",   "ver/editar system prompt"),
        ("/modo",      "cambiar modo (chat/agent/train)"),
        ("/exportar",  "exportar chat a texto"),
        ("exit",       "salir"),
    ]
    for cmd, desc in cmds:
        line = f"  {_c(C.YELLOW, cmd):<28} {_c(C.GRAY, desc)}"
        print(_c(C.CYAN, "|") + line.ljust(w + 32) + _c(C.CYAN, "|"))
    print(_c(C.CYAN, "+" + "=" * w + "+"))
    print()


# ─── Main chat loop ─────────────────────────────────────────────────────────

DEFAULT_SYSTEM_CHAT = (
    "Eres IluminatyBrain, un asistente de escritorio inteligente. "
    "Ves la pantalla del usuario en tiempo real y ayudas con lo que necesite. "
    "Responde de forma util, concisa y en espanol. "
    "Si el usuario pregunta sobre lo que ve en pantalla, usa el contexto proporcionado."
)

DEFAULT_SYSTEM_AGENT = (
    "You are IluminatyBrain, a desktop automation agent. "
    "Reply ONLY with one JSON action. No explanation, no markdown.\n"
    "Actions: click, double_click, type_text, hotkey, scroll, "
    "run_command, browser_navigate, focus_window, wait, done, ask."
)


def run_chat(
    brain,
    api_url: str,
    mode: str,
    autonomy: str,
    save_training: bool,
    model_name: str = "?",
    session_name: Optional[str] = None,
):
    """Main interactive loop."""
    history: list[dict] = []
    training_data: list[dict] = []
    system_prompt = DEFAULT_SYSTEM_AGENT if mode == "agent" else DEFAULT_SYSTEM_CHAT
    session_name = session_name or f"brain_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    total_tokens_out = 0

    # Load session if specified
    if session_name and (SESSIONS_DIR / f"{session_name}.json").exists():
        loaded = _load_session(session_name)
        if loaded:
            history = loaded.get("messages", [])
            training_data = loaded.get("training_data", [])
            print(_c(C.GREEN, f"  Sesion '{session_name}' cargada ({len(history)} mensajes)"))

    backend = brain._backend
    _print_banner(model_name, mode, autonomy, backend)

    if mode == "agent":
        print(_c(C.YELLOW, "  Modo AGENTE: el modelo decide acciones para tu pantalla."))
    elif mode == "train":
        print(_c(C.YELLOW, "  Modo ENTRENAMIENTO: califica respuestas (1-5) para generar datos."))
    else:
        print(_c(C.YELLOW, "  Modo CHAT: conversacion libre. El modelo ve tu pantalla."))
    print()

    while True:
        world = _get_world(api_url)

        # Status line
        print(_status_line(world, model_name, mode, len(history)))

        # Input
        try:
            user_input = input(_c(C.BLUE, " Tu") + " > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        # ─── Slash commands ──────────────────────────────────────────────

        low = user_input.lower()
        if low in ("salir", "exit", "quit", "q"):
            break

        if low == "/pantalla":
            print(_c(C.CYAN, "\n-- Estado de pantalla --"))
            print(_world_summary(world))
            ocr = _get_screen_text(api_url)
            if ocr:
                print(f"  {_c(C.GRAY, 'OCR:')} {ocr[:300]}")
            print()
            continue

        if low == "/historia":
            print(_c(C.CYAN, "\n-- Historial --"))
            if not history:
                print("  (vacio)")
            for i, h in enumerate(history[-15:], 1):
                role = _c(C.BLUE, "Tu ") if h["role"] == "user" else _c(C.GREEN, "IA ")
                content = h["content"][:100].replace("\n", " ")
                print(f"  {i:2d}. [{role}] {content}")
            print()
            continue

        if low == "/limpiar":
            history.clear()
            total_tokens_out = 0
            print(_c(C.GRAY, "  Historial limpiado.\n"))
            continue

        if low == "/stats":
            stats = brain.status()
            print(_c(C.CYAN, "\n-- Estadisticas --"))
            for k, v in stats.items():
                print(f"  {k}: {v}")
            print(f"  total_tokens_out: ~{total_tokens_out}")
            print(f"  messages: {len(history)}")
            print(f"  training_samples: {len(training_data)}")
            print()
            continue

        if low == "/sesiones":
            sessions = _list_sessions()
            print(_c(C.CYAN, "\n-- Sesiones guardadas --"))
            if not sessions:
                print("  (ninguna)")
            for i, s in enumerate(sessions[:10], 1):
                print(
                    f"  {i}. {_c(C.GREEN, s['name'])} "
                    f"({s['messages']} msgs, {s['mode']}, {s['model']})"
                )
            print()
            continue

        if low == "/guardar":
            path = _save_session(session_name, history, model_name, mode, training_data)
            print(_c(C.GREEN, f"  Sesion guardada: {path}\n"))
            continue

        if low.startswith("/cargar"):
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2:
                # Show sessions and ask
                sessions = _list_sessions()
                if not sessions:
                    print(_c(C.GRAY, "  No hay sesiones guardadas.\n"))
                    continue
                print(_c(C.CYAN, "  Sesiones disponibles:"))
                for i, s in enumerate(sessions[:10], 1):
                    print(f"    {i}. {s['name']}")
                try:
                    choice = input(_c(C.YELLOW, "  Numero o nombre: ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(sessions):
                        sname = sessions[idx]["name"]
                    else:
                        print(_c(C.RED, "  Indice invalido.\n"))
                        continue
                else:
                    sname = choice
            else:
                sname = parts[1].strip()

            loaded = _load_session(sname)
            if loaded:
                history = loaded.get("messages", [])
                training_data = loaded.get("training_data", [])
                session_name = sname
                print(_c(C.GREEN, f"  Cargada: {sname} ({len(history)} mensajes)\n"))
            else:
                print(_c(C.RED, f"  Sesion '{sname}' no encontrada.\n"))
            continue

        if low == "/sistema":
            print(_c(C.CYAN, "\n-- System Prompt actual --"))
            print(f"  {system_prompt[:200]}")
            print()
            try:
                new = input(_c(C.YELLOW, "  Nuevo (Enter=mantener): ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if new:
                system_prompt = new
                print(_c(C.GREEN, "  System prompt actualizado.\n"))
            continue

        if low.startswith("/modo"):
            parts = user_input.split(maxsplit=1)
            if len(parts) >= 2 and parts[1] in ("chat", "agent", "train"):
                mode = parts[1]
                system_prompt = DEFAULT_SYSTEM_AGENT if mode == "agent" else DEFAULT_SYSTEM_CHAT
                save_training = mode == "train"
                print(_c(C.GREEN, f"  Modo cambiado a: {mode}\n"))
            else:
                print(_c(C.GRAY, f"  Modo actual: {mode}"))
                print(_c(C.GRAY, "  Uso: /modo chat|agent|train\n"))
            continue

        if low == "/exportar":
            export_path = pathlib.Path(f"brain_chat_export_{session_name}.txt")
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(f"ILUMINATY Brain Chat - {session_name}\n")
                f.write(f"Modelo: {model_name} | Modo: {mode}\n")
                f.write("=" * 60 + "\n\n")
                for h in history:
                    role = "Tu" if h["role"] == "user" else "IA"
                    f.write(f"[{role}] {h['content']}\n\n")
            print(_c(C.GREEN, f"  Exportado a: {export_path}\n"))
            continue

        # ─── Process message ─────────────────────────────────────────────

        history.append({"role": "user", "content": user_input})

        if mode == "agent":
            # Agent mode: decide action
            w = world or {
                "active_surface": "unknown",
                "task_phase": "idle",
                "readiness": True,
                "visual_facts": [],
            }
            action = brain.decide(
                w,
                goal=user_input,
                history=[
                    {"action": h["content"], "success": True}
                    for h in history[-5:]
                    if h["role"] == "assistant"
                ],
            )

            if action:
                action_str = json.dumps(action, ensure_ascii=False)
                print()
                print(
                    _c(C.GREEN, " IA") + " > " +
                    _c(C.BOLD, action_str)
                )

                executed = False
                if autonomy == "suggest":
                    print(_c(C.GRAY, "    (suggest mode)"))
                elif autonomy == "confirm":
                    try:
                        confirm = input(
                            _c(C.YELLOW, "    Ejecutar? [y/N]: ")
                        ).strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    if confirm == "y":
                        result = _execute(action, api_url)
                        ok = result.get("success", False)
                        st = _c(C.GREEN, "OK") if ok else _c(C.RED, "FAIL")
                        err = result.get("error", "")
                        print(f"    [{st}] {err}")
                        executed = True
                        if save_training:
                            training_data.append({
                                "goal": user_input,
                                "surface": w.get("active_surface"),
                                "action": action,
                                "success": ok,
                                "rating": 5 if ok else 1,
                            })
                else:  # auto
                    result = _execute(action, api_url)
                    ok = result.get("success", False)
                    st = _c(C.GREEN, "OK") if ok else _c(C.RED, "FAIL")
                    print(f"    [{st}]")
                    executed = True

                history.append({"role": "assistant", "content": action_str})
            else:
                print(_c(C.RED, " IA") + " > No pude generar accion valida.")

        else:
            # ─── Chat / Train mode ───────────────────────────────────────

            # Build screen context
            screen_ctx = ""
            if world:
                surface = world.get("active_surface", "?")
                phase = world.get("task_phase", "?")
                texts = [
                    f.get("text", "")
                    for f in (world.get("visual_facts") or [])[:3]
                    if f.get("text")
                ]
                screen_ctx = f"app={surface} fase={phase}"
                if texts:
                    screen_ctx += f" visible=[{', '.join(t[:40] for t in texts)}]"

            # Build proper chat prompt
            chat_messages = [
                {"role": h["role"], "content": h["content"]}
                for h in history[-12:]  # Keep last 12 messages for context
            ]
            prompt = _build_chat_prompt(
                chat_messages, system_prompt, backend, screen_ctx
            )

            print(_c(C.GREEN, " IA") + " > ", end="", flush=True)
            t0 = time.time()

            response_text = ""
            token_count = 0
            try:
                if backend == "llamacpp":
                    # llama.cpp native streaming
                    for chunk in brain._model(
                        prompt,
                        max_tokens=512,
                        temperature=0.7,
                        stream=True,
                        stop=[
                            "<|im_end|>", "<|im_start|>",
                            "Usuario:", "\nUsuario",
                        ],
                        echo=False,
                    ):
                        token = chunk["choices"][0]["text"]
                        print(token, end="", flush=True)
                        response_text += token
                        token_count += 1
                else:
                    # transformers: stream via TextIteratorStreamer
                    for chunk in _stream_transformers(brain, prompt, max_tokens=512):
                        # Stop on ChatML end tokens
                        if "<|im_end|>" in chunk:
                            chunk = chunk.split("<|im_end|>")[0]
                            if chunk:
                                print(chunk, end="", flush=True)
                                response_text += chunk
                                token_count += len(chunk.split())
                            break
                        if "<|im_start|>" in chunk:
                            chunk = chunk.split("<|im_start|>")[0]
                            if chunk:
                                print(chunk, end="", flush=True)
                                response_text += chunk
                                token_count += len(chunk.split())
                            break
                        print(chunk, end="", flush=True)
                        response_text += chunk
                        token_count += len(chunk.split())

            except Exception as e:
                print(_c(C.RED, f"\n  [Error: {e}]"))

            elapsed = time.time() - t0
            total_tokens_out += token_count
            tps = token_count / elapsed if elapsed > 0 else 0

            print()
            print(
                _c(C.GRAY, f"    [{elapsed:.1f}s | ~{token_count} tokens | "
                f"{tps:.1f} tok/s]")
            )

            response_clean = response_text.strip()
            history.append({"role": "assistant", "content": response_clean})

            # Training mode: ask for rating
            if mode == "train" and response_clean:
                try:
                    rating_str = input(
                        _c(C.YELLOW, "    Rating 1-5 (Enter=skip): ")
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if rating_str in ("1", "2", "3", "4", "5"):
                    training_data.append({
                        "prompt": user_input,
                        "response": response_clean,
                        "screen_surface": (world or {}).get("active_surface"),
                        "rating": int(rating_str),
                    })
                    print(_c(C.GRAY, f"    Guardado (rating {rating_str})"))

        print()

    # ─── Cleanup on exit ─────────────────────────────────────────────────

    # Auto-save session on exit
    if history:
        path = _save_session(session_name, history, model_name, mode, training_data)
        print(_c(C.GREEN, f"\n  Sesion auto-guardada: {path}"))

    # Save training data
    if training_data:
        out = pathlib.Path("brain_training_session.jsonl")
        with open(out, "a", encoding="utf-8") as f:
            for item in training_data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(_c(C.GREEN, f"  Training data: {len(training_data)} ejemplos -> {out}"))

    print(_c(C.CYAN, "\n  Hasta luego.\n"))


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="IluminatyBrain Chat Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python -m iluminaty.brain_chat                                  # Qwen2.5-3B local (BF16)
  python -m iluminaty.brain_chat --4bit                           # Qwen2.5-3B INT4 (menos VRAM)
  python -m iluminaty.brain_chat --model Qwen/Qwen2.5-7B-Instruct --4bit
  python -m iluminaty.brain_chat --gguf C:/models/mi_modelo.gguf  # GGUF local
  python -m iluminaty.brain_chat --mode agent --autonomy confirm  # Modo agente
  python -m iluminaty.brain_chat --session mi_sesion              # Reanudar sesion
        """,
    )
    parser.add_argument(
        "--model", default="Qwen/Qwen2.5-3B-Instruct",
        help="HuggingFace model ID (default: Qwen/Qwen2.5-3B-Instruct)",
    )
    parser.add_argument(
        "--mode", default="chat",
        choices=["chat", "agent", "train"],
        help="chat=libre, agent=ejecuta acciones, train=genera datos",
    )
    parser.add_argument(
        "--autonomy", default="confirm",
        choices=["suggest", "confirm", "auto"],
        help="Solo en modo agent: suggest/confirm/auto",
    )
    parser.add_argument(
        "--api-url", default=ILUMINATY_URL,
        help=f"ILUMINATY API URL (default: {ILUMINATY_URL})",
    )
    parser.add_argument(
        "--4bit", action="store_true", dest="load_4bit",
        help="Cargar en INT4 (ahorra VRAM, recomendado para GPUs < 8GB)",
    )
    parser.add_argument(
        "--gguf", default=None, metavar="PATH",
        help="Cargar directamente desde un archivo GGUF local",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="Ruta a checkpoint fine-tuneado (LoRA)",
    )
    parser.add_argument(
        "--session", default=None,
        help="Nombre de sesion para reanudar o crear",
    )
    args = parser.parse_args()

    # Check if ILUMINATY is running
    world = _get_world(args.api_url)
    if world:
        print(_c(C.GREEN, f"\n  ILUMINATY conectado ({args.api_url})"))
    else:
        print(_c(C.YELLOW,
            f"\n  ILUMINATY no responde en {args.api_url}\n"
            "  El modelo funcionara sin contexto de pantalla.\n"
            "  Para activarlo: python main.py start --actions"
        ))

    # Load model — sin Ollama, directo a GPU
    from iluminaty.brain_engine import BrainEngine
    t0 = time.time()

    if args.gguf:
        label = pathlib.Path(args.gguf).name
        print(f"\n  Cargando GGUF: {_c(C.BOLD, label)}...")
        try:
            brain = BrainEngine.from_gguf(args.gguf)
        except Exception as e:
            print(_c(C.RED, f"\n  Error cargando GGUF: {e}"))
            sys.exit(1)
    elif args.checkpoint:
        print(f"\n  Cargando checkpoint: {_c(C.BOLD, args.checkpoint)}...")
        try:
            brain = BrainEngine.from_checkpoint(args.checkpoint)
        except Exception as e:
            print(_c(C.RED, f"\n  Error cargando checkpoint: {e}"))
            sys.exit(1)
    else:
        print(f"\n  Cargando {_c(C.BOLD, args.model)} desde HuggingFace...")
        if args.load_4bit:
            print(_c(C.GRAY, "  (INT4 — ahorra VRAM, primera vez descarga el modelo)"))
        else:
            print(_c(C.GRAY, "  (BF16 — primera vez descarga el modelo, queda cacheado)"))
        try:
            brain = BrainEngine.from_huggingface(args.model, load_in_4bit=args.load_4bit)
        except Exception as e:
            print(_c(C.RED, f"\n  Error cargando modelo: {e}"))
            sys.exit(1)

    load_time = time.time() - t0
    print(_c(C.GREEN, f"  Modelo listo en {load_time:.1f}s"))

    run_chat(
        brain=brain,
        api_url=args.api_url,
        mode=args.mode,
        autonomy=args.autonomy,
        save_training=(args.mode == "train"),
        model_name=args.model,
        session_name=args.session,
    )


if __name__ == "__main__":
    main()
