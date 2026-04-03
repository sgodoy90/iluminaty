"""
ILUMINATY - Local Visual Engine (IPA v2.1)
===========================================
Deep visual loop provider abstraction.

Default provider is fully local, dependency-free, and CPU-safe.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PIL import Image
except Exception:  # pragma: no cover - pillow is expected in runtime but keep soft-fail
    Image = None


@dataclass
class VisualTask:
    ref_id: str
    tick_id: int
    timestamp_ms: int
    monitor: int
    frame_bytes: bytes
    mime_type: str
    app_name: str = "unknown"
    window_title: str = "unknown"
    ocr_text: str = ""
    motion_summary: str = ""
    priority: float = 0.5


@dataclass
class VisualFact:
    kind: str
    text: str
    confidence: float
    monitor: int
    timestamp_ms: int
    source: str
    evidence_ref: str
    tick_id: int


@dataclass
class VisualInference:
    timestamp_ms: int
    tick_id: int
    monitor: int
    summary: str
    confidence: float
    source: str
    evidence_ref: str
    facts: list[VisualFact] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["latency_ms"] = round(self.latency_ms, 2)
        data["confidence"] = round(self.confidence, 3)
        return data


class BaseVisualProvider:
    name = "base"

    def analyze(self, task: VisualTask) -> VisualInference:  # pragma: no cover - interface
        raise NotImplementedError


class LocalNativeVisionProvider(BaseVisualProvider):
    """
    Local default provider.
    Uses native heuristics (OCR + motion + UI/window context) only.
    """

    name = "local_native_vision"

    def analyze(self, task: VisualTask) -> VisualInference:
        t0 = time.time()
        now_ms = int(time.time() * 1000)
        facts: list[VisualFact] = []

        app = (task.app_name or "unknown").strip()
        title = (task.window_title or "unknown").strip()
        ocr = (task.ocr_text or "").strip()
        motion = (task.motion_summary or "").strip()

        facts.append(
            VisualFact(
                kind="surface",
                text=f"Active surface {app} | {title[:120]}",
                confidence=0.85 if app != "unknown" else 0.5,
                monitor=task.monitor,
                timestamp_ms=now_ms,
                source=self.name,
                evidence_ref=task.ref_id,
                tick_id=task.tick_id,
            )
        )

        if ocr:
            short_ocr = " ".join(ocr.split())[:220]
            facts.append(
                VisualFact(
                    kind="text",
                    text=f"Visible text: {short_ocr}",
                    confidence=0.75,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        if motion:
            facts.append(
                VisualFact(
                    kind="motion",
                    text=f"Motion context: {motion[:180]}",
                    confidence=0.62,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        low_blob = f"{app} {title} {ocr} {motion}".lower()
        if ("youtube" in low_blob or "video" in low_blob or "player" in low_blob) and motion:
            facts.append(
                VisualFact(
                    kind="activity",
                    text="Likely video/media playback on active surface",
                    confidence=0.64,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        if any(token in low_blob for token in ("chart", "tradingview", "candlestick", "buy", "sell", "rsi", "macd")):
            facts.append(
                VisualFact(
                    kind="domain_hint",
                    text="Possible trading/chart context detected",
                    confidence=0.61,
                    monitor=task.monitor,
                    timestamp_ms=now_ms,
                    source=self.name,
                    evidence_ref=task.ref_id,
                    tick_id=task.tick_id,
                )
            )

        summary_parts = [f.text for f in facts[:3]]
        summary = " | ".join(summary_parts)[:360] if summary_parts else "No visual facts"
        confidence = sum(f.confidence for f in facts) / max(1, len(facts))

        return VisualInference(
            timestamp_ms=now_ms,
            tick_id=task.tick_id,
            monitor=task.monitor,
            summary=summary,
            confidence=confidence,
            source=self.name,
            evidence_ref=task.ref_id,
            facts=facts,
            latency_ms=(time.time() - t0) * 1000.0,
        )


class LocalSmolVLMProvider(BaseVisualProvider):
    """
    Optional local caption augmentation provider.
    Default remains native/local heuristics; this provider is opt-in.
    """

    name = "local_smolvlm"

    @staticmethod
    def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
            return max(minimum, min(maximum, value))
        except Exception:
            return default

    @staticmethod
    def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.environ.get(name, str(default)))
            return max(minimum, min(maximum, value))
        except Exception:
            return default

    @staticmethod
    def _env_choice(name: str, default: str, allowed: set[str]) -> str:
        value = os.environ.get(name, default).strip().lower()
        if value in allowed:
            return value
        return default

    def __init__(self, caption_enabled: Optional[bool] = None):
        self._base = LocalNativeVisionProvider()
        if caption_enabled is None:
            caption_enabled = os.environ.get("ILUMINATY_VLM_CAPTION", "0") == "1"
        self._caption_enabled = bool(caption_enabled)
        self._caption_backend = None
        self._backend_mode = "none"
        self._status = "disabled"
        self._errors = 0
        self._vlm_image_size = self._env_int("ILUMINATY_VLM_IMAGE_SIZE", 384, 224, 768)
        self._vlm_max_new_tokens = self._env_int("ILUMINATY_VLM_MAX_TOKENS", 64, 16, 256)
        self._caption_min_interval_ms = self._env_int("ILUMINATY_VLM_MIN_INTERVAL_MS", 900, 0, 15000)
        self._caption_keepalive_ms = self._env_int("ILUMINATY_VLM_KEEPALIVE_MS", 7000, 500, 120000)
        self._caption_priority_threshold = self._env_float("ILUMINATY_VLM_PRIORITY_THRESHOLD", 0.55, 0.0, 1.0)
        self._device_policy = self._env_choice("ILUMINATY_VLM_DEVICE", "auto", {"auto", "cpu", "cuda", "gpu"})
        self._dtype_policy = self._env_choice("ILUMINATY_VLM_DTYPE", "auto", {"auto", "fp16", "bf16", "fp32"})
        self._runtime_device = "cpu"
        self._runtime_dtype = "fp32"
        self._cuda_available = False
        self._torch_module = None
        self._vlm_torch_dtype = None
        self._last_caption_ms_by_monitor: dict[int, int] = {}
        self._attempts = 0
        self._successes = 0
        if self._caption_enabled:
            self._try_init_caption_backend()

    _VLM_PROMPT = (
        "Describe exactly what is visible on this screen. "
        "Include: application name, main content, any visible text, numbers, UI elements, errors, or anomalies. "
        "Be specific and concise."
    )
    _vlm_processor = None
    _vlm_model = None

    _SMOLVLM_CHAT_TEMPLATE = (
        "<|im_start|>{% for message in messages %}"
        "{{message['role'] | capitalize}}"
        "{% if message['content'][0]['type'] == 'image' %}{{':'}}{% else %}{{': '}}{% endif %}"
        "{% for line in message['content'] %}"
        "{% if line['type'] == 'text' %}{{line['text']}}"
        "{% elif line['type'] == 'image' %}{{ '<image>' }}{% endif %}"
        "{% endfor %}<end_of_utterance>\n{% endfor %}"
        "{% if add_generation_prompt %}{{ 'Assistant:' }}{% endif %}"
    )

    @staticmethod
    def _resolve_torch_runtime(torch_module, device_policy: str, dtype_policy: str) -> dict:
        cuda_available = bool(
            getattr(torch_module, "cuda", None)
            and callable(getattr(torch_module.cuda, "is_available", None))
            and torch_module.cuda.is_available()
        )
        policy = (device_policy or "auto").strip().lower()
        if policy == "gpu":
            policy = "cuda"
        wants_cuda = policy in {"auto", "cuda"}
        use_cuda = bool(wants_cuda and cuda_available)
        device = "cuda:0" if use_cuda else "cpu"

        dtype_key = (dtype_policy or "auto").strip().lower()
        if not use_cuda:
            return {
                "device": device,
                "dtype_obj": torch_module.float32,
                "dtype_label": "fp32",
                "use_cuda": False,
                "cuda_available": cuda_available,
            }

        # GPU path
        if dtype_key in {"fp32"}:
            dtype_obj = torch_module.float32
            dtype_label = "fp32"
        elif dtype_key in {"bf16"}:
            bf16_supported = bool(
                callable(getattr(torch_module.cuda, "is_bf16_supported", None))
                and torch_module.cuda.is_bf16_supported()
            )
            if bf16_supported:
                dtype_obj = torch_module.bfloat16
                dtype_label = "bf16"
            else:
                dtype_obj = torch_module.float16
                dtype_label = "fp16"
        else:
            # auto/fp16
            dtype_obj = torch_module.float16
            dtype_label = "fp16"

        return {
            "device": device,
            "dtype_obj": dtype_obj,
            "dtype_label": dtype_label,
            "use_cuda": True,
            "cuda_available": cuda_available,
        }

    def _move_inputs_to_runtime(self, inputs: dict) -> dict:
        if self._torch_module is None or not inputs:
            return inputs
        moved: dict = {}
        runtime_device = str(self._runtime_device or "cpu")
        runtime_dtype = self._vlm_torch_dtype
        for key, value in inputs.items():
            if not hasattr(value, "to"):
                moved[key] = value
                continue
            try:
                if (
                    runtime_device.startswith("cuda")
                    and runtime_dtype is not None
                    and hasattr(value, "is_floating_point")
                    and callable(value.is_floating_point)
                    and value.is_floating_point()
                ):
                    moved[key] = value.to(device=runtime_device, dtype=runtime_dtype)
                else:
                    moved[key] = value.to(device=runtime_device)
            except Exception:
                moved[key] = value
        return moved

    def _switch_model_to_cpu(self) -> None:
        if self._vlm_model is None:
            return
        try:
            self._vlm_model.to("cpu")
            if hasattr(self._vlm_model, "float"):
                self._vlm_model = self._vlm_model.float()
        except Exception:
            pass
        if self._torch_module is not None:
            self._vlm_torch_dtype = self._torch_module.float32
        self._runtime_device = "cpu"
        self._runtime_dtype = "fp32"

    def _try_init_caption_backend(self) -> None:
        backend = os.environ.get("ILUMINATY_VLM_BACKEND", "smol").strip().lower()
        try:
            if backend in ("blip", "blip_base"):
                from transformers import pipeline  # type: ignore
                import torch  # type: ignore

                runtime = self._resolve_torch_runtime(torch, self._device_policy, self._dtype_policy)
                self._cuda_available = bool(runtime["cuda_available"])

                model_id = os.environ.get("ILUMINATY_VLM_MODEL", "").strip() or "Salesforce/blip-image-captioning-base"
                self._caption_backend = pipeline(
                    "image-to-text",
                    model=model_id,
                    device=0 if runtime["use_cuda"] else -1,
                )
                self._runtime_device = "cuda:0" if runtime["use_cuda"] else "cpu"
                self._runtime_dtype = "fp32"
                self._backend_mode = "blip"
                self._status = "ready"
                return

            import torch  # type: ignore
            from transformers import (  # type: ignore
                SmolVLMImageProcessorPil,
                SmolVLMVideoProcessor,
                SmolVLMProcessor,
                SmolVLMForConditionalGeneration,
                AutoTokenizer,
            )

            runtime = self._resolve_torch_runtime(torch, self._device_policy, self._dtype_policy)
            self._cuda_available = bool(runtime["cuda_available"])
            self._torch_module = torch
            self._runtime_device = str(runtime["device"])
            self._runtime_dtype = str(runtime["dtype_label"])
            self._vlm_torch_dtype = runtime["dtype_obj"]

            model_id = os.environ.get("ILUMINATY_VLM_MODEL", "").strip() or "HuggingFaceTB/SmolVLM2-500M-Instruct"
            image_processor = SmolVLMImageProcessorPil.from_pretrained(
                model_id, do_image_splitting=False
            )
            video_processor = SmolVLMVideoProcessor(image_processor=image_processor)
            tokenizer = AutoTokenizer.from_pretrained(model_id)
            tokenizer.chat_template = self._SMOLVLM_CHAT_TEMPLATE
            proc = SmolVLMProcessor(
                image_processor=image_processor,
                tokenizer=tokenizer,
                video_processor=video_processor,
                image_seq_len=64,
            )
            proc.chat_template = self._SMOLVLM_CHAT_TEMPLATE

            self._vlm_processor = proc
            self._vlm_model = SmolVLMForConditionalGeneration.from_pretrained(
                model_id,
                torch_dtype=runtime["dtype_obj"],
                low_cpu_mem_usage=True,
            )

            if runtime["use_cuda"]:
                try:
                    self._vlm_model.to(runtime["device"])
                except Exception:
                    # Graceful fallback to CPU if CUDA runtime fails.
                    self._switch_model_to_cpu()

            use_int8 = os.environ.get("ILUMINATY_VLM_INT8", "1") == "1"
            if use_int8 and not runtime["use_cuda"]:
                try:
                    from torch.ao.quantization import quantize_dynamic  # type: ignore

                    self._vlm_model = quantize_dynamic(self._vlm_model, {torch.nn.Linear}, dtype=torch.qint8)
                except Exception:
                    pass

            self._vlm_model.eval()
            self._caption_backend = True  # sentinel — real objects are _vlm_processor/_vlm_model
            self._backend_mode = "smol"
            self._status = "ready"
        except Exception as e:
            logger.warning("VLM backend init failed: %s", e)
            self._caption_backend = None
            self._vlm_processor = None
            self._vlm_model = None
            self._backend_mode = "none"
            self._status = "failed"
            self._errors += 1

    def _smol_processor_inputs(self, prompt_text: str, img):
        if self._vlm_processor is None:
            return None
        try:
            out = self._vlm_processor(text=prompt_text, images=[img], return_tensors="pt")
        except Exception:
            try:
                out = self._vlm_processor(text=prompt_text, images=[img])
            except Exception:
                return None
        try:
            input_ids = out.get("input_ids")
            pixel_values = out.get("pixel_values")
            has_tensor_payload = hasattr(input_ids, "shape") or hasattr(pixel_values, "shape")
            return out if has_tensor_payload else None
        except Exception:
            return None

    def _caption(self, image_bytes: bytes) -> str:
        if not self._caption_backend or Image is None:
            return ""
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((self._vlm_image_size, self._vlm_image_size))

            if self._backend_mode == "blip":
                out = self._caption_backend(img, max_new_tokens=self._vlm_max_new_tokens)
                if not out:
                    return ""
                text = str(out[0].get("generated_text", "")).strip()
                return text[:280]

            if self._vlm_processor is None or self._vlm_model is None:
                return ""

            import torch  # type: ignore

            messages = [
                {
                    "role": "user",
                    "content": [{"type": "image"}, {"type": "text", "text": self._VLM_PROMPT}],
                }
            ]
            prompt_text = self._vlm_processor.apply_chat_template(messages, add_generation_prompt=True)
            inputs = self._smol_processor_inputs(prompt_text, img)
            if not inputs:
                return ""
            inputs = self._move_inputs_to_runtime(inputs)
            with torch.no_grad():
                output_ids = self._vlm_model.generate(
                    **inputs, max_new_tokens=self._vlm_max_new_tokens, do_sample=False
                )
            if hasattr(self._vlm_processor, "decode"):
                decoded = self._vlm_processor.decode(output_ids[0], skip_special_tokens=True)
            else:
                decoded = self._vlm_processor.batch_decode(output_ids, skip_special_tokens=True)[0]
            if "Assistant:" in decoded:
                decoded = decoded.split("Assistant:")[-1]
            return str(decoded).strip()[:300]
        except RuntimeError as e:
            # CUDA OOM or runtime mismatch: degrade gracefully to CPU mode.
            message = str(e).lower()
            if "cuda" in message and ("out of memory" in message or "device-side assert" in message):
                self._switch_model_to_cpu()
            self._errors += 1
            return ""
        except Exception as e:
            logger.debug("VLM caption failed: %s", e)
            self._errors += 1
            return ""

    def _coerce_base(self, base: VisualInference, source: str, facts: list[VisualFact], latency_ms: float) -> VisualInference:
        # Always include image_caption (VLM) fact in summary if present
        vlm_facts = [f for f in facts if f.kind == "image_caption"]
        other_facts = [f for f in facts if f.kind != "image_caption"][:3]
        ordered = other_facts + vlm_facts[:1]
        summary_parts = [f.text for f in ordered]
        summary = " | ".join(summary_parts)[:500] if summary_parts else "No visual facts"
        confidence = sum(f.confidence for f in facts) / max(1, len(facts))
        return VisualInference(
            timestamp_ms=base.timestamp_ms,
            tick_id=base.tick_id,
            monitor=base.monitor,
            summary=summary,
            confidence=confidence,
            source=source,
            evidence_ref=base.evidence_ref,
            facts=facts,
            latency_ms=latency_ms,
        )

    def analyze(self, task: VisualTask) -> VisualInference:
        t0 = time.time()
        base = self._base.analyze(task)
        facts = list(base.facts)

        caption = ""
        if self._caption_enabled and self._status == "ready":
            now_ms = int(time.time() * 1000)
            last_ms = self._last_caption_ms_by_monitor.get(task.monitor, 0)
            elapsed = now_ms - int(last_ms)
            high_priority = float(task.priority) >= self._caption_priority_threshold
            allow_interval = elapsed >= self._caption_min_interval_ms
            allow_keepalive = high_priority or elapsed >= self._caption_keepalive_ms
            should_run = allow_interval and allow_keepalive

            if should_run:
                self._attempts += 1
                caption = self._caption(task.frame_bytes)
                self._last_caption_ms_by_monitor[task.monitor] = now_ms
            if caption:
                self._successes += 1
                facts.append(
                    VisualFact(
                        kind="image_caption",
                        text=f"VLM: {caption}",
                        confidence=0.75,
                        monitor=task.monitor,
                        timestamp_ms=base.timestamp_ms,
                        source=f"{self.name}:caption",
                        evidence_ref=task.ref_id,
                        tick_id=task.tick_id,
                    )
                )
        latency_ms = max(base.latency_ms, (time.time() - t0) * 1000.0)
        return self._coerce_base(base=base, source=self.name, facts=facts, latency_ms=latency_ms)

    def status(self) -> dict:
        success_rate = round((self._successes / max(1, self._attempts)) * 100.0, 3)
        return {
            "mode": self._backend_mode,
            "status": self._status,
            "device_policy": self._device_policy,
            "dtype_policy": self._dtype_policy,
            "runtime_device": self._runtime_device,
            "runtime_dtype": self._runtime_dtype,
            "cuda_available": self._cuda_available,
            "image_size": self._vlm_image_size,
            "max_new_tokens": self._vlm_max_new_tokens,
            "min_interval_ms": self._caption_min_interval_ms,
            "keepalive_ms": self._caption_keepalive_ms,
            "priority_threshold": round(float(self._caption_priority_threshold), 3),
            "attempts": self._attempts,
            "successes": self._successes,
            "success_rate_pct": success_rate,
            "errors": self._errors,
        }


def _build_default_provider() -> BaseVisualProvider:
    """
    Provider strategy:
    - default: fully local/native heuristics (`native`)
    - optional: caption-augmented local provider (`smolvlm` / env flag)
    """
    mode = os.environ.get("ILUMINATY_VISION_PROVIDER", "native").strip().lower()
    caption_flag = os.environ.get("ILUMINATY_VLM_CAPTION", "0") == "1"
    if mode in ("smolvlm", "caption", "hybrid") or caption_flag:
        return LocalSmolVLMProvider(caption_enabled=True)
    return LocalNativeVisionProvider()


class VisualEngine:
    """
    Dedicated worker for deep visual inference.
    Queue is bounded with drop-oldest policy to avoid backlog.
    """

    def __init__(
        self,
        provider: Optional[BaseVisualProvider] = None,
        max_queue: int = 24,
        max_history: int = 600,
    ):
        self._provider = provider or _build_default_provider()
        self._queue: deque[VisualTask] = deque(maxlen=max(4, max_queue))
        self._history: deque[VisualInference] = deque(maxlen=max(60, max_history))
        self._latest_by_monitor: dict[int, VisualInference] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._dropped = 0
        self._processed = 0
        self._failures = 0
        self._processed_by_monitor: dict[int, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker, daemon=True, name="ipa-visual-worker")
            self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    def enqueue(self, task: VisualTask) -> bool:
        with self._cond:
            if not self._running:
                return False
            if len(self._queue) >= self._queue.maxlen:
                self._queue.popleft()
                self._dropped += 1
            self._queue.append(task)
            self._cond.notify()
            return True

    def _worker(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(timeout=0.5)
                if not self._running:
                    return
                # RT bias: consume newest task, drop stale backlog.
                task = self._queue.pop()
                self._dropped += len(self._queue)
                self._queue.clear()

            try:
                inference = self._provider.analyze(task)
                with self._lock:
                    self._latest_by_monitor[inference.monitor] = inference
                    self._history.append(inference)
                    self._processed += 1
                    self._processed_by_monitor[inference.monitor] = self._processed_by_monitor.get(inference.monitor, 0) + 1
            except Exception:
                with self._lock:
                    self._failures += 1

    def get_latest(self, monitor_id: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            if monitor_id is None:
                if not self._latest_by_monitor:
                    return None
                latest = max(self._latest_by_monitor.values(), key=lambda x: x.timestamp_ms)
                return latest.to_dict()
            item = self._latest_by_monitor.get(int(monitor_id))
            return item.to_dict() if item else None

    def get_latest_facts(self, monitor_id: Optional[int] = None) -> list[dict]:
        latest = self.get_latest(monitor_id)
        if not latest:
            return []
        return latest.get("facts", [])

    def get_facts_delta(self, since_ms: int, monitor_id: Optional[int] = None) -> list[dict]:
        with self._lock:
            out = []
            for inf in self._history:
                if inf.timestamp_ms <= since_ms:
                    continue
                if monitor_id is not None and inf.monitor != int(monitor_id):
                    continue
                out.extend(asdict(f) for f in inf.facts)
            return out[-40:]

    def describe(
        self,
        frame_bytes: bytes,
        monitor_id: int = 0,
        app_name: str = "",
        window_title: str = "",
        ocr_text: str = "",
    ) -> dict:
        """On-demand VLM inference. Bypasses queue/worker — direct call."""
        task = VisualTask(
            ref_id=f"ondemand_{int(time.time() * 1000)}",
            tick_id=0,
            timestamp_ms=int(time.time() * 1000),
            monitor=monitor_id,
            frame_bytes=frame_bytes,
            mime_type="image/webp",
            app_name=app_name or "unknown",
            window_title=window_title or "unknown",
            ocr_text=ocr_text,
            priority=1.0,
        )
        inference = self._provider.analyze(task)
        with self._lock:
            self._latest_by_monitor[inference.monitor] = inference
            self._history.append(inference)
            self._processed += 1
        return inference.to_dict()

    def query(
        self,
        question: str,
        *,
        at_ms: Optional[int] = None,
        window_seconds: float = 30,
        monitor_id: Optional[int] = None,
    ) -> dict:
        question = (question or "").strip()
        if not question:
            return {
                "answer": "question is required",
                "confidence": 0.0,
                "evidence_refs": [],
                "source": self._provider.name,
            }
        now_ms = int(time.time() * 1000)
        window_cutoff = now_ms - int(max(1.0, float(window_seconds)) * 1000)
        target_ms = int(at_ms) if at_ms is not None else None

        with self._lock:
            candidates = list(self._history)
        if monitor_id is not None:
            candidates = [c for c in candidates if c.monitor == int(monitor_id)]
        if target_ms is not None:
            candidates.sort(key=lambda c: abs(c.timestamp_ms - target_ms))
            candidates = candidates[:8]
        else:
            candidates = [c for c in candidates if c.timestamp_ms >= window_cutoff][-12:]

        if not candidates:
            return {
                "answer": "No visual evidence in the requested time window.",
                "confidence": 0.0,
                "evidence_refs": [],
                "source": self._provider.name,
            }

        q_words = {w.lower() for w in question.split() if len(w) > 2}
        scored = []
        for inf in candidates:
            text = " ".join([inf.summary] + [f.text for f in inf.facts]).lower()
            overlap = len([w for w in q_words if w in text])
            # Recency bonus: newer inferences score higher (decay 0.01 per second of age)
            age_s = max(0, (now_ms - inf.timestamp_ms) / 1000.0)
            recency_bonus = max(0.0, 1.0 - age_s * 0.01)
            score = overlap + inf.confidence + recency_bonus
            scored.append((score, inf))
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0][1]

        answer = best.summary
        evidence_refs = [best.evidence_ref]
        confidence = max(0.1, min(0.95, float(best.confidence)))

        return {
            "answer": answer,
            "confidence": round(confidence, 3),
            "evidence_refs": evidence_refs,
            "source": self._provider.name,
            "timestamp_ms": best.timestamp_ms,
            "tick_id": best.tick_id,
            "monitor": best.monitor,
        }

    def stats(self) -> dict:
        with self._lock:
            provider_status = {}
            if hasattr(self._provider, "status"):
                try:
                    provider_status = self._provider.status()
                except Exception:
                    provider_status = {}
            return {
                "provider": self._provider.name,
                "running": self._running,
                "queue_size": len(self._queue),
                "processed": self._processed,
                "processed_by_monitor": dict(self._processed_by_monitor),
                "dropped": self._dropped,
                "failures": self._failures,
                "history_size": len(self._history),
                "latest_monitors": sorted(self._latest_by_monitor.keys()),
                "provider_status": provider_status,
            }
