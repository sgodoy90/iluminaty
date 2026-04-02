"""
ILUMINATY - Brain Engine
=========================
Direct LLM inference without Ollama dependency.
Supports two backends:

  1. LlamaCpp (GGUF) — uses existing Ollama blobs, zero download
     Fast, low latency, CPU+GPU, no fine-tuning support.

  2. Transformers (BF16/INT4) — downloads from HuggingFace
     Supports fine-tuning via LoRA, same model used for training.

Usage:
  from iluminaty.brain_engine import BrainEngine

  # Option A: use existing Ollama GGUF (no download needed)
  brain = BrainEngine.from_ollama_blob("qwen3:4b")

  # Option B: load from HuggingFace (downloads once, then cached)
  brain = BrainEngine.from_huggingface("Qwen/Qwen3-4B")

  # Option C: load a fine-tuned IluminatyBrain checkpoint
  brain = BrainEngine.from_checkpoint("./brain_checkpoints/latest")

  response = brain.decide(world_state_dict, goal="save the file")
  print(response)  # {"action": "hotkey", "keys": "ctrl+s"}
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Ollama blob discovery ────────────────────────────────────────────────────

_OLLAMA_MODELS_DIR = pathlib.Path.home() / ".ollama" / "models"


def _find_ollama_gguf(model_name: str) -> Optional[pathlib.Path]:
    """Locate the GGUF blob for a given Ollama model name (e.g. 'qwen3:4b')."""
    manifests_dir = _OLLAMA_MODELS_DIR / "manifests"
    blobs_dir = _OLLAMA_MODELS_DIR / "blobs"
    if not manifests_dir.exists() or not blobs_dir.exists():
        return None

    # Parse model name into name:tag
    if ":" in model_name:
        name, tag = model_name.split(":", 1)
    else:
        name, tag = model_name, "latest"

    # Search manifests for matching model
    for registry in manifests_dir.iterdir():
        for namespace in registry.iterdir():
            for model_dir in namespace.iterdir():
                if model_dir.name.lower() != name.lower():
                    continue
                tag_file = model_dir / tag
                if not tag_file.exists():
                    # Try any tag
                    tags = list(model_dir.iterdir())
                    if not tags:
                        continue
                    tag_file = tags[0]
                try:
                    manifest = json.loads(tag_file.read_text())
                    for layer in manifest.get("layers", []):
                        media_type = layer.get("mediaType", "")
                        if "model" in media_type:
                            digest = layer.get("digest", "").replace(":", "-")
                            blob_path = blobs_dir / digest
                            if blob_path.exists():
                                return blob_path
                except Exception as e:
                    logger.debug("Error reading manifest %s: %s", tag_file, e)
    return None


# ─── Brain Engine ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are IluminatyBrain, a desktop automation agent. "
    "Reply ONLY with one JSON action. No explanation, no markdown."
    "\nActions: click, double_click, type_text, hotkey, scroll, "
    "run_command, browser_navigate, focus_window, wait, done, ask."
)

# Few-shot examples to steer the model toward JSON output
_FEW_SHOT = """
Example:
GOAL: save the file
surface=vscode phase=editing ready=True
Next action?
{"action": "hotkey", "keys": "ctrl+s"}

Example:
GOAL: open browser
surface=desktop phase=idle ready=True
Next action?
{"action": "browser_navigate", "url": "https://google.com"}

Example:
GOAL: type hello in terminal
surface=terminal phase=interaction ready=True
Next action?
{"action": "type_text", "text": "hello"}

Example:
GOAL: scroll down
surface=chrome phase=navigation ready=True
Next action?
{"action": "scroll", "amount": 3, "direction": "down"}

Now answer:"""


def _world_to_prompt(world: dict, goal: str, history: list[dict]) -> str:
    """Compact WorldState → prompt string (~150 tokens)."""
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
        f"[{'OK' if h.get('success') else 'FAIL'}] {h.get('action')} {h.get('reason','')[:40]}"
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


def _parse_json(text: str) -> Optional[dict]:
    """Extract first {...} JSON block from LLM output (supports nested objects)."""
    if not text:
        return None
    # Strip <think>...</think> blocks (qwen3 thinking mode)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Match JSON with up to one level of nesting
    match = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


class BrainEngine:
    """
    Direct LLM inference for IluminatyBrain.
    No Ollama required — loads model directly into GPU.
    """

    def __init__(self, backend: str, model_ref):
        """
        backend: 'llamacpp' | 'transformers'
        model_ref: loaded model object
        """
        self._backend = backend
        self._model = model_ref
        self._tokenizer = None   # only for transformers backend
        self._stats = {"calls": 0, "errors": 0, "total_ms": 0.0}

    # ─── Constructors ─────────────────────────────────────────────────────────

    @classmethod
    def from_ollama_blob(cls, model_name: str = "qwen3:4b") -> "BrainEngine":
        """
        Load model weights from existing Ollama GGUF blob — zero download.
        Tries llama-cpp with CUDA first. If CUDA unavailable, falls back to
        downloading the HuggingFace BF16/INT4 version via transformers.
        """
        blob_path = _find_ollama_gguf(model_name)

        # Try llama-cpp with GPU first
        if blob_path:
            try:
                engine = cls._load_llamacpp(str(blob_path), model_name)
                # Quick check: did it actually load on GPU?
                test = engine._model("test", max_tokens=1, echo=False)
                return engine
            except Exception as e:
                logger.warning("llama-cpp load failed (%s) — falling back to transformers", e)

        # Fallback: map Ollama name to HuggingFace ID and load via transformers
        _OLLAMA_TO_HF = {
            "qwen2.5:7b":   "Qwen/Qwen2.5-7B-Instruct",
            "qwen2.5:3b":   "Qwen/Qwen2.5-3B-Instruct",
            "qwen3:4b":     "Qwen/Qwen3-4B",
            "qwen3-vl:4b":  "Qwen/Qwen2.5-VL-7B-Instruct",
            "deepseek-r1:8b": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        }
        base = model_name.split(":")[0] + ":" + model_name.split(":")[-1]
        hf_id = _OLLAMA_TO_HF.get(model_name) or _OLLAMA_TO_HF.get(base)
        if hf_id:
            logger.info("Falling back to HuggingFace: %s", hf_id)
            print(f"[IluminatyBrain] Usando transformers+GPU para {hf_id} (llama-cpp sin CUDA)")
            return cls.from_huggingface(hf_id, load_in_4bit=True)

        raise RuntimeError(
            f"No se pudo cargar '{model_name}'.\n"
            f"Instala llama-cpp con CUDA o usa --hf con el ID de HuggingFace.\n"
            f"Alternativa: python -m iluminaty.brain_chat --hf --model Qwen/Qwen2.5-7B-Instruct"
        )

    @classmethod
    def from_gguf(cls, path: str) -> "BrainEngine":
        """Load from any GGUF file path."""
        return cls._load_llamacpp(path, os.path.basename(path))

    @classmethod
    def _load_llamacpp(cls, gguf_path: str, name: str) -> "BrainEngine":
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "llama-cpp-python not installed. Run:\n"
                "pip install llama-cpp-python --extra-index-url "
                "https://abetlen.github.io/llama-cpp-python/whl/cu128"
            )
        import multiprocessing
        t0 = time.time()
        llm = Llama(
            model_path=gguf_path,
            n_gpu_layers=-1,        # all layers on GPU
            n_ctx=2048,             # context window
            n_threads=min(8, multiprocessing.cpu_count()),
            verbose=False,
        )
        elapsed = time.time() - t0
        logger.info("LlamaCpp loaded '%s' in %.1fs", name, elapsed)
        print(f"[IluminatyBrain] Loaded {name} via llama.cpp in {elapsed:.1f}s (GPU)")
        engine = cls("llamacpp", llm)
        return engine

    @classmethod
    def from_huggingface(
        cls,
        model_id: str = "Qwen/Qwen3-4B",
        load_in_4bit: bool = False,
    ) -> "BrainEngine":
        """
        Load from HuggingFace (downloads once, then cached in ~/.cache/huggingface).
        BF16 = 2.5GB VRAM for Qwen3-4B — fine-tunable via LoRA.
        INT4 (load_in_4bit=True) = 1.3GB VRAM — faster but less precise.
        """
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            import torch
        except ImportError:
            raise ImportError("transformers + torch required. Already installed.")

        logger.info("Loading %s from HuggingFace...", model_id)
        t0 = time.time()

        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

        def _load_model(device_map, quant_config=None):
            kwargs = {
                "device_map": device_map,
                "trust_remote_code": True,
            }
            if quant_config:
                kwargs["quantization_config"] = quant_config
            else:
                kwargs["torch_dtype"] = torch.bfloat16
            return AutoModelForCausalLM.from_pretrained(model_id, **kwargs)

        bnb_config = None
        if load_in_4bit:
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )

        try:
            model = _load_model("cuda", bnb_config)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if not load_in_4bit:
                # Retry with INT4 quantization
                logger.warning("CUDA OOM with BF16 — retrying with INT4 quantization")
                print("[IluminatyBrain] GPU sin memoria con BF16, intentando INT4...")
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                )
                try:
                    model = _load_model("cuda", bnb_config)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    logger.warning("CUDA OOM even with INT4 — falling back to CPU")
                    print("[IluminatyBrain] GPU sin memoria incluso con INT4, usando CPU...")
                    model = _load_model("cpu")
            else:
                # Already INT4 and still OOM — fall back to CPU
                logger.warning("CUDA OOM with INT4 — falling back to CPU")
                print("[IluminatyBrain] GPU sin memoria con INT4, usando CPU...")
                model = _load_model("cpu")

        elapsed = time.time() - t0
        if torch.cuda.is_available():
            vram_gb = torch.cuda.memory_allocated() / 1024**3
            logger.info("HuggingFace loaded '%s' in %.1fs (%.1fGB VRAM)", model_id, elapsed, vram_gb)
            print(f"[IluminatyBrain] Loaded {model_id} in {elapsed:.1f}s ({vram_gb:.1f}GB VRAM)")
        else:
            logger.info("HuggingFace loaded '%s' in %.1fs (CPU)", model_id, elapsed)
            print(f"[IluminatyBrain] Loaded {model_id} in {elapsed:.1f}s (CPU)")

        engine = cls("transformers", model)
        engine._tokenizer = tokenizer
        return engine

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str) -> "BrainEngine":
        """
        Load a fine-tuned IluminatyBrain checkpoint (LoRA merged or base).
        checkpoint_path: directory with adapter_config.json or full model.
        """
        return cls.from_huggingface(checkpoint_path)

    # ─── Inference ────────────────────────────────────────────────────────────

    def decide(
        self,
        world: dict,
        goal: str = "Help the user",
        history: Optional[list[dict]] = None,
    ) -> Optional[dict]:
        """
        Main inference call: world state + goal → action dict.
        Returns parsed action dict or None on failure.
        """
        history = history or []
        prompt = _world_to_prompt(world, goal, history)
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"

        t0 = time.time()
        try:
            if self._backend == "llamacpp":
                text = self._infer_llamacpp(full_prompt)
            else:
                text = self._infer_transformers(full_prompt)
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("[BrainEngine] inference error: %s", e)
            return None

        elapsed_ms = (time.time() - t0) * 1000
        self._stats["calls"] += 1
        self._stats["total_ms"] += elapsed_ms

        action = _parse_json(text)
        if action:
            logger.debug("[BrainEngine] %.0fms -> %s", elapsed_ms, json.dumps(action))
        else:
            self._stats["errors"] += 1
            logger.warning("[BrainEngine] failed to parse JSON from: %s", text[:100])
        return action

    def _infer_llamacpp(self, prompt: str) -> str:
        # Combine few-shot examples with the actual prompt for better JSON adherence
        full = SYSTEM_PROMPT + "\n" + _FEW_SHOT + "\n" + prompt.split("GOAL:")[-1].strip() if "GOAL:" in prompt else SYSTEM_PROMPT + "\n" + _FEW_SHOT + "\n" + prompt
        # Strip the full system from prompt since we embed it above
        result = self._model(
            full,
            max_tokens=64,
            temperature=0.05,
            stop=["\n\n", "Example:", "GOAL:"],
            echo=False,
        )
        return result["choices"][0]["text"].strip()

    def _infer_transformers(self, prompt: str) -> str:
        import torch
        device = next(self._model.parameters()).device
        inputs = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=1024,
        ).to(device)
        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=128,
                temperature=0.05,
                do_sample=False,      # greedy for determinism
                pad_token_id=self._tokenizer.eos_token_id,
            )
        # Decode only new tokens (skip input)
        new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # ─── Status ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        avg_ms = (
            self._stats["total_ms"] / max(1, self._stats["calls"])
        )
        return {
            "backend": self._backend,
            "calls": self._stats["calls"],
            "errors": self._stats["errors"],
            "avg_latency_ms": round(avg_ms, 1),
        }

    def __repr__(self) -> str:
        return f"BrainEngine(backend={self._backend}, calls={self._stats['calls']})"
