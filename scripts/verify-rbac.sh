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
# Deep Review 6276ade 지적 #5: API 호출에 timeout이 없으면 API 서버가 응답을
# 멈췄을 때 스크립트 전체가 무한정 걸린다.
CHECK_TIMEOUT_SEC="${CHECK_TIMEOUT_SEC:-10}"

die() { echo "ERROR: $*" >&2; exit 3; }

printf 'service_account\tcheck\texpect\tactual\tresult\tstderr_summary\n' > "$OUT" \
  || die "TSV 결과 파일 초기화 실패: $OUT"

# args: sa(namespace:name) description expect(yes|no) -- kubectl-auth-can-i-args...
check() {
  local sa="$1" desc="$2" expect="$3"; shift 3
  local errfile out rc actual result err_summary

  errfile="$(mktemp)"
  # stdout만 캡처(kubectl auth can-i는 정상 시 정확히 "yes" 또는 "no" 한 줄만 출력),
  # stderr는 별도 파일로 분리해 오류 원인을 남긴다. timeout으로 API 무응답 시
  # 무한 대기 대신 rc=124로 즉시 실패시킨다.
  out="$(timeout "${CHECK_TIMEOUT_SEC}s" kubectl auth can-i "$@" --as="system:serviceaccount:${sa}" 2>"$errfile")"
  rc=$?
  err_summary="$(head -c 200 "$errfile" | tr '\n\t' '  ')"
  rm -f "$errfile"

  # Deep Review 6276ade 지적 #5: stdout이 yes/no라는 이유만으로 exit code와
  # 모순돼도 그대로 신뢰하면 안 된다. kubectl auth can-i는 yes=rc0, no=rc1이
  # 정상 쌍이므로, stdout·rc 쌍이 이 조합이 아니면 ERROR로 분리한다
  # (예: stdout="no"인데 rc=0 같은 비정상 조합은 결과를 신뢰할 수 없음).
  if { [[ "$out" == "yes" && "$rc" -eq 0 ]] || [[ "$out" == "no" && "$rc" -eq 1 ]]; }; then
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
    if [[ "$out" != "yes" && "$out" != "no" ]]; then
      [[ -z "$err_summary" ]] && err_summary="(stdout='${out}' rc=${rc}, yes/no 아님)"
    else
      err_summary="stdout='${out}' rc=${rc} 불일치 (yes=rc0/no=rc1이어야 함)${err_summary:+ / ${err_summary}}"
    fi
  fi

  # Deep Review 6276ade 지적 #5: TSV 쓰기가 실패해도 무시하고 계속 진행하면
  # 검증 결과가 조용히 유실될 수 있으므로, 쓰기 실패 시 즉시 중단한다.
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sa" "$desc" "$expect" "$actual" "$result" "$err_summary" \
    | tee -a "$OUT" >&2 \
    || die "TSV 파일 쓰기 실패 ($OUT) — 이 시점까지의 결과 기록을 신뢰할 수 없음, 즉시 중단"
}

# P1-MON-01 AC 7 / baseline §8: Prometheus·kube-state-metrics 모두 "쓰기 Verb 없음 + Secret
# 읽기 없음"을 확인해야 한다. 두 SA에 같은 부정 검사 집합을 적용한다.
negative_read_only_checks() {
  local sa="$1"
  check "$sa" "get secrets -A" no get secrets -A
  check "$sa" "list secrets -A" no list secrets -A
  check "$sa" "get nodes --subresource=proxy" no get nodes --subresource=proxy
  check "$sa" "delete pods -A" no delete pods -A
  check "$sa" "create pods -A" no create pods -A
  check "$sa" "patch deployments -A" no patch deployments.apps -A
  check "$sa" "patch nodes" no patch nodes
}

echo "== kube-state-metrics-sa ==" >&2
check "monitoring-api:kube-state-metrics-sa" "list pods -A" yes list pods -A
check "monitoring-api:kube-state-metrics-sa" "list deployments -A" yes list deployments.apps -A
negative_read_only_checks "monitoring-api:kube-state-metrics-sa"

echo "== prometheus-sa ==" >&2
check "monitoring-api:prometheus-sa" "list pods -A (Service Discovery)" yes list pods -A
check "monitoring-api:prometheus-sa" "get nodes --subresource=metrics" yes \
  get nodes --subresource=metrics
check "monitoring-api:prometheus-sa" "get /metrics (non-resource)" yes get /metrics
negative_read_only_checks "monitoring-api:prometheus-sa"

echo "== API를 쓰지 않는 SA (Negative) ==" >&2
check "monitoring-api:grafana-sa" "list pods -n monitoring-api" no -n monitoring-api list pods
check "monitoring-api:alertmanager-sa" "list pods -n monitoring-api" no -n monitoring-api list pods
check "monitoring-node:node-exporter-sa" "list pods -A" no list pods -A

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
