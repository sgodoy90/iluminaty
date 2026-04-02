"""
ILUMINATY Brain Chat Terminal
==============================
Habla con el modelo directamente desde la terminal.
El modelo ve el WorldState actual de tu pantalla en cada mensaje.

Modos:
  --mode chat      Conversacion libre (el modelo responde en texto)
  --mode agent     El modelo decide acciones y las ejecuta (igual que --llm brain)
  --mode train     El modelo responde y TU calificas cada respuesta (genera datos de entrenamiento)

Uso:
  python -m iluminaty.brain_chat
  python -m iluminaty.brain_chat --mode agent --autonomy confirm
  python -m iluminaty.brain_chat --mode train
  python -m iluminaty.brain_chat --model qwen2.5:7b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Optional

ILUMINATY_URL = "http://127.0.0.1:8420"

# ─── Colors ──────────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    CYAN   = "\033[96m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    GRAY   = "\033[90m"
    BLUE   = "\033[94m"
    MAGENTA= "\033[95m"

def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

def _c(color: str, text: str) -> str:
    if _supports_color():
        return color + text + C.RESET
    return text

# ─── ILUMINATY API helpers ────────────────────────────────────────────────────

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
            return data.get("text", "")[:400]
    except Exception:
        return ""

def _execute(action: dict, api_url: str = ILUMINATY_URL) -> dict:
    """Execute an action via ILUMINATY."""
    import urllib.parse
    act = action.get("action", "")
    headers = {"Content-Type": "application/json"}

    def post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{api_url}{path}", data=data, headers=headers, method="POST")
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
            return post("/action/click", {"x": action.get("x",0), "y": action.get("y",0)})
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
        return post("/action/execute", {"instruction": json.dumps(action)})
    except Exception as e:
        return {"success": False, "error": str(e)}

# ─── Chat modes ───────────────────────────────────────────────────────────────

def _world_summary(world: Optional[dict]) -> str:
    if not world:
        return _c(C.GRAY, "  [pantalla: no disponible — ILUMINATY no está corriendo]")
    surface = world.get("active_surface", "?")
    phase   = world.get("task_phase", "?")
    ready   = world.get("readiness", False)
    texts   = [f.get("text","") for f in (world.get("visual_facts") or [])[:3] if f.get("text")]
    s = f"  {_c(C.GRAY, 'pantalla:')} {surface} | {phase} | listo={ready}"
    if texts:
        s += f"\n  {_c(C.GRAY, 'visible:')} {' | '.join(t[:40] for t in texts)}"
    return s


def run_chat(brain, api_url: str, mode: str, autonomy: str, save_training: bool):
    """Main interactive loop."""
    history = []
    training_data = []

    print()
    print(_c(C.CYAN, "╔══════════════════════════════════════════════════════╗"))
    print(_c(C.CYAN, "║") + _c(C.BOLD, "         ILUMINATY Brain Chat Terminal               ") + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "╠══════════════════════════════════════════════════════╣"))
    model_name = getattr(brain, '_model', None) or getattr(brain._model, 'model_path', '?') if hasattr(brain, '_model') else '?'
    print(_c(C.CYAN, "║") + f"  Modelo : {_c(C.GREEN, str(model_name)[:40]):<50}" + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + f"  Modo   : {_c(C.YELLOW, mode):<50}" + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + f"  Control: {_c(C.YELLOW, autonomy):<50}" + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "╠══════════════════════════════════════════════════════╣"))
    print(_c(C.CYAN, "║") + "  Comandos especiales:                               " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   /pantalla  — ver WorldState actual                " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   /historia  — ver historial de acciones            " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   /limpiar   — limpiar historial                    " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   /stats     — estadísticas del modelo              " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   /guardar   — guardar datos de entrenamiento       " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "║") + "   salir / exit / quit — terminar                   " + _c(C.CYAN, "║"))
    print(_c(C.CYAN, "╚══════════════════════════════════════════════════════╝"))
    print()

    if mode == "agent":
        print(_c(C.YELLOW, "Modo AGENTE: el modelo decidirá acciones para ejecutar en tu pantalla."))
    elif mode == "train":
        print(_c(C.YELLOW, "Modo ENTRENAMIENTO: califica cada respuesta con 1-5 para generar datos."))
    else:
        print(_c(C.YELLOW, "Modo CHAT: conversación libre. El modelo ve tu pantalla en tiempo real."))
    print()

    while True:
        # Show current screen state briefly
        world = _get_world(api_url)

        # Prompt
        try:
            user_input = input(_c(C.BLUE, "Tú") + " > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        # Special commands
        if user_input.lower() in ("salir", "exit", "quit", "q"):
            break

        if user_input == "/pantalla":
            print(_c(C.CYAN, "\n── Estado actual de pantalla ──"))
            print(_world_summary(world))
            ocr = _get_screen_text(api_url)
            if ocr:
                print(f"  {_c(C.GRAY, 'texto OCR:')} {ocr[:200]}")
            print()
            continue

        if user_input == "/historia":
            print(_c(C.CYAN, "\n── Historial ──"))
            if not history:
                print("  (vacío)")
            for i, h in enumerate(history[-10:], 1):
                role = _c(C.BLUE, "Tú") if h["role"] == "user" else _c(C.GREEN, "IA ")
                print(f"  {i}. [{role}] {h['content'][:80]}")
            print()
            continue

        if user_input == "/limpiar":
            history.clear()
            print(_c(C.GRAY, "  Historial limpiado."))
            continue

        if user_input == "/stats":
            stats = brain.status()
            print(_c(C.CYAN, "\n── Estadísticas del modelo ──"))
            for k, v in stats.items():
                print(f"  {k}: {v}")
            print()
            continue

        if user_input == "/guardar":
            if training_data:
                import pathlib
                out = pathlib.Path("brain_training_session.jsonl")
                with open(out, "a", encoding="utf-8") as f:
                    for item in training_data:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                print(_c(C.GREEN, f"  {len(training_data)} ejemplos guardados en {out}"))
                training_data.clear()
            else:
                print(_c(C.GRAY, "  No hay datos nuevos para guardar."))
            continue

        # Build context for the model
        history.append({"role": "user", "content": user_input})

        if mode == "agent":
            # Agent mode: decide action from goal
            if world:
                action = brain.decide(world, goal=user_input, history=[
                    {"action": h["content"], "success": True}
                    for h in history[-5:] if h["role"] == "assistant"
                ])
            else:
                action = brain.decide({"active_surface": "unknown", "task_phase": "idle",
                                        "readiness": True, "visual_facts": []},
                                       goal=user_input)

            if action:
                action_str = json.dumps(action, ensure_ascii=False)
                print()
                print(_c(C.GREEN, "IA ") + f"→ {_c(C.BOLD, action_str)}")

                if autonomy == "suggest":
                    print(_c(C.GRAY, "   (modo suggest — no ejecuta)"))
                    history.append({"role": "assistant", "content": action_str})

                elif autonomy == "confirm":
                    confirm = input(_c(C.YELLOW, "   Ejecutar? [y/N/stop]: ")).strip().lower()
                    if confirm == "stop":
                        break
                    if confirm == "y":
                        result = _execute(action, api_url)
                        ok = result.get("success", False)
                        status = _c(C.GREEN, "OK") if ok else _c(C.RED, "FAIL")
                        print(f"   [{status}] {result.get('error', '')}")
                        if save_training:
                            training_data.append({
                                "goal": user_input,
                                "world_surface": (world or {}).get("active_surface"),
                                "action": action,
                                "executed": True,
                                "success": ok,
                                "rating": 5 if ok else 1,
                            })
                    history.append({"role": "assistant", "content": action_str})

                else:  # auto
                    result = _execute(action, api_url)
                    ok = result.get("success", False)
                    status = _c(C.GREEN, "OK") if ok else _c(C.RED, "FAIL")
                    print(f"   [{status}]")
                    history.append({"role": "assistant", "content": action_str})
            else:
                print(_c(C.RED, "IA ") + " No pude generar una acción válida.")

        else:
            # Chat mode: free conversation with screen context
            # Build a conversational prompt
            screen_ctx = ""
            if world:
                surface = world.get("active_surface", "?")
                phase   = world.get("task_phase", "?")
                texts   = [f.get("text","") for f in (world.get("visual_facts") or [])[:3] if f.get("text")]
                screen_ctx = f"[pantalla: {surface}, {phase}, visible: {texts}]"

            # Build multi-turn prompt
            conv_parts = [
                "Eres IluminatyBrain, un asistente de escritorio que ve la pantalla del usuario.",
                "Responde de forma útil y concisa. Puedes hablar en español.",
                f"Estado actual: {screen_ctx}" if screen_ctx else "",
                "",
            ]
            for h in history[-8:]:
                role = "Usuario" if h["role"] == "user" else "Asistente"
                conv_parts.append(f"{role}: {h['content']}")
            conv_parts.append("Asistente:")

            prompt = "\n".join(p for p in conv_parts if p is not None)

            print(_c(C.GREEN, "IA ") + " ", end="", flush=True)
            t0 = time.time()

            # Stream output token by token via llama.cpp
            response_text = ""
            try:
                if brain._backend == "llamacpp":
                    for chunk in brain._model(
                        prompt,
                        max_tokens=300,
                        temperature=0.7,
                        stream=True,
                        stop=["Usuario:", "\nUsuario", "Asistente:", "\n\n\n"],
                        echo=False,
                    ):
                        token = chunk["choices"][0]["text"]
                        print(token, end="", flush=True)
                        response_text += token
                else:
                    # transformers: generate full then print
                    response_text = brain._infer_transformers(prompt)
                    print(response_text, end="", flush=True)
            except Exception as e:
                print(_c(C.RED, f"\n[Error: {e}]"))

            elapsed = time.time() - t0
            print(f"\n  {_c(C.GRAY, f'({elapsed:.1f}s)')}")

            history.append({"role": "assistant", "content": response_text.strip()})

            # Training mode: ask for rating
            if mode == "train" and response_text.strip():
                rating_str = input(_c(C.YELLOW, "   Rating 1-5 (Enter=skip): ")).strip()
                if rating_str in ("1","2","3","4","5"):
                    training_data.append({
                        "prompt": user_input,
                        "response": response_text.strip(),
                        "screen_surface": (world or {}).get("active_surface"),
                        "rating": int(rating_str),
                    })
                    print(_c(C.GRAY, f"   Guardado (rating {rating_str})"))

        print()

    # Save on exit if training data exists
    if training_data:
        import pathlib
        out = pathlib.Path("brain_training_session.jsonl")
        with open(out, "a", encoding="utf-8") as f:
            for item in training_data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(_c(C.GREEN, f"\nSesión guardada: {len(training_data)} ejemplos en {out}"))

    print(_c(C.CYAN, "\nHasta luego."))


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IluminatyBrain Chat Terminal")
    parser.add_argument("--model",    default="qwen2.5:7b",
                        help="Ollama model name o HuggingFace ID (default: qwen2.5:7b)")
    parser.add_argument("--mode",     default="chat",
                        choices=["chat", "agent", "train"],
                        help="chat=libre, agent=ejecuta acciones, train=genera datos")
    parser.add_argument("--autonomy", default="confirm",
                        choices=["suggest", "confirm", "auto"],
                        help="Solo en modo agent: suggest/confirm/auto")
    parser.add_argument("--api-url",  default=ILUMINATY_URL,
                        help=f"ILUMINATY API URL (default: {ILUMINATY_URL})")
    parser.add_argument("--hf",       action="store_true",
                        help="Cargar desde HuggingFace en lugar de Ollama GGUF")
    parser.add_argument("--4bit",     action="store_true", dest="load_4bit",
                        help="Cargar en INT4 (solo con --hf, ahorra VRAM)")
    parser.add_argument("--checkpoint", default=None,
                        help="Ruta a checkpoint fine-tuneado")
    args = parser.parse_args()

    # Check if ILUMINATY is running
    world = _get_world(args.api_url)
    if world:
        print(_c(C.GREEN, f"ILUMINATY conectado ({args.api_url})"))
    else:
        print(_c(C.YELLOW,
            f"ILUMINATY no responde en {args.api_url} — "
            "el modelo funcionará sin contexto de pantalla.\n"
            "Para activarlo: python main.py start --actions"
        ))

    # Load model
    print(f"\nCargando modelo {_c(C.BOLD, args.model)}...")
    from iluminaty.brain_engine import BrainEngine
    t0 = time.time()
    try:
        if args.checkpoint:
            brain = BrainEngine.from_checkpoint(args.checkpoint)
        elif args.hf:
            brain = BrainEngine.from_huggingface(args.model, load_in_4bit=args.load_4bit)
        else:
            brain = BrainEngine.from_ollama_blob(args.model)
    except Exception as e:
        print(_c(C.RED, f"Error cargando modelo: {e}"))
        sys.exit(1)

    print(_c(C.GREEN, f"Listo en {time.time()-t0:.1f}s"))

    run_chat(
        brain=brain,
        api_url=args.api_url,
        mode=args.mode,
        autonomy=args.autonomy,
        save_training=(args.mode == "train"),
    )


if __name__ == "__main__":
    main()
