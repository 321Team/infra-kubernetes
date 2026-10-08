import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CUTOVER = ROOT / "ops/edge01-cutover/edge01-cutover.sh"
CHECK = ROOT / "ops/edge01-cutover/control01-cutover-check.sh"
README = ROOT / "ops/edge01-cutover/README.md"
RUN = "20261010T1000"

BASE = (
    "upstream transaction_api {{\n"
    "    server 10.1.93.50:30080;\n"
    "{extra}"
    "    # server 10.1.93.105:30080;\n"
    "    # server 10.1.93.107:30080;\n"
    "    # server 10.1.93.108:30080;\n"
    "}}\n"
    "server {{\n"
    "    listen 80 default_server;\n"
    "    location = /metrics {{ return 404; }}\n"
    "    location / {{ proxy_pass http://transaction_api; }}\n"
    "}}\n"
)


def cutover_text():
    return CUTOVER.read_text(encoding="utf-8")


def embedded_py(text, starts_with):
    """starts_with로 시작하는 줄 다음부터 단독 'PY' 줄 전까지의 내장 Python을 꺼낸다."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.lstrip().startswith(starts_with))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "PY")
    return "\n".join(lines[start + 1:end])


def run_py(tmp_path, source, args):
    script = tmp_path / "embedded.py"
    script.write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, "-X", "utf8", "-I", str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, encoding="utf-8")


def uv(ip, path, method="GET", status="200 OK"):
    """uvicorn 접근 로그 한 줄"""
    return 'INFO:     %s:51234 - "%s %s HTTP/1.0" %s\n' % (ip, method, path, status)


def nonce_path(tag, i, base="/readyz"):
    return "%s?cutover=cutover-%s-%s-%s" % (base, RUN, tag, i)


# ---- upstream 전환 로직 ----

def run_transform(tmp_path, content, newline="\n"):
    src = tmp_path / "conf"
    out = tmp_path / "after"
    src.write_bytes(content.replace("\n", newline).encode())
    r = run_py(tmp_path, embedded_py(cutover_text(), 'python3 -I - "$BEFORE" "$AFTER"'),
               [src, out, "transaction_api", RUN])
    return r.returncode, src.read_bytes(), out.read_bytes() if out.exists() else None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_transform_changes_only_four_upstream_lines(tmp_path, newline):
    rc, before, after = run_transform(tmp_path, BASE.format(extra="    keepalive 8;\n"), newline)
    assert rc == 0
    b = before.decode().split(newline)
    a = after.decode().split(newline)
    assert len(a) == len(b)
    assert len([1 for x, y in zip(b, a) if x != y]) == 4
    assert a[1].startswith("    # server 10.1.93.50:30080;")
    assert a[3:6] == ["    server 10.1.93.105:30080;",
                      "    server 10.1.93.107:30080;",
                      "    server 10.1.93.108:30080;"]
    assert "location = /metrics { return 404; }" in after.decode()


@pytest.mark.parametrize("extra", [
    "    server 10.9.9.9:80;\n",                     # 예상 밖 유효 server 줄
    "    # server 10.1.93.105:30080 backup;\n",      # Worker 주석 줄 중복
])
def test_transform_refuses_unexpected_shapes(tmp_path, extra):
    rc, _, after = run_transform(tmp_path, BASE.format(extra=extra))
    assert rc != 0
    assert after is None


def test_transform_refuses_missing_worker_line(tmp_path):
    content = BASE.format(extra="").replace("    # server 10.1.93.108:30080;\n", "")
    rc, _, after = run_transform(tmp_path, content)
    assert rc != 0
    assert after is None


# ---- 변경 범위·판정 분기 ----

def mode_block(mode):
    m = re.search(r"\n  %s\)\n(.*?)\n    ;;\n" % mode, cutover_text(), re.S)
    assert m, mode
    return m.group(1)


def test_cutover_keeps_k3s_and_restores_on_failed_config_test():
    text = cutover_text()
    for forbidden in ("systemctl stop k3s", "systemctl disable k3s", "k3s-killall",
                      "set -e\n", "sed -i", "kubectl apply", "kubectl patch",
                      "kubectl delete", "kubectl create"):
        assert forbidden not in text
    assert "nginx -t" in text
    assert "systemctl reload nginx" in text
    assert re.search(r'if nginx_ok; then :; else\n.*?"\$BEFORE" "\$F"', text, re.S)


def test_unconfirmed_is_not_reported_as_ok():
    """B1-R·B2-R: 판정 불가는 종료 4이며 OK를 내지 않는다. 경로 판정은 캡처, 앱 로그는 참고(발견 시 STOP)."""
    text = cutover_text()
    assert re.search(r"unconfirmed\(\) \{.*?exit 4; \}", text)
    apply = mode_block("apply")
    rollback = mode_block("rollback")
    for block in (apply, rollback):
        assert "wait_reloaded || unconfirmed" in block
        assert 'test "$EDGE_PART" = OK || unconfirmed' in block
        assert block.rstrip().splitlines()[-1].strip().startswith('echo "RESULT cutover-')
    # 옛 경로의 직접 증거(로그 발견·캡처 OLD_PATH)는 STOP, 로그 조회 실패는 판정에 쓰지 않는다
    assert 'test "$(tr_get "$T" found)" = 0 || die' in apply
    assert 'test "$EDGE_PART" = OLD_PATH && die' in apply
    assert 'tr_get "$T" err' not in apply + rollback
    assert "ids_exact" not in apply + rollback
    assert "cutover-capture-judge.py apply $RUN apply $WIN" in apply
    assert "EDGE_OK" in apply
    # apply는 캡처가 없으면 바꾸지 않고, rollback은 캡처가 없어도 복원한다
    assert re.search(r'status "\$RUN" "\$WIN" \|\| die', apply)
    assert re.search(r'status "\$RUN" "\$WIN" \|\| echo', rollback)
    assert apply.index('status "$RUN" "$WIN"') < apply.index('sudo cat "$F" > "$BEFORE"')
    assert 'smoke "$TAG" || die' in rollback
    for gone in ("tcpdump", "sock30080", "pkill", "|| true\n    n=$((n"):
        assert gone not in text


def test_reload_check_ignores_draining_workers():
    text = cutover_text()
    body = text[text.index("reloaded_after_conf() {"):text.index("wait_reloaded() {")]
    assert '*"shutting down"*' in body
    assert "return 2" in body and "return 1" in body


# ---- E-K3S 표식 조회(ek3s_trace)를 가짜 k3s로 실행 ----

FAKE_K3S = """#!/usr/bin/env bash
d="$FAKE_DIR"
if [ "$1 $2" = "kubectl get" ]; then cat "$d/pods.txt"; exit "$(cat "$d/pods.rc")"; fi
if [ "$1 $2" = "kubectl logs" ]; then cat "$d/$5.log" 2>/dev/null; exit "$(cat "$d/$5.rc")"; fi
exit 9
"""

HARNESS = """python3 -c 1 >/dev/null 2>&1 || python3() { python -X utf8 "$@"; }
sudo() { "$@"; }
K3S="__K3S__"; LOGDIR="__LOGDIR__"; RUN=__RUN__
__FUNCS__
T=$(ek3s_trace __TAG__ 600 | tail -1)
echo "$T"
echo "err=$(tr_get "$T" err) found=$(tr_get "$T" found) ids=$(tr_get "$T" ids)"
ids_exact "$(tr_get "$T" ids)" 1 2 3 smoke && echo EXACT_1_2_3_SMOKE
"""


def bash_works():
    b = shutil.which("bash")
    if not b:
        return None
    try:
        out = subprocess.run([b, "-c", "echo ok"], capture_output=True, text=True, timeout=10)
        return b if out.stdout.strip() == "ok" else None
    except (OSError, subprocess.TimeoutExpired):
        return None


BASH = bash_works()
needs_bash = pytest.mark.skipif(BASH is None, reason="bash 없음")


def run_ek3s(tmp_path, pods, logs, pods_rc=0, tag="rollback1"):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "pods.txt").write_text("".join("ns %s 1/1 Running 0 1d\n" % p for p in pods), encoding="utf-8")
    (fake / "pods.rc").write_text(str(pods_rc), encoding="utf-8")
    for pod, (rc, body) in logs.items():
        (fake / ("%s.log" % pod)).write_text(body, encoding="utf-8")
        (fake / ("%s.rc" % pod)).write_text(str(rc), encoding="utf-8")
    k3s = tmp_path / "k3s"
    k3s.write_text(FAKE_K3S, encoding="utf-8")
    k3s.chmod(0o755)
    logdir = tmp_path / "logdir"
    logdir.mkdir()
    text = cutover_text()
    funcs = text[text.index("ek3s_trace() {"):text.index("show() {")]
    h = (HARNESS.replace("__K3S__", k3s.as_posix()).replace("__LOGDIR__", logdir.as_posix())
         .replace("__RUN__", RUN).replace("__FUNCS__", funcs).replace("__TAG__", tag))
    hp = tmp_path / "h.sh"
    hp.write_text(h, encoding="utf-8")
    env = dict(os.environ, FAKE_DIR=fake.as_posix())
    r = subprocess.run([BASH, hp.as_posix()], capture_output=True, text=True, encoding="utf-8", env=env)
    return r.stdout + r.stderr


def rollback_lines(ids):
    out = ""
    for i in ids:
        if i == "smoke":
            out += uv("10.42.0.1", nonce_path("rollback1", i, "/api/v1/transactions"), "POST", "201 Created")
        else:
            out += uv("10.42.0.1", nonce_path("rollback1", i))
    return out


@needs_bash
def test_ek3s_all_ok_zero_hits(tmp_path):
    out = run_ek3s(tmp_path, ["transaction-api-a", "other-x"],
                   {"transaction-api-a": (0, uv("10.42.0.1", "/readyz"))})
    assert "err=0 found=0 ids=\n" in out


@needs_bash
def test_ek3s_partial_log_failure_is_err(tmp_path):
    out = run_ek3s(tmp_path, ["transaction-api-a", "transaction-api-b"],
                   {"transaction-api-a": (0, rollback_lines(["1", "2", "3", "smoke"])),
                    "transaction-api-b": (1, "")})
    assert "err=1" in out


@needs_bash
def test_ek3s_get_pods_failure_is_err(tmp_path):
    assert "err=1" in run_ek3s(tmp_path, [], {}, pods_rc=1)


@needs_bash
def test_ek3s_no_matching_pod_is_err(tmp_path):
    assert "err=1" in run_ek3s(tmp_path, ["other-x"], {})


@needs_bash
def test_ek3s_previous_requests_are_not_counted(tmp_path):
    body = (uv("10.42.0.1", "/api/v1/transactions", "POST", "201 Created")      # 이전 일반 POST
            + uv("10.42.0.1", "/readyz?cutover=cutover-20261009T1000-rollback1-1")  # 다른 RUN
            + uv("10.42.0.1", nonce_path("check1", "1")))                          # 다른 tag
    out = run_ek3s(tmp_path, ["transaction-api-a"], {"transaction-api-a": (0, body)})
    assert "err=0 found=0 ids=\n" in out
    assert "EXACT_1_2_3_SMOKE" not in out


@needs_bash
def test_ek3s_current_nonces_exact_once(tmp_path):
    out = run_ek3s(tmp_path, ["transaction-api-a", "transaction-api-b"],
                   {"transaction-api-a": (0, rollback_lines(["1", "2", "3", "smoke"])),
                    "transaction-api-b": (0, "")})
    assert "err=0 found=4 ids=1:1,2:1,3:1,smoke:1" in out
    assert "EXACT_1_2_3_SMOKE" in out


@needs_bash
def test_ek3s_duplicate_nonce_is_not_exact(tmp_path):
    out = run_ek3s(tmp_path, ["transaction-api-a"],
                   {"transaction-api-a": (0, rollback_lines(["1", "1", "2", "3", "smoke"]))})
    assert "ids=1:2,2:1,3:1,smoke:1" in out
    assert "EXACT_1_2_3_SMOKE" not in out


# ---- control01 trace 판정 ----

def run_trace(tmp_path, tag, pod_logs, errs=0):
    d = tmp_path / "t"
    d.mkdir()
    for name, body in pod_logs.items():
        (d / ("%s.log" % name)).write_text(body, encoding="utf-8")
    src = embedded_py(CHECK.read_text(encoding="utf-8"), 'python3 -I - "$T" "cutover-$RUN-$TAG-"')
    r = run_py(tmp_path, src, [d, "cutover-%s-%s-" % (RUN, tag), tag, errs])
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def apply_lines(ids, ip="10.1.93.50"):
    return "".join(uv(ip, nonce_path("apply", i)) for i in ids)


APPLY_IDS = [str(i) for i in range(1, 10)] + ["smoke"]


def test_trace_apply_all_once_is_traced(tmp_path):
    out = run_trace(tmp_path, "apply", {"p1@k8s-worker01": apply_lines(APPLY_IDS[:4]),
                                        "p2@k8s-worker02": apply_lines(APPLY_IDS[4:7]),
                                        "p3@k8s-worker03": apply_lines(APPLY_IDS[7:])})
    assert "verdict=TRACED source_50=yes" in out


@pytest.mark.parametrize("ids", [APPLY_IDS[1:], APPLY_IDS + ["3"]])   # 누락 / 중복
def test_trace_apply_missing_or_duplicate_is_unconfirmed(tmp_path, ids):
    out = run_trace(tmp_path, "apply", {"p1@k8s-worker01": apply_lines(ids)})
    assert "verdict=UNCONFIRMED" in out


def test_trace_log_failure_is_err(tmp_path):
    out = run_trace(tmp_path, "apply", {"p1@k8s-worker01": apply_lines(APPLY_IDS)}, errs=1)
    assert "verdict=ERR" in out


@pytest.mark.parametrize("nodes,verdict", [
    (("k8s-worker01", "k8s-worker02", "k8s-worker03"), "TRACE_READY"),
    (("k8s-worker02", "k8s-worker02", "k8s-worker03"), "TRACE_NOT_READY"),   # w105가 다른 노드에서 관측
])
def test_trace_check_maps_worker_to_node(tmp_path, nodes, verdict):
    logs = {"p%d@%s" % (i, n): uv("10.1.93.50", nonce_path("check1", w))
            for i, (w, n) in enumerate(zip(("w105", "w107", "w108"), nodes))}
    assert "verdict=%s" % verdict in run_trace(tmp_path, "check1", logs)


def test_trace_rollback_expects_none(tmp_path):
    assert "verdict=NONE_OK" in run_trace(tmp_path, "rollback1", {"a@k8s-worker01": uv("10.1.93.50", "/readyz")})


# ---- README·control01 ----

def test_readme_n1_and_trace_criteria():
    text = README.read_text(encoding="utf-8")
    n1 = text[text.index("#### N1 절차"):text.index("## 5. 중단")]
    assert "rc≠0" not in n1
    for required in ("Test-NetConnection 10.1.93.50 -Port 80", "SourceAddress", "10.1.93.29",
                     "INCONCLUSIVE", "FAIL", "Tee-Object"):
        assert required in n1
    assert "12/12행 수집 완결 = positive 3" in text
    assert "C0 판정표" in text
    # B1~B3 보완: 캡처 판정, E-K3S 유지, 원인별 rc 처리, 시간 상한
    for required in ("E-K3S는 유지하며, 앱 로그 기반 판정만 대체", "METHOD_READY", "PATH_OK", "OLD_PATH",
                     "X-Real-IP", "snaplen", "T0+80", "T0+95", "apply 시작 상한 = 창 종료 − 15분 − 65분",
                     "종료 코드·결과의 원인별 처리", "기능 복구 확인·경로 추적 미확인", "false`/`false"):
        assert required in text, required
    assert "T0+75분" not in text


def test_control01_check_is_read_only():
    text = CHECK.read_text(encoding="utf-8")
    for forbidden in ("kubectl apply", " apply ", " patch ", " delete ", " create ",
                      " edit ", " scale ", "rollout", "get secret", "set -e\n"):
        assert forbidden not in text
    for required in ("transaction-api-nodeport", "nodeport-30080-allow", "32085",
                     "count(up == 0)", 'kill "$PF"', "EMPTY", "COLLECTED", "logs \"$pod\"", ".rc\""):
        assert required in text


@needs_bash
@pytest.mark.parametrize('newest,expected', [(1791200000, 0), (1991200000, 1)])
def test_reload_check_parses_c_locale_under_korean_host(tmp_path, newest, expected):
    text = cutover_text()
    body = text[text.index('reloaded_after_conf() {'):text.index('wait_reloaded() {')]
    harness = '''
sudo() { "$@"; }
nginx() { echo '# configuration file /mock/nginx.conf:'; }
stat() { echo NEWEST; }
pgrep() { if test "$1" = -o; then echo 100; else echo 101; fi; }
ps() {
  if test "$2" = args=; then echo 'nginx: worker process'; return; fi
  if test "${LC_ALL:-}" = C; then echo 'Tue Oct  6 10:04:38 2026';
  else echo '화 10월  6 10:04:38 2026'; fi
}
'''.replace('NEWEST', str(newest)) + body + '\nreloaded_after_conf\n'
    hp = tmp_path / 'locale.sh'; hp.write_text(harness, encoding='utf-8')
    result = subprocess.run([BASH, hp.as_posix()], capture_output=True)
    assert result.returncode == expected, result.stderr


@needs_bash
@pytest.mark.parametrize('ready,query_rc,expected', [(True, 0, 0), (False, 0, 4), (True, 1, 4)])
def test_control_trace_shell_propagates_verdict(tmp_path, ready, query_rc, expected):
    tag = 'check032801'
    for n, worker in enumerate(('k8s-worker01', 'k8s-worker02', 'k8s-worker03')):
        (tmp_path / ('p%d.log' % n)).write_text(
            uv('10.1.93.50', nonce_path(tag, ('w105', 'w107', 'w108')[n])) if ready else '', encoding='utf-8')
    fake = tmp_path / 'kubectl'
    fake.write_text('''#!/usr/bin/env bash
if test "$3" = get; then
  printf 'p0 k8s-worker01\\np1 k8s-worker02\\np2 k8s-worker03\\n'
  exit QUERY_RC
fi
cat "FAKE_DIR/$4.log"
'''.replace('QUERY_RC', str(query_rc)).replace('FAKE_DIR', tmp_path.as_posix()), encoding='utf-8')
    fake.chmod(0o755)
    text = CHECK.read_text(encoding='utf-8')
    block = text.split('if test "$PHASE" = trace; then', 1)[1].split('\necho "== $(date', 1)[0]
    harness = ('python3 -c 1 >/dev/null 2>&1 || python3() { python -X utf8 "$@"; }\n'
               + 'K=("%s"); DIR="%s"; STAMP=mock; RUN=%s; TAG=%s; PHASE=trace\n' % (fake.as_posix(), tmp_path.as_posix(), RUN, tag)
               + 'if test "$PHASE" = trace; then' + block)
    hp = tmp_path / 'trace.sh'; hp.write_text(harness, encoding='utf-8')
    result = subprocess.run([BASH, hp.as_posix()], capture_output=True)
    assert result.returncode == expected, result.stdout + result.stderr


CHECK_HARNESS = """
sudo() { if test "$1" = bash; then shift; fake_cap "$@"; else return 0; fi; }
fake_cap() { if test "$2" = status; then test "$CAP_STATE" = RUNNING; else echo report; fi; }
show() { :; }; nginx_ok() { return 0; }; reloaded_after_conf() { return 0; }
send_nonces() { :; }; sent() { :; }; sleep() { :; }; now() { echo 1; }
nonce() { echo marker; }; code() { echo 200; }; ek3s_trace() { echo 'TRACE ek3s tag=x pods=1 err=0 found=0 ids='; }
edge_capture_judge() { EDGE_PART=$FAKE_EDGE; }
die() { echo "STOP: $*"; exit 3; }
STAMP=1; RUN=20261008T0327; WIN=check; EDGE=http://unused; NEW_WORKERS=(); CAP=cap
"""


@needs_bash
@pytest.mark.parametrize("cap_state,edge,rc,word", [
    ("RUNNING", "OK", 0, b"EDGE_CAPTURE_READY"),
    ("RUNNING", "MISMATCH", 3, b"EDGE_CAPTURE_NOT_READY"),      # 캡처에 표식 없음(빈 로그와 같은 상황)
    ("RUNNING", "NOT_CAPTURED", 3, b"EDGE_CAPTURE_NOT_READY"),
    ("ABSENT", "OK", 3, b"STOP:"),                              # 캡처를 먼저 시작하지 않으면 표식 시험 안 함
])
def test_edge_check_requires_capture(tmp_path, cap_state, edge, rc, word):
    hp = tmp_path / "check.sh"
    hp.write_text(CHECK_HARNESS + mode_block("check") + "\n", encoding="utf-8")
    env = dict(os.environ, CAP_STATE=cap_state, FAKE_EDGE=edge)
    result = subprocess.run([BASH, hp.as_posix()], capture_output=True, env=env)
    assert result.returncode == rc, result.stdout + result.stderr
    assert word in result.stdout
