import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CUTOVER = ROOT / "ops/edge01-cutover/edge01-cutover.sh"
CHECK = ROOT / "ops/edge01-cutover/control01-cutover-check.sh"

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


def transform_source():
    text = CUTOVER.read_text(encoding="utf-8")
    m = re.search(r"<<'PY' \|\| die \"새 설정 생성 실패[^\n]*\n(.*?)\nPY\n", text, re.S)
    assert m, "embedded transform not found"
    return m.group(1)


def run_transform(tmp_path, content, newline="\n"):
    src = tmp_path / "conf"
    out = tmp_path / "after"
    src.write_bytes(content.replace("\n", newline).encode())
    script = tmp_path / "t.py"
    script.write_text(transform_source(), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(script), str(src), str(out),
                        "transaction_api", "20261010T1000"],
                       capture_output=True)
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


def test_cutover_keeps_k3s_and_restores_on_failed_config_test():
    text = CUTOVER.read_text(encoding="utf-8")
    for forbidden in ("systemctl stop k3s", "systemctl disable k3s", "k3s-killall",
                      "set -e\n", "sed -i", "kubectl"):
        assert forbidden not in text
    assert "nginx -t" in text
    assert "systemctl reload nginx" in text
    # nginx -t 실패 분기에서 사본으로 되돌린다
    assert re.search(r"if nginx_ok; then :; else\n.*?\"\$BEFORE\" \"\$F\"", text, re.S)


def test_control01_check_is_read_only():
    text = CHECK.read_text(encoding="utf-8")
    for forbidden in ("kubectl apply", " apply ", " patch ", " delete ", " create ",
                      " edit ", " scale ", "rollout", "get secret", "set -e\n"):
        assert forbidden not in text
    for required in ("transaction-api-nodeport", "nodeport-30080-allow", "32085",
                     "count(up == 0)", 'kill "$PF"'):
        assert required in text
