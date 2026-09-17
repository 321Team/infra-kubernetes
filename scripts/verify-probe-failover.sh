#!/usr/bin/env bash
#
# verify-probe-failover.sh
#
# P1-SEC-01 / SEC-07 (High) 수정 — 2차
#
# 1차 수정(테스트 대상 IP 교정)은 GWONUK 재검토(issuecomment-5644891728)에서
# 추가 결함으로 지적됨:
#   "host nc refused + 컨테이너 nc 없음(rc126) -> deny-flow 전체 종료0, 두 경로 PASS"
#   "Worker 1대 200, 2대 연결실패 -> allow-flow 종료0"
# 즉 "도구가 없어서 실행 자체가 안 된 것"과 "실제로 접속이 차단된 것"을 구분하지 못하고
# 둘 다 PASS로 보고하는 결함, 그리고 일부만 성공해도 전체를 성공으로 보고하는 결함이 있었다.
#
# 2차 수정:
#   - 모든 도구 호출은 실행 전 존재 여부를 먼저 확인하고, 없으면 ERROR로 분리한다.
#   - exit code를 원인별로 해석한다 (0=성공, 1=명령 자체의 정상 실패, 124=timeout,
#     126/127=실행 불가/명령 없음). 126/127은 절대 "차단 성공"으로 보지 않는다.
#   - allow-flow는 Worker별 결과를 개별 기록하고, 전체(3/3) 성공만 PASS로 본다.
#     일부만 성공하면 PARTIAL로 별도 표기하고 exit code를 PASS와 다르게 반환한다.
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
SERVICE_PORT=80   # IF-02A: Service 80 -> Pod 8000 (baseline Interface Register 기준)

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

# curl/nc의 exit code를 성공/실패/도구없음/판정불가로 분류한다.
# echo로 "SUCCESS"/"REFUSED"/"NOTOOL"/"UNKNOWN"을 반환한다 (stdout).
classify_exit_code() {
  local rc="$1"
  case "$rc" in
    0)   echo "SUCCESS" ;;
    1)   echo "REFUSED" ;;   # nc: connection refused, curl: 일반 실패(대상 도달은 했으나 실패)
    124) echo "TIMEOUT" ;;
    126|127) echo "NOTOOL" ;;  # 명령을 찾을 수 없거나 실행 권한 없음 — 정책 차단과 무관
    *)   echo "UNKNOWN" ;;
  esac
}

# ── allow-flow: edge01(.50) → Worker:NodePort 허용 확인 ─────────────
cmd_allow_flow() {
  if ! command -v curl >/dev/null 2>&1; then
    log "ERROR: curl 도구가 이 호스트에 없음 — 시험 자체를 수행할 수 없음 (NOT VERIFIED)"
    exit 3
  fi

  log "Allow 흐름 PASS 확인 대상 Worker: ${WORKER_NODES[*]}"
  local pass_count=0
  local total=${#WORKER_NODES[@]}
  local -a results=()

  for w in "${WORKER_NODES[@]}"; do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "http://${w}:${NODEPORT}/readyz" 2>/dev/null)"
    local rc=$?
    if [[ "$rc" -ne 0 ]]; then
      results+=("${w}: ERROR (curl rc=${rc}, 연결 자체 실패 — 방화벽/네트워크/도구 문제와 정책 차단 구분 불가)")
      continue
    fi
    if [[ "$code" == "200" ]]; then
      results+=("${w}: PASS (HTTP 200)")
      pass_count=$((pass_count + 1))
    else
      results+=("${w}: FAIL (HTTP ${code})")
    fi
  done

  printf '%s\n' "${results[@]}" | while IFS= read -r line; do log "$line"; done

  if [[ "$pass_count" -eq "$total" ]]; then
    log "PASS: ${pass_count}/${total} Worker 전체 성공"
    exit 0
  elif [[ "$pass_count" -gt 0 ]]; then
    log "PARTIAL: ${pass_count}/${total}만 성공 — 전체 인수 기준 미달, PASS로 보고하지 않음"
    exit 1
  else
    log "FAIL: 0/${total} 성공"
    exit 1
  fi
}

# Deep Review 6276ade 지적 #4: nc가 REFUSED/TIMEOUT을 반환했다고 곧바로
# "정책이 차단했다"고 볼 수 없다 — 대상 listener 자체가 죽어있거나 무관한 라우팅
# 문제여도 동일한 결과가 나온다. 이를 구분하려면 같은 실행 맥락에서 "반드시
# 성공해야 하는" 대조군(canary) 접속이 실제로 성공하는지 같이 확인해야 한다.
# canary까지 실패하면 이 결과가 정책 때문인지 더 넓은 장애 때문인지 알 수 없으므로
# PASS가 아니라 ERROR(NOT VERIFIED)로 분리한다.
canary_ok_host() {
  # 외부 Client 관점에서 "반드시 허용돼야 하는" 경로: Worker NodePort.
  # (allow-flow에서 검증하는 것과 동일한 경로 — 이게 막혀 있으면 이 호스트의
  # 일반 연결성 자체가 깨져 있다는 뜻이라 DB 차단 결과를 신뢰할 수 없다.)
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 \
    "http://${WORKER_NODES[0]}:${NODEPORT}/readyz" 2>/dev/null)"
  [[ "$code" == "200" ]]
}

canary_ok_pod() {
  # fds-engine Pod 관점에서 "반드시 허용돼야 하는" 경로: 자신의 Service.
  # 이것조차 실패하면 Pod의 클러스터 네트워킹/DNS 자체가 문제라는 뜻이므로
  # DB 차단 결과를 정책 때문이라고 단정할 수 없다.
  local code
  code="$(kubectl exec -n fds-app deployment/fds-engine -- \
    sh -c "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 http://${SERVICE_NAME}.${SERVICE_NS}.svc.cluster.local:${SERVICE_PORT}/readyz" \
    2>/dev/null)"
  [[ "$code" == "200" ]]
}

# ── deny-flow: 일반 Client → DB 직접 접근 차단 확인 (Host Firewall/NetworkPolicy 경계) ──
cmd_deny_flow() {
  local overall_rc=0

  log "Deny 흐름 FAIL 확인: 일반 Client -> ${DB_HOST}:${DB_PORT} (직접 접근이므로 반드시 실패해야 정상)"
  if ! command -v nc >/dev/null 2>&1; then
    log "ERROR: nc 도구가 이 호스트에 없음 — 차단 여부를 확인할 수 없음 (NOT VERIFIED, PASS 아님)"
    overall_rc=3
  else
    timeout 5 nc -zv -w 3 "$DB_HOST" "$DB_PORT" >/tmp/verify-deny-host.$$ 2>&1
    local rc=$?
    local class
    class="$(classify_exit_code "$rc")"
    case "$class" in
      SUCCESS)
        log "FAIL: DB 직접 접근이 성공함 — Host Firewall/NetworkPolicy 미적용 의심"
        overall_rc=1
        ;;
      REFUSED|TIMEOUT)
        if command -v curl >/dev/null 2>&1 && canary_ok_host; then
          log "PASS: DB 직접 접근 차단 확인 (${class}, rc=${rc}); 대조군(Worker NodePort) 접속은 정상 — 이 호스트의 일반 연결성은 살아있음"
        else
          log "ERROR: DB 접속이 ${class}(rc=${rc})지만 대조군(Worker NodePort) 접속도 실패 — 이 호스트 자체의 네트워크 문제와 정책 차단을 구분할 수 없음 (NOT VERIFIED)"
          overall_rc=3
        fi
        ;;
      NOTOOL)
        log "ERROR: nc 실행 불가(rc=${rc}) — 도구 문제이지 정책 차단이 아님 (NOT VERIFIED)"
        overall_rc=3
        ;;
      *)
        log "ERROR: 알 수 없는 종료코드(rc=${rc}) — 원인 확인 필요 (NOT VERIFIED)"
        cat /tmp/verify-deny-host.$$ >&2
        overall_rc=3
        ;;
    esac
    rm -f /tmp/verify-deny-host.$$
  fi

  log "fds-engine Pod 내부 -> DB 직접 접근 차단 확인 (egress 미허용, K8s Control Plane에서 실행)"
  if ! command -v kubectl >/dev/null 2>&1; then
    log "ERROR: kubectl 없음 — Pod 내부 시험을 수행할 수 없음 (NOT VERIFIED)"
    [[ "$overall_rc" -eq 0 ]] && overall_rc=3
  else
    # 컨테이너 안에 nc가 있는지 먼저 확인한다. 없으면(rc126/127) "차단 성공"이 아니라
    # "확인 불가"로 분리해야 한다 — 이게 GWONUK이 지적한 핵심 결함이다.
    if ! kubectl exec -n fds-app deployment/fds-engine -- sh -c 'command -v nc' >/dev/null 2>&1; then
      log "ERROR: fds-engine 컨테이너 내부에 nc가 없음 — 차단 여부를 확인할 수 없음 (NOT VERIFIED, PASS 아님)"
      [[ "$overall_rc" -eq 0 ]] && overall_rc=3
    else
      kubectl exec -n fds-app deployment/fds-engine -- timeout 5 nc -zv -w 3 "$DB_HOST" "$DB_PORT" >/tmp/verify-deny-pod.$$ 2>&1
      local rc2=$?
      local class2
      class2="$(classify_exit_code "$rc2")"
      case "$class2" in
        SUCCESS)
          log "FAIL: fds-engine에서 DB 직접 접근이 성공함 — NetworkPolicy egress 확인 필요"
          overall_rc=1
          ;;
        REFUSED|TIMEOUT)
          if kubectl exec -n fds-app deployment/fds-engine -- sh -c 'command -v curl' >/dev/null 2>&1 && canary_ok_pod; then
            log "PASS: fds-engine -> DB 직접 접근 차단 확인 (${class2}, rc=${rc2}); 대조군(자신의 Service) 접속은 정상 — Pod 네트워킹 자체는 살아있음"
          else
            log "ERROR: DB 접속이 ${class2}(rc=${rc2})지만 대조군(자신의 Service) 접속도 실패/확인 불가 — Pod 네트워킹 문제와 정책 차단을 구분할 수 없음 (NOT VERIFIED)"
            [[ "$overall_rc" -eq 0 ]] && overall_rc=3
          fi
          ;;
        NOTOOL)
          log "ERROR: 컨테이너 내 nc 실행 불가(rc=${rc2}, kubectl exec 자체 오류 포함) — 정책 차단과 무관 (NOT VERIFIED)"
          [[ "$overall_rc" -eq 0 ]] && overall_rc=3
          ;;
        *)
          log "ERROR: 알 수 없는 종료코드(rc=${rc2}) — 원인 확인 필요 (NOT VERIFIED)"
          cat /tmp/verify-deny-pod.$$ >&2
          [[ "$overall_rc" -eq 0 ]] && overall_rc=3
          ;;
      esac
      rm -f /tmp/verify-deny-pod.$$
    fi
  fi

  exit "$overall_rc"
}

# ── probe-failover: DB 장애 주입 -> 503 -> Endpoint 제외 -> 복구 -> 재편입 ──
cmd_probe_failover() {
  local svc_url="http://${SERVICE_NAME}.${SERVICE_NS}.svc.cluster.local:${SERVICE_PORT}/readyz"

  if ! command -v curl >/dev/null 2>&1; then
    log "ERROR: curl 없음 — 시험 수행 불가 (NOT VERIFIED)"
    exit 3
  fi
  # Deep Review 6276ade 지적 #4: 예전 코드는 kubectl이 없으면 "Endpoint 확인 불가"라고만
  # 로그를 찍고 넘어갔는데, Endpoint 제외 여부가 이 시험의 핵심 증거이므로 kubectl 없이는
  # 시험 자체를 진행할 수 없다.
  if ! command -v kubectl >/dev/null 2>&1; then
    log "ERROR: kubectl 없음 — Endpoint 제외 여부를 실제로 확인할 수 없어 이 시험 전체를 신뢰할 수 없음 (NOT VERIFIED)"
    exit 3
  fi

  log "1) 정상 상태 확인: $svc_url"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" 2>/dev/null)"
  local rc=$?
  if [[ "$rc" -ne 0 ]]; then
    log "ERROR: 사전 상태 확인 자체가 실패(curl rc=${rc}) — 서비스 접근 경로 확인 필요 (NOT VERIFIED)"
    exit 3
  fi
  [[ "$code" == "200" ]] || { log "FAIL: 사전 상태가 이미 비정상 (HTTP $code) — 장애 주입 전 정상화 필요"; exit 1; }
  log "PASS: 사전 상태 정상 (HTTP 200)"

  local endpoints_before
  endpoints_before="$(kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)"
  if [[ -z "$endpoints_before" ]]; then
    log "ERROR: 장애 주입 전인데 Endpoint에 준비된 주소가 하나도 없음 — 기준선 자체가 비정상 (NOT VERIFIED)"
    exit 3
  fi
  log "장애 주입 전 Endpoint 기준선: ${endpoints_before}"

  log "2) DB 강제 차단은 db01 자체 방화벽 또는 D-NET-01 관리 호스트에서 수동으로 수행하십시오."
  log "   (이 스크립트는 자동으로 방화벽을 조작하지 않습니다 — 관할 티켓 경계 유지)"
  read -r -p "DB 차단을 적용하셨으면 Enter, 건너뛰려면 Ctrl+C: " _

  # Deep Review 6276ade 지적 #4: 예전 코드는 "kubectl get endpoints"를 출력만 하고
  # 실제로 Pod가 빠졌는지 assert하지 않았고, readyz != 503이면 WARN만 찍고 넘어가서
  # 장애 단계 확인이 사실상 결과에 영향을 못 줬다 (뒤에서 복구만 200이면 그냥 PASS로
  # 새어나갈 수 있었음). 이제 503 여부와 Endpoint 실제 변화를 함께 판정 근거로 삼고,
  # 둘 다 확인되지 않으면 이 단계에서 FAIL로 즉시 중단한다.
  log "3) Readiness 실패 및 Service Endpoint 제외 확인"
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" 2>/dev/null)"
  local endpoints_during
  endpoints_during="$(kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)"
  kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o wide

  local injection_verified=0
  if [[ "$code" == "503" ]]; then
    log "PASS: readyz 503 확인"
    injection_verified=1
  else
    log "WARN: 기대값 503이 아니라 HTTP $code — 아래 Endpoint 실제 제외 여부로 판정을 이어간다."
  fi

  if [[ -z "$endpoints_during" ]]; then
    log "PASS: 장애 중 Endpoint에서 모든 주소가 실제로 제외됨"
    injection_verified=1
  elif [[ "$endpoints_during" == "$endpoints_before" ]]; then
    log "FAIL: 장애 중에도 Endpoint 주소가 장애 전과 동일함(${endpoints_during}) — Pod가 실제로 제외되지 않음"
  else
    log "WARN: Endpoint가 일부만 바뀜 (전=${endpoints_before} / 중=${endpoints_during}) — 완전 제외로 보지 않음"
  fi

  if [[ "$injection_verified" -ne 1 ]]; then
    log "FAIL: 장애 주입 단계에서 readyz 503과 Endpoint 완전 제외 중 어느 것도 확인되지 않음 — 복구 결과와 무관하게 FAIL"
    exit 1
  fi

  log "4) DB 복구 후 Endpoint 재편입 확인"
  read -r -p "DB 차단을 해제하셨으면 Enter: " _
  sleep 15  # readinessProbe periodSeconds 대기
  local endpoints_after
  endpoints_after="$(kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)"
  kubectl get endpoints "$SERVICE_NAME" -n "$SERVICE_NS" -o wide
  code="$(curl -s -o /dev/null -w '%{http_code}' "$svc_url" 2>/dev/null)"
  if [[ "$code" == "200" && -n "$endpoints_after" ]]; then
    log "PASS: 복구 후 재편입 확인 (HTTP 200, Endpoint=${endpoints_after})"
  else
    log "FAIL: 복구 후에도 HTTP $code 또는 Endpoint 미복구(${endpoints_after:-없음}) — readinessProbe 설정/DB 복구 여부 재확인 필요"
    exit 1
  fi
}

case "${1:-}" in
  allow-flow)      cmd_allow_flow ;;
  deny-flow)       cmd_deny_flow ;;
  probe-failover)  cmd_probe_failover ;;
  *)
    echo "사용법: $0 {allow-flow|deny-flow|probe-failover}" >&2
    echo "exit code: 0=PASS, 1=FAIL, 2=PARTIAL, 3=ERROR/NOT VERIFIED" >&2
    exit 1
    ;;
esac
