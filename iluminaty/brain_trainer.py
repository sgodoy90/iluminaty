"""
ILUMINATY - Brain Trainer
==========================
Fine-tune a small LLM on ILUMINATY action data using LoRA.

The training data comes from AppBehaviorCache — every action
ILUMINATY executes gets recorded with:
  - app_name, window_title (context)
  - action + params (what was done)
  - success/failure (label)

We convert this into (WorldState prompt, Action JSON) pairs
and fine-tune Qwen3-4B (or any HuggingFace causal LM) with LoRA.

After training, the model is saved and can be loaded directly
via BrainEngine.from_checkpoint("./brain_checkpoints/latest").

Usage:
  python -m iluminaty.brain_trainer --model Qwen/Qwen3-4B \\
    --db ~/.iluminaty/app_behavior_cache.sqlite3 \\
    --output ./brain_checkpoints \\
    --epochs 3 --min-samples 100

Requirements:
  pip install peft accelerate bitsandbytes datasets trl
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Data extraction from AppBehaviorCache ───────────────────────────────────

def load_training_data(db_path: str, min_samples: int = 50) -> list[dict]:
    """
    Load successful action outcomes from AppBehaviorCache SQLite.
    Returns list of {prompt, completion} training pairs.
    """
    db = pathlib.Path(db_path).expanduser()
    if not db.exists():
        raise FileNotFoundError(
            f"AppBehaviorCache not found at {db}.\n"
            f"Run ILUMINATY with --actions for a few sessions first to collect data."
        )

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        """
        SELECT app_name, window_title, action, params_sig,
               success, reason, method_used, duration_ms
        FROM outcomes
        WHERE success = 1
        ORDER BY ts_ms DESC
        LIMIT 10000
        """
    ).fetchall()
    conn.close()

    if len(rows) < min_samples:
        raise ValueError(
            f"Only {len(rows)} successful examples in cache (need {min_samples}).\n"
            f"Keep using ILUMINATY to collect more data, then re-run training."
        )

    logger.info("Loaded %d training examples from %s", len(rows), db)
    pairs = []
    for row in rows:
        app_name, window_title, action, params_sig, success, reason, method, duration_ms = row
        try:
            params = json.loads(params_sig) if params_sig else {}
        except Exception:
            params = {}

        # Build a synthetic WorldState prompt
        prompt = _build_synthetic_prompt(app_name, window_title, action, params)

        # Build the target action JSON
        action_dict = {"action": action}
        action_dict.update(params)
        completion = json.dumps(action_dict, ensure_ascii=False)

        pairs.append({"prompt": prompt, "completion": completion})

    return pairs


def _build_synthetic_prompt(
    app_name: str, window_title: str,
    action: str, params: dict,
) -> str:
    """
    Build a minimal WorldState prompt from cached action metadata.
    Real training will use actual WorldState snapshots — this bootstraps.
    """
    # Infer goal from action type
    goal_hints = {
        "click": f"click on element in {app_name}",
        "type_text": f"type text in {app_name}",
        "hotkey": f"use keyboard shortcut in {app_name}",
        "run_command": "execute a terminal command",
        "browser_navigate": "navigate to a URL",
        "focus_window": f"switch to {window_title}",
        "scroll": f"scroll in {app_name}",
    }
    goal = goal_hints.get(action, f"complete task in {app_name}")

    # Infer phase from action
    phase_hints = {
        "click": "interaction", "double_click": "interaction",
        "type_text": "editing", "hotkey": "editing",
        "run_command": "execution", "browser_navigate": "navigation",
        "focus_window": "navigation", "scroll": "navigation",
    }
    phase = phase_hints.get(action, "interaction")

    prompt = (
        f"GOAL: {goal}\n"
        f"surface={app_name} phase={phase} ready=true\n"
        f"window=\"{window_title[:60]}\"\n"
        f"Next action?"
    )
    return prompt


# ─── Training ─────────────────────────────────────────────────────────────────

def train(
    model_id: str = "Qwen/Qwen3-4B",
    db_path: str = "~/.iluminaty/app_behavior_cache.sqlite3",
    output_dir: str = "./brain_checkpoints",
    epochs: int = 3,
    batch_size: int = 4,
    learning_rate: float = 2e-4,
    min_samples: int = 100,
    lora_r: int = 16,
    lora_alpha: int = 32,
    load_in_4bit: bool = True,
):
    """
    Full fine-tuning pipeline: load data → load model → LoRA → train → save.
    """
    try:
        import torch
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer,
            BitsAndBytesConfig, TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, TaskType
        from trl import SFTTrainer
        from datasets import Dataset
    except ImportError as e:
        raise ImportError(
            f"Fine-tuning dependencies missing: {e}\n"
            f"Run: pip install peft accelerate bitsandbytes datasets trl"
        )

    output = pathlib.Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    # ── 1. Load training data ──────────────────────────────────────────────
    print(f"\n[BrainTrainer] Loading training data from {db_path}...")
    pairs = load_training_data(db_path, min_samples=min_samples)
    print(f"[BrainTrainer] {len(pairs)} training examples")

    # Convert to HuggingFace Dataset
    # Format: full text = system + prompt + completion
    from iluminaty.brain_engine import SYSTEM_PROMPT

    texts = []
    for p in pairs:
        text = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{p['prompt']}<|im_end|>\n"
            f"<|im_start|>assistant\n{p['completion']}<|im_end|>"
        )
        texts.append({"text": text})

    dataset = Dataset.from_list(texts)
    print(f"[BrainTrainer] Dataset created: {len(dataset)} examples")

    # ── 2. Load base model ────────────────────────────────────────────────
    print(f"\n[BrainTrainer] Loading {model_id}...")
    t0 = time.time()

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    if load_in_4bit:
        print("[BrainTrainer] Loading in INT4 (bitsandbytes) to save VRAM...")
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            quantization_config=bnb_config,
            device_map="cuda",
            trust_remote_code=True,
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            trust_remote_code=True,
        )

    vram = torch.cuda.memory_allocated() / 1024**3
    print(f"[BrainTrainer] Model loaded in {time.time()-t0:.1f}s ({vram:.1f}GB VRAM)")

    # ── 3. Apply LoRA ─────────────────────────────────────────────────────
    print(f"\n[BrainTrainer] Applying LoRA (r={lora_r}, alpha={lora_alpha})...")
    lora_config = LoraConfig(
        r=lora_r,
        lora_alpha=lora_alpha,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"[BrainTrainer] Trainable params: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    # ── 4. Training arguments ─────────────────────────────────────────────
    training_args = TrainingArguments(
        output_dir=str(output),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=max(1, 8 // batch_size),
        learning_rate=learning_rate,
        fp16=False,
        bf16=True,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        report_to="none",        # no wandb/mlflow
        dataloader_num_workers=0,
        remove_unused_columns=True,
    )

    # ── 5. Train ──────────────────────────────────────────────────────────
    print(f"\n[BrainTrainer] Training {epochs} epochs on {len(dataset)} examples...")
    print(f"[BrainTrainer] Output: {output}")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=512,
        args=training_args,
    )

    t0 = time.time()
    trainer.train()
    elapsed = time.time() - t0
    print(f"\n[BrainTrainer] Training complete in {elapsed/60:.1f} minutes")

    # ── 6. Save ───────────────────────────────────────────────────────────
    latest = output / "latest"
    latest.mkdir(exist_ok=True)
    model.save_pretrained(str(latest))
    tokenizer.save_pretrained(str(latest))
    print(f"[BrainTrainer] Model saved to {latest}")
    print(f"[BrainTrainer] Load with: BrainEngine.from_checkpoint('{latest}')")

    # Save training metadata
    meta = {
        "base_model": model_id,
        "examples": len(pairs),
        "epochs": epochs,
        "lora_r": lora_r,
        "elapsed_minutes": round(elapsed / 60, 1),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (output / "training_meta.json").write_text(json.dumps(meta, indent=2))
    return str(latest)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train IluminatyBrain on your own action data")
    parser.add_argument("--model", default="Qwen/Qwen3-4B",
                        help="Base model from HuggingFace (default: Qwen/Qwen3-4B)")
    parser.add_argument("--db", default="~/.iluminaty/app_behavior_cache.sqlite3",
                        help="Path to AppBehaviorCache SQLite database")
    parser.add_argument("--output", default="./brain_checkpoints",
                        help="Output directory for checkpoints")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--min-samples", type=int, default=100,
                        help="Minimum training examples required")
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--bf16", action="store_true", default=False,
                        help="Load in BF16 instead of INT4 (needs more VRAM)")
    parser.add_argument("--check-data", action="store_true",
                        help="Only check training data, don't train")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.check_data:
        try:
            pairs = load_training_data(args.db, min_samples=1)
            print(f"Training data: {len(pairs)} examples available")
            print("Sample:")
            import random
            sample = random.choice(pairs)
            print(f"  prompt:     {sample['prompt'][:100]}")
            print(f"  completion: {sample['completion']}")
        except Exception as e:
            print(f"Error: {e}")
        return

    train(
        model_id=args.model,
        db_path=args.db,
        output_dir=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        min_samples=args.min_samples,
        lora_r=args.lora_r,
        load_in_4bit=not args.bf16,
    )


if __name__ == "__main__":
    main()
