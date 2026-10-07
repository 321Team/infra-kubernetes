#!/usr/bin/env bash
# P1-SEC-01 AC-06 Probe 1단계: 격리 시험 Pod 로 readiness 실패→Endpoint 제외→복원, liveness 실패→재시작→회복 관측
# REV=v2.0-20261007 — GWONUK lifecycle fixes; see README for provenance/approval.
#
# 운영 Pod·Deployment·Service 는 변경하지 않는다. 이 실행이 만든 시험 Pod 1개·Service 1개만 만들고 지운다.
# 실제 APP(transaction-api/fds-engine)의 /readyz·/livez HTTP Probe 동작은 확인하지 않는다(2단계, 별도).
#
#   bash probe.sh render                          kubectl 없이 만들 YAML 을 출력(서버 접근 없음)
#   KC="kubectl" bash probe.sh precheck           읽기 전용 사전 확인
#   KC="kubectl" CONFIRM=YES bash probe.sh run    시험 Pod/Service 생성·관측·UID 조건 삭제
#
# 범위·상수는 코드에 고정(KC, CONFIRM). 호스트 Python 3 필요; 도구 설치 없음.
# 종료 코드: 0=모든 단계(T0~T3)를 관측하고 정리까지 기록 완료, 2=STOP(생성 전 중단), 3=시작 후 중단·실패(정리 시도함),
#           12=기록·봉인 실패, 13=정리 실패 또는 소유권 불명(수동 확인 필요). **0 은 시험 성공이 아니라 "관측 완료"이며 AC 판정이 아니다.**
set -uo pipefail
set +x
LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 2
source "$LIB_DIR/common.sh" || exit 2
REV="v2.0-20261007"
MODE="${1:-}"
KC="${KC:-kubectl}"

NS=fds-app
# 외부(public registry) pull 없음: 승인 Harbor 의 exact digest(manifests/02-securitycontext.yaml 의 transaction-api 와 같은 값)를
# 이미 이 이미지를 실행 중인 고정 노드에서만 쓰고, imagePullPolicy Never 로 cache 부재 시 pull 하지 않고 실패한다.
IMAGE='10.1.93.54/fds/transaction-api@sha256:a7093136d12640da00570c18ed278eacbb8e9ba3637c7d509571346eac45cc33'
IMAGE_DIGEST='sha256:a7093136d12640da00570c18ed278eacbb8e9ba3637c7d509571346eac45cc33'
TARGET_NODE='k8s-worker01'
# 앱 이미지는 최소(apko) 런타임이라 test/rm/touch 같은 외부 실행파일이 있다고 가정하지 않는다.
# 시작 명령·Probe·장애 주입을 모두 이미지의 Python 실행파일 하나로 통일한다(소스: fds-msa transaction-api/Dockerfile ENTRYPOINT /venv/bin/python3, USER 10001:999).
PY='/venv/bin/python3'
POLL=3
READY_WAIT=90 T1_WAIT=60 T2_WAIT=60 T3_RESTART_WAIT=90 T3_RECOVER_WAIT=90
TOTAL_LIMIT=900     # 관측 구간 전체 상한(초)
CLEAN_LIMIT=90      # 정리 구간 별도 상한(초)

RUN_ID="$(date -u +%m%d%H%M%S)-$RANDOM-$RANDOM"
POD="sec01-probe-test-$RUN_ID"
SVC="sec01-probe-test-$RUN_ID"

render_pod() {
cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: $NS
  labels:
    app.kubernetes.io/managed-by: sec01-ac06-test
    sec01-probe-test: "$RUN_ID"
spec:
  automountServiceAccountToken: false
  enableServiceLinks: false
  hostNetwork: false
  hostPID: false
  hostIPC: false
  nodeSelector:
    kubernetes.io/hostname: $TARGET_NODE
  restartPolicy: Always
  terminationGracePeriodSeconds: 5
  activeDeadlineSeconds: 1800
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 999
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: probe
      image: $IMAGE
      imagePullPolicy: Never
      command: ["$PY", "-I", "-B", "-c", "import pathlib,time; [pathlib.Path('/tmp/'+n).touch() for n in ('ready','alive')]; time.sleep(3600)"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        requests:
          cpu: 20m
          memory: 32Mi
        limits:
          cpu: 200m
          memory: 128Mi
      volumeMounts:
        - name: tmp
          mountPath: /tmp
      readinessProbe:
        exec:
          command: ["$PY", "-I", "-B", "-c", "import os,sys; sys.exit(0 if os.path.exists('/tmp/ready') else 1)"]
        initialDelaySeconds: 2
        periodSeconds: 5
        timeoutSeconds: 5
        successThreshold: 1
        failureThreshold: 2
      livenessProbe:
        exec:
          command: ["$PY", "-I", "-B", "-c", "import os,sys; sys.exit(0 if os.path.exists('/tmp/alive') else 1)"]
        initialDelaySeconds: 10
        periodSeconds: 5
        timeoutSeconds: 5
        successThreshold: 1
        failureThreshold: 3
  volumes:
    - name: tmp
      emptyDir:
        sizeLimit: 1Mi
EOF
}
render_svc() {
cat <<EOF
apiVersion: v1
kind: Service
metadata:
  name: $SVC
  namespace: $NS
  labels:
    app.kubernetes.io/managed-by: sec01-ac06-test
    sec01-probe-test: "$RUN_ID"
spec:
  type: ClusterIP
  publishNotReadyAddresses: false
  selector:
    sec01-probe-test: "$RUN_ID"
  ports:
    - name: probe
      port: 8080
      targetPort: 8080
      protocol: TCP
EOF
}

if [ "$MODE" = render ]; then render_pod; echo "---"; render_svc; exit 0; fi
case "$MODE" in precheck|run) ;; *) echo "사용: $0 render|precheck|run" >&2; exit 2;; esac
[ "$MODE" = run ] && [ "${CONFIRM:-}" != YES ] && { echo "STOP: run 은 CONFIRM=YES 필요" >&2; exit 2; }

stop() { echo "STOP: $*" >&2; if declare -F finish >/dev/null; then finish 2; else exit 2; fi; }
for c in date sha256sum tar mktemp sort diff grep sed head awk cut timeout python3 find xargs tr cat mv; do command -v "$c" >/dev/null 2>&1 || stop "$c 명령 없음"; done
umask 077
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$(mktemp -d "p1-sec01-ac06-probe-$MODE-$TS.XXXXXX")" || stop "배타적 출력 폴더를 만들 수 없음"
START_EPOCH=$(mono)
phase_begin precheck 180
S="$OUT/99-summary-provisional.txt"
FAIL=0; CLEAN_FAIL=0; POD_UID=""; SVC_UID=""; CLEANED=0; FINISHED=0
POD_ATTEMPT=0; SVC_ATTEMPT=0; RESOLVED_UID=""
w() { printf '%s\n' "$2" >> "$1" 2>/dev/null || { echo "쓰기 실패: $1" >&2; FAIL=1; return 12; }; }
say() { echo "$*"; w "$OUT/run.log" "$(date -u +%FT%TZ) $*"; }
# 모든 kubectl 호출은 남은 phase 예산과 15초 중 작은 값 + KILL 1초로 제한한다.
kc() { timeout 15 $KC --request-timeout=10s "$@"; }
elapsed() { echo $(( $(mono) - START_EPOCH )); }

w "$OUT/00-environment.txt" "rev=$REV mode=$MODE started_utc=$TS ns=$NS image=$IMAGE node=$TARGET_NODE run_id=$RUN_ID"
(cd "$LIB_DIR" && sha256sum probe.sh backup.sh common.sh checks.py) > "$OUT/source-sha256.txt" || stop "소스 해시 기록 실패"
w "$OUT/00-environment.txt" "script_sha256=$(sha256sum "$0" 2>/dev/null | cut -c1-64)"
render_pod > "$OUT/pod.yaml"; render_svc > "$OUT/svc.yaml"
w "$OUT/00-environment.txt" "pod_yaml_sha256=$(sha256sum "$OUT/pod.yaml" | cut -c1-64) svc_yaml_sha256=$(sha256sum "$OUT/svc.yaml" | cut -c1-64)"
{ kc version -o json 2>&1 | head -c 1500; } > "$OUT/00-kubectl-version.txt"

# ---------------- 읽기 전용 조회(조회 실패는 빈 결과가 아니라 실패로 취급) ----------------
nodes_ready_line() { kc get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' 2>&1; }
snapshot_pods() {
  kc -n "$NS" get pods -o json | timeout 10 python3 "$LIB_DIR/checks.py" baseline pods "$POD"
}
snapshot_eps() {
  kc -n "$NS" get endpointslices -o json | timeout 10 python3 "$LIB_DIR/checks.py" baseline eps "$SVC"
}
pod_state() {   # uid|phase|Ready|restartCount|containerID. 조회 실패면 rc=1
  local o rc
  o="$(kc -n "$NS" get pod "$POD" -o jsonpath='{.metadata.uid}{"|"}{.status.phase}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"|"}{.status.containerStatuses[0].restartCount}{"|"}{.status.containerStatuses[0].containerID}' 2>"$OUT/pod-query.stderr")"; rc=$?
  [ "$rc" -eq 0 ] || return 1
  printf '%s' "$o"
}
# ready 미지정(nil)은 Kubernetes 의미대로 true 로 해석한다(EndpointSlice conditions). API 실패·해석 불가는 rc=1 → 호출부가 중단한다.
# 출력: true | false | absent  (absent 는 **성공한 조회**에서 UID 가 일치하는 endpoint 가 없을 때만)
ep_state() {
  local l rc uid ready found=0 usable=0
  l="$(kc -n "$NS" get endpointslices -l "kubernetes.io/service-name=$SVC" \
    -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.uid}{"|"}{.conditions.ready}{"\n"}{end}' \
    2> "$OUT/ep-query.stderr")"; rc=$?
  w "$OUT/ep-query-history.txt" "$(date -u +%FT%TZ) rc=$rc stdout=$(printf '%s' "$l" | tr '\n' ';') stderr=$(head -c 200 "$OUT/ep-query.stderr")" || return 1
  [ "$rc" -eq 0 ] || return 1
  while IFS='|' read -r uid ready; do
    [ "$uid" = "$POD_UID" ] || continue
    found=1
    case "$ready" in true|'') usable=1;; false) :;; *) return 1;; esac
  done <<< "$l"
  if [ "$found" -eq 0 ]; then echo absent
  elif [ "$usable" -eq 1 ]; then echo true
  else echo false; fi
}
check_nodes() {
  kc get nodes -o json > "$OUT/nodes-$1.json" 2> "$OUT/nodes-$1.stderr" || return 1
  timeout 10 python3 "$LIB_DIR/checks.py" nodes < "$OUT/nodes-$1.json" > "$OUT/nodes-$1.txt" 2>&1
}

# ---------------- 소유권 확인·정리(성공·실패·timeout·중단 공통, UID 조건 삭제만, 이름 삭제 fallback 없음) ----------------
#   출력: OWNED|<uid>  /  NOTFOUND  /  FOREIGN(소유 대조 불일치)  /  UNKNOWN(조회 실패)
adopt_owned() {   # $1=pod|svc $2=name
  local o rc a_uid a_run a_mgr a_img a_sel own=no
  o="$(kc -n "$NS" get "$1" "$2" --ignore-not-found -o jsonpath='{.metadata.uid}{"|"}{.metadata.labels.sec01-probe-test}{"|"}{.metadata.labels.app\.kubernetes\.io/managed-by}{"|"}{.spec.containers[0].image}{"|"}{.spec.selector.sec01-probe-test}' 2>&1)"; rc=$?
  w "$OUT/adopt-$1.txt" "rc=$rc out=$(printf '%s' "$o" | head -c 300)" || { echo UNKNOWN; return; }
  if [ "$rc" -ne 0 ]; then echo UNKNOWN; return; fi
  if [ -z "$o" ]; then echo NOTFOUND; return; fi
  IFS='|' read -r a_uid a_run a_mgr a_img a_sel <<< "$o"
  [ "$1" = pod ] && [ "$a_img" = "$IMAGE" ] && own=yes
  [ "$1" = svc ] && [ "$a_sel" = "$RUN_ID" ] && own=yes
  if [ -n "$a_uid" ] && [ "$a_run" = "$RUN_ID" ] && [ "$a_mgr" = sec01-ac06-test ] && [ "$own" = yes ]; then echo "OWNED|$a_uid"; else echo FOREIGN; fi
}
resolve_uid() {   # create 요청 시도 후 UID 를 못 받은 경우: 이름으로 읽기 GET → 소유 대조 → UID 회수. 불명이면 이름 삭제 없이 수동 인계
  local r; r="$(adopt_owned "$1" "$2")"; w "$OUT/adopt-$1-resolve.txt" "$r"
  case "$r" in
    OWNED\|*) RESOLVED_UID="${r#OWNED|}";;
    NOTFOUND) RESOLVED_UID="";;
    *) RESOLVED_UID=""; CLEAN_FAIL=1; say "소유권 불명/예상 외 객체($1 $2: $r) - 이름 삭제 없이 수동 인계 필요";;
  esac
}
delete_owned() {  # $1=kind(pods|services) $2=name $3=uid(비어 있지 않음)
  local body out rc
  body="$(printf '{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"%s"}}' "$3")"
  out="$(printf '%s\n' "$body" | kc delete --raw "/api/v1/namespaces/$NS/$1/$2" -f - 2>&1)"; rc=$?
  { echo "## delete $1/$2 uid=$3 rc=$rc"; echo "body=$body"; echo "$out" | head -c 400; echo; } >> "$OUT/50-cleanup.txt"
  [ "$rc" -eq 0 ] || { say "삭제 요청 실패 rc=$rc $1/$2 (이름 삭제로 대체하지 않음)"; CLEAN_FAIL=1; }
}
verify_gone() {   # $1=kind(pod|svc) $2=name; shared cleanup deadline
  local out="NOT_QUERIED" rc
  while budget_ok; do
    out="$(kc -n "$NS" get "$1" "$2" --ignore-not-found -o name 2>&1)"; rc=$?
    if [ "$rc" -eq 0 ] && [ -z "$out" ]; then w "$OUT/50-cleanup.txt" "NotFound 확인 $1/$2"; return 0; fi
    pause_bounded 2 || break
  done
  w "$OUT/50-cleanup.txt" "NotFound 미확인 $1/$2 last=$(printf '%s' "$out" | head -c 200)"; CLEAN_FAIL=1; return 1
}
cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  phase_begin cleanup "$CLEAN_LIMIT"
  say "정리 시작"
  if [ "$POD_ATTEMPT" -eq 1 ] && [ -z "$POD_UID" ]; then resolve_uid pod "$POD"; POD_UID="$RESOLVED_UID"; fi
  if [ "$SVC_ATTEMPT" -eq 1 ] && [ -z "$SVC_UID" ]; then resolve_uid svc "$SVC"; SVC_UID="$RESOLVED_UID"; fi
  [ -n "$POD_UID" ] && delete_owned pods "$POD" "$POD_UID"
  [ -n "$SVC_UID" ] && delete_owned services "$SVC" "$SVC_UID"
  [ -n "$POD_UID" ] && verify_gone pod "$POD"
  [ -n "$SVC_UID" ] && verify_gone svc "$SVC"
  CLEANED=1
  say "정리 끝 clean_fail=$CLEAN_FAIL"
}
base_changed() {   # 0=변화 있음 또는 조회 불가(안전 쪽으로 중단), 1=기준선과 동일
  local p e
  p="$(snapshot_pods)" || return 0
  e="$(snapshot_eps)" || return 0
  [ "$p" != "$(cat "$OUT/baseline-pods.txt")" ] && return 0
  [ "$e" != "$(cat "$OUT/baseline-endpointslices.txt")" ] && return 0
  return 1
}
finish() {   # $1=exit code(생성 후 중단이면 3). 봉인 뒤에는 OUT 안의 파일을 바꾸지 않는다.
  local rc="$1" st
  [ "$FINISHED" -eq 0 ] || return
  FINISHED=1
  if [ "$POD_ATTEMPT" -eq 1 ] || [ "$SVC_ATTEMPT" -eq 1 ]; then
    cleanup
    phase_begin postcheck 90
    snapshot_pods > "$OUT/after-pods.txt" 2> "$OUT/after-pods.stderr" || rc=3
    snapshot_eps > "$OUT/after-endpointslices.txt" 2> "$OUT/after-endpointslices.stderr" || rc=3
    diff "$OUT/baseline-pods.txt" "$OUT/after-pods.txt" > "$OUT/diff-pods.txt" 2>&1; prc=$?
    diff "$OUT/baseline-endpointslices.txt" "$OUT/after-endpointslices.txt" > "$OUT/diff-endpointslices.txt" 2>&1; erc=$?
    [ "$prc" -eq 0 ] && [ "$erc" -eq 0 ] || rc=3
    if check_nodes post; then w "$S" "nodes_post=6/6_Ready"; else w "$S" "nodes_post=NOT_6/6_Ready"; rc=3; fi
    w "$S" "baseline_pods_diff_rc=$prc baseline_endpointslices_diff_rc=$erc"
  fi
  w "$S" "observation_exit_code=$rc cleanup_failed=$CLEAN_FAIL elapsed_sec=$(elapsed)"
  seal_out
  if [ "$CLEAN_FAIL" -ne 0 ]; then st=13; elif [ "$FAIL" -ne 0 ]; then st=12; else st="$rc"; fi
  completion "$st"; st=$?
  cat "$S"
  exit "$st"
}
trap on_signal INT TERM
trap 'rc=$?; if [ "$FINISHED" -ne 1 ]; then [ "$rc" -ne 0 ] || rc=3; printf "Unexpected exit rc=%s\n" "$rc" >&2; finish "$rc"; fi' EXIT
abort() { say "ABORT: $*"; finish 3; }
stage_guard() {    # 각 단계 뒤(=다음 주입 전): 운영 Pod·EndpointSlice 기준선 일치와 노드 6/6 확인. 조회 불가·변화면 중단
  observation_ok || abort "$1: 관측 상한 도달/기록 실패"
  base_changed && abort "$1: 운영 Pod/EndpointSlice 가 기준선과 다르거나 조회할 수 없음"
  check_nodes "$1" || abort "$1: 노드 6/6 Ready 가 아님"
  observation_ok || abort "$1: 관측 상한 도달/기록 실패"
}

# ---------------- 사전 확인 ----------------
check_nodes pre || stop "노드 6/6 Ready 가 아님(OUT/nodes-pre.txt)"
kc get ns "$NS" > "$OUT/ns.txt" 2>&1 || stop "namespace $NS 조회 실패"
kc -n "$NS" get resourcequota > "$OUT/resourcequota.txt" 2>&1 || stop "resourcequota 조회 실패"
kc -n "$NS" get limitrange > "$OUT/limitrange.txt" 2>&1 || stop "limitrange 조회 실패"
kc -n "$NS" get pod,svc -o name > "$OUT/existing-objects.txt" 2>&1 || stop "기존 객체 조회 실패(existing-objects.txt)"
grep "sec01-probe-test" "$OUT/existing-objects.txt" > "$OUT/existing-test-objects.txt" || true
[ ! -s "$OUT/existing-test-objects.txt" ] || stop "이전 시험 객체가 남아 있음(OUT/existing-test-objects.txt) - 덮어쓰지 않음"
snapshot_pods > "$OUT/baseline-pods.txt" || stop "운영 Pod 기준선 조회 실패"
snapshot_eps > "$OUT/baseline-endpointslices.txt" || stop "운영 EndpointSlice 기준선 조회 실패"
[ "$(cat "$OUT/baseline-pods.txt")" != "[]" ] || stop "운영 Pod 기준선이 비어 있음"
w "$OUT/00-environment.txt" "baseline_pods=$(grep -c . "$OUT/baseline-pods.txt") baseline_endpointslices=$(grep -c . "$OUT/baseline-endpointslices.txt")"
# 이미지 cache 확인(읽기 전용): 고정 노드에서 같은 digest 를 이미 실행 중인 Pod 가 있어야 한다(없으면 Never 로는 기동 불가 → STOP)
kc get node "$TARGET_NODE" -o jsonpath='{.metadata.name}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}' > "$OUT/target-node.txt" 2>&1 || stop "고정 노드 조회 실패"
grep -q "^$TARGET_NODE|True$" "$OUT/target-node.txt" || stop "고정 노드 $TARGET_NODE 가 없거나 Ready 가 아님(target-node.txt)"
kc -n "$NS" get pods -o jsonpath='{range .items[*]}{.spec.nodeName}{"|"}{range .status.containerStatuses[*]}{.imageID}{";"}{end}{"\n"}{end}' > "$OUT/image-cache-evidence.txt" 2>&1 || stop "image cache 근거 조회 실패"
grep "^$TARGET_NODE|" "$OUT/image-cache-evidence.txt" | grep -q "$IMAGE_DIGEST" || stop "$TARGET_NODE 에서 $IMAGE_DIGEST 를 쓰는 Pod 를 찾지 못함 - cache 근거 없음(image-cache-evidence.txt). 외부 pull 은 하지 않는다"
w "$S" "image_cached_on_target_node=yes(간접 근거: 같은 digest 를 쓰는 Pod 존재) node=$TARGET_NODE digest=$IMAGE_DIGEST pull_policy=Never"
observation_ok || stop "사전 점검 상한 도달/기록 실패"
if [ "$MODE" = precheck ]; then
  say "PRECHECK 완료(읽기 전용). rc0=필수 사전 조회 적합·봉인 완료. 이미지 cache/Python 실제 기동 보증은 아님; run T0에서 확인한다."
  w "$S" "PRECHECK_RESULT=OK"; finish 0
fi


phase_begin observation "$TOTAL_LIMIT"
# ---------------- 생성 ----------------
w "$S" "PROVISIONAL=true SEAL_STATUS=PENDING (관측 요약이며 판정이 아님)"
observation_ok || abort "생성 전 시간/기록 확인 실패"
# create 요청 **전에** 소유권 추적을 시작한다. 요청과 UID 대입 사이에 신호·오류가 나도 cleanup 이 이름으로 읽기 GET → 소유 대조 → UID 회수한다.
say "create Pod: $POD"
POD_ATTEMPT=1
POD_UID="$(kc create -f "$OUT/pod.yaml" -o jsonpath='{.metadata.uid}' 2> "$OUT/create-pod.stderr")" ; rc=$?
w "$OUT/create-pod.rc" "$rc"
if [ "$rc" -ne 0 ] || [ -z "$POD_UID" ]; then POD_UID=""; abort "Pod create 응답 이상(rc=$rc) - 서버 객체 유무를 소유권 확인 후 정리"; fi
w "$OUT/00-environment.txt" "pod_uid=$POD_UID"
observation_ok || abort "Service 생성 전 시간/기록 확인 실패"
say "create Service: $SVC"
SVC_ATTEMPT=1
SVC_UID="$(kc create -f "$OUT/svc.yaml" -o jsonpath='{.metadata.uid}' 2> "$OUT/create-svc.stderr")"; rc=$?
w "$OUT/create-svc.rc" "$rc"
if [ "$rc" -ne 0 ] || [ -z "$SVC_UID" ]; then SVC_UID=""; abort "Service create 응답 이상(rc=$rc) - 서버 객체 유무를 소유권 확인 후 정리"; fi
w "$OUT/00-environment.txt" "svc_uid=$SVC_UID"

# ---------------- 관측 ----------------
# wait_for <label> <최대초> <조건식>   조건식에서 P_READY P_RESTART P_CID P_PHASE EP 를 쓴다. 조회 실패·UID 교체·T1/T2 중 예상 밖 restart 는 즉시 중단.
wait_for() {
  local label="$1" max="$2" cond="$3" PS start old_deadline="$PHASE_DEADLINE" stage_deadline
  start=$(mono); stage_deadline=$(( start + max ))
  [ "$stage_deadline" -lt "$PHASE_DEADLINE" ] && PHASE_DEADLINE="$stage_deadline"
  while budget_ok; do
    PS="$(pod_state)" || abort "$label: Pod 조회 실패/시간 초과"
    IFS='|' read -r P_UID P_PHASE P_READY P_RESTART P_CID <<< "$PS"
    EP="$(ep_state)" || abort "$label: EndpointSlice 조회/해석 실패/시간 초과"
    budget_ok || break
    w "$OUT/poll-$label.txt" "$(date -u +%FT%TZ) t=$(( $(mono) - start )) pod=$PS ep_ready=$EP"
    [ "${P_UID:-}" = "$POD_UID" ] || abort "$label: Pod UID 교체됨"
    [[ "${P_RESTART:-}" =~ ^[0-9]+$ ]] || abort "$label: restartCount 해석 불가"
    case "$label" in T1|T2) [ "$P_RESTART" = "$R0" ] || abort "$label: 예상 밖 restart";; esac
    if eval "$cond"; then
      budget_ok || break
      PHASE_DEADLINE="$old_deadline"; return 0
    fi
    check_nodes "$label" || abort "$label: 노드 조회 실패/NotReady"
    pause_bounded "$POLL" || break
  done
  PHASE_DEADLINE="$old_deadline"
  return 1
}

wait_for T0 "$READY_WAIT" '[ "$P_READY" = True ] && [ "$EP" = true ]' && w "$S" "T0_ready_and_endpoint_true=yes restart=$P_RESTART" || { w "$S" "T0_ready_and_endpoint_true=NO"; kc -n "$NS" describe pod "$POD" > "$OUT/describe-T0.txt" 2>&1; kc -n "$NS" get events --field-selector "involvedObject.uid=$POD_UID" > "$OUT/events-T0.txt" 2>&1; abort "T0 Ready 미달성($PY 부재·이미지 기동 실패 등, describe-T0.txt/events-T0.txt 참조)"; }
R0="$P_RESTART"; CID0="$P_CID"
stage_guard T0
kc -n "$NS" get pod "$POD" -o json > "$OUT/pod-T0.json" 2> "$OUT/pod-T0.stderr" || abort "T0 Pod 조회 실패"
timeout 10 python3 "$LIB_DIR/checks.py" token < "$OUT/pod-T0.json" > "$OUT/token-T0.txt" 2>&1 || abort "T0 SA token 차단 미확인 — 주입하지 않음"

# T1: readiness 실패 주입 (실패하면 T2/T3 미시작)
say "T1: remove /tmp/ready"
kc -n "$NS" exec "$POD" -c probe -- "$PY" -I -B -c "import os; os.remove('/tmp/ready')" > "$OUT/T1-inject.stdout" 2> "$OUT/T1-inject.stderr"; IRC=$?; w "$OUT/T1-inject.rc" "$IRC"
[ "$IRC" -eq 0 ] || { w "$S" "T1_inject_rc=$IRC"; abort "T1 장애 주입 명령 실패(rc=$IRC) - T2/T3 미시작"; }
if wait_for T1 "$T1_WAIT" '[ "$P_READY" = False ] && { [ "$EP" = false ] || [ "$EP" = absent ]; } && [ "$P_RESTART" = "$R0" ]'; then w "$S" "T1_notready_and_endpoint_not_ready=yes endpoint_value=$EP restart_unchanged=yes"; else w "$S" "T1_notready_and_endpoint_not_ready=NO(timeout)"; abort "T1 관측 timeout - T2/T3 미시작"; fi
stage_guard T1

# T2: 복원 (실패하면 T3 미시작)
say "T2: create /tmp/ready"
kc -n "$NS" exec "$POD" -c probe -- "$PY" -I -B -c "import pathlib; pathlib.Path('/tmp/ready').touch()" > "$OUT/T2-inject.stdout" 2> "$OUT/T2-inject.stderr"; IRC=$?; w "$OUT/T2-inject.rc" "$IRC"
[ "$IRC" -eq 0 ] || { w "$S" "T2_inject_rc=$IRC"; abort "T2 복원 명령 실패(rc=$IRC) - T3 미시작"; }
if wait_for T2 "$T2_WAIT" '[ "$P_READY" = True ] && [ "$EP" = true ] && [ "$P_RESTART" = "$R0" ]'; then w "$S" "T2_recovered_ready_and_endpoint_true=yes restart_unchanged=yes"; else w "$S" "T2_recovered_ready_and_endpoint_true=NO(timeout)"; abort "T2 회복 관측 timeout - T3 미시작"; fi
stage_guard T2

# T3: liveness 실패 주입 → 재시작 → 시작 명령이 표식 재생성 → 회복. 어느 단계든 미관측이면 실패로 중단(rc3)한다.
say "T3: remove /tmp/alive"
kc -n "$NS" exec "$POD" -c probe -- "$PY" -I -B -c "import os; os.remove('/tmp/alive')" > "$OUT/T3-inject.stdout" 2> "$OUT/T3-inject.stderr"; IRC=$?; w "$OUT/T3-inject.rc" "$IRC"
[ "$IRC" -eq 0 ] || { w "$S" "T3_inject_rc=$IRC"; abort "T3 장애 주입 명령 실패(rc=$IRC)"; }
if wait_for T3-restart "$T3_RESTART_WAIT" '[ -n "${P_RESTART:-}" ] && [ "$P_RESTART" -gt "$R0" ] 2>/dev/null'; then
  w "$S" "T3_restart_count_increased=yes before=$R0 after=$P_RESTART container_id_changed=$([ "$P_CID" != "$CID0" ] && echo yes || echo NO)"
else w "$S" "T3_restart_count_increased=NO(timeout)"; abort "T3 재시작 미관측"; fi
if wait_for T3-recover "$T3_RECOVER_WAIT" '[ "$P_READY" = True ] && [ "$EP" = true ]'; then w "$S" "T3_recovered_ready_and_endpoint_true=yes"; else w "$S" "T3_recovered_ready_and_endpoint_true=NO(timeout)"; abort "T3 회복 미관측"; fi
stage_guard T3

kc -n "$NS" get events --field-selector "involvedObject.uid=$POD_UID" > "$OUT/events-final.txt" 2>&1 || abort "final events 조회 실패"
kc -n "$NS" get pod "$POD" -o json > "$OUT/pod-final.json" 2> "$OUT/pod-final.stderr" || abort "final Pod 조회 실패"
timeout 10 python3 "$LIB_DIR/checks.py" token < "$OUT/pod-final.json" > "$OUT/token-check.txt" 2>&1 || abort "SA token 차단 미확인"
w "$S" "projected_serviceaccount_token_sources=0 automountServiceAccountToken=false"
budget_ok || abort "관측 상한 도달"
w "$S" "ALL_STAGES_OBSERVED=yes (관측 완료이며 AC 판정이 아님)"
finish 0
