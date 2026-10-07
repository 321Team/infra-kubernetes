import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CUTOVER = ROOT / "ops/edge01-cutover/edge01-cutover.sh"
CHECK = ROOT / "ops/edge01-cutover/control01-cutover-check.sh"
README = ROOT / "ops/edge01-cutover/README.md"

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


def heredoc_after(marker):
    """marker 줄 다음부터 단독 'PY' 줄 전까지의 내장 Python을 꺼낸다."""
    lines = cutover_text().split("\n")
    start = next(i for i, l in enumerate(lines) if marker in l and l.rstrip().endswith(("<<'PY'", "<<'PY' || die \"새 설정 생성 실패(파일은 바꾸지 않았다)\"")))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "PY")
    return "\n".join(lines[start + 1:end])


def run_py(tmp_path, source, args):
    script = tmp_path / "embedded.py"
    script.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-X", "utf8", "-I", str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


# ---- upstream 전환 로직 ----

def run_transform(tmp_path, content, newline="\n"):
    src = tmp_path / "conf"
    out = tmp_path / "after"
    src.write_bytes(content.replace("\n", newline).encode())
    r = run_py(tmp_path, heredoc_after('python3 -I - "$BEFORE" "$AFTER"'),
               [src, out, "transaction_api", "20261010T1000"])
    return r.returncode, src.read_bytes(), out.read_bytes() if out.exists() else None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_transform_changes_only_four_upstream_lines(tmp_path, newline):
    rc, before, after = run_transform(tmp_path, BASE.format(extra="    keepalive 8;\n"), newline)
    assert rc == 0
    b = before.decode().split(newline)
    a = after.decode().split(newline)
    assert len(a) == len(b)
    changed = [(x, y) for x, y in zip(b, a) if x != y]
    assert len(changed) == 4
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


# ---- 변경 범위·복구 ----

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
    """B1·B2: 반영·경유를 확인하지 못하면 종료 4(UNCONFIRMED)이며 OK를 내지 않는다."""
    text = cutover_text()
    assert re.search(r"unconfirmed\(\) \{.*?exit 4; \}", text)
    apply = mode_block("apply")
    rollback = mode_block("rollback")
    for block in (apply, rollback):
        assert "wait_reloaded || unconfirmed" in block
        assert '"$(obs_get "$OBS" capture)" = ok || unconfirmed' in block
        assert "참고:" not in block
        assert block.rstrip().splitlines()[-1].strip().startswith('echo "RESULT cutover-')
    assert '"$(obs_get "$OBS" new)" -ge 9 || unconfirmed' in apply
    assert '"$(obs_get "$OBS" new)" = 0 || unconfirmed' in rollback
    assert "smoke apply || die" in apply
    assert "smoke rollback || die" in rollback
    assert 'test "$P" -ge 1 || unconfirmed' in rollback
    assert "sock30080" not in text


def test_reload_check_ignores_draining_workers():
    text = cutover_text()
    body = text[text.index("reloaded_after_conf() {"):text.index("wait_reloaded() {")]
    assert '*"shutting down"*' in body
    assert "return 2" in body and "return 1" in body


# ---- SYN 분류기 ----

def run_classifier(tmp_path, cap_lines, err_text, ok="9", n="9"):
    cap = tmp_path / "cap.txt"
    err = tmp_path / "cap.err"
    cap.write_text("\n".join(cap_lines) + "\n", encoding="utf-8")
    err.write_text(err_text, encoding="utf-8")
    r = run_py(tmp_path, heredoc_after('"${NEW_WORKERS[@]}" <<'),
               [cap, err, "10.42.0.0/24", ok, n, "10.1.93.105", "10.1.93.107", "10.1.93.108"])
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def syn(src, sport, dst, dport, prefix="ens160 Out "):
    return "12:00:00.000000 %sIP %s.%s > %s.%s: Flags [S], seq 1, win 64240, length 0" % (
        prefix, src, sport, dst, dport)


def test_classifier_counts_worker_syn_and_ignores_probe_noise(tmp_path):
    workers = ["10.1.93.105", "10.1.93.107", "10.1.93.108"] * 3
    lines = [syn("10.1.93.50", 40000 + i, w, 30080) for i, w in enumerate(workers)]
    lines += [
        syn("10.42.0.1", 50001, "10.42.0.7", 8000, prefix=""),       # kubelet probe(E-K3S)
        syn("10.1.93.50", 50002, "10.1.93.50", 80, prefix="lo In "),   # curl → nginx
        "12:00:01.000000 ens160 Out IP 10.1.93.50.40000 > 10.1.93.105.30080: Flags [S.], ack 1",
    ]
    out = run_classifier(tmp_path, lines, "listening on any, link-type LINUX_SLL2\n")
    assert out == "OBS new=9 ek3s=1 other=1 http_ok=9/9 capture=ok"


def test_classifier_marks_capture_failure(tmp_path):
    out = run_classifier(tmp_path, [], "tcpdump: any: You don't have permission\n")
    assert out.endswith("capture=fail")


# ---- README·control01 ----

def test_readme_n1_is_not_plain_nonzero_rc():
    """B3: N1은 rc≠0 단독 기준이 아니라 출발지·대조·도구 오류를 구분한다."""
    text = README.read_text(encoding="utf-8")
    n1 = text[text.index("#### N1 절차"):text.index("## 5. 중단")]
    assert "rc≠0" not in n1
    for required in ("Test-NetConnection 10.1.93.50 -Port 80", "SourceAddress", "10.1.93.29",
                     "INCONCLUSIVE", "FAIL", "Tee-Object"):
        assert required in n1
    assert "12/12행 수집 완결 = positive 3" in text
    assert "C0 판정표" in text


def test_control01_check_is_read_only():
    text = CHECK.read_text(encoding="utf-8")
    for forbidden in ("kubectl apply", " apply ", " patch ", " delete ", " create ",
                      " edit ", " scale ", "rollout", "get secret", "set -e\n"):
        assert forbidden not in text
    for required in ("transaction-api-nodeport", "nodeport-30080-allow", "32085",
                     "count(up == 0)", 'kill "$PF"', "EMPTY", "COLLECTED"):
        assert required in text
