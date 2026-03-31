# Stress Report — IPA v2.1 Release Gate

Generated (UTC): 2026-03-31 08:59:34Z

## 1) Concurrent HTTP Mixed Load

- Duration: 90s | Workers: 8 | Total calls: 14748 | Total RPS: 160.62
- Errors: 91 | Error rate: 0.617%

| Endpoint | Count | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) |
|---|---:|---:|---:|---:|---:|
| `GET /perception/world` | 5983 | 31.516 | 66.925 | 92.547 | 35.118 |
| `POST /action/precheck` | 2949 | 33.946 | 70.151 | 93.424 | 37.405 |
| `POST /action/execute` | 2883 | 36.571 | 73.088 | 100.136 | 39.811 |
| `POST /perception/query` | 2933 | 33.553 | 68.333 | 94.422 | 36.856 |

## 2) Stale Context Gate (SAFE)

- Iterations: 600 | Blocked: 600 | Blocked rate: 100.0%
- Reasons: {'context_tick_mismatch': 600}
- Latency: p50=14.959ms | p95=33.894ms | p99=54.927ms

## 3) Recovery Stress (Fail Once -> Recover)

- Iterations target: 800 | Completed: 800 | Raw attempts: 800
- Successes: 800 | Failures: 0 | Skipped(scene_not_stable): 0
- Success rate: 100.0% | Recovered successes: 800 (100.0%)
- Latency: p50=13.558ms | p95=35.782ms | p99=56.949ms

## 4) WebSocket Soak (`/perception/stream`)

- Duration: 45s | Messages: 357 | Msg/s: 7.933
- Malformed: 0 (0.0%) | avg interval: 126.571ms | p95 interval: 134.646ms

## 5) Release Gate Verdict

**PASS** — all gates satisfied.

## Gate Thresholds

- `p95 <= 300ms` for `GET /perception/world` and `POST /action/execute`
- HTTP mixed-load error rate `<= 1.0%`
- SAFE stale-context blocked rate `>= 95%`
- Recovery success rate `>= 98%` and recovered-rate `>= 95%`
- WebSocket malformed messages `= 0` and throughput `>= 2 msg/s`
