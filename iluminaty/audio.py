"""
ILUMINATY - Audio Capture Engine
==================================
Captura de audio del sistema y/o microfono.
Mismo patron que video: ring buffer en RAM, cero disco.

Modos:
  --audio off       No captura audio (default)
  --audio system    Solo system audio (lo que suena en la PC)
  --audio mic       Solo microfono
  --audio all       Ambos

Arquitectura:
  sounddevice -> PCM chunks -> AudioRingBuffer (RAM) -> API
  
  El buffer guarda chunks de audio en PCM16 con timestamp.
  Para transcripcion, se concatenan los chunks y se envian
  a Whisper (local) o Deepgram/OpenAI (cloud).
"""

import io
import logging
import time
import wave
import threading
import base64
from collections import deque
from dataclasses import dataclass
from typing import Optional, Callable

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AudioChunk:
    """Un chunk de audio en el ring buffer."""
    timestamp: float
    pcm_data: bytes       # PCM16 mono
    duration_ms: int      # duracion en ms
    sample_rate: int
    rms_level: float      # volumen RMS normalizado 0.0-1.0
    is_speech: bool       # VAD simple: hay voz?


class AudioRingBuffer:
    """
    Ring buffer de audio en RAM. Cero disco.
    Guarda los ultimos N segundos de audio como PCM chunks.
    """

    def __init__(self, max_seconds: int = 60, chunk_duration_ms: int = 500):
        self.max_seconds = max_seconds
        self.chunk_duration_ms = chunk_duration_ms
        self.max_chunks = int((max_seconds * 1000) / chunk_duration_ms)
        self._buffer: deque[AudioChunk] = deque(maxlen=self.max_chunks)
        self._lock = threading.Lock()
        self._chunk_count: int = 0
        self._speech_chunks: int = 0

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def memory_bytes(self) -> int:
        with self._lock:
            return sum(len(c.pcm_data) for c in self._buffer)

    @property
    def memory_mb(self) -> float:
        return self.memory_bytes / (1024 * 1024)

    @property
    def stats(self) -> dict:
        return {
            "chunks": self.size,
            "max_chunks": self.max_chunks,
            "memory_mb": round(self.memory_mb, 2),
            "total_captured": self._chunk_count,
            "speech_chunks": self._speech_chunks,
            "buffer_seconds": self.max_seconds,
        }

    def push(self, chunk: AudioChunk):
        self._chunk_count += 1
        if chunk.is_speech:
            self._speech_chunks += 1
        with self._lock:
            self._buffer.append(chunk)

    def get_latest(self, seconds: float = 5.0) -> list[AudioChunk]:
        cutoff = time.time() - seconds
        with self._lock:
            return [c for c in self._buffer if c.timestamp >= cutoff]

    def get_audio_wav(self, seconds: float = 10.0, sample_rate: int = 16000) -> bytes:
        """
        Concatena los ultimos N segundos de audio y retorna como WAV en memoria.
        Cero disco — todo en BytesIO.
        """
        chunks = self.get_latest(seconds)
        if not chunks:
            return b""

        pcm_data = b"".join(c.pcm_data for c in chunks)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)

        return buf.getvalue()

    def get_speech_segments(self, seconds: float = 30.0) -> list[dict]:
        """Retorna segmentos donde hubo voz (para transcripcion selectiva)."""
        chunks = self.get_latest(seconds)
        segments = []
        current_segment = None

        for chunk in chunks:
            if chunk.is_speech:
                if current_segment is None:
                    current_segment = {
                        "start": chunk.timestamp,
                        "end": chunk.timestamp + chunk.duration_ms / 1000,
                        "chunks": [chunk],
                    }
                else:
                    current_segment["end"] = chunk.timestamp + chunk.duration_ms / 1000
                    current_segment["chunks"].append(chunk)
            else:
                if current_segment is not None:
                    segments.append(current_segment)
                    current_segment = None

        if current_segment:
            segments.append(current_segment)

        return segments

    def clear(self):
        with self._lock:
            self._buffer.clear()


class AudioCapture:
    """
    Motor de captura de audio.
    Usa sounddevice para captura cross-platform (Windows/Mac/Linux).
    """

    def __init__(
        self,
        buffer: AudioRingBuffer,
        mode: str = "off",           # off, system, mic, all
        sample_rate: int = 16000,    # 16kHz es standard para STT
        chunk_duration_ms: int = 500,
        vad_threshold: float = 0.01, # umbral RMS para deteccion de voz
    ):
        self.buffer = buffer
        self.mode = mode
        self.sample_rate = sample_rate
        self.chunk_duration_ms = chunk_duration_ms
        self.vad_threshold = vad_threshold
        self._running = False
        self._stream = None
        self._pcm_accumulator = bytearray()
        self._chunk_samples = int(sample_rate * chunk_duration_ms / 1000)

    @property
    def is_running(self) -> bool:
        return self._running

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback de sounddevice — se llama por cada bloque de audio."""
        if status:
            pass  # overflow/underflow, ignorar

        # Convertir a PCM16 bytes
        pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
        self._pcm_accumulator.extend(pcm)

        # Cuando acumulamos suficiente para un chunk
        bytes_per_chunk = self._chunk_samples * 2  # 2 bytes per sample (int16)
        while len(self._pcm_accumulator) >= bytes_per_chunk:
            chunk_data = bytes(self._pcm_accumulator[:bytes_per_chunk])
            self._pcm_accumulator = self._pcm_accumulator[bytes_per_chunk:]

            # Calcular RMS (volumen)
            arr = np.frombuffer(chunk_data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr ** 2)) / 32767.0)

            # VAD simple: hay voz si RMS > threshold
            is_speech = rms > self.vad_threshold

            chunk = AudioChunk(
                timestamp=time.time(),
                pcm_data=chunk_data,
                duration_ms=self.chunk_duration_ms,
                sample_rate=self.sample_rate,
                rms_level=round(rms, 4),
                is_speech=is_speech,
            )
            self.buffer.push(chunk)

    def start(self):
        """Inicia captura de audio."""
        if self._running or self.mode == "off":
            return

        try:
            import sounddevice as sd

            # Seleccionar device segun modo
            device = None
            if self.mode == "mic":
                device = sd.default.device[0]  # input default
            elif self.mode == "system":
                # System audio (loopback) — depende del OS
                # Windows: necesita WASAPI loopback o virtual cable
                # Por ahora usar input default
                device = sd.default.device[0]

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                device=device,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
        except Exception as e:
            print(f"[iluminaty] audio capture error: {e}")
            self._running = False

    def stop(self):
        """Detiene captura."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug("Audio stream close failed: %s", e)
            self._stream = None

    def get_devices(self) -> list[dict]:
        """Lista dispositivos de audio disponibles."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            result = []
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    result.append({
                        "id": i,
                        "name": d["name"],
                        "channels": d["max_input_channels"],
                        "sample_rate": d["default_samplerate"],
                        "is_default": i == sd.default.device[0],
                    })
            return result
        except Exception:
            return []


# ─── Transcription Engine ───

class TranscriptionEngine:
    """
    Transcripcion de audio a texto.
    Fallback chain: Whisper local -> API cloud -> None

    Para real-time usamos chunks pequenos.
    Para resumen usamos audio acumulado.
    """

    def __init__(self):
        self._whisper_model = None
        self._engine = self._detect_engine()

    def _detect_engine(self) -> str:
        """Detecta que engine de transcripcion esta disponible."""
        # Try faster-whisper (mas rapido que openai-whisper)
        try:
            from faster_whisper import WhisperModel
            return "faster-whisper"
        except ImportError:
            logger.debug("faster-whisper not installed; trying next transcription engine")

        # Try openai-whisper
        try:
            import whisper
            return "whisper"
        except ImportError:
            logger.debug("openai-whisper not installed; transcription disabled")

        return "none"

    @property
    def available(self) -> bool:
        return self._engine != "none"

    @property
    def engine(self) -> str:
        return self._engine

    def _load_model(self):
        """Carga el modelo de transcripcion (lazy loading)."""
        if self._whisper_model is not None:
            return

        if self._engine == "faster-whisper":
            from faster_whisper import WhisperModel
            # tiny = mas rapido, menos preciso. Suficiente para real-time.
            self._whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        elif self._engine == "whisper":
            import whisper
            self._whisper_model = whisper.load_model("tiny")

    def transcribe_wav(self, wav_bytes: bytes) -> dict:
        """
        Transcribe audio WAV a texto.
        Returns: { "text": str, "language": str, "segments": [...] }
        """
        if not self.available:
            return {"text": "", "language": "", "segments": [], "engine": "none"}

        self._load_model()

        # Escribir WAV a buffer temporal (whisper necesita archivo o numpy)
        try:
            if self._engine == "faster-whisper":
                # faster-whisper acepta bytes directamente via BytesIO
                buf = io.BytesIO(wav_bytes)
                segments, info = self._whisper_model.transcribe(buf, language=None)
                text_parts = []
                seg_list = []
                for seg in segments:
                    text_parts.append(seg.text)
                    seg_list.append({
                        "start": round(seg.start, 2),
                        "end": round(seg.end, 2),
                        "text": seg.text.strip(),
                    })
                return {
                    "text": " ".join(text_parts).strip(),
                    "language": info.language if info else "",
                    "segments": seg_list,
                    "engine": "faster-whisper",
                }

            elif self._engine == "whisper":
                # openai-whisper necesita archivo en disco o numpy
                # Usamos numpy desde WAV bytes
                import wave
                buf = io.BytesIO(wav_bytes)
                with wave.open(buf, "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

                result = self._whisper_model.transcribe(audio, language=None)
                return {
                    "text": result.get("text", "").strip(),
                    "language": result.get("language", ""),
                    "segments": [
                        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                        for s in result.get("segments", [])
                    ],
                    "engine": "whisper",
                }

        except Exception as e:
            return {"text": "", "language": "", "segments": [], "engine": self._engine, "error": str(e)}

        return {"text": "", "language": "", "segments": [], "engine": self._engine}

    def transcribe_chunks(self, chunks: list[AudioChunk]) -> dict:
        """Transcribe una lista de AudioChunks concatenados."""
        if not chunks:
            return {"text": "", "segments": [], "engine": self._engine}

        # Concatenar PCM y convertir a WAV
        pcm_data = b"".join(c.pcm_data for c in chunks)
        sample_rate = chunks[0].sample_rate

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)

        return self.transcribe_wav(buf.getvalue())
