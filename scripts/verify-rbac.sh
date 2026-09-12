#!/usr/bin/env bash
#
# verify-rbac.sh
#
# P1-SEC-01 / SEC-08 (High) 수정 — kubectl auth can-i의 TYPE/NAME 구문과
# subresource(nodes/metrics, nodes/proxy, pods/exec 등) 구문 혼동 교정.
#
# 대응 리뷰: fds-project-baseline#19 (issuecomment-5627308483), SEC-08
#   - `kubectl auth can-i get nodes/metrics` 형태가 아니라
#     `kubectl auth can-i get nodes --subresource=metrics` 형태가 정확한 구문.
#   - 예상되는 거부(no)의 non-zero exit code를 정상 기록으로 취급한다
#     (실패로 간주해서 set -e로 스크립트를 죽이지 않는다).
#
# 실행 위치: K8s Control Plane (kubectl 접속 가능한 관리 호스트)
#
# 사용법: ./verify-rbac.sh [출력파일.tsv]

set -uo pipefail  # -e는 의도적으로 뺀다 (거부=no는 non-zero exit이며 정상 케이스)

OUT="${1:-rbac-verification-$(date +%Y%m%dT%H%M%SZ).tsv}"
FAIL_COUNT=0

printf 'service_account\tcheck\texpect\tactual\tresult\n' > "$OUT"

# args: sa(namespace:name) description expect(yes|no) -- kubectl-auth-can-i-args...
check() {
  local sa="$1" desc="$2" expect="$3"; shift 3
  local actual
  if kubectl auth can-i "$@" --as="system:serviceaccount:${sa}" >/dev/null 2>&1; then
    actual="yes"
  else
    actual="no"
  fi

  local result="PASS"
  if [[ "$actual" != "$expect" ]]; then
    result="FAIL"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  printf '%s\t%s\t%s\t%s\t%s\n' "$sa" "$desc" "$expect" "$actual" "$result" | tee -a "$OUT" >&2
}

echo "== kube-state-metrics-sa ==" >&2
check "monitoring-api:kube-state-metrics-sa" "list pods -A" yes list pods -A
check "monitoring-api:kube-state-metrics-sa" "delete pods -A" no delete pods -A

echo "== prometheus-sa ==" >&2
# 올바른 subresource 구문: --subresource=<sub>, TYPE/NAME으로 붙여쓰지 않는다.
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
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "FAIL: $FAIL_COUNT건 기대값과 불일치 — Evidence로 그대로 보관하고 원인 분석 필요" >&2
  exit 1
fi
echo "PASS: 전체 항목 기대값과 일치" >&2
