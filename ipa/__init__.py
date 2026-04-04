"""IPA v3 — Real Eyes: Compressed Visual Stream for AI perception.

100% nuestro. Sin SigLIP, sin TurboQuant, sin dependencias de terceros
en el núcleo. Solo numpy + pillow + imagehash.

Qué hace IPA:
  Captura frames de pantalla y los convierte en un stream comprimido
  eficiente usando I-frames y P-frames (como un codec de video).
  
  El cambio clave vs screenshots: IPA solo transmite lo que cambió,
  manteniendo un buffer temporal en RAM a bajo costo.

Qué NO hace IPA (y no necesita):
  - "Entender" el contenido visual — eso lo hace Claude/GPT-4o
  - Búsqueda semántica por texto — feature secundaria, no esencial
  - Depender de modelos de Google, OpenAI ni ningún lab externo

La IA recibe imágenes reales (WebP) via see_now/what_changed.
IPA es la infraestructura de captura y cambio, no la inteligencia visual.

Usage:
    from ipa import IPAEngine

    engine = IPAEngine()
    frame  = engine.feed(pil_image)       # Feed a frame
    ctx    = engine.context(seconds=30)   # Get visual context
    motion = engine.motion(seconds=5)     # Get motion field
"""

from ipa.types import (
    PatchFrame,
    MotionField,
    VisualContext,
    Region,
    KeyMoment,
    SearchResult,
)
from ipa.engine import IPAEngine

__version__ = "3.0.0"
__all__ = [
    "IPAEngine",
    "PatchFrame",
    "MotionField",
    "VisualContext",
    "Region",
    "KeyMoment",
    "SearchResult",
]
