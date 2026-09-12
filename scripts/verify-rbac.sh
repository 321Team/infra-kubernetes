#!/usr/bin/env bash
#
# verify-rbac.sh
#
# P1-SEC-01 / SEC-08 (High) 수정 — 2차
#
# 1차 수정(구문 --subresource 교정)은 GWONUK 재검토(issuecomment-5644891728)에서
# 추가 결함으로 지적됨:
#   "RBAC 긍정 3건 yes, 부정 13건 API 통신오류 rc2 -> 전체 PASS"
#   즉 API 서버에 접속조차 안 되는 상황(kubeconfig 오류, 네트워크 단절 등)을
#   "no"(권한 없음, 정상)로 착각해서 전부 PASS 처리하는 결함이 있었다.
#
# 2차 수정: exit code가 아니라 stdout 문자열("yes"/"no")을 1차 판정 기준으로 쓰고,
# stdout이 정확히 yes/no가 아니면 무조건 ERROR로 분리한다. PASS/FAIL/ERROR
# 3단계 결과를 명확히 구분하며, ERROR가 하나라도 있으면 전체를 PASS로 보고하지 않는다.
#
# 실행 위치: K8s Control Plane (kubectl 접속 가능한 관리 호스트)
#
# 사용법: ./verify-rbac.sh [출력파일.tsv]

set -uo pipefail

OUT="${1:-rbac-verification-$(date +%Y%m%dT%H%M%SZ).tsv}"
PASS_COUNT=0
FAIL_COUNT=0
ERROR_COUNT=0

printf 'service_account\tcheck\texpect\tactual\tresult\tstderr_summary\n' > "$OUT"

# args: sa(namespace:name) description expect(yes|no) -- kubectl-auth-can-i-args...
check() {
  local sa="$1" desc="$2" expect="$3"; shift 3
  local errfile out rc actual result err_summary

  errfile="$(mktemp)"
  # stdout만 캡처(kubectl auth can-i는 정상 시 정확히 "yes" 또는 "no" 한 줄만 출력),
  # stderr는 별도 파일로 분리해 오류 원인을 남긴다.
  out="$(kubectl auth can-i "$@" --as="system:serviceaccount:${sa}" 2>"$errfile")"
  rc=$?
  err_summary="$(head -c 200 "$errfile" | tr '\n\t' '  ')"
  rm -f "$errfile"

  # kubectl auth can-i의 exit code는 yes=0, no=1이지만, API 서버 접속 실패·인증
  # 오류 등도 nonzero를 반환할 수 있어 exit code만으로는 no와 구분되지 않는다.
  # stdout이 정확히 "yes" 또는 "no"인 경우에만 그 값을 신뢰한다.
  if [[ "$out" == "yes" || "$out" == "no" ]]; then
    actual="$out"
    if [[ "$actual" == "$expect" ]]; then
      result="PASS"
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      result="FAIL"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  else
    actual="ERROR"
    result="ERROR"
    ERROR_COUNT=$((ERROR_COUNT + 1))
    [[ -z "$err_summary" ]] && err_summary="(stdout='${out}' rc=${rc}, yes/no 아님)"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sa" "$desc" "$expect" "$actual" "$result" "$err_summary" \
    | tee -a "$OUT" >&2
}

echo "== kube-state-metrics-sa ==" >&2
check "monitoring-api:kube-state-metrics-sa" "list pods -A" yes list pods -A
check "monitoring-api:kube-state-metrics-sa" "delete pods -A" no delete pods -A

echo "== prometheus-sa ==" >&2
check "monitoring-api:prometheus-sa" "get nodes --subresource=metrics" yes \
  get nodes --subresource=metrics
check "monitoring-api:prometheus-sa" "get nodes --subresource=proxy" no \
  get nodes --subresource=proxy
check "monitoring-api:prometheus-sa" "get secrets -A" no \
  get secrets -A

echo "== transaction-api-sa / fds-engine-sa (Negative) ==" >&2
check "fds-app:transaction-api-sa" "list pods -n fds-app" no -n fds-app list pods
check "fds-app:fds-engine-sa" "list pods -n fds-app" no -n fds-app list pods

echo "== cicd-runner-sa ==" >&2
check "ci-cd:cicd-runner-sa" "create deployments -n fds-app" no \
  -n fds-app create deployments
check "ci-cd:cicd-runner-sa" "delete deployments -n fds-app" no \
  -n fds-app delete deployments
check "ci-cd:cicd-runner-sa" "update deployment/transaction-api -n fds-app" yes \
  -n fds-app update deployment/transaction-api
check "ci-cd:cicd-runner-sa" "update deployment/other-app -n fds-app (resourceNames 제한 확인)" no \
  -n fds-app update deployment/other-app
check "ci-cd:cicd-runner-sa" "get secrets -n fds-app" no \
  -n fds-app get secrets
check "ci-cd:cicd-runner-sa" "list secrets -n fds-app" no \
  -n fds-app list secrets
check "ci-cd:cicd-runner-sa" "create pods --subresource=exec -n fds-app (Worker 직접 접근 금지 확인)" no \
  -n fds-app create pods --subresource=exec
check "ci-cd:cicd-runner-sa" "create rolebindings -n fds-app (RBAC 자체 변경 금지 확인)" no \
  -n fds-app create rolebindings
check "ci-cd:cicd-runner-sa" "'*' '*' -A (Cluster Admin 미보유 확인)" no \
  '*' '*' -A

echo "----" >&2
echo "결과 파일: $OUT" >&2
echo "PASS=$PASS_COUNT FAIL=$FAIL_COUNT ERROR=$ERROR_COUNT" >&2

# ERROR가 하나라도 있으면 "확인 완료"로 볼 수 없으므로 FAIL과 구분된 exit code(2)로 보고한다.
if [[ "$ERROR_COUNT" -gt 0 ]]; then
  echo "NOT VERIFIED: ${ERROR_COUNT}건이 API 통신 오류 등으로 yes/no를 확인하지 못함 — 이 항목들은 PASS/FAIL로 보고하지 않음" >&2
  exit 2
fi
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "FAIL: ${FAIL_COUNT}건 기대값과 불일치" >&2
  exit 1
fi
echo "PASS: 전체 ${PASS_COUNT}건 기대값과 일치, ERROR 없음" >&2
