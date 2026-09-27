#!/usr/bin/env bash
#
# verify-probe-failover.sh
#
# P1-SEC-01 / SEC-07 (High) 수정 — 3차 (PR #7 c9ec46d 리뷰 반영)
#
# 2차까지의 수정(도구 부재·일부 성공을 PASS로 보지 않음, 대조군 도입)은 유지한다.
# 3차에서 고친 것:
#   - exit code 계약을 문서와 일치: 0=PASS, 1=FAIL, 2=PARTIAL, 3=ERROR/NOT VERIFIED.
#   - Pod 내부 deny 시험: fds-engine 컨테이너에는 nc/curl이 보장되지 않고, 이 PR의
#     NetworkPolicy상 fds-engine egress는 DNS만 허용이라 예전 대조군(transaction-api
#     Service HTTP)은 항상 실패했다. 이미지에 있는 python3로 TCP 시도와 DNS 대조군을 수행한다.
#     NetworkPolicy 차단은 DROP이므로 TIMEOUT만 차단 후보로 본다. REFUSED는 패킷이
#     db01까지 도달했다는 뜻이라 정책 차단 증거가 아니다.
#   - probe-failover: CP 호스트는 cluster.local 이름을 풀지 못하고, transaction-api
#     ingress는 edge01/Prometheus만 허용한다. HTTP 대신 Pod Ready 조건과 EndpointSlice
#     ready 주소를 제한 시간 동안 폴링해 판정한다(readiness 실패 감지에 최대
#     periodSeconds×failureThreshold가 걸리므로 즉시 1회 조회로 판정하지 않는다).
#
# 실행 위치:
#   - allow-flow / deny-flow 호스트 부분: 외부 테스트 호스트(edge01 또는 동일 대역 관리 워크스테이션)
#   - deny-flow Pod 부분 / probe-failover: kubectl 접속 가능한 관리 호스트
#
# 사용법: ./verify-probe-failover.sh {allow-flow|deny-flow|probe-failover}
# exit code: 0=PASS, 1=FAIL, 2=PARTIAL, 3=ERROR/NOT VERIFIED

set -uo pipefail

WORKER_NODES=("10.1.93.105" "10.1.93.107" "10.1.93.108")
NODEPORT=30080
DB_HOST="10.1.93.55"
DB_PORT=5432
SERVICE_NS="fds-app"
SERVICE_NAME="transaction-api"
DENY_POD_DEPLOY="deployment/fds-engine"
WAIT_SEC="${WAIT_SEC:-90}"      # readiness 실패/복구 감지 대기 상한
POLL_SEC="${POLL_SEC:-5}"

EXIT_PASS=0; EXIT_FAIL=1; EXIT_PARTIAL=2; EXIT_ERROR=3

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

# nc exit code 분류 (호스트 경로 전용)
classify_exit_code() {
  case "$1" in
    0)   echo "SUCCESS" ;;
    1)   echo "REFUSED" ;;
    124) echo "TIMEOUT" ;;
    126|127) echo "NOTOOL" ;;
    *)   echo "UNKNOWN" ;;
  esac
}

# ── allow-flow: edge01(.50) → Worker:NodePort 허용 확인 ─────────────
cmd_allow_flow() {
  command -v curl >/dev/null 2>&1 || { log "ERROR: curl 없음 — 시험 수행 불가 (NOT VERIFIED)"; exit "$EXIT_ERROR"; }

  local pass=0 fail=0 err=0 total=${#WORKER_NODES[@]} w code rc
  for w in "${WORKER_NODES[@]}"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "http://${w}:${NODEPORT}/readyz" 2>/dev/null)"
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
      log "${w}: ERROR (curl rc=${rc}, 연결 실패 — 정책/네트워크/대상 문제 구분 불가)"
      err=$((err + 1))
    elif [[ "$code" == "200" ]]; then
      log "${w}: PASS (HTTP 200)"; pass=$((pass + 1))
    else
      log "${w}: FAIL (HTTP ${code})"; fail=$((fail + 1))
    fi
  done

  log "결과: PASS=${pass} FAIL=${fail} ERROR=${err} / ${total}"
  if [[ "$pass" -eq "$total" ]]; then log "PASS: ${total}/${total}"; exit "$EXIT_PASS"; fi
  if [[ "$err" -gt 0 && "$pass" -eq 0 && "$fail" -eq 0 ]]; then log "NOT VERIFIED"; exit "$EXIT_ERROR"; fi
  if [[ "$pass" -gt 0 ]]; then log "PARTIAL: ${pass}/${total} — 전체 인수 기준 미달"; exit "$EXIT_PARTIAL"; fi
  log "FAIL: 0/${total}"; exit "$EXIT_FAIL"
}

canary_ok_host() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 \
    "http://${WORKER_NODES[0]}:${NODEPORT}/readyz" 2>/dev/null)"
  [[ "$code" == "200" ]]
}

# fds-engine 컨테이너에서 python3로 실행하는 점검. 출력: 한 단어
#   dns    : kubernetes.default.svc.cluster.local 이름 해석 → OK / FAIL
#   tcp    : DB_HOST:DB_PORT 3초 연결 → CONNECTED / REFUSED / TIMEOUT / ERROR:<type>
pod_probe() {
  local mode="$1"
  kubectl exec -n "$SERVICE_NS" "$DENY_POD_DEPLOY" -- python3 -I -c '
import socket, sys
mode, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
if mode == "dns":
    try:
        socket.getaddrinfo("kubernetes.default.svc.cluster.local", 443)
        print("OK")
    except Exception:
        print("FAIL")
    sys.exit(0)
s = socket.socket(); s.settimeout(3)
try:
    s.connect((host, port)); print("CONNECTED")
except ConnectionRefusedError:
    print("REFUSED")
except (socket.timeout, TimeoutError):
    print("TIMEOUT")
except Exception as e:
    print("ERROR:" + type(e).__name__)
finally:
    s.close()
' "$mode" "$DB_HOST" "$DB_PORT" 2>/dev/null
}

# ── deny-flow: 일반 Client/Pod → DB 직접 접근 차단 확인 ──────────────
cmd_deny_flow() {
  # 실제 접근 성공(FAIL)이 하나라도 있으면 FAIL, 없고 판정 불가가 있으면 ERROR.
  local has_fail=0 has_err=0
  worst() { case "$1" in "$EXIT_FAIL") has_fail=1 ;; "$EXIT_ERROR") has_err=1 ;; esac; }

  log "[호스트] 일반 Client -> ${DB_HOST}:${DB_PORT} (반드시 실패해야 정상)"
  if ! command -v nc >/dev/null 2>&1; then
    log "ERROR: nc 없음 — 호스트 경로 NOT VERIFIED"; worst "$EXIT_ERROR"
  else
    timeout 5 nc -z -w 3 "$DB_HOST" "$DB_PORT" >/dev/null 2>&1
    local rc=$? class
    class="$(classify_exit_code "$rc")"
    case "$class" in
      SUCCESS) log "FAIL: 호스트에서 DB 직접 접근 성공"; worst "$EXIT_FAIL" ;;
      REFUSED|TIMEOUT)
        if command -v curl >/dev/null 2>&1 && canary_ok_host; then
          log "PASS: 호스트 경로 차단(${class}, rc=${rc}), 대조군 NodePort 정상"
        else
          log "ERROR: 차단(${class})이지만 대조군 NodePort도 실패 — NOT VERIFIED"; worst "$EXIT_ERROR"
        fi ;;
      *) log "ERROR: nc rc=${rc} (${class}) — NOT VERIFIED"; worst "$EXIT_ERROR" ;;
    esac
  fi

  log "[Pod] ${DENY_POD_DEPLOY} -> ${DB_HOST}:${DB_PORT} (NetworkPolicy egress 미허용)"
  if ! command -v kubectl >/dev/null 2>&1; then
    log "ERROR: kubectl 없음 — Pod 경로 NOT VERIFIED"; worst "$EXIT_ERROR"
  else
    local dns tcp
    dns="$(pod_probe dns)"
    if [[ "$dns" != "OK" ]]; then
      log "ERROR: 대조군(클러스터 DNS 해석) 실패 또는 exec/python3 불가(출력='${dns}') — Pod 네트워킹 문제와 정책 차단 구분 불가 (NOT VERIFIED)"
      worst "$EXIT_ERROR"
    else
      tcp="$(pod_probe tcp)"
      case "$tcp" in
        TIMEOUT)   log "PASS: Pod -> DB 차단(TIMEOUT=DROP), 대조군 DNS 정상" ;;
        CONNECTED) log "FAIL: Pod에서 DB 직접 연결 성공 — NetworkPolicy egress 확인 필요"; worst "$EXIT_FAIL" ;;
        REFUSED)   log "ERROR: REFUSED — 패킷이 db01까지 도달함(정책 DROP 아님). NetworkPolicy 차단 증거로 쓰지 않음"; worst "$EXIT_ERROR" ;;
        *)         log "ERROR: Pod TCP 시험 판정 불가(출력='${tcp}') — NOT VERIFIED"; worst "$EXIT_ERROR" ;;
      esac
    fi
  fi

  if [[ "$has_fail" -eq 1 ]]; then exit "$EXIT_FAIL"; fi
  if [[ "$has_err" -eq 1 ]]; then exit "$EXIT_ERROR"; fi
  exit "$EXIT_PASS"
}

# ready 상태인 EndpointSlice 주소 수
ready_endpoint_count() {
  kubectl get endpointslices -n "$SERVICE_NS" -l "kubernetes.io/service-name=${SERVICE_NAME}" \
    -o jsonpath='{range .items[*].endpoints[?(@.conditions.ready==true)]}{.addresses[0]}{"\n"}{end}' 2>/dev/null \
    | sed '/^$/d' | wc -l
}

# 조건이 참이 될 때까지 WAIT_SEC 동안 폴링. $1: 설명, $2: 비교식(count 변수 사용)
wait_for() {
  local desc="$1" expr="$2" deadline=$((SECONDS + WAIT_SEC)) count
  while :; do
    count="$(ready_endpoint_count)" || count=-1
    if eval "$expr"; then log "확인: ${desc} (ready=${count})"; return 0; fi
    (( SECONDS >= deadline )) && { log "시간 초과(${WAIT_SEC}s): ${desc} 미충족 (ready=${count})"; return 1; }
    sleep "$POLL_SEC"
  done
}

# ── probe-failover: DB 장애 주입 -> Endpoint 제외 -> 복구 -> 재편입 ──
cmd_probe_failover() {
  command -v kubectl >/dev/null 2>&1 || { log "ERROR: kubectl 없음 — NOT VERIFIED"; exit "$EXIT_ERROR"; }

  local baseline
  baseline="$(ready_endpoint_count)"
  if ! [[ "$baseline" =~ ^[0-9]+$ ]] || [[ "$baseline" -eq 0 ]]; then
    log "ERROR: 장애 주입 전 ready Endpoint가 없음(${baseline:-조회 실패}) — 기준선 비정상 (NOT VERIFIED)"
    exit "$EXIT_ERROR"
  fi
  log "1) 기준선: ready Endpoint ${baseline}개"

  log "2) DB 차단은 db01 방화벽 또는 D-NET-01 관리 호스트에서 수동으로 수행하십시오 (이 스크립트는 방화벽을 조작하지 않음)."
  read -r -p "DB 차단을 적용하셨으면 Enter, 중단하려면 Ctrl+C: " _

  log "3) readiness 실패로 Endpoint가 모두 제외되는지 최대 ${WAIT_SEC}s 확인"
  wait_for "장애 중 ready Endpoint 0개" '[[ "$count" -eq 0 ]]' \
    || { log "FAIL: 장애 주입 후에도 Endpoint가 제외되지 않음 — 복구 결과와 무관하게 FAIL"; exit "$EXIT_FAIL"; }

  log "4) DB 복구 후 재편입 확인"
  read -r -p "DB 차단을 해제하셨으면 Enter: " _
  wait_for "복구 후 ready Endpoint ${baseline}개" '[[ "$count" -ge '"$baseline"' ]]' \
    || { log "FAIL: 복구 후 Endpoint 미복구"; exit "$EXIT_FAIL"; }

  log "PASS: 장애 시 Endpoint 제외 및 복구 후 재편입 확인"
  exit "$EXIT_PASS"
}

case "${1:-}" in
  allow-flow)      cmd_allow_flow ;;
  deny-flow)       cmd_deny_flow ;;
  probe-failover)  cmd_probe_failover ;;
  *)
    echo "사용법: $0 {allow-flow|deny-flow|probe-failover}" >&2
    echo "exit code: 0=PASS, 1=FAIL, 2=PARTIAL, 3=ERROR/NOT VERIFIED" >&2
    exit "$EXIT_FAIL"
    ;;
esac
