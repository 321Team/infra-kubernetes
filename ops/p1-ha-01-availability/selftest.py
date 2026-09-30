#!/usr/bin/env python3
"""probe.py·judge.py 로컬 자체시험. 운영 서버에서 실행하지 않는다. (약 20초, 종료 코드 0 = 전부 기대대로)

1부 probe.py 실제 실행 — 127.0.0.2~4:18080 모의 /readyz 서버
  P1 정상: 4초 뒤 127.0.0.3을 내림 → 종료 코드 0, outstanding 0, 모든 tick 3건, 발사 지연 < 0.2초
  P2 포화: 3대 모두 1.5초 지연 + max_inflight 4 → 종료 코드 2, STOP LOAD_GENERATOR_SATURATED,
     STOP한 tick부터 요청 0건(부하가 다음 구간으로 넘치지 않음), 그 로그의 판정은 NOT_VERIFIED
2부 judge.py — 결정안 길이(각 관찰 구간 61초)의 합성 로그
  J1 정상 → PASS / J2 정상 Worker 2대 1.5초 지연 → FAIL(B) / J3 정상 Worker 000 1건 → FAIL(A)
  J4 생성기 늦은 시작 / J5 발사 지연 0.3초 / J6 tick 누락 / J7 기준선 비200 → NOT_VERIFIED
  J8 기준선 59초 / J9 재배치 Ready 뒤 59초 / J10 uncordon 뒤 59초 / J11 순서 역전 → NOT_VERIFIED
  J12 판정 구간 끝 뒤에 완료된 성공은 B에서 제외 → PASS
"""
import http.server
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK, PORT = ["127.0.0.2", "127.0.0.3", "127.0.0.4"], 18080
HOSTS, DRAINED = ["10.1.93.105", "10.1.93.107", "10.1.93.108"], "10.1.93.107"
EXIT = {"PASS": 0, "FAIL": 1, "NOT_VERIFIED": 2}
state = {"mode": "normal"}
results = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if state["mode"] == "slow_all":
            time.sleep(1.5)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ready"}')

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # 이름 조회(getfqdn) 지연 회피
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "local", self.server_address[1]


def check(name, ok, detail):
    results.append(ok)
    print(f"{name:34s} {'OK' if ok else 'MISMATCH'} | {detail}")


def judge(log, timeline, drained):
    p = subprocess.run([sys.executable, os.path.join(HERE, "judge.py"), log, timeline, drained],
                       capture_output=True, text=True, encoding="utf-8")
    verdict = next((l.split()[1] for l in p.stdout.splitlines() if l.startswith("판정:")), "ERROR")
    return verdict, p.returncode, next((l for l in p.stdout.splitlines() if l.startswith("판정:")), "")


def write_timeline(path, tl):
    with open(path, "w", encoding="utf-8") as f:
        for key, value in tl.items():
            f.write(f"{key} {value:.3f}\n")


def probe_run(mode, workdir, max_inflight=None):
    state["mode"] = mode
    servers = {}
    for host in MOCK:
        srv = Server((host, PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[host] = srv
    log = os.path.join(workdir, f"probe-{mode}.log")
    cmd = [sys.executable, os.path.join(HERE, "probe.py"), log, "11", ",".join(MOCK), str(PORT)]
    proc = subprocess.Popen(cmd + ([str(max_inflight)] if max_inflight else []),
                            stdout=subprocess.PIPE, text=True, encoding="utf-8")
    if mode == "normal":
        time.sleep(4)
        gone = servers.pop("127.0.0.3")
        gone.shutdown()
        gone.server_close()  # 소켓까지 닫아야 Local NodePort처럼 즉시 연결 거부
    stdout, _ = proc.communicate(timeout=60)
    for srv in servers.values():
        srv.shutdown()
        srv.server_close()
    rows = [line.split() for line in open(log, encoding="utf-8") if line.strip()]
    return proc.returncode, stdout, log, rows


def timeline(base=1_000_000.0, baseline=61.0, ready_after=13.0, after_ready=61.0, after_uncordon=61.0):
    b0 = base + 5
    d0 = b0 + baseline
    r0 = d0 + ready_after
    u0 = r0 + after_ready
    return {"baseline_start": b0, "drain_start": d0, "rescheduled_ready": r0, "uncordon": u0, "end": u0 + after_uncordon}


def synth(tl, start=None):
    """0.5초 tick, 정상 Worker는 10ms에 200, drain 대상은 drain 뒤 000인 합성 probe 로그 행"""
    start = tl["baseline_start"] - 5 if start is None else start
    rows, k = [], 0
    while start + k * 0.5 <= tl["end"] + 5:
        sched = start + k * 0.5
        for host in HOSTS:
            drained = host == DRAINED and sched >= tl["drain_start"]
            rows.append([k, sched, sched + 0.001, host, "000" if drained else "200", sched + 0.001 + (0.001 if drained else 0.010)])
        k += 1
    return rows


def judge_synth(name, tl, rows, want, workdir):
    log, tlf = os.path.join(workdir, f"{name}.log"), os.path.join(workdir, f"{name}.tl")
    with open(log, "w", encoding="utf-8") as f:
        for k, sched, sent, host, code, done in rows:
            f.write(f"{k} {sched:.3f} {sent:.3f} {host} {code} {done:.3f} {(done - sent) * 1000:.1f}\n")
    write_timeline(tlf, tl)
    verdict, rc, line = judge(log, tlf, DRAINED)
    check(name, verdict == want and rc == EXIT[want], f"기대 {want} → {line} (rc={rc})")


with tempfile.TemporaryDirectory() as tmp:
    # 1부 — probe.py 실제 실행
    rc, out, log, rows = probe_run("normal", tmp)
    per_tick = Counter(r[0] for r in rows)
    max_late = max(float(r[2]) - float(r[1]) for r in rows)
    check("P1 probe 정상", rc == 0 and "outstanding 0" in out and "saturated False" in out
          and set(per_tick.values()) == {3} and max_late < 0.2,
          f"rc={rc}, tick {len(per_tick)}개 모두 3건={set(per_tick.values()) == {3}}, 최대 발사 지연 {max_late:.3f}s")
    rc, out, log, rows = probe_run("slow_all", tmp, max_inflight=4)
    stop = re.search(r"LOAD_GENERATOR_SATURATED tick (\d+)", out)
    after = [r for r in rows if stop and int(r[0]) >= int(stop.group(1))]
    check("P2 probe 포화 fail-close", rc == 2 and stop is not None and not after,
          f"rc={rc}, STOP tick={stop.group(1) if stop else None}, STOP 이후 요청 {len(after)}건")
    tl = timeline(base=float(rows[0][1]) + 0.1) if rows else timeline()
    tlf = os.path.join(tmp, "p2.tl")
    write_timeline(tlf, tl)
    verdict, jrc, line = judge(log, tlf, "127.0.0.3")
    check("P2 포화 로그 판정", verdict == "NOT_VERIFIED" and jrc == 2, line)

    # 2부 — judge.py 합성 로그
    tl = timeline()
    judge_synth("J1 정상", tl, synth(tl), "PASS", tmp)

    rows = synth(tl)
    t0 = tl["drain_start"] + 30
    for r in rows:
        if r[3] != DRAINED and t0 <= r[1] <= t0 + 0.5:
            r[5] = r[2] + 1.5
    judge_synth("J2 정상 Worker 1.5초 지연(B)", tl, rows, "FAIL", tmp)

    rows = synth(tl)
    hit = next(r for r in rows if r[3] == "10.1.93.105" and r[1] >= tl["drain_start"] + 20)
    hit[4], hit[5] = "000", hit[2] + 1.0
    judge_synth("J3 정상 Worker 000 1건(A)", tl, rows, "FAIL", tmp)

    judge_synth("J4 생성기 늦은 시작", tl, synth(tl, start=tl["baseline_start"] + 2), "NOT_VERIFIED", tmp)

    rows = synth(tl)
    hit = next(r for r in rows if r[1] >= tl["drain_start"] + 10)
    hit[2], hit[5] = hit[1] + 0.3, hit[1] + 0.31
    judge_synth("J5 발사 지연 0.3초", tl, rows, "NOT_VERIFIED", tmp)

    rows = synth(tl)
    gone = next(r[0] for r in rows if r[1] >= tl["drain_start"] + 40)
    judge_synth("J6 tick 누락", tl, [r for r in rows if r[0] != gone], "NOT_VERIFIED", tmp)

    rows = synth(tl)
    hit = next(r for r in rows if r[3] == DRAINED and r[1] >= tl["baseline_start"] + 10)
    hit[4] = "000"
    judge_synth("J7 기준선 비200", tl, rows, "NOT_VERIFIED", tmp)

    for name, kw in (("J8 기준선 59초", {"baseline": 59.0}), ("J9 재배치 Ready 뒤 59초", {"after_ready": 59.0}),
                     ("J10 uncordon 뒤 59초", {"after_uncordon": 59.0})):
        short = timeline(**kw)
        judge_synth(name, short, synth(short), "NOT_VERIFIED", tmp)

    bad = timeline()
    bad["rescheduled_ready"], bad["uncordon"] = bad["uncordon"], bad["rescheduled_ready"]
    judge_synth("J11 순서 역전", bad, synth(timeline()), "NOT_VERIFIED", tmp)

    rows = synth(tl)
    last = max(r[1] for r in rows if r[1] <= tl["end"])
    for r in rows:
        if r[1] == last and r[3] != DRAINED:
            r[5] = r[2] + 1.2
    judge_synth("J12 구간 끝 뒤 완료 성공 제외(B)", tl, rows, "PASS", tmp)

print("SELFTEST", "PASS" if all(results) else "FAIL", f"({sum(results)}/{len(results)})")
sys.exit(0 if all(results) else 1)
