# IPA v2 — Documento Maestro

## 1) Resumen Ejecutivo
ILUMINATY V2 posiciona a la IA externa como cerebro y a ILUMINATY como sistema operativo de percepción y acción en tiempo real (ojos + manos).  
Objetivo: pasar de snapshots sueltos a percepción semántica continua con control en bucle cerrado y memoria episódica en RAM, sin persistencia de frames en disco.

Targets:
- Latencia semántica objetivo: `p95 <= 300ms` (CPU-first).
- Memoria visual: RAM-only.
- Memoria contextual: ventana deslizante de `90s` + compresión por transiciones relevantes.
- Contrato universal para proveedores IA vía HTTP + MCP.

## 2) Arquitectura IPA v2

### 2.1 Signal Layer (captura y señales)
Componentes:
- Captura por monitor con `monitor_id`.
- `change_score` continuo (0..1) por monitor.
- pHash y optical flow.
- OCR throttled.
- Estado de ventana activa y contexto de app/workflow.

Estado:
- Implementado con aislamiento por monitor en `MonitorPerceptionState`.
- Sin contaminación cross-monitor en gates de cambio/movimiento/hash.

### 2.2 Semantic Layer (WorldState)
Contrato principal `WorldState`:
- `timestamp_ms`
- `task_phase`
- `active_surface`
- `entities`
- `affordances`
- `attention_targets`
- `uncertainty`
- `readiness`
- `readiness_reasons`
- `risk_mode`

Estado:
- Implementado en `iluminaty/world_state.py`.
- Actualización periódica desde percepción.
- Trazas comprimidas por boundaries de estado + feedback de acciones.

### 2.3 Control Layer (closed-loop)
Pipeline:
1. `precheck`
2. `execute`
3. `verify`
4. `recover`

Estado:
- Implementado en API y usado por MCP.
- En `SAFE/HYBRID`, `precheck` ya integra validación de readiness contextual (bloquea cuando no hay contexto suficiente para actuar de forma segura).

### 2.4 Workers Sys v1 (orquestación operativa)
Componentes:
- `Monitor Workers`: digest semántico por monitor (scene/phase/readiness/staleness).
- `Spatial Worker`: topología de monitores + monitor activo.
- `Fusion Worker`: resumen global unificado para decisión.
- `Intent Worker`: timeline de intenciones emitidas por ejecución.
- `Action Arbiter`: lease single-writer para evitar colisiones entre agentes.
- `Verify Worker`: timeline de verificación y resultados.
- `Memory Worker`: compresión de eventos de workers en RAM.

Estado:
- Implementado en `iluminaty/workers.py`.
- Integrado en `PerceptionEngine` (actualización continua por tick).
- Integrado en `/action/execute` con claim/release automático del arbiter.

## 3) Modos Operativos

### SAFE (default)
- Kill switch activo.
- Safety checks activos.
- Readiness contextual aplicada para ejecución.

### RAW (0 seguridad)
- Sin guardrails de safety/readiness.
- Requisito mínimo: kill switch local.

### HYBRID
- Guardrails para acciones críticas/destructivas.
- Permite mayor libertad en acciones no críticas.

## 4) Contratos HTTP implementados

Percepción:
- `GET /perception/world`
- `GET /perception/trace?seconds=...`
- `GET /perception/readiness`
- `WS /perception/stream`

Control:
- `POST /action/precheck`
- `POST /action/execute`
- `POST /action/raw`
- `POST /action/verify`
- `GET /operating/mode`
- `POST /operating/mode`

Workers:
- `GET /workers/status`
- `GET /workers/monitor/{monitor_id}`
- `POST /workers/action/claim`
- `POST /workers/action/release`

## 5) Contratos MCP implementados

Tools IPA v2:
- `perception_world`
- `perception_trace`
- `action_precheck`
- `do_action`
- `raw_action`
- `verify_action`
- `set_operating_mode`
- `workers_status`
- `workers_monitor`
- `workers_claim_action`
- `workers_release_action`

Integración:
- MCP enruta `do_action` hacia `/action/execute`.
- MCP enruta `raw_action` hacia `/action/raw`.
- MCP ahora soporta header `x-api-key` vía `ILUMINATY_API_KEY` para entornos protegidos.

## 6) Memoria episódica RAM (90s)
Estrategia:
- Buffer temporal comprimido por cambios semánticos (no por frame).
- Se conservan transiciones significativas (`task_phase`, `active_surface`, `readiness`, evento dominante).
- Acciones ejecutadas se insertan como eventos semánticos en trace.

Beneficio:
- Continuidad narrativa para agentes sin persistir imágenes en disco.

## 7) Estado por Fases

### Fase 0 — Estabilización base
- Hecho: separación de estado por monitor.
- Hecho: contrato de ventana activo consistente (`name/app_name/window_title/title/pid/bounds`).

### Fase 1 — Semantic Core
- Hecho: `WorldStateEngine` + readiness + uncertainty.
- Hecho: endpoints `/perception/world` y `/perception/readiness`.

### Fase 2 — Closed-loop Control
- Hecho: `precheck -> execute -> verify -> recover`.
- Hecho: feedback de acciones al trace semántico.

### Fase 3 — MCP Realtime
- Hecho: tools y handlers IPA v2.
- Hecho: stream semántico vía WebSocket.

### Fase 4 — Domain Packs
- Parcial: base lista (affordances + entities).
- Pendiente: packs explícitos por dominio (trading/coding/soporte/backoffice).

### Fase 5 — Performance Hardening
- Parcial: throttling y degradación base ya presentes.
- Pendiente: benchmark automatizado p95 y budget de memoria por carga.

## 8) Próximos Hitos Técnicos
- Añadir benchmark automático (`p50/p95`) para `/perception/world` y `/action/precheck`.
- Afinar readiness por dominio (ej. trading vs coding) con políticas contextuales.
- Añadir detector formal de “event boundaries” por objetivo de tarea.
- Domain packs con affordances + riesgo + verificadores especializados.
- Observabilidad: counters por modo (`SAFE/RAW/HYBRID`) y tasa de bloqueo por readiness.

## 9) Principios No Negociables
- IA externa decide.
- ILUMINATY observa, estructura y ejecuta.
- RAM-only para contexto visual.
- Contrato estable y portable para múltiples proveedores IA.
