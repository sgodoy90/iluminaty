"""
ILUMINATY - E02: Spatial Layout Map
=====================================
La IA sabe DONDE esta cada cosa en la pantalla.
No solo pixels — regiones semanticas con nombres.

"El editor esta arriba (60%), la terminal abajo (30%),
el sidebar a la izquierda (10%)"

Usa OCR blocks + visual zones para construir un mapa
que persiste entre frames.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScreenZone:
    """Una zona semantica de la pantalla."""
    name: str             # "editor", "terminal", "sidebar", "browser"
    x: int
    y: int
    width: int
    height: int
    coverage_pct: float   # % de la pantalla que ocupa
    content_type: str     # "code", "text", "ui", "media", "empty"
    last_updated: float = 0.0

    def contains(self, px: int, py: int) -> bool:
        return (self.x <= px < self.x + self.width and
                self.y <= py < self.y + self.height)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "position": f"({self.x},{self.y})",
            "size": f"{self.width}x{self.height}",
            "coverage": f"{self.coverage_pct:.1f}%",
            "content": self.content_type,
        }


class SpatialMap:
    """
    Mapa espacial de la pantalla.
    Divide la pantalla en zonas semanticas y las nombra.
    """

    def __init__(self):
        self._zones: list[ScreenZone] = []
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._last_analysis: float = 0

    def analyze_from_ocr(self, ocr_blocks: list, frame_width: int, frame_height: int):
        """
        Construye el layout map a partir de bloques OCR.
        Agrupa bloques cercanos en zonas.
        """
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._last_analysis = time.time()

        if not ocr_blocks:
            self._zones = [ScreenZone(
                name="full_screen", x=0, y=0,
                width=frame_width, height=frame_height,
                coverage_pct=100.0, content_type="unknown"
            )]
            return

        # Dividir pantalla en cuadrantes y analizar densidad de texto
        zones = []
        grid = [
            ("top-left", 0, 0, frame_width // 2, frame_height // 2),
            ("top-right", frame_width // 2, 0, frame_width // 2, frame_height // 2),
            ("bottom-left", 0, frame_height // 2, frame_width // 2, frame_height // 2),
            ("bottom-right", frame_width // 2, frame_height // 2, frame_width // 2, frame_height // 2),
            # Barras
            ("top-bar", 0, 0, frame_width, frame_height // 8),
            ("bottom-bar", 0, frame_height * 7 // 8, frame_width, frame_height // 8),
            ("left-sidebar", 0, 0, frame_width // 6, frame_height),
        ]

        total_area = frame_width * frame_height

        for name, zx, zy, zw, zh in grid:
            # Contar bloques OCR en esta zona
            blocks_in_zone = [
                b for b in ocr_blocks
                if (b.get("x", 0) >= zx and b.get("x", 0) < zx + zw and
                    b.get("y", 0) >= zy and b.get("y", 0) < zy + zh)
            ]

            if blocks_in_zone:
                # Determinar tipo de contenido
                texts = " ".join(b.get("text", "") for b in blocks_in_zone)
                content_type = self._classify_content(texts)
                coverage = (zw * zh) / total_area * 100

                zones.append(ScreenZone(
                    name=name,
                    x=zx, y=zy, width=zw, height=zh,
                    coverage_pct=round(coverage, 1),
                    content_type=content_type,
                    last_updated=time.time(),
                ))

        self._zones = zones if zones else [ScreenZone(
            name="full_screen", x=0, y=0,
            width=frame_width, height=frame_height,
            coverage_pct=100.0, content_type="unknown"
        )]

    def _classify_content(self, text: str) -> str:
        """Clasifica el tipo de contenido basado en el texto."""
        text_lower = text.lower()

        code_indicators = ["def ", "class ", "import ", "function ", "const ", "var ",
                          "return ", "if ", "{", "}", "()", "=>", "//", "/*", "#!/"]
        ui_indicators = ["button", "click", "menu", "file", "edit", "view", "settings",
                        "save", "open", "close", "new", "delete"]
        terminal_indicators = ["$", ">>>", "C:\\", "/home/", "npm ", "pip ", "git ",
                              "error:", "warning:", "info:"]

        code_score = sum(1 for i in code_indicators if i in text_lower)
        ui_score = sum(1 for i in ui_indicators if i in text_lower)
        terminal_score = sum(1 for i in terminal_indicators if i in text_lower)

        scores = {"code": code_score, "ui": ui_score, "terminal": terminal_score}
        best = max(scores, key=scores.get)

        if scores[best] == 0:
            return "text"
        return best

    def describe_position(self, x: int, y: int) -> str:
        """Describe una posicion en lenguaje natural."""
        if self._frame_width == 0:
            return "unknown position"

        # Posicion relativa
        x_pct = x / self._frame_width
        y_pct = y / self._frame_height

        h_pos = "left" if x_pct < 0.33 else ("right" if x_pct > 0.66 else "center")
        v_pos = "top" if y_pct < 0.33 else ("bottom" if y_pct > 0.66 else "middle")

        # Buscar zona
        for zone in self._zones:
            if zone.contains(x, y):
                return f"{v_pos}-{h_pos} (in {zone.name}, {zone.content_type})"

        return f"{v_pos}-{h_pos}"

    def to_ai_context(self) -> str:
        """Genera descripcion del layout para el AI prompt."""
        if not self._zones:
            return "Screen layout not analyzed yet."

        lines = ["### Screen Layout"]
        for zone in self._zones:
            lines.append(f"- **{zone.name}**: {zone.content_type} content ({zone.coverage_pct}% of screen)")
        return "\n".join(lines)

    def get_zones(self) -> list[dict]:
        return [z.to_dict() for z in self._zones]

    @property
    def zone_count(self) -> int:
        return len(self._zones)
