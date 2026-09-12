#!/usr/bin/env bash
#
# verify-probe-failover.sh
#
# P1-SEC-01 / SEC-07 (High) 수정 — 테스트 대상 IP/포트 오류 교정 및
# "도구 부재/timeout"을 정책 차단 PASS로 오판하지 않도록 근거를 분리 기록.
#
# 대응 리뷰: fds-project-baseline#19 (issuecomment-5627308483), SEC-07
#   - 기존 `curl http://10.1.93.109:30080/readyz`는 실제 Worker NodePort 대상이 아님
#     (.109는 API LB, Worker는 .105/.107/.108) → 아래 WORKER_NODES로 교체
#   - Allow 시험은 edge01(.50)에서, Deny 시험은 일반 클라이언트에서 실행해야
#     Host Firewall/NetworkPolicy 판정 경계를 구분할 수 있다.
#   - DB 강제 차단 → 503 → Endpoint 제외 → 복구 → Endpoint 재편입까지
#     "curl 단발 호출"이 아니라 각 단계를 순서대로 확인한다.
#
# 실행 위치:
#   - allow/deny 플로우 시험(§1,§2): 외부 테스트 호스트(edge01 또는 동일 대역 관리 워크스테이션)
#   - probe failover 시험(§3): K8s Control Plane (Service ClusterIP 접근 가능한 호스트)
#
# 사용법: ./verify-probe-failover.sh {allow-flow|deny-flow|probe-failover}

set -uo pipefail

WORKER_NODES=("10.1.93.105" "10.1.93.107" "10.1.93.108")  # Worker01~03 실제 IP. 인벤토리 확정 후 반영.
NODEPORT=30080
DB_HOST="10.1.93.55"
DB_PORT=5432
SERVICE_NS="fds-app"
SERVICE_NAME="transaction-api"
SERVICE_PORT=8000   # 원본 문서상 §1.2/3.7 Service:80이 아니라 8000이 실제 컨테이너 포트임에 주의

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

# ── allow-flow: edge01(.50) → Worker:NodePort 허용 확인 ─────────────
cmd_allow_flow() {
  log "Allow 흐름 PASS 확인 대상 Worker: ${WORKER_NODES[*]}"
  local any_pass=0
  for w in "${WORKER_NODES[@]}"; do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "http://${w}:${NODEPORT}/readyz" || echo "000")"
    if [[ "$code" == "200" ]]; then
      log "PASS: ${w}:${NODEPORT}/readyz -> HTTP $code"
      any_pass=1
    else
      log "WARN: ${w}:${NODEPORT}/readyz -> HTTP $code (이 노드는 실패 — 다른 노드 결과와 함께 판단)"
    fi
  done
  if [[ "$any_pass" -eq 0 ]]; then
    log "FAIL: 3개 Worker 전부 200 응답 없음 — 도구 부재/네트워크 문제와 정책 차단을 구분하려면"
    log "      먼저 'curl -v'로 TCP 연결 자체가 되는지, edge01(.50)에서 실행 중인지 확인할 것."
    exit 1
  fi
}

# ── deny-flow: 일반 Client → DB 직접 접근 차단 확인 (Host Firewall/NetworkPolicy 경계) ──
cmd_deny_flow() {
  log "Deny 흐름 FAIL 확인: 일반 Client -> ${DB_HOST}:${DB_PORT} (직접 접근이므로 반드시 실패해야 정상)"
  if command -v nc >/dev/null 2>&1; then
    if nc -zv -w 3 "$DB_HOST" "$DB_PORT" 2>&1; then
      log "FAIL: DB 직접 접근이 성공함 — Host Firewall/NetworkPolicy 미적용 의심"
      exit 1
    else
      log "PASS: DB 직접 접근 차단 확인 (timeout/refused)"
    fi
  else
    log "WARN: nc 도구 없음 — 이 결과를 '정책 차단 PASS'로 기록하지 말 것. 도구 설치 후 재시험 필요."
    exit 2
  fi

  log "fds-engine Pod 내부 -> DB 직접 접근 차단 확인 (egress 미허용, K8s Control Plane에서 실행)"
  if kubectl exec -n fds-app deployment/fds-engine -- nc -zv -w 3 "$DB_HOST" "$DB_PORT" 2>&1; then
    log "FAIL: fds-engine에서 DB 직접 접근이 성공함 — NetworkPolicy egress 확인 필요"
    exit 1
  else
    log "PASS: fds-engine -> DB 직접 접근 차단 확인"
  fi
}

# ── probe-failover: DB 장애 주입 -> 503 -> Endpoint 제외 -> 복구 -> 재편입 ──
cmd_probe_failover() {
  local svc_url="http://${SERVICE_NAME}.${SERVICE_NS}.svc.cluster.local:${SERVICE_PORT}/readyz"

  log "1) 정상 상태 확인: $svc_url"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" || echo "000")"
  [[ "$code" == "200" ]] || { log "FAIL: 사전 상태가 이미 비정상 (HTTP $code) — 장애 주입 전 정상화 필요"; exit 1; }
  log "PASS: 사전 상태 정상 (HTTP 200)"

  log "2) DB 강제 차단은 db01 자체 방화벽 또는 D-NET-01 관리 호스트에서 수동으로 수행하십시오."
  log "   (이 스크립트는 자동으로 방화벽을 조작하지 않습니다 — 관할 티켓 경계 유지)"
  read -r -p "DB 차단을 적용하셨으면 Enter, 건너뛰려면 Ctrl+C: " _

  log "3) Readiness 실패 및 Service Endpoint 제외 확인"
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" || echo "000")"
  if [[ "$code" != "503" ]]; then
    log "WARN: 기대값 503이 아니라 HTTP $code — 모든 Endpoint가 제외될 때만 503이 강제되는 건 아니므로,"
    log "      Pod 개수·readinessProbe 주기를 함께 확인. 이 결과만으로 FAIL 단정하지 않음."
  else
    log "PASS: readyz 503 확인"
  fi
  kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o wide

  log "4) DB 복구 후 Endpoint 재편입 확인"
  read -r -p "DB 차단을 해제하셨으면 Enter: " _
  sleep 15  # readinessProbe periodSeconds 대기
  kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o wide
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" || echo "000")"
  if [[ "$code" == "200" ]]; then
    log "PASS: 복구 후 재편입 확인 (HTTP 200)"
  else
    log "FAIL: 복구 후에도 HTTP $code — readinessProbe 설정/DB 복구 여부 재확인 필요"
    exit 1
  fi
}

case "${1:-}" in
  allow-flow)      cmd_allow_flow ;;
  deny-flow)       cmd_deny_flow ;;
  probe-failover)  cmd_probe_failover ;;
  *)
    echo "사용법: $0 {allow-flow|deny-flow|probe-failover}" >&2
    exit 1
    ;;
esac
