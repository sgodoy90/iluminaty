# Auditoria Exhaustiva - 2026-04-17

## Alcance

- Runtime core (`iluminaty/server.py`, `iluminaty/perception.py`, `iluminaty/watch_engine.py`, `iluminaty/ocr_worker.py`, `iluminaty/recording.py`).
- Configuracion trading (`iluminaty/trading/config.py`).
- CLI/migracion y runners de calidad (`tests/benchmark_ipa_v21.py`, `tests/stress_ipa_v21_release_gate.py`).
- Seguridad/auth, multi-monitor, precheck SAFE/HYBRID, estabilidad de WebSocket.

## Evidencia de validacion

- `py -m pytest -q` -> PASS (suite completa).
- `py -m pytest -q tests/test_server_precheck.py tests/test_server_stability.py tests/test_watch_memory_integration.py tests/test_perception_deep_loop_focus.py` -> PASS (con skips esperados).
- `py -m pytest -q tests/test_server_monitor_strictness.py tests/test_capture_callback_monitor_consistency.py tests/test_mcp_server_auth.py tests/test_server_workers_endpoints.py tests/test_server_workers_scheduler_endpoints.py tests/test_action_watchers.py` -> PASS.
- Compilacion (`py_compile`) runtime + tests -> PASS.
- Benchmark IPA v2.1 (autenticado) -> PASS (`p95 <= 300ms` en todos los casos del runner).
- Stress gate IPA v2.1 (autenticado, 20s/3 workers) -> PASS.

## Hallazgos y correcciones aplicadas

### P1-001 - Precheck SAFE permitia bypass por coordenadas extremas

- Problema: `_extract_target_xy()` descartaba coords fuera de rango artificial y `_target_check()` terminaba en `no_coordinates` (allowed), evitando bloqueo por `target_out_of_bounds`.
- Correccion: `_extract_target_xy()` ahora conserva coordenadas parseables y delega validacion real al layout de monitores.
- Archivo: `iluminaty/server.py` (`def _extract_target_xy`, comentarios de validacion de bounds).
- Estado: **CERRADO**.

### P1-002 - Deep loop visual inactivo por default

- Problema: `ILUMINATY_VLM_MODE` default en `on_demand`, lo que evitaba enqueue continuo de tareas visuales.
- Correccion: default actualizado a `continuous`; `on_demand` sigue disponible por override explicito.
- Archivo: `iluminaty/perception.py` (init de `_vlm_mode`).
- Estado: **CERRADO**.

### P1-003 - Trading config no deterministica por autoload implícito de `.env`

- Problema: `from_env()` cargaba `.env` siempre; tests y ejecuciones "limpias" quedaban contaminadas por secretos locales.
- Correccion: autoload de `.env` ahora es **opt-in** via `TRADING_AUTO_DOTENV=1`.
- Archivo: `iluminaty/trading/config.py` (`_auto_dotenv_enabled`, `from_env`).
- Estado: **CERRADO**.

### P2-004 - Warning de sintaxis por escape en dashboard embebido

- Problema: `SyntaxWarning` por secuencia de escape en regex JS embebido.
- Correccion: escape corregido.
- Archivo: `iluminaty/dashboard.py`.
- Estado: **CERRADO**.

### P1-005 - Runners de benchmark/stress rotos por auth

- Problema: scripts de benchmark/stress no enviaban `x-api-key` ni token WS y fallaban con 401/WS disconnect.
- Correccion:
  - se centralizo `_API_KEY`,
  - `TestClient` ahora usa headers auth,
  - WS usa `token`.
- Archivos: `tests/benchmark_ipa_v21.py`, `tests/stress_ipa_v21_release_gate.py`.
- Estado: **CERRADO**.

### P2-006 - Distorsion de latencia en stress runner por timeout duro por request

- Problema: `run_concurrent_http_load` creaba un thread por request (`_run_with_timeout`), inflando p95/p99 del propio harness.
- Correccion: timeout duro queda **opt-in** via `ILUMINATY_STRESS_HARD_TIMEOUT=1`; default ejecuta directo para medir latencia real.
- Archivo: `tests/stress_ipa_v21_release_gate.py`.
- Estado: **CERRADO**.

## Riesgos pendientes (no bloqueantes en esta pasada)

- Fallback global de frame cuando no se especifica `monitor_id` puede mezclar contexto entre monitores en llamadas no orientadas.
- Recomendacion: en consumidores de alto riesgo usar siempre `monitor_id` explicito y/o fase de orientacion previa (`spatial state`/`map`).

## Resultado global de la pasada

- Bugs criticos confirmados en esta fase: **5**
- Bugs corregidos: **5**
- Riesgos no bloqueantes abiertos: **1**
- Estado final de release gate local: **VERDE**
