"""
ILUMINATY - Vision Intelligence Layer
=======================================
Capa de inteligencia que transforma frames crudos en datos
que cualquier IA puede entender:

1. OCR - Lee texto visible en pantalla
2. Layout Analysis - Detecta regiones, ventanas, elementos UI
3. Annotation Overlay - Permite al usuario marcar zonas con un lápiz
4. Universal Description - Genera descripción estructurada en inglés

El output es un "frame enriquecido" que tiene:
- La imagen original (para modelos de visión)
- Texto extraído por OCR (para modelos de texto)
- Coordenadas de anotaciones del usuario
- Metadata estructurada (qué app, qué región, qué cambió)

Así la IA no necesita "adivinar" qué está viendo — recibe
datos estructurados + imagen como contexto visual.
"""

import io
import json
import logging
import time
from typing import Optional
from dataclasses import dataclass, field
from PIL import Image, ImageDraw, ImageFont

from .ring_buffer import FrameSlot

logger = logging.getLogger(__name__)


# ─── OCR Engine (RapidOCR -> Tesseract -> None fallback chain) ───

def _try_import_rapidocr():
    """RapidOCR forzando CPU — DirectML solo en ocr_worker.py (thread dedicado)."""
    try:
        import os
        os.environ.setdefault('ORT_DISABLE_DML', '1')
        from rapidocr import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def _try_import_tesseract():
    """Fallback: pytesseract."""
    try:
        import pytesseract
        return pytesseract
    except ImportError:
        return None


class OCREngine:
    """
    Extrae texto de frames usando OCR.
    Fallback chain: RapidOCR (ONNX) -> Tesseract -> None

    RapidOCR es 3-5x mas rapido que Tesseract y no necesita
    instalar binarios del sistema. Solo pip install.

    Features:
    - OCR caching: si el frame no cambio, devuelve cache
    - Region OCR: crop antes de OCR para velocidad
    - Bloques con posiciones: para blur de contenido sensible
    """

    def __init__(self):
        self._rapid = _try_import_rapidocr()
        self._tesseract = _try_import_tesseract() if not self._rapid else None
        self._engine_name = "rapidocr" if self._rapid else ("tesseract" if self._tesseract else "none")
        self._cache_hash: Optional[str] = None
        self._cache_result: Optional[dict] = None

    @property
    def available(self) -> bool:
        return self._rapid is not None or self._tesseract is not None

    @property
    def engine(self) -> str:
        return self._engine_name

    def extract_text(self, frame_bytes: bytes, frame_hash: Optional[str] = None) -> dict:
        """
        Extrae texto del frame.
        Si frame_hash coincide con el cache, devuelve resultado cacheado.
        Returns: { "text": str, "blocks": [...], "confidence": float, "engine": str }
        """
        # Cache check: si el frame no cambio, devolver cache
        if frame_hash and frame_hash == self._cache_hash and self._cache_result:
            return {**self._cache_result, "cached": True}

        if self._rapid:
            result = self._extract_rapidocr(frame_bytes)
        elif self._tesseract:
            result = self._extract_tesseract(frame_bytes)
        else:
            result = {"text": "", "blocks": [], "confidence": 0.0, "engine": "none", "ocr_available": False}

        # Update cache
        if frame_hash:
            self._cache_hash = frame_hash
            self._cache_result = result

        return result

    def _extract_rapidocr(self, frame_bytes: bytes) -> dict:
        """OCR con RapidOCR (ONNX). Rapido y preciso."""
        try:
            import numpy as np
            img = Image.open(io.BytesIO(frame_bytes))
            img_array = np.array(img)

            result = self._rapid(img_array)

            if result is None or result.txts is None:
                return {"text": "", "blocks": [], "confidence": 0.0, "engine": "rapidocr", "ocr_available": True}

            blocks = []
            full_text_parts = []

            for i, (box, txt, score) in enumerate(zip(result.boxes, result.txts, result.scores)):
                if score < 0.3:
                    continue
                # box es [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x_min, x_max = int(min(xs)), int(max(xs))
                y_min, y_max = int(min(ys)), int(max(ys))

                blocks.append({
                    "text": txt,
                    "x": x_min,
                    "y": y_min,
                    "w": x_max - x_min,
                    "h": y_max - y_min,
                    "confidence": round(float(score) * 100, 1),
                })
                full_text_parts.append(txt)

            avg_conf = sum(b["confidence"] for b in blocks) / max(len(blocks), 1)

            return {
                "text": "\n".join(full_text_parts),
                "blocks": blocks,
                "confidence": round(avg_conf, 1),
                "engine": "rapidocr",
                "ocr_available": True,
                "block_count": len(blocks),
            }
        except Exception as e:
            return {"text": "", "blocks": [], "confidence": 0.0, "engine": "rapidocr", "error": str(e), "ocr_available": True}

    def _extract_tesseract(self, frame_bytes: bytes) -> dict:
        """Fallback OCR con Tesseract."""
        try:
            img = Image.open(io.BytesIO(frame_bytes))
            full_text = self._tesseract.image_to_string(img)
            data = self._tesseract.image_to_data(img, output_type=self._tesseract.Output.DICT)

            blocks = []
            for i in range(len(data["text"])):
                text = data["text"][i].strip()
                conf = int(data["conf"][i])
                if text and conf > 30:
                    blocks.append({
                        "text": text,
                        "x": data["left"][i],
                        "y": data["top"][i],
                        "w": data["width"][i],
                        "h": data["height"][i],
                        "confidence": conf,
                    })

            avg_conf = sum(b["confidence"] for b in blocks) / max(len(blocks), 1)
            return {
                "text": full_text.strip(),
                "blocks": blocks,
                "confidence": round(avg_conf, 1),
                "engine": "tesseract",
                "ocr_available": True,
                "block_count": len(blocks),
            }
        except Exception as e:
            return {"text": "", "blocks": [], "confidence": 0.0, "engine": "tesseract", "error": str(e), "ocr_available": True}

    def extract_region(
        self,
        frame_bytes: bytes,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        zoom_factor: float = 1.0,
    ) -> dict:
        """OCR solo de una region especifica. BUG-010 fix: bypasses cache (region != full frame)."""
        img = Image.open(io.BytesIO(frame_bytes))
        # Bounds check
        x = max(0, x)
        y = max(0, y)
        w = min(w, img.width - x)
        h = min(h, img.height - y)
        if w <= 0 or h <= 0:
            return {"text": "", "blocks": [], "confidence": 0.0, "engine": self._engine_name}
        cropped = img.crop((x, y, x + w, y + h))
        zf = float(zoom_factor or 1.0)
        if zf > 1.05:
            target_w = max(8, int(cropped.width * zf))
            target_h = max(8, int(cropped.height * zf))
            cropped = cropped.resize((target_w, target_h), Image.LANCZOS)
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        # No frame_hash = no cache (region OCR is always fresh)
        result = self.extract_text(buf.getvalue(), frame_hash=None)
        if isinstance(result, dict):
            result["region_zoom_factor"] = round(max(1.0, zf), 2)
        return result


# ─── Annotation System ───

@dataclass
class Annotation:
    """Una anotación del usuario sobre un frame."""
    id: str
    type: str           # "circle", "arrow", "rect", "text", "freehand"
    x: int
    y: int
    width: int = 0
    height: int = 0
    color: str = "#FF0000"   # rojo por default
    thickness: int = 3
    text: str = ""           # para anotaciones de texto
    points: list = field(default_factory=list)  # para freehand
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class AnnotationLayer:
    """
    Capa de anotaciones que el usuario puede dibujar sobre los frames.
    Las anotaciones son temporales (viven en RAM) y se aplican
    como overlay al frame antes de enviarlo a la IA.
    
    Esto permite al usuario decir "mira AQUÍ" señalando con el lápiz.
    """

    def __init__(self):
        self._annotations: list[Annotation] = []

    def add(self, annotation: Annotation) -> str:
        """Agrega una anotación. Retorna el ID."""
        self._annotations.append(annotation)
        return annotation.id

    def clear(self):
        """Borra todas las anotaciones."""
        self._annotations.clear()

    def remove(self, annotation_id: str) -> bool:
        """Borra una anotación por ID."""
        before = len(self._annotations)
        self._annotations = [a for a in self._annotations if a.id != annotation_id]
        return len(self._annotations) < before

    @property
    def annotations(self) -> list[Annotation]:
        return list(self._annotations)

    def render_overlay(self, frame_bytes: bytes) -> bytes:
        """
        Dibuja las anotaciones sobre el frame y retorna el JPEG resultante.
        El frame original NO se modifica — se crea una copia con el overlay.
        """
        if not self._annotations:
            return frame_bytes

        img = Image.open(io.BytesIO(frame_bytes)).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        for ann in self._annotations:
            color = ann.color
            t = ann.thickness

            if ann.type == "rect":
                draw.rectangle(
                    [ann.x, ann.y, ann.x + ann.width, ann.y + ann.height],
                    outline=color, width=t
                )
            elif ann.type == "circle":
                r = max(ann.width, ann.height) // 2
                cx, cy = ann.x + ann.width // 2, ann.y + ann.height // 2
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=t)
            elif ann.type == "arrow":
                # Línea con punta
                ex, ey = ann.x + ann.width, ann.y + ann.height
                draw.line([ann.x, ann.y, ex, ey], fill=color, width=t)
                # Punta de flecha simple
                draw.polygon([
                    (ex, ey),
                    (ex - 10, ey - 10),
                    (ex + 10, ey - 10),
                ], fill=color)
            elif ann.type == "text":
                try:
                    font = ImageFont.truetype("arial.ttf", 20)
                except OSError:
                    font = ImageFont.load_default()
                draw.text((ann.x, ann.y), ann.text, fill=color, font=font)
            elif ann.type == "freehand" and ann.points:
                if len(ann.points) >= 2:
                    draw.line(ann.points, fill=color, width=t)

        # Merge overlay sobre imagen
        composite = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        composite.save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    def to_description(self) -> list[dict]:
        """
        Convierte anotaciones a descripción textual para la IA.
        Así la IA sabe exactamente dónde mirar sin depender solo de visión.
        """
        descriptions = []
        for ann in self._annotations:
            desc = {
                "id": ann.id,
                "type": ann.type,
                "position": f"({ann.x}, {ann.y})",
                "size": f"{ann.width}x{ann.height}",
            }
            if ann.type == "text":
                desc["label"] = ann.text
            if ann.type == "rect":
                desc["description"] = f"Red rectangle highlighting area at ({ann.x},{ann.y}) size {ann.width}x{ann.height}"
            elif ann.type == "circle":
                desc["description"] = f"Circle drawn around area at ({ann.x},{ann.y})"
            elif ann.type == "arrow":
                desc["description"] = f"Arrow pointing to ({ann.x + ann.width},{ann.y + ann.height})"
            descriptions.append(desc)
        return descriptions


# ─── Active Window Detection (Windows) ───

def get_active_window_info() -> dict:
    """Detecta la ventana activa. Cross-platform: Windows, macOS, Linux."""
    import sys
    if sys.platform == "win32":
        return _get_active_window_windows()
    elif sys.platform == "darwin":
        return _get_active_window_macos()
    else:
        return _get_active_window_linux()


def _get_active_window_windows() -> dict:
    """Windows: usa ctypes + user32."""
    try:
        import ctypes
        import ctypes.wintypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)

        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))

        app_name = "unknown"
        try:
            import psutil
            proc = psutil.Process(pid.value)
            app_name = (proc.name() or "unknown").replace(".exe", "")
        except Exception as e:
            logger.debug("Could not resolve active process name from pid %s: %s", pid.value, e)

        return {
            "name": app_name,
            "app_name": app_name,
            "window_title": buf.value,
            "title": buf.value,
            "pid": pid.value,
            "bounds": {
                "left": rect.left, "top": rect.top,
                "right": rect.right, "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            },
        }
    except Exception:
        return {
            "name": "unknown",
            "app_name": "unknown",
            "window_title": "unknown",
            "title": "unknown",
            "pid": 0,
            "bounds": {},
        }


def _get_active_window_macos() -> dict:
    """macOS: usa subprocess + AppleScript."""
    try:
        import subprocess
        script = '''
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set winName to ""
            try
                set winName to name of front window of frontApp
            end try
            return appName & "|" & winName
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2
        )
        parts = result.stdout.strip().split("|", 1)
        app_name = parts[0] if parts else "unknown"
        win_title = parts[1] if len(parts) > 1 else ""
        return {
            "name": app_name,
            "app_name": app_name,
            "window_title": win_title or app_name,
            "title": f"{app_name} - {win_title}" if win_title else app_name,
            "pid": 0,
            "bounds": {},
        }
    except Exception:
        return {
            "name": "unknown",
            "app_name": "unknown",
            "window_title": "unknown",
            "title": "unknown",
            "pid": 0,
            "bounds": {},
        }


def _get_active_window_linux() -> dict:
    """Linux X11: usa xdotool si esta disponible."""
    try:
        import subprocess
        # Get active window ID
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()

        # Get window name
        title = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()

        # Get app/class name
        app_name = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowclassname"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip() or "unknown"

        # Get PID
        pid = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()

        return {
            "name": app_name,
            "app_name": app_name,
            "window_title": title or app_name,
            "title": title or app_name,
            "pid": int(pid) if pid.isdigit() else 0,
            "bounds": {},
        }
    except Exception:
        return {
            "name": "unknown",
            "app_name": "unknown",
            "window_title": "unknown",
            "title": "unknown",
            "pid": 0,
            "bounds": {},
        }


# ─── Enriched Frame (lo que la IA realmente recibe) ───

@dataclass
class EnrichedFrame:
    """
    Frame enriquecido = imagen + texto + anotaciones + metadata.
    Esto es lo que la IA consume. Idioma: siempre inglés.
    """
    timestamp: float
    image_bytes: bytes
    width: int
    height: int
    ocr_text: str
    ocr_blocks: list
    annotations: list
    active_window: dict
    change_score: float

    def to_ai_prompt(self) -> str:
        """
        Genera un prompt estructurado en inglés que cualquier IA entiende.
        Este es el 'idioma universal' de ILUMINATY.
        """
        parts = [
            "## Current Screen State",
            f"**Time**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}",
            f"**Active Window**: {self.active_window.get('title', 'unknown')}",
            f"**Resolution**: {self.width}x{self.height}",
            f"**Change Level**: {'high' if self.change_score > 0.5 else 'low'}",
        ]

        if self.ocr_text:
            # BUG-011 fix: show truncation notice if text was cut
            max_chars = 2000
            truncated = self.ocr_text[:max_chars]
            trunc_note = f" (truncated from {len(self.ocr_text)} chars)" if len(self.ocr_text) > max_chars else ""
            parts.append(f"\n### Visible Text (OCR{trunc_note})\n```\n{truncated}\n```")

        if self.annotations:
            parts.append("\n### User Annotations (LOOK HERE)")
            for ann in self.annotations:
                desc = ann.get("description", ann.get("label", f"{ann['type']} at {ann['position']}"))
                parts.append(f"- **{ann['type'].upper()}**: {desc}")

        parts.append(
            "\n### Instructions"
            "\nAn image of the screen is attached. "
            "The OCR text above shows what is readable. "
            "If user annotations are present, focus your analysis on those areas first."
        )

        return "\n".join(parts)

    def to_dict(self, include_image: bool = False) -> dict:
        """Serialización JSON del frame enriquecido."""
        import base64
        result = {
            "timestamp": self.timestamp,
            "width": self.width,
            "height": self.height,
            "ocr_text": self.ocr_text,
            "ocr_blocks_count": len(self.ocr_blocks),
            "annotations": self.annotations,
            "active_window": self.active_window,
            "change_score": self.change_score,
            "ai_prompt": self.to_ai_prompt(),
        }
        if include_image:
            result["image_base64"] = base64.b64encode(self.image_bytes).decode("ascii")
        return result


class VisionIntelligence:
    """
    Orquesta OCR + Anotaciones + Window Detection + Auto-Blur
    para producir EnrichedFrames que cualquier IA puede consumir.
    """

    def __init__(self, auto_blur_sensitive: bool = True):
        self.ocr = OCREngine()
        self.annotations = AnnotationLayer()
        self.auto_blur = auto_blur_sensitive

        # Import security components
        from .security import SensitiveDetector, ScreenBlurrer
        self._sensitive = SensitiveDetector(auto_redact=True)
        self._blurrer = ScreenBlurrer()

    def enrich_frame(self, slot: FrameSlot, run_ocr: bool = True) -> EnrichedFrame:
        """
        Toma un frame crudo y lo enriquece con:
        - OCR text (con redaccion de contenido sensible)
        - Auto-blur de regiones con passwords/cards/keys
        - Anotaciones del usuario (overlay + descripciones)
        - Info de ventana activa
        """
        image_bytes = slot.frame_bytes
        ocr_result = {"text": "", "blocks": [], "confidence": 0.0}
        blur_count = 0

        # OCR (opcional — es el paso mas lento, caching ayuda)
        if run_ocr and self.ocr.available:
            ocr_result = self.ocr.extract_text(slot.frame_bytes, frame_hash=slot.phash)

            # Auto-blur: detectar contenido sensible y blur esas regiones
            if self.auto_blur and ocr_result.get("blocks"):
                regions_to_blur = []
                for block in ocr_result["blocks"]:
                    findings = self._sensitive.scan_text(block["text"])
                    if findings:
                        regions_to_blur.append({
                            "x": block["x"],
                            "y": block["y"],
                            "w": block["w"],
                            "h": block["h"],
                        })

                if regions_to_blur:
                    image_bytes = self._blurrer.blur_regions(image_bytes, regions_to_blur)
                    blur_count = len(regions_to_blur)

                # Redact text too
                ocr_result["text"] = self._sensitive.redact_text(ocr_result.get("text", ""))

        # Anotaciones
        ann_descriptions = self.annotations.to_description()

        # Si hay anotaciones, renderizar overlay sobre el frame
        if self.annotations.annotations:
            image_bytes = self.annotations.render_overlay(image_bytes)

        # Ventana activa
        window_info = get_active_window_info()

        return EnrichedFrame(
            timestamp=slot.timestamp,
            image_bytes=image_bytes,
            width=slot.width,
            height=slot.height,
            ocr_text=ocr_result.get("text", ""),
            ocr_blocks=ocr_result.get("blocks", []),
            annotations=ann_descriptions,
            active_window=window_info,
            change_score=slot.change_score,
        )
