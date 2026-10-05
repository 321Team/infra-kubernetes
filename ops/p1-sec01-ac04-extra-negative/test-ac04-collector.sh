#!/usr/bin/env bash
# collect-ac04-extra-negative.sh (v2) 의 로컬 시험. 네트워크 접속·서버 접근 없음.
# hostname / ip / timeout / tar 를 PATH 스텁으로 바꿔 판정·종료 코드·Source 결속·범위 고정을 확인한다.
# 사용: bash test-ac04-collector.sh [path/to/collect-ac04-extra-negative.sh]
SCRIPT="$(cd "$(dirname "${1:-./collect-ac04-extra-negative.sh}")" && pwd)/$(basename "${1:-./collect-ac04-extra-negative.sh}")"
PASS=0; FAILN=0; RESULTS=()
check() { if [ "$3" -eq 0 ]; then PASS=$((PASS+1)); RESULTS+=("  PASS  $1: $2"); else FAILN=$((FAILN+1)); RESULTS+=("  FAIL  $1: $2"); fi; }

make_stubs() {  # $1 = dir
  local B="$1/bin"; mkdir -p "$B"
  cat > "$B/hostname" <<'S'
#!/usr/bin/env bash
case "${1:-}" in -s) echo "${STUB_HOST:-ansible01}" ;; -I) echo "${STUB_SRC:-10.250.10.10} " ;; *) echo "${STUB_HOST:-ansible01}" ;; esac
S
  cat > "$B/ip" <<'S'
#!/usr/bin/env bash
# ip -4 route get <ip>
echo "$3 via 10.250.10.1 dev ens160 src ${STUB_SRC:-10.250.10.10} uid 1000"
S
  cat > "$B/timeout" <<'S'
#!/usr/bin/env bash
# timeout <sec> bash -c <script> _ <host> <port>
echo "call $*" >> "${STUB_LOG:-/dev/null}"
if [ -z "${6:-}" ]; then   # /dev/tcp 자체 확인(인자 없음)
  [ "${STUB_NO_DEVTCP:-0}" = 1 ] && { echo "bash: /dev/tcp/127.0.0.1/1: No such file or directory" >&2; exit 1; }
  echo "bash: connect: Connection refused" >&2; exit 1
fi
host="$6"; port="$7"; mode="${STUB_MODE:-normal}"
ctrl="${STUB_CTRL_PORT:-22}"
case "$mode" in
  all_connected) exit 0 ;;
  all_timeout)   exit 124 ;;
  refused_neg)   if [ "$port" = "$ctrl" ]; then exit 0; fi; echo "bash: connect: Connection refused" >&2; exit 1 ;;
  noroute_neg)   if [ "$port" = "$ctrl" ]; then exit 0; fi; echo "bash: connect: No route to host" >&2; exit 1 ;;
  ctrl_fail_w03) if [ "$port" = "$ctrl" ]; then [ "$host" = 10.1.93.108 ] && exit 124; exit 0; fi; exit 124 ;;
  normal|*)      if [ "$port" = "$ctrl" ]; then exit 0; fi; exit 124 ;;
esac
S
  chmod +x "$B"/*
}

run() {  # $1 name  $2 role  rest = env assignments ; 결과: RC, OUTDIR, OUT_TXT
  local name="$1" role="$2"; shift 2
  local T; T="$(mktemp -d)"; make_stubs "$T"; mkdir -p "$T/work"
  local ctrl=22; [ "$role" = edge ] && ctrl=30080
  local host=ansible01 src=10.250.10.10; [ "$role" = edge ] && { host=edge01; src=10.1.93.50; }
  ( cd "$T/work" && env STUB_HOST="$host" STUB_SRC="$src" STUB_CTRL_PORT="$ctrl" STUB_LOG="$T/calls.log" PATH="$T/bin:$PATH" "$@" bash "$SCRIPT" "$role" > "$T/out.txt" 2>&1 ); RC=$?
  OUTDIR="$(ls -d "$T"/work/p1-sec01-ac04-extra-neg-* 2>/dev/null | head -1)"
  CASE_T="$T"
}
has() { grep -q -- "$1" "$CASE_T/out.txt"; }
sumhas() { grep -q -- "$1" "$OUTDIR/99-summary.txt" 2>/dev/null; }
probe_calls() { local n; n="$(grep -c '^call [0-9]* bash -c .* _ ' "$CASE_T/calls.log" 2>/dev/null)"; echo "${n:-0}"; }
rows() { awk 'NR>1' "$OUTDIR/results.tsv" 2>/dev/null | wc -l; }

# T1 정상(mgmt)
run T1 mgmt STUB_MODE=normal
check T1 "정상: rc=0, COMPLETE, BLOCKED_VALID" $([ "$RC" -eq 0 ] && sumhas "COLLECTOR_STATUS=COMPLETE" && sumhas "AC_VERDICT=BLOCKED_VALID"; echo $?)
check T1 "행 12개(대조 3 + 차단 9), 차단 유효 9" $([ "$(rows)" -eq 12 ] && sumhas "valid_block=9"; echo $?)
check T1 "봉인(SHA256SUMS·tar·sidecar) 생성" $([ -s "$OUTDIR/SHA256SUMS" ] && ls "$CASE_T"/work/*.tar.gz.sha256 >/dev/null 2>&1; echo $?)
check T1 "route src 확인 파일 기록" $(grep -c 'route_src=10.250.10.10' "$OUTDIR/00-route-source.txt" | grep -q '^3$'; echo $?)
rm -rf "$CASE_T"

# T2 모든 접속이 CONNECTED -> FAIL, rc!=0 (v1 은 rc=0 이었다)
run T2 mgmt STUB_MODE=all_connected
check T2 "차단 기대인데 CONNECTED 면 rc=11, FAIL" $([ "$RC" -eq 11 ] && sumhas "AC_VERDICT=FAIL" && sumhas "fail=9"; echo $?)
check T2 "FAIL 이면서 PASS 로 끝나지 않음(FINAL 줄에 FAIL)" $(has "FINAL COLLECTOR_STATUS=COMPLETE AC_VERDICT=FAIL"; echo $?)
rm -rf "$CASE_T"

# T3 대조까지 전부 TIMEOUT -> 유효한 차단 PASS 로 세지 않음(INCONCLUSIVE), rc!=0 (v1 은 PASS=9, rc=0 이었다)
run T3 mgmt STUB_MODE=all_timeout
check T3 "대조 불충족이면 rc=10, INCONCLUSIVE" $([ "$RC" -eq 10 ] && sumhas "AC_VERDICT=INCONCLUSIVE"; echo $?)
check T3 "유효한 차단(valid_block)은 0, TIMEOUT 은 관측값으로 보존" $(sumhas "valid_block=0" && grep -c 'TIMEOUT' "$OUTDIR/results.tsv" | grep -q '^12$'; echo $?)
check T3 "대조 불충족 사유가 verdict-rows 에 기록" $(grep -q '대조 불충족' "$OUTDIR/verdict-rows.tsv"; echo $?)
rm -rf "$CASE_T"

# T4 범위 고정: 환경변수로 대상·포트·제한시간·대조포트를 바꿔도 무시된다(v1 은 NEG_PORTS 공백이면 차단 행 0개로 성공 종료)
run T4 mgmt STUB_MODE=normal NEG_PORTS="" WORKERS="1.2.3.4" TMO=0 CTRL_PORT=1 NEG=""
check T4 "환경변수 변경을 무시하고 12행·rc=0" $([ "$RC" -eq 0 ] && [ "$(rows)" -eq 12 ] && sumhas "neg_ports=9100 9101 10250"; echo $?)
check T4 "대상은 고정된 Worker 3대(1.2.3.4 로 접속하지 않음)" $(! grep -q '1.2.3.4' "$CASE_T/calls.log"; echo $?)
rm -rf "$CASE_T"

# T5 route src 불일치 -> 접속 전 STOP(rc=2), 시험 접속 호출 0회
run T5 mgmt STUB_MODE=normal STUB_SRC=10.1.93.50
check T5 "route src 가 예상과 다르면 STOP(rc=2)" $([ "$RC" -eq 2 ] && has "STOP:" && has "Source 불일치"; echo $?)
check T5 "접속 호출 0회(자체 확인 외 시험 접속 없음)" $([ "$(probe_calls)" -eq 0 ]; echo $?)
rm -rf "$CASE_T"

# T6 hostname 불일치 -> STOP
run T6 mgmt STUB_MODE=normal STUB_HOST=control01
check T6 "hostname 이 예상과 다르면 STOP(rc=2), 접속 0회" $([ "$RC" -eq 2 ] && has "hostname=" && [ "$(probe_calls)" -eq 0 ]; echo $?)
rm -rf "$CASE_T"

# T7 차단 기대 행이 REFUSED -> 차단 PASS 로 읽지 않음
run T7 mgmt STUB_MODE=refused_neg
check T7 "REFUSED 는 INCONCLUSIVE(rc=10), 유효 차단 0" $([ "$RC" -eq 10 ] && sumhas "valid_block=0" && sumhas "inconclusive=9"; echo $?)
check T7 "REFUSED 로 분류되고 stderr 가 보존됨" $(grep -q 'REFUSED' "$OUTDIR/results.tsv" && grep -qi 'refused' "$OUTDIR/results.tsv"; echo $?)
rm -rf "$CASE_T"

# T8 No route 같은 오류는 REFUSED 가 아니라 ERROR (v1 은 짧은 실패를 전부 REFUSED 로 분류)
run T8 mgmt STUB_MODE=noroute_neg
check T8 "No route 는 ERROR 로 남고 REFUSED 로 읽지 않음" $(grep -q 'ERROR' "$OUTDIR/results.tsv" && ! grep -q 'REFUSED' "$OUTDIR/results.tsv" && grep -qi 'No route' "$OUTDIR/results.tsv"; echo $?)
check T8 "ERROR 는 유효 차단이 아니며 rc=10" $([ "$RC" -eq 10 ]; echo $?)
rm -rf "$CASE_T"

# T9 edge: worker03 대조만 실패(Local endpoint 부재). 다른 Worker 성공으로 worker03 을 승격하지 않는다
run T9 edge STUB_MODE=ctrl_fail_w03
# ctrl_fail_w03 은 대조 포트(30080)에서 .108 만 TIMEOUT, 차단 포트는 모두 TIMEOUT
check T9 "worker03 만 대조 불충족: 유효 차단 6, 불확정 3, rc=10" $([ "$RC" -eq 10 ] && sumhas "valid_block=6" && sumhas "inconclusive=3"; echo $?)
check T9 "worker03 알려진 한계가 기록되고 승격 안 됨" $(grep -q 'worker03 edge 대조는 Local endpoint 부재' "$OUTDIR/verdict-rows.tsv" && [ "$(grep -c 'VALID_BLOCK' "$OUTDIR/verdict-rows.tsv")" -eq 6 ]; echo $?)
check T9 "edge 대조 포트는 30080" $(grep -q 'control_port: 30080' "$OUTDIR/00-environment.txt"; echo $?)
rm -rf "$CASE_T"

# T10 봉인 실패(tar 실패) -> 수집 성공으로 끝내지 않음(rc=12)
run T10 mgmt STUB_MODE=normal
rm -rf "$CASE_T"
T="$(mktemp -d)"; make_stubs "$T"; mkdir -p "$T/work"; printf '#!/usr/bin/env bash\nexit 1\n' > "$T/bin/tar"; chmod +x "$T/bin/tar"
( cd "$T/work" && env STUB_HOST=ansible01 STUB_SRC=10.250.10.10 STUB_CTRL_PORT=22 STUB_LOG="$T/calls.log" STUB_MODE=normal PATH="$T/bin:$PATH" bash "$SCRIPT" mgmt > "$T/out.txt" 2>&1 ); RC=$?
CASE_T="$T"
check T10 "tar 봉인 실패면 INCOMPLETE(rc=12)" $([ "$RC" -eq 12 ] && has "INCOMPLETE" && has "FINAL COLLECTOR_STATUS=INCOMPLETE"; echo $?)
rm -rf "$T"

# T11 /dev/tcp 미지원 bash -> STOP
run T11 mgmt STUB_MODE=normal STUB_NO_DEVTCP=1
check T11 "/dev/tcp 미지원이면 STOP(rc=2), 시험 접속 0회" $([ "$RC" -eq 2 ] && has "/dev/tcp" && [ "$(probe_calls)" -eq 0 ]; echo $?)
rm -rf "$CASE_T"

# T12 사용법
T="$(mktemp -d)"; ( cd "$T" && bash "$SCRIPT" bogus > "$T/o.txt" 2>&1 ); RC=$?
check T12 "잘못된 인자는 rc=2" $([ "$RC" -eq 2 ]; echo $?)
rm -rf "$T"

# T13 edge 정상
run T13 edge STUB_MODE=normal
check T13 "edge 정상: rc=0, 대조 포트 30080, 차단 유효 9" $([ "$RC" -eq 0 ] && sumhas "BLOCKED_VALID" && grep -q '30080' "$OUTDIR/results.tsv"; echo $?)
rm -rf "$CASE_T"

echo "=== collect-ac04-extra-negative.sh 로컬 시험 결과"
printf '%s\n' "${RESULTS[@]}"
echo "=== PASS=$PASS FAIL=$FAILN"
[ "$FAILN" -eq 0 ]
