"""IPA v3 — Real Eyes Demo: Live screen perception with descriptions.

The AI SEES your screen in real-time and DESCRIBES what's happening.
Combines IPA motion tracking + SmolVLM2 visual descriptions.

Usage:
    python -m ipa.demo_realtime                    # Default: 3fps, GPU
    python -m ipa.demo_realtime --fps 5 --device cuda
    python -m ipa.demo_realtime --duration 60      # Run for 60 seconds
"""
from __future__ import annotations
import argparse
import io
import os
import sys
import time
import threading

import numpy as np
from PIL import Image

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── VLM Loader ──────────────────────────────────────────────────────────────

class MiniVLM:
    """Minimal SmolVLM2 wrapper for on-demand frame description."""

    def __init__(self, device: str = "cuda"):
        self._model = None
        self._processor = None
        self._device = device
        self._loaded = False
        self._lock = threading.Lock()

    _CHAT_TEMPLATE = (
        "<|im_start|>{% for message in messages %}"
        "{{message['role'] | capitalize}}"
        "{% if message['content'][0]['type'] == 'image' %}{{':'}}{% else %}{{': '}}{% endif %}"
        "{% for line in message['content'] %}"
        "{% if line['type'] == 'text' %}{{line['text']}}"
        "{% elif line['type'] == 'image' %}{{ '<image>' }}{% endif %}"
        "{% endfor %}<end_of_utterance>\n{% endfor %}"
        "{% if add_generation_prompt %}{{ 'Assistant:' }}{% endif %}"
    )

    def load(self):
        if self._loaded:
            return
        import torch
        from transformers import (
            SmolVLMImageProcessorPil,
            SmolVLMVideoProcessor,
            SmolVLMProcessor,
            SmolVLMForConditionalGeneration,
            AutoTokenizer,
        )

        model_name = os.environ.get("ILUMINATY_VLM_MODEL", "HuggingFaceTB/SmolVLM2-256M-Instruct")
        print(f"  \033[90mLoading VLM: {model_name}...\033[0m")
        t0 = time.time()

        # Build processor manually (same as ILUMINATY's visual_engine.py)
        image_proc = SmolVLMImageProcessorPil.from_pretrained(model_name, do_image_splitting=False)
        video_proc = SmolVLMVideoProcessor(image_processor=image_proc)
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        tokenizer.chat_template = self._CHAT_TEMPLATE
        self._processor = SmolVLMProcessor(
            image_processor=image_proc, tokenizer=tokenizer,
            video_processor=video_proc, image_seq_len=64,
        )
        self._processor.chat_template = self._CHAT_TEMPLATE

        dtype = torch.float16 if self._device == "cuda" else torch.float32
        self._model = SmolVLMForConditionalGeneration.from_pretrained(
            model_name, torch_dtype=dtype, low_cpu_mem_usage=True,
        ).to(self._device)
        self._model.eval()
        self._loaded = True
        print(f"  \033[90mVLM loaded in {time.time()-t0:.1f}s\033[0m")

    def describe(self, image: Image.Image, max_tokens: int = 80) -> tuple[str, float]:
        """Describe an image. Returns (description, latency_ms)."""
        if not self._loaded:
            self.load()

        import torch

        # Resize for VLM
        img = image.resize((384, 384), Image.LANCZOS) if max(image.size) > 384 else image

        messages = [{"role": "user", "content": [
            {"type": "image"},
            {"type": "text", "text": "Describe what you see on this screen in one sentence. Be specific about the content, application, and any visible activity."},
        ]}]

        t0 = time.perf_counter()
        with self._lock:
            prompt = self._processor.apply_chat_template(messages, add_generation_prompt=True)
            inputs = self._processor(text=prompt, images=[img], return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

            with torch.no_grad():
                out = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
            text = self._processor.batch_decode(out, skip_special_tokens=True)[0]

        latency = (time.perf_counter() - t0) * 1000

        # Extract assistant response
        if "Assistant:" in text:
            text = text.split("Assistant:")[-1].strip()
        elif "assistant" in text.lower():
            parts = text.lower().split("assistant")
            text = parts[-1].strip(": \n")

        return text, latency

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ── Main Demo ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IPA v3 — Real Eyes: Live Perception Demo")
    parser.add_argument("--fps", type=int, default=3, help="Capture FPS")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--device", default="cuda", help="Device for encoder (cpu/cuda)")
    parser.add_argument("--describe-interval", type=float, default=3.0, help="Seconds between VLM descriptions")
    parser.add_argument("--no-vlm", action="store_true", help="Disable VLM descriptions (IPA only)")
    args = parser.parse_args()

    import mss
    from ipa.engine import IPAEngine

    print(f"\n\033[1;36m{'━'*70}\033[0m")
    print(f"\033[1;37m  IPA v3 — REAL EYES: Live Visual Perception Demo\033[0m")
    print(f"\033[1;36m{'━'*70}\033[0m")
    print(f"  \033[90mFPS: {args.fps} | Duration: {args.duration}s | Device: {args.device}\033[0m")
    print(f"  \033[90mVLM: {'disabled' if args.no_vlm else 'SmolVLM2 (descriptions every ~{:.0f}s)'.format(args.describe_interval)}\033[0m")

    # Init IPA
    engine = IPAEngine(config={"device": args.device, "int8": False})

    # Init VLM (optional)
    vlm = None
    if not args.no_vlm:
        try:
            vlm = MiniVLM(device=args.device)
        except Exception as e:
            print(f"  \033[33mVLM unavailable: {e}\033[0m")

    print(f"\n  \033[1;33m▶ Press ENTER to start capturing your screen...\033[0m", end="")
    input()

    print(f"\n\033[1;32m  ● LIVE PERCEPTION ACTIVE — watching your screen\033[0m\n")
    print(f"  {'Time':>7}  {'Motion':<18}  {'Patches':>8}  {'Type':>3}  {'Latency':>8}  Description")
    print(f"  {'─'*90}")

    interval = 1.0 / args.fps
    start = time.time()
    last_describe = 0
    last_motion = ""
    frame_count = 0
    total_vlm_tokens = 0
    total_ipa_tokens = 0
    descriptions = []
    latencies = []

    with mss.mss() as sct:
        mon = sct.monitors[1]
        next_cap = time.time()

        while (time.time() - start) < args.duration:
            now = time.time()
            if now < next_cap:
                time.sleep(max(0, next_cap - now - 0.001))
                continue
            next_cap = now + interval

            # Capture
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

            # IPA process
            t0 = time.perf_counter()
            frame = engine.feed(img, metadata={"window_name": "live"})
            ipa_ms = (time.perf_counter() - t0) * 1000
            latencies.append(ipa_ms)
            frame_count += 1

            # Motion
            motion = engine.motion(seconds=3)
            elapsed = now - start

            # Description (VLM) on interval or scene change
            desc = ""
            vlm_ms = 0
            do_describe = (
                vlm is not None
                and vlm.is_loaded or (frame_count <= 2)  # first frames trigger load
            )
            time_for_describe = (now - last_describe) >= args.describe_interval
            motion_changed = motion.motion_type != last_motion and last_motion != ""

            if vlm is not None and (time_for_describe or motion_changed or frame_count == 1):
                try:
                    if not vlm.is_loaded:
                        vlm.load()
                    desc, vlm_ms = vlm.describe(img, max_tokens=60)
                    last_describe = now
                    descriptions.append({"t": round(elapsed, 1), "desc": desc, "vlm_ms": round(vlm_ms)})
                    total_vlm_tokens += len(desc) // 4
                except Exception as e:
                    desc = f"[VLM error: {e}]"

            last_motion = motion.motion_type
            total_ipa_tokens += 59  # IPA context cost

            # Print
            mtype = motion.motion_type
            color = (
                "\033[90m" if mtype == "static" else
                "\033[33m" if mtype in ("typing", "cursor") else
                "\033[35m" if mtype == "video" else
                "\033[36m"
            )

            lat_str = f"{ipa_ms:.0f}ms"
            if vlm_ms > 0:
                lat_str = f"{ipa_ms:.0f}+{vlm_ms:.0f}ms"

            desc_str = ""
            if desc:
                # Truncate to fit terminal
                desc_short = desc[:80] + "..." if len(desc) > 80 else desc
                desc_str = f"\033[1;37m{desc_short}\033[0m"

            print(f"  [{elapsed:05.1f}s]  {color}{mtype:<18}\033[0m  {frame.n_changed:>6}    {frame.frame_type}  {lat_str:>8}  {desc_str}")

    # ── Summary ─────────────────────────────────────────────────────────
    ctx = engine.context(seconds=args.duration)
    buf = engine.status()["stream"]
    lat_arr = np.array(latencies)

    print(f"\n\033[1;36m{'━'*70}\033[0m")
    print(f"\033[1;37m  SUMMARY\033[0m")
    print(f"\033[1;36m{'━'*70}\033[0m")
    print(f"  Frames processed:     {frame_count}")
    print(f"  Avg IPA latency:      {np.mean(lat_arr):.0f} ms")
    print(f"  VLM descriptions:     {len(descriptions)}")
    print(f"  Buffer memory:        {buf['memory_kb']:.0f} KB")
    print(f"  IPA tokens total:     {total_ipa_tokens} ({total_ipa_tokens/max(frame_count,1):.0f}/frame)")
    print(f"  VLM tokens total:     {total_vlm_tokens}")
    print(f"  Equivalent screenshots: {frame_count * 30000} tokens (saved {(1 - (total_ipa_tokens+total_vlm_tokens)/max(frame_count*30000,1))*100:.1f}%)")

    if descriptions:
        print(f"\n\033[1;37m  DESCRIPTIONS LOG:\033[0m")
        for d in descriptions:
            print(f"  \033[90m[{d['t']:05.1f}s]\033[0m {d['desc'][:100]}")

    print(f"\n\033[1;37m  FINAL CONTEXT (what the AI sees now):\033[0m")
    print(f"  {ctx.to_text()}")
    print(f"\n\033[1;36m{'━'*70}\033[0m\n")


if __name__ == "__main__":
    main()
