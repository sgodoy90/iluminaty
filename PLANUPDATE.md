# ILUMINATY - Plan de Actualizacion v1.0
## "Computer Use Nivel Dios: De Ojos a Manos"

**Fecha:** 29 de Marzo, 2026
**Version actual:** 0.5.0 → **Version objetivo:** 1.0.0
**Autor:** Godo (sgodoy90) + Claude Code (Opus 4.6)

---

## Resumen

Esta actualizacion transforma ILUMINATY de un sistema de **percepcion** (ojos) a un sistema de **percepcion + accion** (ojos + manos). La IA no solo ve la pantalla — ahora puede controlar la computadora con precision quirurgica a traves de 7 capas de interaccion.

### Principio de Diseño: Cascada Inteligente

```
IA dice: "Guarda el archivo"

Intento 1: API Directa (VS Code command) .......... <10ms
Intento 2: Keyboard (Ctrl+S) ...................... ~50ms
Intento 3: UI Tree (busca boton "Save") ........... ~100ms
Intento 4: Vision (OCR + click en "Save") ......... ~500ms
```

Siempre intenta el metodo mas rapido primero. Si falla, cae al siguiente.

---

## Orden de Implementacion

### Sprint 1: Foundation (Capa 1 + Capa 7 Safety)
> "Primero las manos basicas y SIEMPRE con seguridad"

| # | Archivo Nuevo | Descripcion | Prioridad |
|---|---------------|-------------|-----------|
| 1 | `iluminaty/windows.py` | Window manager: move, resize, focus, minimize, maximize, close, list | CRITICA |
| 2 | `iluminaty/clipboard.py` | Clipboard avanzado: read/write text+images, historial | CRITICA |
| 3 | `iluminaty/process_mgr.py` | Process manager: list, open, close, kill procesos | CRITICA |
| 4 | `iluminaty/autonomy.py` | 3 niveles: SUGGEST / CONFIRM / AUTO | CRITICA |
| 5 | `iluminaty/audit.py` | Audit log persistente (SQLite encrypted) | CRITICA |
| 6 | `iluminaty/safety.py` | Kill switch global + whitelist + rate limits adaptativos | CRITICA |

**Upgrade:**
- `iluminaty/actions.py` → Agregar: double_click, right_click, drag_drop, press/release, unicode text

**Integrar en:**
- `iluminaty/server.py` → Nuevos endpoints: `/action/*`, `/windows/*`, `/clipboard/*`, `/process/*`, `/safety/*`
- `iluminaty/main.py` → Nuevos args: `--actions`, `--autonomy`, `--action-whitelist`
- `iluminaty/__init__.py` → Exportar nuevos modulos

---

### Sprint 2: UI Intelligence (Capa 2 + Capa 3)
> "De pixeles a elementos reales"

| # | Archivo Nuevo | Descripcion | Prioridad |
|---|---------------|-------------|-----------|
| 7 | `iluminaty/ui_tree.py` | Accessibility Tree cross-platform (UIAutomation Win, AXUIElement Mac, AT-SPI Linux) | CRITICA |
| 8 | `iluminaty/vscode.py` | VS Code command bridge: ejecutar cualquier comando via CLI/API | ALTA |
| 9 | `iluminaty/terminal.py` | Terminal PTY: ejecutar comandos, leer output, detectar errores en tiempo real | ALTA |
| 10 | `iluminaty/git_ops.py` | Git wrapper: commit, push, pull, branch, diff, log sin abrir terminal | ALTA |

**Upgrade:**
- `iluminaty/spatial.py` → Integrar datos del UI Tree (elementos reales, no solo OCR zones)
- `iluminaty/actions.py` → Agregar: `click_element(name)`, `type_in_field(field_name, text)` usando UI Tree

**Nuevos endpoints:**
- `/ui/elements` → Lista todos los elementos UI visibles
- `/ui/find?name=Save` → Buscar elemento por nombre/rol
- `/ui/click?name=Save` → Click en elemento por nombre
- `/ui/type?field=Email&text=test@test.com` → Escribir en campo por nombre
- `/vscode/command?cmd=workbench.action.files.save` → Ejecutar comando VS Code
- `/terminal/exec?cmd=npm test` → Ejecutar comando en terminal
- `/git/status`, `/git/commit`, `/git/push` → Operaciones Git

---

### Sprint 3: Web + Files (Capa 4 + Capa 5)
> "El browser y el file system bajo control"

| # | Archivo Nuevo | Descripcion | Prioridad |
|---|---------------|-------------|-----------|
| 11 | `iluminaty/browser.py` | Chrome DevTools Protocol: DOM, forms, network, JS, tabs | ALTA |
| 12 | `iluminaty/filesystem.py` | File system sandbox: read, write, search, watch con permisos | ALTA |

**Nuevos endpoints:**
- `/browser/dom` → DOM completo de la pagina activa
- `/browser/click?selector=#submit` → Click en elemento DOM
- `/browser/fill` → Llenar formulario web
- `/browser/navigate?url=...` → Navegar a URL
- `/browser/tabs` → Listar/crear/cerrar tabs
- `/files/read?path=...` → Leer archivo (sandbox)
- `/files/write` → Escribir archivo (con backup)
- `/files/search?pattern=*.py&contains=TODO` → Buscar archivos
- `/files/watch?path=./src` → Observar cambios en tiempo real

---

### Sprint 4: Brain (Capa 6)
> "De acciones individuales a agente autonomo"

| # | Archivo Nuevo | Descripcion | Prioridad |
|---|---------------|-------------|-----------|
| 13 | `iluminaty/resolver.py` | Action resolver: cascada API > UI Tree > Vision > Keyboard | CRITICA |
| 14 | `iluminaty/intent.py` | Intent classifier: "guarda el archivo" → accion concreta | ALTA |
| 15 | `iluminaty/planner.py` | Task decomposer: tareas complejas → sub-acciones con dependencias | ALTA |
| 16 | `iluminaty/verifier.py` | Post-action verification: confirmar que la accion tuvo efecto | ALTA |
| 17 | `iluminaty/recovery.py` | Error recovery: alternativas automaticas + escalation | MEDIA |

**Nuevos endpoints:**
- `POST /agent/do` → Intent-based: "guarda el archivo", "abre Chrome", "ejecuta los tests"
- `POST /agent/plan` → Devuelve plan de sub-acciones sin ejecutar (dry run)
- `POST /agent/execute` → Ejecuta un plan aprobado
- `GET /agent/status` → Estado del agente (idle, planning, executing, waiting_confirmation)

---

## Archivos Modificados (Existentes)

| Archivo | Cambios |
|---------|---------|
| `iluminaty/actions.py` | +double_click, +right_click, +drag_drop, +press/release, +unicode, +click_element (UI Tree), +type_in_field |
| `iluminaty/spatial.py` | Integrar UI Tree data, mejorar zone detection |
| `iluminaty/server.py` | +50 endpoints nuevos para todas las capas |
| `iluminaty/main.py` | +CLI args: --actions, --autonomy, --browser-debug-port, --file-sandbox |
| `iluminaty/__init__.py` | Exportar todos los modulos nuevos |
| `iluminaty/mcp_server.py` | +10 MCP tools nuevos (do_action, find_element, run_command, etc.) |
| `iluminaty/fusion.py` | Incluir action state + autonomy level en el prompt de AI |

## Archivos Nuevos (17 modulos)

```
iluminaty/
├── [existentes - 25 modulos]
├── windows.py          # Capa 1: Window management
├── clipboard.py        # Capa 1: Clipboard avanzado
├── process_mgr.py      # Capa 1: Process management
├── ui_tree.py          # Capa 2: Accessibility Tree
├── vscode.py           # Capa 3: VS Code command bridge
├── terminal.py         # Capa 3: Terminal PTY
├── git_ops.py          # Capa 3: Git operations
├── browser.py          # Capa 4: Chrome DevTools Protocol
├── filesystem.py       # Capa 5: File system sandbox
├── resolver.py         # Capa 6: Action cascade resolver
├── intent.py           # Capa 6: Intent classifier
├── planner.py          # Capa 6: Task decomposer
├── verifier.py         # Capa 6: Post-action verification
├── recovery.py         # Capa 6: Error recovery
├── autonomy.py         # Capa 7: Autonomy levels
├── audit.py            # Capa 7: Persistent audit log
└── safety.py           # Capa 7: Kill switch + whitelist
```

**Total post-update: 42 modulos (~12,000+ LOC estimados)**

---

## Metricas de Exito

| Metrica | v0.5 (actual) | v1.0 (objetivo) |
|---------|---------------|------------------|
| Acciones disponibles | 7 (basicas) | 42+ (7 capas) |
| Velocidad de accion | N/A (solo vision) | <10ms (API) a 500ms (vision fallback) |
| Precision de click | Coordenadas pixel | Elemento exacto por nombre/rol |
| UI element detection | OCR only | Accessibility Tree + OCR + pixel |
| Browser control | Screenshot only | DOM completo via CDP |
| File system access | None | Sandbox con permisos |
| Error recovery | None | Cascada automatica 4 niveles |
| Safety | Basic rate limit | 3 niveles autonomia + whitelist + audit |
| Modulos totales | 25 | 42 |

---

## Dependencias Nuevas

| Package | Uso | Plataforma | Opcional |
|---------|-----|------------|----------|
| `comtypes` | UIAutomation (Windows) | Windows | Si (graceful fallback) |
| `pyobjc-framework-ApplicationServices` | AXUIElement (macOS) | macOS | Si |
| `pyperclip` | Clipboard cross-platform | Todas | No |
| `psutil` | Process management | Todas | No |
| `websockets` | Chrome DevTools Protocol | Todas | Si |
| `watchdog` | File system watcher | Todas | Si |
| `gitpython` | Git operations | Todas | Si |
| `keyboard` | Global hotkey (kill switch) | Todas | Si |

---

## Notas

- **Cada modulo nuevo sigue el mismo patron**: clase principal, graceful degradation si dependencia falta, integra con server.py via endpoints
- **Safety PRIMERO**: Capa 7 se implementa en Sprint 1, no al final. Todo lo demas se construye SOBRE el sistema de seguridad.
- **Backward compatible**: Nada de v0.5 se rompe. Todos los endpoints existentes siguen funcionando.
- **Sprint 1 es el mas critico**: Sin window management + safety + action upgrade, no se puede construir nada encima.
