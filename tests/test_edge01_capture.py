"""Edge Cutover(#19) 요청줄 캡처(cutover-capture.sh)와 판정(cutover-capture-judge.py) 시험."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "ops/edge01-cutover/cutover-capture.sh"
JUDGE = ROOT / "ops/edge01-cutover/cutover-capture-judge.py"
RUN = "20261008T1600"
T0 = 1791450000.0

POD_IP = {"k8s-worker01": "10.244.1.11", "k8s-worker02": "10.244.2.12", "k8s-worker03": "10.244.3.13"}
EK3S_IP = "10.42.0.9"


def cap_text():
    return CAP.read_text(encoding="utf-8")


def report_py():
    lines = cap_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.lstrip().startswith('python3 -I - "$DIR" "$H" "$RUN" "$WIN"'))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "PY")
    return "\n".join(lines[start + 1:end])


# ---- tcpdump -A 해석(report) ----

def packet(t, sip, sport, dip, seq, reqline, xri=True, iface="cali1234 In  "):
    body = ".....E..x@.@.....%s\nHost: 10.1.93.50\n" % reqline
    if xri:
        body += "X-Real-IP: 10.1.93.50\nX-Forwarded-For: 10.1.93.50\n"
    body += "Connection: close\n\n"
    return "%.6f %sIP %s.%s > %s.8000: Flags [P.], seq %d:%d, ack 1, win 502, length 120\n%s" % (
        t, iface, sip, sport, dip, seq, seq + 120, body)


def run_report(tmp_path, decoded, rc="124", err=None, decoded_rc="0", pcap=True):
    d = tmp_path / "cap"
    d.mkdir()
    (d / "decoded.txt").write_text(decoded, encoding="utf-8")
    (d / "decoded.rc").write_text(decoded_rc + "\n", encoding="utf-8")
    (d / "tcpdump.rc").write_text(rc + "\n", encoding="utf-8")
    (d / "tcpdump.err").write_text(err if err is not None else
                                   "listening on any\n5 packets captured\n5 packets received by filter\n0 packets dropped by kernel\n",
                                   encoding="utf-8")
    (d / "ready.ts").write_text("%.3f\n" % T0, encoding="utf-8")
    (d / "stop.ts").write_text("%.3f\n" % (T0 + 100), encoding="utf-8")
    if pcap:
        (d / "capture.pcap").write_bytes(b"pcap")
    src = tmp_path / "report.py"
    src.write_text(report_py(), encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", "-I", str(src), str(d), "k8s-worker01", RUN, "apply"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()


def req(tag, i, method="GET", path="/readyz", run=RUN):
    return "%s %s?cutover=cutover-%s-%s-%s HTTP/1.0" % (method, path, run, tag, i)


def test_report_extracts_hits_and_dedupes(tmp_path):
    ip = POD_IP["k8s-worker01"]
    dec = (packet(T0 + 1, "10.1.93.50", 40001, ip, 1000, req("apply", 1))
           + packet(T0 + 1, "10.1.93.50", 40001, ip, 1000, req("apply", 1), iface="cni0 Out ")   # 같은 패킷 다른 인터페이스
           + packet(T0 + 1.2, "10.1.93.50", 40001, ip, 1000, req("apply", 1))                      # 재전송
           + packet(T0 + 2, "10.1.93.50", 40002, ip, 5000, req("apply", "smoke", "POST", "/api/v1/transactions"))
           + packet(T0 + 3, "10.1.93.50", 40003, ip, 7000, req("apply", 2, run="20261008T0327"))   # 다른 RUN
           + packet(T0 + 4, "10.1.93.50", 40004, ip, 9000, "GET /readyz HTTP/1.1"))                # 표식 없는 probe
    out = run_report(tmp_path, dec)
    hits = [l for l in out if l.startswith("HIT ")]
    assert len(hits) == 2
    assert "id=1 " in hits[0] and "src=10.1.93.50:40001 dst=%s:8000" % ip in hits[0]
    assert "method=GET proto=HTTP/1.0 hdr_host=10.1.93.50 xri=10.1.93.50" in hits[0]
    assert "id=smoke " in hits[1] and "method=POST" in hits[1]
    assert out[-1].startswith("CAPTURE host=k8s-worker01 run=%s win=apply err=0 reason=- " % RUN)
    assert "hits=2 " in out[-1] and "pcap_sha256=" in out[-1]


def test_report_without_xri_marks_dash(tmp_path):
    out = run_report(tmp_path, packet(T0 + 1, "10.1.93.50", 40001, "10.244.1.11", 1, req("check1", "w105"), xri=False,
                                      iface=""))
    assert "xri=- " in out[0] + " "


@pytest.mark.parametrize("kw,reason", [
    (dict(rc="1"), "tcpdump_rc=1"),
    (dict(err="listening on any\n"), "no_stats"),
    (dict(err="3 packets captured\n3 packets received by filter\n2 packets dropped by kernel\n"), "dropped=2"),
    (dict(decoded_rc="1"), "decode_rc=1"),
    (dict(pcap=False), "no_pcap"),
])
def test_report_capture_errors_are_err(tmp_path, kw, reason):
    out = run_report(tmp_path, "", **kw)
    assert " err=1 " in out[-1] and reason in out[-1]


def test_capture_script_scope_is_fixed():
    text = cap_text()
    assert "tcp dst port 8000 and src host 10.1.93.50" in text
    assert "tcp and dst net 10.42.0.0/16" in text
    assert "SNAPLEN=300" in text and '-s "$3"' in text
    assert "-Z root" in text and "-U" in text
    assert "이미 있음" in text                       # 같은 RUN·WIN 재사용 금지
    for forbidden in ("set -e\n", "-s 0", "-s0", "iptables", "nft ", "systemctl", "kubectl"):
        assert forbidden not in text


# ---- 판정 ----

def sent_lines(tag, ids, t=T0 + 10, http=None):
    out = []
    for n, i in enumerate(ids):
        code = http or ("201" if i == "smoke" else "200")
        out.append("SENT run=%s tag=%s id=%s http=%s t=%.3f" % (RUN, tag, i, code, t + n))
    return "\n".join(out) + "\n"


def hit(host, tag, i, dst, sport, xri="10.1.93.50", src="10.1.93.50", win="apply", run=RUN):
    return ("HIT host=%s run=%s win=%s tag=%s id=%s t=%.6f src=%s:%d dst=%s:8000 seq=%d method=GET proto=HTTP/1.0 "
            "hdr_host=10.1.93.50 xri=%s" % (host, run, win, tag, i, T0 + 20, src, sport, dst, sport * 10, xri))


def capture(host, win="apply", err=0, ready=T0, stop=T0 + 100):
    return "CAPTURE host=%s run=%s win=%s err=%d reason=- ready=%.3f stop=%.3f pkts=10 dropped=0 blocks=10 hits=3 pcap_sha256=ab" % (
        host, RUN, win, err, ready, stop)


def pods():
    out = ["POD cluster=k8s name=fds-app/ta-%d uid=uid-%d ip=%s node=%s" % (n, n, ip, h)
           for n, (h, ip) in enumerate(sorted(POD_IP.items()))]
    out.append("POD cluster=ek3s name=fds/transaction-api-x uid=uid-e ip=%s node=edge01" % EK3S_IP)
    return "\n".join(out) + "\n"


APPLY = [str(i) for i in range(1, 10)] + ["smoke"]
HOSTS = sorted(POD_IP)


def apply_files(ids=APPLY, edge_ids=(), xri="10.1.93.50", worker_caps=HOSTS, ready=T0, skip_pods=False):
    lines = [sent_lines("apply", APPLY), capture("edge01", ready=ready)]
    for h in worker_caps:
        lines.append(capture(h, ready=ready))
    for n, i in enumerate(ids):
        h = HOSTS[n % 3]
        lines.append(hit(h, "apply", i, POD_IP[h], 41000 + n, xri=xri))
    for n, i in enumerate(edge_ids):
        lines.append(hit("edge01", "apply", i, EK3S_IP, 42000 + n))
    if not skip_pods:
        lines.append(pods())
    return "\n".join(lines) + "\n"


def run_judge(tmp_path, args, *texts):
    paths = []
    for n, t in enumerate(texts):
        p = tmp_path / ("in%d.txt" % n)
        p.write_text(t, encoding="utf-8")
        paths.append(str(p))
    r = subprocess.run([sys.executable, "-X", "utf8", "-I", str(JUDGE)] + args + paths,
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout


def test_judge_apply_path_ok(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files())
    assert rc == 0, out
    assert "verdict=PATH_OK" in out
    assert out.count("   BIND ") == 10


@pytest.mark.parametrize("ids", [APPLY[1:], APPLY + ["3"]])         # 누락 / 중복(다른 연결)
def test_judge_apply_missing_or_duplicate_is_unconfirmed(tmp_path, ids):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(ids=ids))
    assert rc == 4 and "verdict=UNCONFIRMED" in out


def test_judge_apply_old_path_hit_fails(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(edge_ids=["4"]))
    assert rc == 3 and "verdict=OLD_PATH" in out and "edge=OLD_PATH" in out


def test_judge_apply_requires_nginx_signature(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(xri="-"))
    assert rc == 4 and "X-Real-IP" in out


def test_judge_apply_requires_all_worker_captures(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(worker_caps=HOSTS[:2]))
    assert rc == 4 and "k8s-worker03 캡처 없음" in out


def test_judge_apply_capture_must_be_ready_before_first_request(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(ready=T0 + 15))
    assert rc == 4 and "늦음" in out


def test_judge_apply_requires_pod_binding(tmp_path):
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], apply_files(skip_pods=True))
    assert rc == 4 and "Pod 없음" in out


def test_judge_apply_pod_on_other_node_is_unconfirmed(tmp_path):
    text = apply_files().replace("ip=%s node=k8s-worker01" % POD_IP["k8s-worker01"],
                                 "ip=%s node=k8s-worker02" % POD_IP["k8s-worker01"])
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], text)
    assert rc == 4 and "노드 Pod" in out


def test_judge_apply_other_tag_in_window_is_unconfirmed(tmp_path):
    text = apply_files() + hit("k8s-worker01", "check1", "w105", POD_IP["k8s-worker01"], 43000, xri="-") + "\n"
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], text)
    assert rc == 4 and "다른 표식" in out


def test_judge_apply_failed_request_is_unconfirmed(tmp_path):
    text = apply_files().replace("id=5 http=200", "id=5 http=502")
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], text)
    assert rc == 4


def test_judge_reads_ansible_json_output(tmp_path):
    """Worker 보고가 ansible 출력(stdout 문자열·stdout_lines) 안에 있어도 같은 판정."""
    plain = apply_files()
    worker = [l for l in plain.splitlines() if l.startswith(("HIT host=k8s", "CAPTURE host=k8s"))]
    rest = [l for l in plain.splitlines() if not l.startswith(("HIT host=k8s", "CAPTURE host=k8s"))]
    blob = 'k8s-worker01 | CHANGED => ' + json.dumps({"stdout": "\r\n".join(worker), "stdout_lines": worker}, indent=4)
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply"], "\n".join(rest) + "\n", blob)
    assert rc == 0, out


def test_judge_edge_only_apply(tmp_path):
    text = sent_lines("apply", APPLY) + capture("edge01") + "\n" + pods()
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply", "--edge-only"], text)
    assert rc == 0 and "scope=edge01" in out and "edge=OK" in out
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply", "--edge-only"],
                        text + hit("edge01", "apply", "2", EK3S_IP, 1) + "\n")
    assert rc == 3 and "edge=OLD_PATH" in out
    rc, out = run_judge(tmp_path, ["apply", RUN, "apply", "apply", "--edge-only"], sent_lines("apply", APPLY))
    assert rc == 4 and "edge=NOT_CAPTURED" in out


def check_files(edge_xri="10.1.93.50", worker_xri="-", swap=False):
    tag = "check160101"
    lines = [sent_lines(tag, ["1", "w105", "w107", "w108"]), capture("edge01", win="check"), pods(),
             hit("edge01", tag, "1", EK3S_IP, 1, xri=edge_xri, win="check")]
    for n, (h, w) in enumerate(zip(HOSTS, ("w105", "w107", "w108"))):
        lines.append(capture(h, win="check"))
        if swap and n < 2:
            w = ("w107", "w105")[n]
        lines.append(hit(h, tag, w, POD_IP[h], 2 + n, xri=worker_xri, win="check"))
    return tag, "\n".join(lines) + "\n"


def test_judge_check_method_ready(tmp_path):
    tag, text = check_files()
    rc, out = run_judge(tmp_path, ["check", RUN, tag, "check"], text)
    assert rc == 0 and "verdict=METHOD_READY" in out


@pytest.mark.parametrize("kw", [dict(edge_xri="-"), dict(worker_xri="10.1.93.50"), dict(swap=True)])
def test_judge_check_wrong_shape_not_ready(tmp_path, kw):
    tag, text = check_files(**kw)
    rc, out = run_judge(tmp_path, ["check", RUN, tag, "check"], text)
    assert rc == 4 and "verdict=METHOD_NOT_READY" in out


def rollback_files(edge_ids=("1", "2", "3", "smoke"), worker_hit=False, workers=True):
    tag = "rollback163000"
    lines = [sent_lines(tag, ["1", "2", "3", "smoke"]), capture("edge01", win="rollback"), pods()]
    lines += [hit("edge01", tag, i, EK3S_IP, 50 + n, win="rollback") for n, i in enumerate(edge_ids)]
    if workers:
        lines += [capture(h, win="rollback") for h in HOSTS]
    if worker_hit:
        lines.append(hit("k8s-worker02", tag, "2", POD_IP["k8s-worker02"], 60, win="rollback"))
    return tag, "\n".join(lines) + "\n"


def test_judge_rollback_path_ok_with_and_without_workers(tmp_path):
    tag, text = rollback_files()
    rc, out = run_judge(tmp_path, ["rollback", RUN, tag, "rollback"], text)
    assert rc == 0 and "verdict=PATH_OK" in out
    tag, text = rollback_files(workers=False)
    rc, out = run_judge(tmp_path, ["rollback", RUN, tag, "rollback"], text)
    assert rc == 0 and "Worker 캡처 없음" in out


def test_judge_rollback_missing_or_new_path(tmp_path):
    tag, text = rollback_files(edge_ids=("1", "3", "smoke"))
    rc, out = run_judge(tmp_path, ["rollback", RUN, tag, "rollback"], text)
    assert rc == 4 and "verdict=UNCONFIRMED" in out
    tag, text = rollback_files(edge_ids=("1", "3", "smoke"), worker_hit=True)
    rc, out = run_judge(tmp_path, ["rollback", RUN, tag, "rollback"], text)
    assert rc == 3 and "verdict=NEW_PATH" in out


def test_judge_rejects_bad_arguments(tmp_path):
    rc, _ = run_judge(tmp_path, ["apply", "2026-10-08", "apply", "apply"], "")
    assert rc == 2


# ---- start → status → report 전체 흐름(Linux 전용: setsid·pgrep·timeout, 가짜 tcpdump) ----

FAKE_TCPDUMP = r'''#!/usr/bin/env bash
for a in "$@"; do case "$a" in -r) MODE=read ;; -w) MODE=write ;; esac; done
if test "${MODE:-}" = read; then
  printf '%s\n' "__T__ cali1 In  IP 10.1.93.50.40001 > 10.244.1.11.8000: Flags [P.], seq 77:197, ack 1, length 120" \
    "....GET /readyz?cutover=cutover-__RUN__-apply-1 HTTP/1.0" "Host: 10.1.93.50" "X-Real-IP: 10.1.93.50" ""
  exit 0
fi
echo "listening on any, link-type LINUX_SLL2" >&2
trap 'printf "1 packet captured\n1 packet received by filter\n0 packets dropped by kernel\n" >&2; exit 0' TERM
while :; do sleep 0.1; done
'''


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux 전용(setsid·pgrep)")
def test_capture_start_status_report_flow(tmp_path):
    import os
    import random
    import shutil
    run = "20990101T%04d" % random.randint(0, 9999)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("id", 'echo 0\n'), ("hostname", 'echo k8s-worker01\n'),
                       ("tcpdump", FAKE_TCPDUMP.replace("__RUN__", run).replace("__T__", "%.6f" % 4102444800.5))):
        p = bindir / name
        p.write_text("#!/usr/bin/env bash\n" + body if name != "tcpdump" else body, encoding="utf-8")
        p.chmod(0o755)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]))
    d = Path("/var/tmp/edge-cutover-capture-%s" % run)
    try:
        sh = lambda *a: subprocess.run(["bash", str(CAP)] + list(a), capture_output=True, text=True, env=env, timeout=60)
        r = sh("start", run, "apply", "60")
        assert r.returncode == 0 and "CAPTURE_READY host=k8s-worker01" in r.stdout, r.stdout + r.stderr
        assert sh("status", run, "apply").returncode == 0
        assert sh("start", run, "apply", "60").returncode == 3            # 같은 RUN·WIN 재시작 거부
        r = sh("report", run, "apply")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "HIT host=k8s-worker01 run=%s win=apply tag=apply id=1 " % run in r.stdout
        assert " err=0 " in r.stdout and " hits=1 " in r.stdout
        assert (d / "apply" / "tcpdump.rc").read_text().strip() == "0"
        assert sh("status", run, "apply").returncode == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
