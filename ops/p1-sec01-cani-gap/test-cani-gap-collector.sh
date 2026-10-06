#!/usr/bin/env bash
# collect-cani-gap.sh 로컬 시험. 가짜 kubectl 스텁만 사용하며 클러스터·네트워크에 접근하지 않는다.
# 사용: bash test-cani-gap-collector.sh ./collect-cani-gap.sh
set -u
S="$(cd "$(dirname "${1:?collector 경로}")" && pwd)/$(basename "$1")"
T="$(mktemp -d)"; PASSN=0; FAILN=0
ok()  { PASSN=$((PASSN+1)); echo "PASS: $1"; }
bad() { FAILN=$((FAILN+1)); echo "FAIL: $1"; }
expect_eq() { [ "$2" = "$3" ] && ok "$1" || bad "$1 (기대 '$3', 실제 '$2')"; }

cat > "$T/k.sh" <<'STUB'
#!/usr/bin/env bash
a="$*"
[[ "$1" == version ]] && { [[ "$MODE" == noversion ]] && { echo "conn refused" >&2; exit 1; }; echo stub; exit 0; }
[[ "$1" == get ]] && { [[ "$MODE" == auxfail && "$a" == *clusterroles* ]] && { echo "denied" >&2; exit 1; }; echo obj; exit 0; }
[[ "$a" != *"--as="* ]] && { [[ "$MODE" == preyes ]] && { echo no; exit 1; }; echo yes; exit 0; }
[[ "$a" == *nonexistent* ]] && { echo no; exit 1; }
[[ "$a" == *prometheus-sa* ]] && { echo yes; exit 0; }
if [[ "$a" == *cicd-runner-sa* && "$a" != *other-app* && ( "$a" == *" get deployment/"* || "$a" == *" update deployment/"* || "$a" == *" patch deployment/"* ) ]]; then
  [[ "$MODE" == err  && "$a" == *"get deployment/fds-engine"* ]] && { echo yes; echo boom >&2; exit 2; }
  [[ "$MODE" == slow && "$a" == *"get deployment/fds-engine"* ]] && { sleep 3; echo yes; exit 0; }
  echo yes; exit 0
fi
[[ "$MODE" == mismatch && "$a" == *"create statefulsets"* && "$a" == *cicd-runner-sa* ]] && { echo no; exit 0; }
[[ "$MODE" == fail     && "$a" == *"create jobs"* && "$a" == *cicd-runner-sa* ]] && { echo yes; exit 0; }
[[ "$MODE" == bytes ]] && { printf 'no

'; exit 1; }
echo no; exit 1
STUB
chmod +x "$T/k.sh"

run() {  # name [ENV=VAL...]  -> $T/<name>: RC, 폴더
  local n="$1"; shift; mkdir -p "$T/$n"
  ( cd "$T/$n" && env MODE="$n" KUBECTL="$T/k.sh" CHECK_TIMEOUT_SEC=1 "$@" bash "$S" >out.txt 2>err.txt; echo $? > rc.txt )
  RC="$(cat "$T/$n/rc.txt")"; DIR="$(ls -d "$T/$n"/p1-sec01-cani-gap-v3-*/ "$T/$n"/out/ 2>/dev/null | head -1)"
}
summ() { head -1 "$DIR/99-summary-provisional.txt" 2>/dev/null; }

run ok;       expect_eq "정상 TIER=A: 종료코드 0" "$RC" 0
echo "$(summ)" | grep -q 'EXPECTED=53 GOT=53 TOTAL=55 UNIQUE_IDS=55 PASS=53 FAIL=0 ERROR_OR_TIMEOUT=0 RECORD_ONLY=2 AUX_RC_NONZERO=0' && ok "정상: 53행 PASS, 기록 전용 2행" || bad "정상 요약: $(summ)"
grep -q 'SEAL_STATUS=COMPLETE' "$T/ok"/*.completion.txt && ok "정상: 봉인 COMPLETE" || bad "정상 봉인"
( cd "$DIR" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) && ok "정상: SHA256SUMS 검산 일치" || bad "SHA256SUMS 검산"
[ "$(awk -F'\t' 'NR>1&&$5==""' "$DIR/results.tsv" | wc -l)" = 0 ] && ok "모든 행에 RC 기록" || bad "RC 누락 행"
[ "$(ls "$DIR/raw"/R001.* | wc -l)" = 3 ] && ok "행별 stdout/stderr/rc 원본 보존" || bad "raw 원본"
run okab TIER=AB; DIRAB="$DIR"
echo "$(summ)" | grep -q 'EXPECTED=81 GOT=81 TOTAL=83.*AUX_RC_NONZERO=0' && ok "TIER=AB: 81행 + 기록 전용 2행" || bad "TIER=AB: $(summ)"
run err;      expect_eq "yes + rc2: 종료코드 11" "$RC" 11
grep -qP '\tERROR\tyes\tERROR\t2\t' "$DIR/results.tsv" && ok "yes+rc2 는 ERROR(PASS 아님)" || bad "yes+rc2 판정"
run slow;     expect_eq "timeout: 종료코드 11" "$RC" 11
grep -qP '\tTIMEOUT\t' "$DIR/results.tsv" && ok "timeout 은 TIMEOUT 행(rc124)" || bad "TIMEOUT 행"
run mismatch; expect_eq "no + rc0: 종료코드 11" "$RC" 11
grep -qP '\tERROR\tno\tERROR\t0\t' "$DIR/results.tsv" && ok "no+rc0 은 ERROR" || bad "no+rc0 판정"
run fail;     expect_eq "기대 불일치: 종료코드 10" "$RC" 10
grep -qP '\tFAIL\tno\tyes\t0\t' "$DIR/results.tsv" && ok "기대 no 인데 yes/rc0 은 FAIL" || bad "FAIL 판정"
run noversion; expect_eq "kubectl version 실패여도 사전 점검 결과 유지(폴더 생성 후 진행): 종료코드 0" "$RC" 0
run preyes;   expect_eq "사전 점검(관리자 yes/rc0) 실패: 종료코드 2" "$RC" 2
[ ! -e "$T/preyes"/*.completion.txt ] && ok "사전 점검 실패 시 시험 미시작(completion 없음)" || bad "사전 점검 실패 후 completion 생성"
mkdir -p "$T/wf/out/results.tsv"; ( cd "$T/wf" && OUT="$T/wf/out" KUBECTL="$T/k.sh" bash "$S" >/dev/null 2>&1; echo $? > rc.txt )
expect_eq "TSV 쓰기 실패: 즉시 중단 종료코드 3" "$(cat "$T/wf/rc.txt")" 3
( cd "$T/ok" && TIER=Z KUBECTL="$T/k.sh" bash "$S" >/dev/null 2>&1; echo $? > rc2.txt )
[ "$(cat "$T/ok/rc2.txt")" = 3 ] && ok "잘못된 TIER 는 거부(종료코드 3)" || bad "TIER 거부"

# ---- B1 및 후속 시험 (PR #64 리뷰 반영) ----
mkdir -p "$T/cf/out.completion.txt"; ( cd "$T/cf" && OUT="$T/cf/out" KUBECTL="$T/k.sh" bash "$S" >/dev/null 2>"$T/cf/err.txt"; echo $? > rc.txt )
expect_eq "B1: 완료 기록 쓰기 실패는 비0(종료코드 3)" "$(cat "$T/cf/rc.txt")" 3
grep -q '완료 기록 쓰기 실패' "$T/cf/err.txt" && ok "B1: 완료 기록 실패 사유 출력" || bad "B1 사유"
mkdir -p "$T/fb" "$T/tarfail"; printf '#!/bin/sh\nexit 1\n' > "$T/fb/tar"; chmod +x "$T/fb/tar"
( cd "$T/tarfail" && PATH="$T/fb:$PATH" KUBECTL="$T/k.sh" bash "$S" >out.txt 2>err.txt; echo $? > rc.txt )
expect_eq "봉인(tar) 실패: 종료코드 13" "$(cat "$T/tarfail/rc.txt")" 13
grep -q 'SEAL_STATUS=FAILED' "$T/tarfail"/*.completion.txt && ok "봉인 실패는 completion 에 SEAL_STATUS=FAILED" || bad "SEAL_STATUS"
run auxfail;  expect_eq "보조 수집(clusterroles) 실패: 판정 종료코드는 유지(0)" "$RC" 0
echo "$(summ)" | grep -q 'AUX_RC_NONZERO=1' && ok "보조 수집 비0 RC 가 summary 에 노출" || bad "AUX 노출: $(summ)"
grep -q 'AUX_RC_NONZERO=1' "$T/auxfail"/*.completion.txt && ok "보조 수집 비0 RC 가 completion 에 노출" || bad "AUX completion"
run bytes;    printf 'no\n\n' | cmp -s - "$DIR/raw/R001.stdout" && ok "수신 바이트 보존: stdout 끝의 빈 줄 유지" || bad "바이트 보존"
rm -rf "$T"
echo "합계: PASS=$PASSN FAIL=$FAILN"; [ "$FAILN" = 0 ]
