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
import time
from typing import Optional
from dataclasses import dataclass, field
from PIL import Image, ImageDraw, ImageFont

from .ring_buffer import FrameSlot


# ─── OCR Engine (Windows native + Tesseract fallback) ───

def _try_import_tesseract():
    """Intenta importar pytesseract. Si no está, OCR queda deshabilitado."""
    try:
        import pytesseract
        return pytesseract
    except ImportError:
        return None


class OCREngine:
    """
    Extrae texto de frames usando OCR.
    - Primer intento: pytesseract (si está instalado)
    - Fallback: sin OCR, solo metadata de imagen
    
    El texto extraído va en inglés siempre al output,
    pero el OCR lee el idioma que sea de la pantalla.
    """

    def __init__(self):
        self._tesseract = _try_import_tesseract()
        self._available = self._tesseract is not None

    @property
    def available(self) -> bool:
        return self._available

    def extract_text(self, frame_bytes: bytes, lang: str = "eng") -> dict:
        """
        Extrae texto del frame.
        Returns: { "text": str, "blocks": [...], "confidence": float }
        """
        if not self._available:
            return {"text": "", "blocks": [], "confidence": 0.0, "ocr_available": False}

        img = Image.open(io.BytesIO(frame_bytes))

        try:
            # Texto completo
            full_text = self._tesseract.image_to_string(img, lang=lang)

            # Datos detallados con posiciones
            data = self._tesseract.image_to_data(img, lang=lang, output_type=self._tesseract.Output.DICT)

            blocks = []
            for i in range(len(data["text"])):
                text = data["text"][i].strip()
                conf = int(data["conf"][i])
                if text and conf > 30:  # filtrar ruido
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
                "ocr_available": True,
            }
        except Exception as e:
            return {"text": "", "blocks": [], "confidence": 0.0, "error": str(e), "ocr_available": True}

    def extract_region(self, frame_bytes: bytes, x: int, y: int, w: int, h: int, lang: str = "eng") -> dict:
        """OCR solo de una región específica del frame."""
        img = Image.open(io.BytesIO(frame_bytes))
        cropped = img.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        return self.extract_text(buf.getvalue(), lang=lang)


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

        return {
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
        return {"title": "unknown", "pid": 0, "bounds": {}}


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
            "title": f"{app_name} - {win_title}" if win_title else app_name,
            "pid": 0,
            "bounds": {},
        }
    except Exception:
        return {"title": "unknown", "pid": 0, "bounds": {}}


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
        name = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()

        # Get PID
        pid = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()

        return {
            "title": name,
            "pid": int(pid) if pid.isdigit() else 0,
            "bounds": {},
        }
    except Exception:
        return {"title": "unknown", "pid": 0, "bounds": {}}


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
            # Limitar texto a 2000 chars para no explotar contexto
            truncated = self.ocr_text[:2000]
            parts.append(f"\n### Visible Text (OCR)\n```\n{truncated}\n```")

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
    Orquesta OCR + Anotaciones + Window Detection para producir
    EnrichedFrames que cualquier IA puede consumir.
    """

    def __init__(self):
        self.ocr = OCREngine()
        self.annotations = AnnotationLayer()

    def enrich_frame(self, slot: FrameSlot, run_ocr: bool = True) -> EnrichedFrame:
        """
        Toma un frame crudo y lo enriquece con:
        - OCR text
        - Anotaciones del usuario (overlay + descripciones)
        - Info de ventana activa
        """
        # OCR (opcional — es el paso más lento)
        if run_ocr and self.ocr.available:
            ocr_result = self.ocr.extract_text(slot.frame_bytes)
        else:
            ocr_result = {"text": "", "blocks": [], "confidence": 0.0}

        # Anotaciones
        ann_descriptions = self.annotations.to_description()

        # Si hay anotaciones, renderizar overlay sobre el frame
        if self.annotations.annotations:
            image_bytes = self.annotations.render_overlay(slot.frame_bytes)
        else:
            image_bytes = slot.frame_bytes

        # Ventana activa
        window_info = get_active_window_info()

        return EnrichedFrame(
            timestamp=slot.timestamp,
            image_bytes=image_bytes,
            width=slot.width,
            height=slot.height,
            ocr_text=ocr_result["text"],
            ocr_blocks=ocr_result["blocks"],
            annotations=ann_descriptions,
            active_window=window_info,
            change_score=slot.change_score,
        )
