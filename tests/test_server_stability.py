"""
Stress test T03 — S01 M002
Verifica estabilidad del servidor post-fix durante 10 minutos.
Corre requests continuos a endpoints de OCR y health, detecta crashes.
Usa http.client con keep-alive para medir latencia real (no overhead urllib).
"""
import time
import json
import threading
import http.client
import sys

BASE_HOST = "127.0.0.1"
BASE_PORT = 8420
KEY  = "ILUM-dev-local"
DURATION = 600   # 10 min
QUICK    = "--quick" in sys.argv
if QUICK:
    DURATION = 60

def get(path, timeout=5):
    conn = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=timeout)
    conn.request("GET", path, headers={"X-API-Key": KEY})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data

def post(path, timeout=10):
    conn = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=timeout)
    conn.request("POST", path, body=b"", headers={"X-API-Key": KEY, "Content-Length": "0"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data

stats = {
    "requests": 0, "errors": 0, "crashes": 0,
    "max_latency_ms": 0, "health_failures": 0,
}
lock = threading.Lock()
stop_event = threading.Event()

def record(latency_ms, error=False):
    with lock:
        stats["requests"] += 1
        if error: stats["errors"] += 1
        if latency_ms > stats["max_latency_ms"]:
            stats["max_latency_ms"] = latency_ms

def health_poller():
    consecutive_failures = 0
    while not stop_event.is_set():
        t0 = time.perf_counter()
        try:
            h = get("/health")
            ms = (time.perf_counter() - t0) * 1000
            record(ms)
            assert h.get("status") == "alive"
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            record(0, error=True)
            with lock:
                stats["health_failures"] += 1
            if consecutive_failures >= 3:
                with lock: stats["crashes"] += 1
                print(f"\n[CRASH] health failed {consecutive_failures}x: {e}", flush=True)
        time.sleep(1.0)

def ocr_load():
    while not stop_event.is_set():
        t0 = time.perf_counter()
        try:
            get("/ipa/status")
            record((time.perf_counter() - t0) * 1000)
        except Exception:
            record(0, error=True)
        time.sleep(2.0)

def spatial_load():
    while not stop_event.is_set():
        t0 = time.perf_counter()
        try:
            get("/ipa/context")
            record((time.perf_counter() - t0) * 1000)
        except Exception:
            record(0, error=True)
        time.sleep(3.0)

def concurrent_health():
    """Burst of concurrent /health requests — triggers GIL contention if any."""
    while not stop_event.is_set():
        burst = []
        def _one():
            t0 = time.perf_counter()
            try:
                get("/health")
                record((time.perf_counter()-t0)*1000)
            except:
                record(0, error=True)
        ts = [threading.Thread(target=_one) for _ in range(4)]
        for t in ts: t.start()
        for t in ts: t.join()
        time.sleep(5.0)

if __name__ == "__main__":
    try:
        h = get("/health")
        assert h.get("status") == "alive"
        print(f"Server alive — {DURATION}s stress test starting", flush=True)
    except Exception as e:
        print(f"FAIL: server not responding: {e}")
        sys.exit(1)

    threads = [
        threading.Thread(target=health_poller,    daemon=True),
        threading.Thread(target=ocr_load,          daemon=True),
        threading.Thread(target=spatial_load,      daemon=True),
        threading.Thread(target=concurrent_health, daemon=True),
    ]
    for t in threads: t.start()

    start = time.time()
    last_print = start

    try:
        while time.time() - start < DURATION:
            time.sleep(1)
            now = time.time()
            if now - last_print >= 10:
                elapsed = now - start
                with lock:
                    r, e, c = stats["requests"], stats["errors"], stats["crashes"]
                    ml, hf  = stats["max_latency_ms"], stats["health_failures"]
                print(
                    f"[{elapsed:5.0f}s] reqs={r} errors={e}({e/max(r,1)*100:.1f}%) "
                    f"crashes={c} max_lat={ml:.0f}ms",
                    flush=True
                )
                if c > 0:
                    print("CRASH — aborting", flush=True); break
                last_print = now
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        stop_event.set()

    elapsed = time.time() - start
    with lock:
        r, e, c, ml = stats["requests"], stats["errors"], stats["crashes"], stats["max_latency_ms"]

    print(f"\n{'='*60}", flush=True)
    print(f"STRESS TEST — {elapsed:.0f}s", flush=True)
    print(f"  Requests:    {r}", flush=True)
    print(f"  Errors:      {e} ({e/max(r,1)*100:.1f}%)", flush=True)
    print(f"  Crashes:     {c}", flush=True)
    print(f"  Max latency: {ml:.0f}ms", flush=True)
    print(f"{'='*60}", flush=True)

    if c == 0 and (e / max(r, 1)) < 0.05:
        print("PASS — server stable", flush=True); sys.exit(0)
    else:
        print("FAIL", flush=True); sys.exit(1)

