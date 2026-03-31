# Benchmark Report — IPA v2.1

Fecha: 2026-03-31 (America/Costa_Rica)  
Runner: `tests/benchmark_ipa_v21.py`  
Comando:

```bash
py tests/benchmark_ipa_v21.py --iterations 600 --warmup 60 --workers 6
```

## Objetivo

- Validar latencia de endpoints críticos de percepción y control.
- Criterio de aceptación: `p95 <= 300ms`.

## Resultados

| Case | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) | RPS | Target |
|---|---:|---:|---:|---:|---:|---|
| `GET /perception/world (single)` | 3.261 | 4.497 | 5.492 | 3.318 | 301.31 | PASS |
| `POST /action/precheck (single)` | 8.329 | 17.305 | 28.714 | 9.320 | 107.28 | PASS |
| `POST /action/execute (single)` | 8.868 | 20.931 | 38.739 | 10.459 | 95.60 | PASS |
| `POST /perception/query (single)` | 3.687 | 4.909 | 5.801 | 3.931 | 254.34 | PASS |
| `GET /perception/world (concurrent)` | 15.983 | 22.112 | 28.209 | 16.336 | 329.75 | PASS |
| `POST /action/execute (concurrent)` | 21.311 | 29.933 | 33.606 | 21.986 | 247.57 | PASS |

**Resumen:** `0/6` casos fallaron el objetivo `p95 <= 300ms`.

## Cobertura del benchmark

- Perfil single-thread y concurrente.
- Endpoints semánticos (`/perception/world`, `/perception/query`) y de control (`/action/precheck`, `/action/execute`).
- Incluye validación de flujo con checks de contexto/frescura.

## Notas

- Este benchmark usa stubs de alta fidelidad en proceso (FastAPI `TestClient`) para medir overhead real del stack API/control de ILUMINATY.
- Complementar con benchmark de entorno real (multi-monitor + carga de UI/video prolongada) antes de cerrar hardening de performance en producción.
