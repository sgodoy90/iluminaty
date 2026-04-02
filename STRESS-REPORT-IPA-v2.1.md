# Stress Report — IPA v2.1 Release Gate

Generated (UTC): 2026-04-02 06:58:05Z

## 1) Concurrent HTTP Mixed Load

- Duration: 45s | Workers: 8 | Total calls: 14443 | Total RPS: 320.86
- Errors: 0 | Error rate: 0.0%

| Endpoint | Count | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) |
|---|---:|---:|---:|---:|---:|
| `GET /perception/world` | 5718 | 22.813 | 29.836 | 35.428 | 23.149 |
| `POST /action/precheck` | 2914 | 24.807 | 32.097 | 36.702 | 25.206 |
| `POST /action/execute` | 2946 | 27.519 | 35.323 | 41.381 | 27.926 |
| `POST /perception/query` | 2865 | 24.775 | 31.918 | 36.822 | 25.078 |

## 2) Stale Context Gate (SAFE)

- Iterations: 500 | Blocked: 500 | Blocked rate: 100.0%
- Reasons: {'context_tick_mismatch': 500}
- Latency: p50=4.118ms | p95=4.891ms | p99=5.35ms

## 3) Recovery Stress (Fail Once -> Recover)

- Iterations target: 700 | Completed: 700 | Raw attempts: 700
- Successes: 700 | Failures: 0 | Skipped(scene_not_stable): 0
- Success rate: 100.0% | Recovered successes: 700 (100.0%)
- Latency: p50=9.126ms | p95=10.639ms | p99=11.302ms

## 4) WebSocket Soak (`/perception/stream`)

- Duration: 45s | Messages: 357 | Msg/s: 7.933
- Malformed: 0 (0.0%) | avg interval: 126.627ms | p95 interval: 127.307ms

## 5) Release Gate Verdict

**PASS** — all gates satisfied.

## Gate Thresholds

- `p95 <= 300ms` for `GET /perception/world` and `POST /action/execute`
- HTTP mixed-load error rate `<= 1.0%`
- SAFE stale-context blocked rate `>= 95%`
- Recovery success rate `>= 98%` and recovered-rate `>= 95%`
- WebSocket malformed messages `= 0` and throughput `>= 2 msg/s`
