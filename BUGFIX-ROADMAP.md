# ILUMINATY BUGFIX ROADMAP (UNIFICADO)
## Fecha base: 2026-04-02

## Objetivo
Cerrar brechas reales de precision, contexto y coherencia producto sin romper flujo actual.

## Estado de avance (2026-04-02)
- Fase 0:
- Completado: strict monitor en endpoints (`monitor_frame_not_available`), callback de captura sin fallback cross-monitor, modo `--monitor 0` como default (auto multi-monitor), OCR nativo por region/ventana/monitor.
- En validacion continua: smoke multi-monitor largo.
- Fase 1:
- Completado (parcial): orientacion obligatoria para acciones `destructive/system` en precheck.
- Pendiente: telemetria extendida y baseline E2E larga.

---

## Fase 0 (P0) - Estabilidad de contexto multi-monitor
Duracion estimada: 2-4 dias

1. Eliminar fallback cross-monitor en `_latest_slot_for_monitor`.
- Archivo: `iluminaty/server.py`.
- Cambio:
  - si se pide `monitor_id` y no hay frame para ese monitor, responder error controlado (`404 monitor_frame_not_available`) en vez de `get_latest()` global.
- Criterio de aceptacion:
  - `vision/*?monitor_id=N` nunca devuelve `monitor_id` diferente a `N`.

2. Corregir callback en `ScreenCapture` para usar el slot correcto.
- Archivo: `iluminaty/capture.py`.
- Cambio:
  - pasar al callback el slot recien empujado (o `get_latest_for_monitor(self.config.monitor)`), no `get_latest()` global.
- Criterio de aceptacion:
  - en multi-monitor, callback reporta monitor consistente en 100% de eventos.

3. Arreglar logica de arranque multi-monitor por default.
- Archivo: `iluminaty/main.py`.
- Cambio propuesto:
  - `monitor=0` => auto multi-monitor.
  - `monitor>=1` => monitor fijo.
- Criterio de aceptacion:
  - `--monitor 1` captura solo monitor 1.
  - `--monitor 0` activa orchestrator multi-monitor.

4. OCR de alta precision por ventana/region nativa.
- Archivos: `iluminaty/server.py`, `iluminaty/vision.py` (y helper de captura puntual).
- Cambio:
  - nueva ruta opcional de OCR nativo sin downscale para region critica.
- Criterio de aceptacion:
  - mejora medible de OCR en UI densa (terminal/editor) vs ruta actual.

---

## Fase 1 (P1) - Navegacion humana robusta
Duracion estimada: 3-5 dias

1. Endurecer orientacion obligatoria en acciones de alto riesgo.
- Integrar chequeo de contexto/monitor antes de ejecutar acciones no triviales en `/action/execute`.

2. Mejorar fallback de monitor activo.
- Archivo: `iluminaty/monitors.py`.
- Cambio:
  - cuando no hay `window_bounds`, usar ultimo monitor activo real, no hardcode `1`.

3. Test suite para regressions de contexto.
- Nuevos tests:
  - `test_server_monitor_strictness.py`
  - `test_capture_callback_monitor_consistency.py`
  - `test_main_monitor_mode_selection.py`
  - `test_ocr_native_region_path.py`

4. Definir baseline "human navigation cycle" como contrato.
- `orient -> locate -> focus -> read -> act -> verify`
- Exponer telemetria de cada paso para debug rapido.

---

## Fase 2 (P1/P2) - Coherencia app + web + licensing
Duracion estimada: 2-3 dias

1. Fuente unica de verdad para conteos.
- Exponer endpoint de capacidades derivado de `licensing.py`.
- Desktop/Web consumen ese endpoint en vez de hardcode.

2. Alinear narrativa comercial con estado real.
- Corregir `17/42+/48/50` segun modo/plan real.
- Corregir anchors/links rotos de docs.

3. Revisar gating de tools base de orientacion.
- Evaluar mover `list_windows`, `focus_window`, `see_monitor` a Free o crear subset minimo equivalente.

---

## Fase 3 (P2) - Latencia y throughput MCP
Duracion estimada: 3-4 dias

1. Batch endpoint/tool para acciones encadenadas.
- Objetivo: reducir round-trips MCP->HTTP en secuencias cortas.

2. Cache corto de contexto operativo.
- TTL corto para `windows/active`, `spatial/state`, `vision/window` en operaciones consecutivas.

3. Metricas de latencia por tramo.
- Medir:
  - mcp_parse_ms
  - api_roundtrip_ms
  - action_exec_ms
  - verify_ms

---

## Fase 4 (Arquitectura opcional) - Workers por monitor
Duracion estimada: 5-8 dias

1. Worker local por monitor con micro-worldstate.
2. Coordinator central con routing por monitor y fusion global.
3. Contrato temporal:
- `global_tick_id`
- `monitor_tick_id`
- `staleness_ms_monitor`

Cuándo hacerla:
- Solo despues de completar Fase 0/1.

---

## Gate de calidad (antes de merge final)

1. Precision multi-monitor:
- 0 contaminacion cross-monitor en endpoints con `monitor_id`.

2. Performance:
- `p95 perception/world <= 300ms` en carga normal.

3. Navegacion:
- demo guiada de 10 interacciones consecutivas sin desalineacion de contexto.

4. Consistencia producto:
- conteos y capacidades iguales entre backend, app y web.

5. Pruebas:
- unit + integration + smoke e2e en entorno reproducible.

---

## Riesgos y mitigacion

1. Riesgo de introducir regresiones en rutas de accion.
- Mitigacion: feature flags + tests nuevos + rollout por fases.

2. Riesgo de degradar latencia con OCR nativo.
- Mitigacion: usar OCR nativo solo bajo demanda y con threshold.

3. Riesgo de drift entre marketing y codigo.
- Mitigacion: endpoint de capacidades + consumo automatico en UI.
