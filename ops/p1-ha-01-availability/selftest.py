#!/usr/bin/env python3
"""probe.py·judge.py 로컬 모의 시험. 운영 서버에서 실행하지 않는다.

127.0.0.2~4:18080에 /readyz 모의 서버 3대를 띄우고, 4초 뒤 127.0.0.3을 내린다(drain 모사).
시나리오 두 개를 실제로 돌리고, 그 로그로 판정 네 가지를 확인한다.
  1) normal : 기대 PASS
  2) slow   : 정상 Worker 2대가 1초 동안 1.5초씩 늦게 응답 → 기대 FAIL(B)
  3) normal 로그 + 구간 시작을 5초 앞당긴 timeline(생성기가 늦게 시작한 것과 같음) → 기대 NOT_VERIFIED
  4) normal 로그 + drain 대상을 127.0.0.2로 잘못 지정(정상 Worker에 실패가 있는 것과 같음) → 기대 FAIL(A)
사용: python3 selftest.py   (약 25초, 종료 코드 0 = 네 가지 모두 기대대로)
"""
import http.server
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOSTS = ["127.0.0.2", "127.0.0.3", "127.0.0.4"]
PORT = 18080
state = {"slow": False}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if state["slow"] and self.server.server_address[0] != "127.0.0.3":
            time.sleep(1.5)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ready"}')

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):  # 이름 조회(getfqdn) 지연 회피
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "local", self.server_address[1]


def scenario(mode, workdir):
    log, timeline = os.path.join(workdir, f"{mode}.log"), os.path.join(workdir, f"{mode}.tl")
    servers = {}
    for host in HOSTS:
        srv = Server((host, PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[host] = srv
    probe = subprocess.Popen([sys.executable, os.path.join(HERE, "probe.py"), log, "11", ",".join(HOSTS), str(PORT)],
                             stdout=subprocess.DEVNULL)
    t0 = time.time()
    time.sleep(4)
    servers["127.0.0.3"].shutdown()
    servers["127.0.0.3"].server_close()
    if mode == "slow":
        time.sleep(2)
        state["slow"] = True
        time.sleep(1)
        state["slow"] = False
        time.sleep(3)
    else:
        time.sleep(6)
    probe.wait()
    for host in ("127.0.0.2", "127.0.0.4"):
        servers[host].shutdown()
        servers[host].server_close()
    with open(timeline, "w", encoding="utf-8") as f:
        f.write(f"baseline_start {t0 + 1:.3f}\ndrain_start {t0 + 4:.3f}\nrescheduled_ready {t0 + 5:.3f}\n"
                f"uncordon {t0 + 8:.3f}\nend {t0 + 10:.3f}\n")
    return log, timeline


def judge(log, timeline, drained):
    out = subprocess.run([sys.executable, os.path.join(HERE, "judge.py"), log, timeline, drained],
                         capture_output=True, text=True, encoding="utf-8").stdout
    verdict = [line for line in out.splitlines() if line.startswith("판정:")]
    return (verdict[0].split()[1] if verdict else "ERROR"), out


with tempfile.TemporaryDirectory() as tmp:
    normal_log, normal_tl = scenario("normal", tmp)
    slow_log, slow_tl = scenario("slow", tmp)
    early_tl = os.path.join(tmp, "early.tl")
    with open(normal_tl, encoding="utf-8") as src, open(early_tl, "w", encoding="utf-8") as dst:
        for line in src:
            name, value = line.split()
            dst.write(f"{name} {float(value) - 5:.3f}\n" if name == "baseline_start" else line)
    cases = [
        ("1 normal", judge(normal_log, normal_tl, "127.0.0.3"), "PASS"),
        ("2 slow", judge(slow_log, slow_tl, "127.0.0.3"), "FAIL"),
        ("3 late-start", judge(normal_log, early_tl, "127.0.0.3"), "NOT_VERIFIED"),
        ("4 wrong-drain", judge(normal_log, normal_tl, "127.0.0.2"), "FAIL"),
    ]
    ok = True
    for name, (got, out), want in cases:
        detail = " / ".join(l for l in out.splitlines() if l.startswith(("[A]", "[B]", "[무결성]")))
        print(f"{name:14s} 기대 {want:12s} 결과 {got:12s} {'OK' if got == want else 'MISMATCH'} | {detail}")
        ok &= got == want
    print("SELFTEST", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
