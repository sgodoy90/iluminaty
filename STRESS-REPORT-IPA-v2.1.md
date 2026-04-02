# Stress Report — IPA v2.1 Release Gate

Generated (UTC): 2026-04-02 13:50:41Z

## 1) Concurrent HTTP Mixed Load

- Duration: 20s | Workers: 6 | Total calls: 5320 | Total RPS: 265.81
- Errors: 0 | Error rate: 0.0%

| Endpoint | Count | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) |
|---|---:|---:|---:|---:|---:|
| `GET /perception/world` | 2149 | 20.126 | 27.714 | 37.48 | 20.868 |
| `POST /action/precheck` | 1085 | 22.02 | 30.224 | 49.54 | 22.97 |
| `POST /action/execute` | 1055 | 24.762 | 32.885 | 40.222 | 25.538 |
| `POST /perception/query` | 1031 | 21.862 | 29.807 | 40.452 | 22.599 |

## 2) Stale Context Gate (SAFE)

- Iterations: 500 | Blocked: 500 | Blocked rate: 100.0%
- Reasons: {'context_tick_mismatch': 500}
- Latency: p50=3.781ms | p95=4.85ms | p99=5.505ms

## 3) Recovery Stress (Fail Once -> Recover)

- Iterations target: 700 | Completed: 700 | Raw attempts: 700
- Successes: 700 | Failures: 0 | Skipped(scene_not_stable): 0
- Success rate: 100.0% | Recovered successes: 700 (100.0%)
- Latency: p50=9.051ms | p95=11.807ms | p99=14.483ms

## 4) WebSocket Soak (`/perception/stream`)

- Duration: 45s | Messages: 357 | Msg/s: 7.933
- Malformed: 0 (0.0%) | avg interval: 126.454ms | p95 interval: 127.795ms

## 5) Release Gate Verdict

**PASS** — all gates satisfied.

## Gate Thresholds

- `p95 <= 300ms` for `GET /perception/world` and `POST /action/execute`
- HTTP mixed-load error rate `<= 1.0%`
- SAFE stale-context blocked rate `>= 95%`
- Recovery success rate `>= 98%` and recovered-rate `>= 95%`
- WebSocket malformed messages `= 0` and throughput `>= 2 msg/s`
