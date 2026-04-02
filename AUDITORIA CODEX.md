# AUDITORIA CODEX

Fecha: 2026-04-02  
Proyecto: ILUMINATY (IPA RT + Memoria Temporal v2.1)  
Alcance: backend `iluminaty`, app desktop (`desktop-app`) y web (`website`), con validación de coherencia cruzada, pruebas funcionales y stress.

## 1) Resumen Ejecutivo
El código está en una base **funcional y sólida**, y en esta pasada se corrigieron incoherencias reales que sí podían afectar operación:

1. **Bug crítico desktop/runtime**: el polling de estado podía disparar instalaciones de dependencias repetidas (`pip`) cada pocos segundos.
2. **Desalineación de capacidades** entre backend/MCP vs app/web (contadores de tools y mensajes de plan desactualizados).
3. **Riesgo de desconexión al cambiar puerto** desde Settings con servidor en ejecución.

Estado final tras correcciones:
- Backend: estable, pruebas verdes.
- Desktop app: estable, sin side effects agresivos en polling runtime.
- Web: métricas/claims alineadas con el estado actual de MCP.
- Stress gate: **PASS**.

## 2) Hallazgos y Correcciones Aplicadas

### H1. Runtime polling con efecto colateral (CRÍTICO) — CORREGIDO
Problema:
- `get_runtime_status` llamaba flujo de bootstrap/install.
- La UI consulta estado cada ~4s.
- Resultado potencial: intentos de instalación repetidos y consumo innecesario de CPU/IO/red.

Corrección:
- Se agregó `probe_runtime(...)` en Rust (`desktop-app/src-tauri/src/lib.rs`) para chequeo **read-only**.
- `get_runtime_status` ahora usa `probe_runtime`, sin instalar paquetes.
- `bootstrap_runtime` mantiene el flujo instalador explícito cuando el usuario lo solicita.

Impacto:
- El estado runtime ya no “ensucia” ni ralentiza el sistema por polling.
- Mejora fuerte de estabilidad percibida en desktop.

### H2. Cambio de puerto en caliente podía romper conectividad (ALTO) — CORREGIDO
Problema:
- Si el usuario guardaba nuevo `api_port` con server activo, la app pasaba a consultar el puerto nuevo antes de reiniciar backend.

Corrección:
- En `desktop-app/src/main.js`, `saveSettings` ahora detecta cambio de puerto.
- Si el server está online, ejecuta secuencia controlada:
  - `stop_server`
  - `start_server`
  - espera corta de estabilización
- Luego aplica `/config` y `/operating/mode`.

Impacto:
- Se evita “quedar ciego” por mismatch de puertos durante sesión activa.

### H3. Incoherencias de plan/herramientas entre backend y frontends (MEDIO) — CORREGIDO
Problema:
- Desktop/Web mostraban valores viejos (`17`, `25`, `44`) mientras MCP real expone 48 tools (free tier expandido).

Corrección:
- Se alinearon métricas visibles:
  - Free MCP tools: **29**
  - Total MCP tools: **48**
- Archivos actualizados:
  - `desktop-app/src/index.html`
  - `desktop-app/src/main.js`
  - `website/index.html`
  - `website/dashboard.html`
- Se actualizó también la capa de licencia para mantener contrato interno consistente:
  - `iluminaty/licensing.py` (sets FREE/PRO/ALL MCP tools)
  - `iluminaty/server.py` (`/license/status` para `mcp_tools.max`)
  - `iluminaty/mcp_server.py` (comentarios de conteos actualizados)

Impacto:
- Mensaje de producto y estado operativo alineados entre código, app y web.

### H4. Riesgo de deriva futura entre licencia y catálogo MCP (MEDIO) — MITIGADO
Corrección preventiva:
- Nuevo test `tests/test_license_mcp_sync.py`:
  - asegura que sets de licencia y gating MCP estén sincronizados.
  - asegura que todo tool registrado en `TOOLS` esté cubierto por catálogo licenciado.

## 3) Validaciones Ejecutadas

### Backend / Python
Comando:
- `py -3.13 -m pytest tests -q`

Resultado:
- **32 passed** en ~3.3s

Incluye:
- percepción, gating de contexto, VLM scheduler, focus multi-monitor, contrato server/MCP, sincronía licencia-tools (nuevo).

### Desktop / Rust
Comando:
- `cargo check` en `desktop-app/src-tauri`

Resultado:
- **OK** (compila sin errores).

### Frontend JS sintaxis
Comandos:
- `node --check desktop-app/src/main.js`
- `node --check website/main.js`

Resultado:
- **OK** ambos.

### Stress Gate (release-like)
Comando:
- `py -3.13 tests/stress_ipa_v21_release_gate.py --duration 45 --workers 8`

Resultado:
- **VERDICT: PASS**
- HTTP mixed load:
  - calls: `14443`
  - rps: `320.86`
  - errors: `0.0%`
  - `GET /perception/world p95 = 29.836ms`
  - `POST /action/precheck p95 = 32.097ms`
  - `POST /action/execute p95 = 35.323ms`
  - `POST /perception/query p95 = 31.918ms`
- Stale gate:
  - blocked `500/500` (`100%`) por `context_tick_mismatch` (correcto)
- Recovery:
  - completed `700/700`
  - success `100%`
  - recovered `100%`
- WS soak:
  - messages `357`
  - avg interval `126.627ms`
  - malformed `0`

Reporte generado:
- `STRESS-REPORT-IPA-v2.1.md`

## 4) Coherencia App ↔ Backend ↔ Web (Estado actual)

Validaciones cruzadas ejecutadas:
- Endpoints usados por desktop vs rutas reales FastAPI: **sin faltantes**.
- Selectores DOM referenciados por JS en desktop/web: **sin faltantes**.
- Conteo MCP real:
  - `TOOLS`: 48
  - free gate MCP: 29
  - all/pro gate MCP: 48

Conclusión:
- La base quedó en **unísono operativo** para esta versión.

## 5) Riesgos Residuales (honesto)

1. **Costo VLM**: sigue siendo el principal costo computacional si se habilita captioning local continuo.
2. **Claims de marketing vs evolución futura**: números visibles pueden volver a desalinearse si se agregan tools sin actualizar web/app.
3. **UX de bootstrap**: aunque ya no instala en polling, la primera instalación sigue siendo pesada por naturaleza (depende de red/hardware).

## 6) Recomendaciones inmediatas (siguiente bloque)

1. Integrar métricas de capacidades dinámicas en frontend (leer `/license/status`) para evitar hardcodes.
2. Añadir benchmark repetible CPU-only vs GPU para perfilar VLM por hardware clase (entry/mid/high).
3. Añadir test e2e de “change port while running” (desktop integration) para congelar la corrección.

## 7) Veredicto
Con el estado actual y esta auditoría, **no hay evidencia de bloqueo técnico inmediato** que justifique cancelar el proyecto mañana.  
Sí hay retos duros (sobre todo VLM/costo), pero la plataforma quedó más robusta, coherente y testeada que la iteración anterior.
