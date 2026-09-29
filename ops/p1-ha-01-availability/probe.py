#!/usr/bin/env python3
"""P1-HA-01(#22) Drain 가용성 측정 생성기 — 결정안 1절 구현.

0.5초 고정 스케줄(tick)마다 Worker 3대에 GET /readyz를 1건씩 동시에 보낸다.
응답을 기다리지 않고 다음 tick을 보낸다(요청마다 스레드, 새 연결).
연결 timeout 1초, 전체 timeout 2초. 넘으면 000.

사용: python3 probe.py <출력파일> <실행초> [host1,host2,host3] [port]
출력 한 줄: tick 예정시각 발사시각 worker 코드 완료시각 소요ms   (시각은 epoch 초)
"""
import http.client
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

OUT, DUR = sys.argv[1], float(sys.argv[2])
WORKERS = sys.argv[3].split(",") if len(sys.argv) > 3 else ["10.1.93.105", "10.1.93.107", "10.1.93.108"]
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 30080
INTERVAL, CONNECT_TIMEOUT, TOTAL_TIMEOUT = 0.5, 1.0, 2.0

lock = threading.Lock()
out = open(OUT, "a", buffering=1)


def request(tick, sched, worker):
    sent = time.time()
    code = "000"
    try:
        conn = http.client.HTTPConnection(worker, PORT, timeout=CONNECT_TIMEOUT)
        conn.connect()
        conn.sock.settimeout(max(0.01, TOTAL_TIMEOUT - (time.time() - sent)))
        conn.request("GET", "/readyz")
        resp = conn.getresponse()
        resp.read()
        code = str(resp.status)
        conn.close()
    except Exception:
        code = "000"
    done = time.time()
    if done - sent > TOTAL_TIMEOUT:
        code = "000"
    with lock:
        out.write(f"{tick} {sched:.3f} {sent:.3f} {worker} {code} {done:.3f} {(done - sent) * 1000:.1f}\n")


start_mono, start_wall = time.monotonic(), time.time()
print(time.strftime("%H:%M:%S"), "START", f"{start_wall:.3f}", flush=True)
with ThreadPoolExecutor(max_workers=48) as pool:
    tick = 0
    while tick * INTERVAL <= DUR:
        wait = start_mono + tick * INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        sched = start_wall + tick * INTERVAL
        for worker in WORKERS:
            pool.submit(request, tick, sched, worker)
        tick += 1
out.close()
print(time.strftime("%H:%M:%S"), "END ticks", tick, flush=True)
