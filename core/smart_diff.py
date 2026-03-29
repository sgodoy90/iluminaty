"""
ILUMINATY - Smart Visual Diff
================================
No solo detectar SI algo cambio, sino QUE y DONDE cambio.

Niveles:
1. Frame-level: "algo cambio" (ya existe - hash MD5)
2. Region-level: "la region (200,300)-(500,400) cambio" (NUEVO)
3. Semantic-level: "aparecio una nueva ventana" (FUTURO)

Tecnicas:
- Divide el frame en grid NxN
- Compara cada celda con hash independiente
- Solo envia las celdas que cambiaron (delta frames)
- Heatmap de cambios acumulados en el tiempo
"""

import io
import hashlib
import numpy as np
from PIL import Image
from typing import Optional
from dataclasses import dataclass


@dataclass
class DiffRegion:
    """Una region que cambio entre dos frames."""
    grid_x: int
    grid_y: int
    pixel_x: int
    pixel_y: int
    pixel_w: int
    pixel_h: int
    change_intensity: float  # 0.0 a 1.0


@dataclass
class FrameDiff:
    """Resultado de comparar dos frames."""
    changed: bool
    change_percentage: float  # 0-100
    changed_regions: list[DiffRegion]
    total_cells: int
    changed_cells: int
    heatmap: Optional[list[list[float]]] = None  # grid NxN con intensidad


class SmartDiff:
    """
    Comparador visual inteligente.
    Divide frames en grid y detecta que celdas cambiaron.
    """

    def __init__(self, grid_cols: int = 8, grid_rows: int = 6, threshold: float = 0.05):
        """
        grid_cols x grid_rows = celdas del grid.
        threshold = minimo cambio para considerar "diferente" (0-1).
        """
        self.grid_cols = grid_cols
        self.grid_rows = grid_rows
        self.threshold = threshold
        self._prev_hashes: Optional[list[list[str]]] = None
        self._prev_cells: Optional[list[list[np.ndarray]]] = None
        self._heatmap: list[list[float]] = [
            [0.0] * grid_cols for _ in range(grid_rows)
        ]
        self._heatmap_decay = 0.9  # cuanto se desvanece el heatmap por frame

    def _frame_to_array(self, frame_bytes: bytes) -> np.ndarray:
        """Convierte frame bytes a numpy array."""
        img = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
        return np.array(img)

    def _split_grid(self, arr: np.ndarray) -> list[list[np.ndarray]]:
        """Divide array en grid NxN de celdas."""
        h, w = arr.shape[:2]
        cell_h = h // self.grid_rows
        cell_w = w // self.grid_cols
        grid = []
        for row in range(self.grid_rows):
            row_cells = []
            for col in range(self.grid_cols):
                y1 = row * cell_h
                y2 = y1 + cell_h if row < self.grid_rows - 1 else h
                x1 = col * cell_w
                x2 = x1 + cell_w if col < self.grid_cols - 1 else w
                row_cells.append(arr[y1:y2, x1:x2])
            grid.append(row_cells)
        return grid

    def _cell_hash(self, cell: np.ndarray) -> str:
        """Hash rapido de una celda del grid."""
        # Downscale a 8x8 y hashear para velocidad
        small = Image.fromarray(cell).resize((8, 8))
        return hashlib.md5(np.array(small).tobytes()).hexdigest()

    def _cell_diff_intensity(self, cell_a: np.ndarray, cell_b: np.ndarray) -> float:
        """
        Calcula intensidad de cambio entre dos celdas (0-1).
        Usa Mean Absolute Difference normalizado.
        """
        if cell_a.shape != cell_b.shape:
            return 1.0
        diff = np.abs(cell_a.astype(float) - cell_b.astype(float))
        return float(diff.mean() / 255.0)

    def compare(self, frame_bytes: bytes) -> FrameDiff:
        """
        Compara frame actual con el anterior.
        Retorna diff detallado con regiones cambiadas.
        """
        arr = self._frame_to_array(frame_bytes)
        h, w = arr.shape[:2]
        cell_h = h // self.grid_rows
        cell_w = w // self.grid_cols

        current_grid = self._split_grid(arr)
        current_hashes = [
            [self._cell_hash(current_grid[r][c]) for c in range(self.grid_cols)]
            for r in range(self.grid_rows)
        ]

        # Si no hay frame anterior, todo es "nuevo"
        if self._prev_hashes is None:
            self._prev_hashes = current_hashes
            self._prev_cells = current_grid
            return FrameDiff(
                changed=True,
                change_percentage=100.0,
                changed_regions=[],
                total_cells=self.grid_cols * self.grid_rows,
                changed_cells=self.grid_cols * self.grid_rows,
                heatmap=None,
            )

        # Comparar celda por celda
        changed_regions = []
        changed_count = 0
        total = self.grid_cols * self.grid_rows

        for row in range(self.grid_rows):
            for col in range(self.grid_cols):
                if current_hashes[row][col] != self._prev_hashes[row][col]:
                    # Calcular intensidad del cambio
                    intensity = self._cell_diff_intensity(
                        self._prev_cells[row][col],
                        current_grid[row][col]
                    )

                    if intensity >= self.threshold:
                        changed_count += 1
                        self._heatmap[row][col] = min(
                            self._heatmap[row][col] + intensity, 1.0
                        )

                        changed_regions.append(DiffRegion(
                            grid_x=col,
                            grid_y=row,
                            pixel_x=col * cell_w,
                            pixel_y=row * cell_h,
                            pixel_w=cell_w,
                            pixel_h=cell_h,
                            change_intensity=round(intensity, 3),
                        ))
                    else:
                        # Decay heatmap for unchanged
                        self._heatmap[row][col] *= self._heatmap_decay
                else:
                    self._heatmap[row][col] *= self._heatmap_decay

        # Update previous
        self._prev_hashes = current_hashes
        self._prev_cells = current_grid

        change_pct = (changed_count / total) * 100 if total > 0 else 0

        return FrameDiff(
            changed=changed_count > 0,
            change_percentage=round(change_pct, 1),
            changed_regions=changed_regions,
            total_cells=total,
            changed_cells=changed_count,
            heatmap=[
                [round(self._heatmap[r][c], 3) for c in range(self.grid_cols)]
                for r in range(self.grid_rows)
            ],
        )

    def get_delta_regions(self, frame_bytes: bytes, diff: FrameDiff) -> list[dict]:
        """
        Extrae solo las regiones que cambiaron como mini-images.
        Para enviar deltas en vez del frame completo -> ahorro masivo de tokens.
        """
        if not diff.changed_regions:
            return []

        img = Image.open(io.BytesIO(frame_bytes))
        deltas = []

        for region in diff.changed_regions:
            crop = img.crop((
                region.pixel_x, region.pixel_y,
                region.pixel_x + region.pixel_w,
                region.pixel_y + region.pixel_h,
            ))
            buf = io.BytesIO()
            crop.save(buf, format="WEBP", quality=80)
            import base64
            deltas.append({
                "grid": f"({region.grid_x},{region.grid_y})",
                "pixel": f"({region.pixel_x},{region.pixel_y})",
                "size": f"{region.pixel_w}x{region.pixel_h}",
                "intensity": region.change_intensity,
                "image_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
                "image_bytes": len(buf.getvalue()),
            })

        return deltas

    def diff_to_description(self, diff: FrameDiff, frame_width: int, frame_height: int) -> str:
        """
        Genera descripcion textual del diff para el AI prompt.
        """
        if not diff.changed:
            return "No visual changes detected since last frame."

        lines = [
            f"**Visual Changes**: {diff.change_percentage}% of screen changed "
            f"({diff.changed_cells}/{diff.total_cells} regions)",
        ]

        # Agrupar cambios por zona
        if diff.changed_regions:
            top_changes = sorted(diff.changed_regions, key=lambda r: r.change_intensity, reverse=True)[:5]
            lines.append("**Most changed areas**:")
            for r in top_changes:
                # Describir posicion en terminos humanos
                x_pct = r.pixel_x / frame_width
                y_pct = r.pixel_y / frame_height
                pos = []
                if y_pct < 0.33:
                    pos.append("top")
                elif y_pct > 0.66:
                    pos.append("bottom")
                else:
                    pos.append("middle")
                if x_pct < 0.33:
                    pos.append("left")
                elif x_pct > 0.66:
                    pos.append("right")
                else:
                    pos.append("center")

                lines.append(
                    f"  - {'-'.join(pos)} ({r.pixel_x},{r.pixel_y}): "
                    f"intensity {r.change_intensity:.1%}"
                )

        return "\n".join(lines)

    def reset(self):
        """Reset del estado del diff."""
        self._prev_hashes = None
        self._prev_cells = None
        self._heatmap = [
            [0.0] * self.grid_cols for _ in range(self.grid_rows)
        ]
