# IPA Encoder — Cerrando la Brecha

## El gap que existe hoy

IPA v3 usa `imagehash` como encoder — un hash perceptual de 64 bits que
detecta si la pantalla cambió pero no "entiende" qué hay en ella.

Cuando Claude/GPT-4o necesita hacer click en "el ícono de papelera" o
"el botón azul de la esquina derecha", tiene que estimarlo desde la imagen.
Esa estimación falla en:

- Íconos pequeños sin texto
- Elementos visualmente similares (múltiples botones del mismo color)
- UIs densas con muchos elementos
- Apps con diseños no estándar

**El gap vs Computer Use:** Anthropic entrena sus modelos específicamente
para entender UIs y dar coordenadas precisas. Nosotros dependemos de la
visión general de Claude/GPT-4o que no fue optimizada para esto.

---

## La solución: IPAEncoder propio

Un modelo de embedding de patches de UI entrenado específicamente en
interfaces de software. No necesita entender "un perro en el parque" —
necesita entender "este rectángulo gris con texto es un botón disabled".

**Lo que hace:**
- Dado un patch de 16×16 o 32×32 píxeles de pantalla
- Produce un vector de 128 dimensiones
- Dos patches del mismo elemento (hover vs normal) → vectores similares
- Un botón y un campo de texto → vectores distintos

**Lo que no necesita hacer:**
- Entender contenido natural (fotos, objetos del mundo real)
- Multimodal texto-imagen
- Generar texto

---

## Arquitectura propuesta: ViT-Tiny UI

```
Input: imagen completa de pantalla (1920×1080)
       ↓
Dividir en patches de 16×16 píxeles → 120×67 = 8040 patches
       ↓
ViT-Tiny encoder (6M parámetros, ~20MB)
  - 12 attention heads
  - 192-dim hidden size
  - 12 transformer layers
       ↓
Output: vector 128-dim por patch
       ↓
Para cada patch: [x, y, embedding_128d]
```

**Por qué ViT-Tiny:**
- 6M parámetros vs 400M de SigLIP → carga en <100ms en CPU
- Inferencia <5ms en CPU, <1ms en GPU
- Entrenado en UI → mejor precisión para nuestro dominio
- Sin dependencias de Google ni OpenAI

---

## Objetivo de entrenamiento: Contrastive Learning

La técnica es **SimCLR** (Self-Supervised Contrastive Learning) — no necesitas
etiquetas manuales. El modelo aprende de pares de imágenes similares.

### Pares positivos (similares → vectores cercanos):
- El mismo botón en estado normal y hover
- El mismo campo de texto vacío y con cursor
- El mismo ícono en tamaño diferente
- El mismo elemento con diferente tema (dark/light mode)
- El mismo elemento en diferentes resoluciones de pantalla

### Pares negativos (distintos → vectores lejanos):
- Un botón y un campo de texto
- Un ícono de guardado y uno de papelera
- Un menú y un botón
- Un elemento clickeable y texto estático

El modelo aprende a representar "este tipo de elemento UI" sin que nadie
le diga explícitamente qué es cada cosa.

---

## Fuente de datos — cómo generarlos

### Fuente 1: ILUMINATY mismo (ya disponible)
```python
# Mientras el usuario trabaja, capturar:
# frame → UITree → {elemento, bounding_box, estado}

for frame in ipa_buffer:
    elements = ui_tree.get_elements()
    for el in elements:
        patch = crop_patch(frame, el.x, el.y, el.w, el.h)
        label = {
            "role": el.role,           # "button", "textfield", etc.
            "name": el.name,           # "Save", "Cancel", etc.
            "state": el.state,         # "normal", "hover", "disabled"
        }
        save_to_dataset(patch, label)
```

1 hora de uso normal → ~50,000 patches con etiquetas automáticas.
El UITree da el ground truth gratis — sin etiquetado manual.

### Fuente 2: Dataset RICO (público, libre)
- 66,000 screenshots de apps Android anotadas
- Universidad de Michigan, libre para uso comercial
- URL: https://interactionmining.org/rico

### Fuente 3: Generación sintética
```python
from PIL import Image, ImageDraw, ImageFont

# Generar 100K variaciones de elementos UI
for _ in range(100_000):
    element_type = random.choice(["button", "input", "checkbox", "link"])
    text = random.choice(BUTTON_TEXTS)
    style = random.choice(STYLES)   # colores, bordes, tamaños
    state = random.choice(["normal", "hover", "disabled", "focused"])
    
    img = render_element(element_type, text, style, state)
    # Este par (img, img_augmented) es un par positivo
```

En 2 horas de CPU: 500,000 pares sintéticos. Sin copyright.
Las augmentaciones (rotación leve, cambio de color, ruido) generan los pares.

### Fuente 4: Web scraping con Playwright
```python
for url in TOP_1000_WEBSITES:
    page = await browser.new_page()
    await page.goto(url)
    screenshot = await page.screenshot()
    elements = await page.query_selector_all("button, input, a, select")
    for el in elements:
        box = await el.bounding_box()
        # crop patch from screenshot using box
        # element type from tagName/role
```

1 día de scraping → ~2M patches de webs reales.

---

## Pipeline de entrenamiento

```
Dataset: 500K pares (patch_a, patch_b, is_similar)
    ↓
Preprocesamiento:
  - Resize todos los patches a 32×32
  - Normalizar [0,1]
  - Augmentaciones: flip, color jitter, blur leve
    ↓
Modelo: ViT-Tiny
  - Backbone: timm.create_model("vit_tiny_patch16_224", pretrained=False)
  - Projection head: Linear(192, 128) → L2 normalize
    ↓
Loss: NT-Xent (contrastive loss de SimCLR)
  - Pares positivos: mismo elemento, estados distintos
  - Pares negativos: elementos distintos en el mismo batch
    ↓
Optimizador: AdamW lr=3e-4, cosine schedule
Batch size: 256 (128 pares)
Epochs: 100
    ↓
Hardware: RTX 3070 (8GB VRAM)
Tiempo estimado: 8-16 horas para dataset 500K
```

---

## Cómo integrar en IPA v3

Una vez entrenado, el encoder reemplaza al de imagehash:

```python
# ipa/encoder.py — nivel 3 (nuevo, propio)
class VisualEncoder:
    
    def load(self):
        if self.device == "ipa_encoder":
            self._level = 3
            self._load_ipa_encoder()
    
    def _load_ipa_encoder(self):
        """Load our own UI encoder — tiny, fast, domain-specific."""
        import torch
        import timm
        
        self._model = timm.create_model(
            "vit_tiny_patch16_32",    # 32×32 input patches
            pretrained=False,
            num_classes=0,            # embedding mode
            embed_dim=192,
        )
        # Load our trained weights
        weights = torch.load("ipa/weights/ipa_encoder_v1.pt")
        self._model.load_state_dict(weights)
        self._model.eval()
    
    def encode_patches(self, image):
        # Level 3: 8×8 grid → 64 patches of 32×32 each → 64 × 128 vectors
        patches = split_into_patches(image, patch_size=32, stride=32)
        embeddings = self._model(patches)   # (64, 128)
        return embeddings
```

**Ventaja vs imagehash:**
- imagehash: 1 vector global de 64 bits → solo detecta "cambió algo"
- IPAEncoder: 64 vectores de 128d → sabe qué elemento específico cambió y dónde

**Ventaja vs SigLIP:**
- SigLIP: 400MB, 768d, entrenado en imágenes generales
- IPAEncoder: ~20MB, 128d, entrenado en UI específicamente → más preciso para nuestro caso

---

## Smart Locate con IPAEncoder

Con IPAEncoder, `smart_locate` puede encontrar elementos visuales sin texto:

```python
# Hoy: solo funciona para texto visible (OCR)
act(action="click", target="Save button")   # ✅ tiene texto
act(action="click", target="trash icon")    # ❌ no tiene texto

# Con IPAEncoder:
# 1. Extraer embedding del ícono de papelera de la pantalla
# 2. Comparar con embedding de referencia de "trash icon"
# 3. Encontrar el patch más similar → coordenadas exactas
act(action="click", target="trash icon")    # ✅ funciona por similitud visual
```

La librería de referencias se construye automáticamente desde el dataset
de entrenamiento — cada elemento con su embedding "canónico".

---

## Cronograma estimado

| Fase | Descripción | Tiempo |
|---|---|---|
| **Recolector de datos** | Script ILUMINATY + UITree auto-etiqueta | 2 días |
| **Datos sintéticos** | Generador PIL para 500K pares | 1 día |
| **Arquitectura** | ViT-Tiny + SimCLR en PyTorch | 2-3 días |
| **Entrenamiento v1** | RTX 3070, dataset 500K | 1-2 días GPU |
| **Evaluación** | ¿Encuentra elementos correctamente? | 1 día |
| **Integración IPA** | Nuevo nivel en encoder.py | 1 día |
| **smart_locate v2** | Búsqueda por similitud visual | 2 días |
| **Total** | Primera versión funcional | **~2-3 semanas** |

---

## Métricas de éxito

El encoder es "bueno" cuando:

1. **Mismo elemento, estados distintos** → cosine similarity > 0.85
   (botón Save en normal vs hover)

2. **Elementos distintos** → cosine similarity < 0.3
   (botón Save vs campo de búsqueda)

3. **Smart locate accuracy** > 90% en elementos con texto
   (línea base que ya tenemos con OCR)

4. **Smart locate accuracy** > 75% en elementos sin texto
   (objetivo principal del encoder)

5. **Inferencia** < 10ms en CPU para una pantalla completa

---

## Por qué es estratégicamente importante

Cuando IPAEncoder esté listo:

- IPA v3 será 100% nuestro en todos los niveles
- smart_locate funcionará para cualquier elemento, con o sin texto
- La precisión de click igualará o superará a Computer Use
- El modelo pequeño (~20MB) permite distribución sin descargas pesadas
- Podemos entrenarlo continuamente con datos reales de usuarios
  (opt-in, privado, local)

Este es el componente que convierte ILUMINATY de "muy bueno para texto"
a "preciso para cualquier elemento UI" — cerrando el único gap real
que tiene vs Computer Use.
