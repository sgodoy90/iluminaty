import logging
logger = logging.getLogger(__name__)
"""
ILUMINATY - AI Provider Adapters
==================================
Adaptadores para enviar frames/audio a diferentes IAs.
Cada adapter traduce del formato ILUMINATY al formato
que espera cada provider.

Providers soportados:
  - Gemini Live API (streaming bidireccional via WebSocket)
  - OpenAI Realtime API (frame-by-frame images)
  - OpenAI Vision API (batch images)
  - Claude Vision (batch images)
  - Generic (cualquier API que acepte base64 images)

Uso:
  adapter = GeminiLiveAdapter(api_key="...")
  adapter.connect()
  adapter.send_frame(frame_bytes)
  response = adapter.get_response()
"""

import io
import json
import time
import base64
import asyncio
import threading
from typing import Optional, Callable
from dataclasses import dataclass


@dataclass
class AIResponse:
    """Respuesta de un provider de IA."""
    text: str
    provider: str
    model: str
    latency_ms: float
    tokens_used: int = 0
    audio_bytes: Optional[bytes] = None  # para respuestas de voz


class BaseAdapter:
    """Base para todos los adapters."""

    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def send_frame(self, frame_bytes: bytes, prompt: str = "") -> Optional[AIResponse]:
        raise NotImplementedError


class GeminiLiveAdapter(BaseAdapter):
    """
    Adapter para Gemini Multimodal Live API.
    Streaming bidireccional via WebSocket.

    Gemini Live puede:
    - Recibir frames de video en streaming
    - Recibir audio en streaming
    - Responder con texto o audio en real-time
    - Ejecutar function calls basadas en lo que ve

    Requiere: pip install google-genai
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        super().__init__(api_key, model)
        self._session = None
        self._response_queue: list[str] = []
        self._on_response: Optional[Callable] = None

    def connect(self):
        """Conecta al Gemini Live API."""
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._connected = True
        except ImportError:
            raise RuntimeError("Install google-genai: pip install google-genai")
        except Exception as e:
            raise RuntimeError(f"Gemini connection failed: {e}")

    def disconnect(self):
        self._connected = False
        self._session = None

    def send_frame(self, frame_bytes: bytes, prompt: str = "", mime_type: str = "image/webp") -> Optional[AIResponse]:
        """
        Envia un frame a Gemini Vision (non-streaming).
        Para streaming real, usar send_frame_stream().
        """
        if not self._connected:
            return None

        start = time.time()
        try:
            from google import genai
            b64 = base64.b64encode(frame_bytes).decode("ascii")

            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    {
                        "parts": [
                            {"text": prompt or "Describe what you see on this screen."},
                            {"inline_data": {"mime_type": mime_type, "data": b64}},
                        ]
                    }
                ],
            )

            latency = (time.time() - start) * 1000
            return AIResponse(
                text=response.text or "",
                provider="gemini",
                model=self.model,
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return AIResponse(
                text=f"Error: {e}",
                provider="gemini",
                model=self.model,
                latency_ms=round((time.time() - start) * 1000, 1),
            )

    async def start_live_session(self, system_prompt: str = ""):
        """
        Inicia una sesion de streaming bidireccional con Gemini Live.
        Los frames se envian continuamente y Gemini responde en real-time.
        """
        if not self._connected:
            return

        try:
            from google import genai

            config = {
                "response_modalities": ["TEXT"],
            }
            if system_prompt:
                config["system_instruction"] = system_prompt

            self._session = await self._client.aio.live.connect(
                model=self.model,
                config=config,
            )
        except Exception as e:
            logger.error("[iluminaty] Gemini Live session error: %s", e)

    async def send_frame_live(self, frame_bytes: bytes, mime_type: str = "image/webp"):
        """Envia un frame al stream de Gemini Live."""
        if not self._session:
            return
        try:
            b64 = base64.b64encode(frame_bytes).decode("ascii")
            await self._session.send(
                input={"mime_type": mime_type, "data": b64},
                end_of_turn=False,
            )
        except Exception as e:
            logger.warning("[iluminaty] Gemini Live send error: %s", e)

    async def send_audio_live(self, pcm_data: bytes, sample_rate: int = 16000):
        """Envia audio PCM al stream de Gemini Live."""
        if not self._session:
            return
        try:
            b64 = base64.b64encode(pcm_data).decode("ascii")
            await self._session.send(
                input={"mime_type": "audio/pcm", "data": b64},
                end_of_turn=False,
            )
        except Exception as e:
            logger.warning("[iluminaty] Gemini Live audio error: %s", e)

    def on_response(self, callback: Callable):
        """Registra callback para respuestas de Gemini Live."""
        self._on_response = callback


class OpenAIAdapter(BaseAdapter):
    """
    Adapter para OpenAI APIs.
    
    Dos modos:
    1. Vision API: envia imagenes como parte del mensaje (batch)
    2. Realtime API: frame-by-frame en sesion de voz (streaming)
    
    Requiere: pip install openai
    """

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        super().__init__(api_key, model)
        self._client = None

    def connect(self):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
            self._connected = True
        except ImportError:
            raise RuntimeError("Install openai: pip install openai")

    def disconnect(self):
        self._connected = False
        self._client = None

    def send_frame(self, frame_bytes: bytes, prompt: str = "", mime_type: str = "image/webp") -> Optional[AIResponse]:
        """Envia un frame a OpenAI Vision API."""
        if not self._connected or not self._client:
            return None

        start = time.time()
        try:
            b64 = base64.b64encode(frame_bytes).decode("ascii")
            data_url = f"data:{mime_type};base64,{b64}"

            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "Describe what you see on this screen."},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                }],
                max_tokens=1000,
            )

            latency = (time.time() - start) * 1000
            text = response.choices[0].message.content if response.choices else ""
            tokens = response.usage.total_tokens if response.usage else 0

            return AIResponse(
                text=text,
                provider="openai",
                model=self.model,
                latency_ms=round(latency, 1),
                tokens_used=tokens,
            )
        except Exception as e:
            return AIResponse(
                text=f"Error: {e}",
                provider="openai",
                model=self.model,
                latency_ms=round((time.time() - start) * 1000, 1),
            )

    def send_multi_frame(self, frames: list[bytes], prompt: str = "", mime_type: str = "image/webp") -> Optional[AIResponse]:
        """Envia multiples frames (para contexto temporal)."""
        if not self._connected or not self._client:
            return None

        start = time.time()
        try:
            content = [{"type": "text", "text": prompt or "These are sequential frames from a screen. Describe what is happening."}]
            for frame_bytes in frames[:10]:  # max 10 frames
                b64 = base64.b64encode(frame_bytes).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}", "detail": "low"},
                })

            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=1000,
            )

            latency = (time.time() - start) * 1000
            text = response.choices[0].message.content if response.choices else ""
            tokens = response.usage.total_tokens if response.usage else 0

            return AIResponse(
                text=text,
                provider="openai",
                model=self.model,
                latency_ms=round(latency, 1),
                tokens_used=tokens,
            )
        except Exception as e:
            return AIResponse(
                text=f"Error: {e}",
                provider="openai",
                model=self.model,
                latency_ms=round((time.time() - start) * 1000, 1),
            )


class ClaudeAdapter(BaseAdapter):
    """
    Adapter para Claude Vision API (Anthropic).
    Envia imagenes como parte del mensaje.
    
    Requiere: pip install anthropic
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        super().__init__(api_key, model)
        self._client = None

    def connect(self):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
            self._connected = True
        except ImportError:
            raise RuntimeError("Install anthropic: pip install anthropic")

    def disconnect(self):
        self._connected = False
        self._client = None

    def send_frame(self, frame_bytes: bytes, prompt: str = "", mime_type: str = "image/webp") -> Optional[AIResponse]:
        """Envia un frame a Claude Vision."""
        if not self._connected or not self._client:
            return None

        start = time.time()
        try:
            b64 = base64.b64encode(frame_bytes).decode("ascii")

            # Claude usa image/jpeg o image/png, no webp
            media_type = "image/jpeg" if "jpeg" in mime_type else "image/png"
            if "webp" in mime_type:
                # Convertir WebP a JPEG para Claude
                from PIL import Image
                img = Image.open(io.BytesIO(frame_bytes))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                media_type = "image/jpeg"

            response = self._client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": prompt or "Describe what you see on this screen."},
                    ],
                }],
            )

            latency = (time.time() - start) * 1000
            text = response.content[0].text if response.content else ""
            tokens = (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0

            return AIResponse(
                text=text,
                provider="claude",
                model=self.model,
                latency_ms=round(latency, 1),
                tokens_used=tokens,
            )
        except Exception as e:
            return AIResponse(
                text=f"Error: {e}",
                provider="claude",
                model=self.model,
                latency_ms=round((time.time() - start) * 1000, 1),
            )


class GenericAdapter(BaseAdapter):
    """
    Adapter generico para cualquier API que acepte imagenes base64.
    Configurable via URL + headers + body template.
    """

    def __init__(
        self,
        api_key: str,
        endpoint_url: str,
        model: str = "",
        headers: Optional[dict] = None,
    ):
        super().__init__(api_key, model)
        self.endpoint_url = endpoint_url
        self.headers = headers or {}

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def send_frame(self, frame_bytes: bytes, prompt: str = "", mime_type: str = "image/webp") -> Optional[AIResponse]:
        """Envia frame via HTTP POST con base64."""
        import urllib.request
        start = time.time()
        try:
            b64 = base64.b64encode(frame_bytes).decode("ascii")
            body = json.dumps({
                "image": b64,
                "mime_type": mime_type,
                "prompt": prompt or "Describe this screen.",
                "model": self.model,
            }).encode()

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                **self.headers,
            }

            req = urllib.request.Request(self.endpoint_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())

            latency = (time.time() - start) * 1000
            return AIResponse(
                text=result.get("text", result.get("response", str(result))),
                provider="generic",
                model=self.model,
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return AIResponse(
                text=f"Error: {e}",
                provider="generic",
                model=self.model,
                latency_ms=round((time.time() - start) * 1000, 1),
            )


# ─── Ollama Adapter ───────────────────────────────────────────────────────────

class OllamaAdapter(BaseAdapter):
    """
    Local LLM via Ollama (http://localhost:11434).
    Supports any model pulled with `ollama pull <model>`.
    No API key needed — runs 100% local.

    Compatible models on RTX 3070 (8GB):
      qwen3-vl:4b     — multimodal, 3.3GB  ← recommended
      qwen2.5:7b      — text only,  4.7GB
      qwen3:4b        — text only,  2.5GB
    """

    def __init__(self, api_key: str = "", model: str = "qwen3-vl:4b",
                 base_url: str = "http://localhost:11434"):
        super().__init__(api_key="", model=model)
        self._base_url = base_url.rstrip("/")
        self._model = model

    def connect(self) -> bool:
        try:
            import urllib.request
            req = urllib.request.Request(f"{self._base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                import json
                data = json.loads(resp.read())
                available = [m["name"] for m in data.get("models", [])]
                if self._model not in available:
                    # try prefix match (e.g. "qwen3-vl" matches "qwen3-vl:4b")
                    base = self._model.split(":")[0]
                    match = next((m for m in available if m.startswith(base)), None)
                    if match:
                        self._model = match
                self._connected = True
                return True
        except Exception as e:
            logger.warning("Ollama connect failed: %s", e)
            self._connected = False
            return False

    def ask(self, prompt: str, system: str = "") -> str:
        """Simple text completion — returns response string."""
        resp = self.send_frame(b"", prompt=prompt, system_prompt=system)
        return resp.text if resp else ""

    def send_frame(self, frame_bytes: bytes, prompt: str = "",
                   mime_type: str = "image/webp",
                   system_prompt: str = "") -> Optional[AIResponse]:
        """
        Send prompt (+ optional image) to Ollama.
        Uses /api/generate with stream=True to handle qwen3 thinking mode correctly.
        If frame_bytes is non-empty and model is multimodal, attaches the image.
        """
        import urllib.request
        import json
        import base64

        start = time.time()

        # Build full prompt string (qwen3 responds best to direct prompts)
        if system_prompt and prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        elif system_prompt:
            full_prompt = system_prompt
        else:
            full_prompt = prompt

        payload: dict = {
            "model": self._model,
            "prompt": full_prompt,
            "stream": True,
            "options": {
                "temperature": 0.05,
                "num_predict": 256,
            },
        }

        # Attach image if provided and non-empty
        if frame_bytes:
            img_b64 = base64.b64encode(frame_bytes).decode("ascii")
            payload["images"] = [img_b64]

        body = json.dumps(payload).encode()

        try:
            req = urllib.request.Request(
                f"{self._base_url}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            tokens_collected = []
            total_in = 0
            total_out = 0
            with urllib.request.urlopen(req, timeout=90) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    tok = chunk.get("response", "")
                    if tok:
                        tokens_collected.append(tok)
                    if chunk.get("done"):
                        total_in = chunk.get("prompt_eval_count", 0) or 0
                        total_out = chunk.get("eval_count", 0) or 0
                        break

            text = "".join(tokens_collected)
            return AIResponse(
                text=text,
                provider="ollama",
                model=self._model,
                latency_ms=round((time.time() - start) * 1000, 1),
                tokens_used=total_in + total_out,
            )
        except Exception as e:
            logger.error("Ollama send_frame failed: %s", e)
            return None

    def disconnect(self):
        self._connected = False


# ─── Kimi API Adapter (OpenAI-compatible) ────────────────────────────────────

class KimiAdapter(BaseAdapter):
    """
    Moonshot AI Kimi K2 / K2.5 via API.
    OpenAI-compatible endpoint — 10x cheaper than Claude.
    Requires API key from platform.moonshot.ai

    Models:
      moonshot-v1-8k    — fast, cheap ($0.15/M tokens)
      kimi-k2-instruct  — agentic, coding ($0.60/M tokens)
    """

    def __init__(self, api_key: str, model: str = "moonshot-v1-8k"):
        super().__init__(api_key=api_key, model=model)
        self._base_url = "https://api.moonshot.cn/v1"

    def connect(self) -> bool:
        self._connected = bool(self.api_key)
        return self._connected

    def ask(self, prompt: str, system: str = "") -> str:
        resp = self.send_frame(b"", prompt=prompt, system_prompt=system)
        return resp.text if resp else ""

    def send_frame(self, frame_bytes: bytes, prompt: str = "",
                   mime_type: str = "image/webp",
                   system_prompt: str = "") -> Optional[AIResponse]:
        import urllib.request
        import json
        import base64

        start = time.time()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if frame_bytes:
            img_b64 = base64.b64encode(frame_bytes).decode("ascii")
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}},
                    {"type": "text", "text": prompt or "What do you see?"},
                ],
            })
        else:
            messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 256,
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return AIResponse(
                    text=text,
                    provider="kimi",
                    model=self.model,
                    latency_ms=round((time.time() - start) * 1000, 1),
                    tokens_used=usage.get("total_tokens", 0),
                )
        except Exception as e:
            logger.error("Kimi API send_frame failed: %s", e)
            return None

    def disconnect(self):
        self._connected = False


# ─── Adapter Factory ───

ADAPTERS = {
    "gemini": GeminiLiveAdapter,
    "openai": OpenAIAdapter,
    "claude": ClaudeAdapter,
    "ollama": OllamaAdapter,
    "kimi": KimiAdapter,
    "generic": GenericAdapter,
}


def create_adapter(provider: str, api_key: str, **kwargs) -> BaseAdapter:
    """Factory para crear adapters por nombre."""
    cls = ADAPTERS.get(provider)
    if not cls:
        raise ValueError(f"Unknown provider: {provider}. Available: {list(ADAPTERS.keys())}")
    return cls(api_key=api_key, **kwargs)
