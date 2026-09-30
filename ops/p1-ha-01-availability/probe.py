#!/usr/bin/env python3
"""P1-HA-01(#22) Drain 가용성 측정 생성기 — 결정안 1절 구현.

0.5초 고정 스케줄(tick)마다 Worker 3대에 GET /readyz를 1건씩 동시에 보낸다.
응답을 기다리지 않고 다음 tick을 보낸다(요청마다 스레드, 새 연결).
연결 timeout 1초, 전체 timeout 2초. 넘으면 000.

fail-close:
- 동시 요청(in-flight)을 직접 센다. 다음 tick을 넣으면 상한(기본 24)을 넘는 경우 새 요청을 넣지 않고
  "STOP LOAD_GENERATOR_SATURATED"를 출력한 뒤 종료 코드 2로 끝낸다. 측정이 끊기므로 판정은 NOT_VERIFIED다.
  실행 스레드 수 = 상한이라 executor 대기열에 요청이 쌓이지 않는다.
  정상이면 in-flight는 timeout 2초 ÷ 0.5초 × 3대 ≈ 최대 15건이다.
- 끝날 때 남은 요청을 최대 5초 기다리고 outstanding 수를 출력한다. 0이 아니면 종료 코드 3.

사용: python3 probe.py <출력파일> <실행초> [host1,host2,host3] [port] [max_inflight]
출력 한 줄: tick 예정시각 발사시각 worker 코드 완료시각 소요ms   (시각은 epoch 초)
"""
import concurrent.futures
import http.client
import sys
import threading
import time

OUT, DUR = sys.argv[1], float(sys.argv[2])
WORKERS = sys.argv[3].split(",") if len(sys.argv) > 3 else ["10.1.93.105", "10.1.93.107", "10.1.93.108"]
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 30080
MAX_INFLIGHT = int(sys.argv[5]) if len(sys.argv) > 5 else 24
INTERVAL, CONNECT_TIMEOUT, TOTAL_TIMEOUT, DRAIN_WAIT = 0.5, 1.0, 2.0, 5.0

lock = threading.Lock()
out = open(OUT, "a", buffering=1)


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


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
log("START", f"{start_wall:.3f}", "max_inflight", MAX_INFLIGHT)
pending, saturated, tick = set(), False, 0
pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_INFLIGHT)
while tick * INTERVAL <= DUR:
    wait = start_mono + tick * INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    pending = {f for f in pending if not f.done()}
    if len(pending) + len(WORKERS) > MAX_INFLIGHT:
        saturated = True
        log("STOP", "LOAD_GENERATOR_SATURATED", "tick", tick, "pending", len(pending))
        break
    sched = start_wall + tick * INTERVAL
    for worker in WORKERS:
        pending.add(pool.submit(request, tick, sched, worker))
    tick += 1
_, not_done = concurrent.futures.wait(pending, timeout=DRAIN_WAIT)
outstanding = len(not_done)
pool.shutdown(wait=True)
out.close()
log("END", "ticks", tick, "outstanding", outstanding, "saturated", saturated)
sys.exit(2 if saturated else 3 if outstanding else 0)
