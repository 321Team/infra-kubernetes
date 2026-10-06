#!/usr/bin/env bash
# sec01-secretbox-rollback-v4.sh 의 fixture(가짜 클러스터) 시험. 클러스터에 접근하지 않는다.
# 사용: bash test-rollback-v4.sh [path/to/sec01-secretbox-rollback-v4.sh]
# 환경변수 ONLY=S1,S3 처럼 지정하면 해당 case 만 실행한다.
SCRIPT="$(cd "$(dirname "${1:-./sec01-secretbox-rollback-v4.sh}")" && pwd)/$(basename "${1:-./sec01-secretbox-rollback-v4.sh}")"
PASS=0; FAILN=0; RESULTS=()
export FIXTURE_PYTHON="$(command -v python3)"

make_fixture() {   # $1 = dir
  local FX="$1"
  mkdir -p "$FX/bin" "$FX/state/store/default" "$FX/state/uid" "$FX/etc/enc" "$FX/etc/manifests" "$FX/root"
  echo "apiVersion: v1" > "$FX/etc/manifests/kube-apiserver.yaml"
  cat > "$FX/etc/enc/encryption-config.yaml" <<'Y'
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - secretbox:
          keys:
            - name: key1
              secret: FAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKEK=
      - identity: {}
Y
  chmod 600 "$FX/etc/enc/encryption-config.yaml"
  printf 'kube-system/default-token\nfds-app/harbor-pull\nmonitoring-api/discord\n' > "$FX/state/base-secrets"
  echo 1 > "$FX/state/gen"
  cat > "$FX/bin/hostname" <<'S'
#!/usr/bin/env bash
[ "${1:-}" = "-I" ] && { echo "10.1.93.104 "; exit 0; }
echo k8s-control03
S
  cat > "$FX/bin/awk" <<'S'
#!/usr/bin/env bash
# B2 시험용: apiserver Pod 판정 파서(-v exp_n=...)에만 실패를 주입한다. 나머지 awk 호출은 실제 awk 로 위임한다.
step="$(cat "$STEP_FILE" 2>/dev/null)"; phase=other; [[ "$step" == cleanup || "$step" == audit_after ]] && phase=post; [ -z "$step" ] && phase=pre
for arg in "$@"; do
  case "$arg" in
    */pre-apiserver-pods.txt) phase=pre ;;
    */post-apiserver-pods.txt) phase=post ;;
  esac
done
if [[ " $* " == *" exp_n="* ]] && { [ "${FAULT:-}:$phase" = parser_pre:pre ] || [ "${FAULT:-}:$phase" = parser_post:post ]; }; then
  echo "awk: injected parser failure" >&2; exit 42
fi
REAL="$(PATH="${PATH#"$FX"/bin:}" command -v awk)"; exec "$REAL" "$@"
S
  cat > "$FX/bin/ss" <<'S'
#!/usr/bin/env bash
# 관측 오류 주입: ss_error(rc!=0), ss_empty(rc=0 이지만 출력 없음)
[ "${FAULT:-}" = ss_error ] && { echo "ss: cannot open netlink socket" >&2; exit 1; }
[ "${FAULT:-}" = ss_empty ] && exit 0
echo "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process"
if [ -f "$MANIFEST" ] || [ "${FAULT:-}" = restart_not_observed ]; then echo "LISTEN 0      4096               *:6443            *:*"; else touch "$FX/state/was_down"; fi
S
  cat > "$FX/bin/kubectl" <<'S'
#!/usr/bin/env bash
ST="$FX/state"; server=""; ns=""; args=(); rest=()
for a in "$@"; do case "$a" in --kubeconfig=*) ;; --server=*) server="${a#--server=}";; *) rest+=("$a");; esac; done
i=0; while [ $i -lt ${#rest[@]} ]; do if [ "${rest[$i]}" = "-n" ]; then ns="${rest[$((i+1))]}"; i=$((i+2)); else args+=("${rest[$i]}"); i=$((i+1)); fi; done
explicit_server="$server"; [ -n "$server" ] || server="${FX_KUBECONFIG_SERVER:-https://10.1.93.109:6443}"
SELF="https://10.1.93.104:6443"; LB="https://10.1.93.109:6443"
step="$(cat "$STEP_FILE" 2>/dev/null)"
# 컨테이너 ID: manifest 가 내려갔다 올라오면 세대가 바뀐다(cid_unchanged 결함이면 안 바뀜)
if [ -f "$ST/was_down" ] && [ -f "$MANIFEST" ] && [ "${FAULT:-}" != cid_unchanged ]; then echo $(( $(cat "$ST/gen") + 1 )) > "$ST/gen"; rm -f "$ST/was_down"; fi
self_down() { [ "$server" = "$SELF" ] && [ ! -f "$MANIFEST" ]; }
fault_at() { [ "${FAULT:-}" = "$1" ] && [ "$step" = "$2" ]; }
wants_uid() { [[ " ${args[*]} " == *"metadata.uid"* ]]; }
mode_prefix() { if grep -E '^\s*- (secretbox|identity)' "$ENC" | head -1 | grep -q identity; then printf 'k8s\0\n\f\n\002v1\n\006Secret'; else printf 'k8s:enc:secretbox:v1:key1:'; fi; }
all_names() { cat "$ST/base-secrets"; for f in "$ST"/store/*/*; do [ -f "$f" ] && echo "$(basename "$(dirname "$f")")/$(basename "$f")"; done; }
new_uid() { local n; n=$(cat "$ST/uidseq" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$ST/uidseq"; echo -n "uid-$n" > "$ST/uid/$1"; cat "$ST/uid/$1"; }
case "${args[0]:-} ${args[1]:-}" in
  "get nodes")
    if [[ " ${args[*]} " == *" --no-headers "* ]]; then for n in cp1 cp2 cp3 w1 w2 w3; do echo "k8s-$n Ready control-plane 20d v1.36.4"; done
    else echo "NAME STATUS"; for n in cp1 cp2 cp3 w1 w2 w3; do echo "k8s-$n Ready"; done; fi; exit 0 ;;
  "get --raw=/readyz") if self_down; then echo "connection refused" >&2; exit 1; fi; printf 'ok'; exit 0 ;;
  "config view") echo -n "${FX_KUBECONFIG_SERVER:-https://10.1.93.109:6443}"; exit 0 ;;
  "get pod") echo -n "containerd://cid-$(cat "$ST/gen")"; exit 0 ;;
  "get pods")
    # v4.4: 3 CP mirror Pod. FAULT 로 post(step=cleanup/audit_after)·pre(step 비어 있음) 구간에 결함을 주입한다.
    phase=other; [[ "$step" == cleanup || "$step" == audit_after ]] && phase=post; [ -z "$step" ] && phase=pre
    r3=true; rows=3; c=0
    if [ "$phase" = post ]; then c=$(( $(cat "$ST/podcalls" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$ST/podcalls"; fi
    case "${FAULT:-}:$phase" in
      pods_transient:post) [ "$c" -le 2 ] && r3=false ;;
      pods_never:post)     r3=false ;;
      pods_missing_row:post) rows=2 ;;
      pods_pre_notready:pre) r3=false ;;
      pods_rc_fail:post)   echo "Error from server: etcdserver: request timed out" >&2; exit 1 ;;
      pods_timeout:post)   sleep 5 ;;
    esac
    echo "NAME READY RESTARTS"
    echo "kube-apiserver-k8s-control01 true 4"
    [ "$rows" -ge 3 ] && echo "kube-apiserver-k8s-control02 true 25"
    echo "kube-apiserver-k8s-control03 $r3 0"
    exit 0 ;;
  "get secrets")
    [ "${FAULT:-}" = secrets_all_fail ] && { echo 'Error from server (Forbidden): secrets is forbidden' >&2; exit 1; }
    if [[ " ${args[*]} " == *"custom-columns="* ]]; then
      if [ "$step" = snapshot_before ] && [ "${FAULT:-}" = rv_before_rc ]; then echo 'fds-app harbor-pull base-uid 1'; exit 42; fi
      if [ "$step" = audit_after ]; then
        case "${FAULT:-}" in
          rv_after_rc) echo 'fds-app harbor-pull base-uid 1'; exit 42 ;;
          rv_after_bad) echo 'malformed'; exit 0 ;;
        esac
      fi
      while read -r key; do
        ns1="${key%/*}"; n1="${key#*/}"; uid="base-$n1"; rv=1
        if [ -f "$ST/uid/$n1" ]; then uid="$(cat "$ST/uid/$n1")"; fi
        if [ "$step" = audit_after ] && [ "$key" = fds-app/harbor-pull ]; then
          case "${FAULT:-}" in rv_plain|rv_encrypted|rv_query|rv_unknown|rv_empty|rv_bail) rv=2 ;; rv_recreated) uid=replaced-uid ;; esac
        fi
        printf '%s %s %s %s\n' "$ns1" "$n1" "$uid" "$rv"
      done < <(all_names)
      if [ "$step" = audit_after ]; then
        case "${FAULT:-}" in
          rv_new) echo 'fds-app new-secret uid-new 2' ;;
          rv_foreign_prefix) echo 'default enc-rb-test-foreign uid-foreign 2' ;;
        esac
      fi
      exit 0
    fi
    if self_down; then exit 1; fi; all_names | awk -F/ '{print $1" "$2" Opaque 1 1d"}'; exit 0 ;;
  "get secret")
    if [ "${args[2]:-}" = "--no-headers" ] || [ -z "${args[2]:-}" ]; then
      [ "${FAULT:-}" = secret_list_fail ] && { echo 'Error from server (Forbidden): secrets is forbidden' >&2; exit 1; }
      for f in "$ST"/store/default/*; do [ -f "$f" ] && echo "$(basename "$f") Opaque 1 1s"; done; exit 0
    fi
    name="${args[2]}"
    if fault_at lb_read_fail read_b_lb && [ "$explicit_server" = "$LB" ]; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at peer_read_fail read_b_peer; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at post_read_fail read_b_after; then echo "Unable to connect to the server" >&2; exit 1; fi
    # 원복 후: 대상 CP 직접만 실패 / LB 만 실패 / 다른 CP 만 실패
    if fault_at post_self_read_fail read_b_after && [ "$explicit_server" = "$SELF" ]; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at post_self_read_a_fail read_a_after && [ "$explicit_server" = "$SELF" ]; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at post_lb_read_fail read_b_after_lb; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at post_peer_read_fail read_b_after_peer; then echo "Unable to connect to the server" >&2; exit 1; fi
    if fault_at read_old_fail read_a_rolled; then echo "Forbidden" >&2; exit 1; fi
    if self_down; then echo "connection refused" >&2; exit 1; fi
    if [ -f "$ST/store/${ns:-default}/$name" ]; then
      if wants_uid; then
        u="$(cat "$ST/uid/$name" 2>/dev/null)"
        # 정리 단계에서 외부가 같은 이름으로 다시 만든 상황(UID 교체)
        if [ "${FAULT:-}" = uid_replaced ] && [ "$step" = cleanup ] && [[ "$name" == *-b ]]; then u="uid-foreign"; fi
        echo -n "$u"; exit 0
      fi
      echo -n "$name"; exit 0
    fi
    echo "Error from server (NotFound): secrets \"$name\" not found" >&2; exit 1 ;;
  "create secret")
    name="${args[3]}"
    if self_down; then echo "connection refused" >&2; exit 1; fi
    # precheck 이후 외부가 같은 이름을 먼저 만든 경쟁(AlreadyExists)
    if [ "${FAULT:-}" = race_exists ] && [ "$step" = create_a ] && [ ! -f "$ST/store/default/$name" ]; then echo -n foreign-value > "$ST/store/default/$name"; echo -n uid-foreign > "$ST/uid/$name"; fi
    if [ -f "$ST/store/default/$name" ]; then echo "Error from server (AlreadyExists): secrets \"$name\" already exists" >&2; exit 1; fi
    if fault_at prefix_before_plain create_a || fault_at restored_prefix_plain create_c; then printf 'k8s\0\n\f\n\002v1\n\006Secret' > "$ST/store/default/$name"
    elif fault_at post_self_write_plain create_c && [ "$explicit_server" = "$SELF" ]; then printf 'k8s\0\n\f\n\002v1\n\006Secret' > "$ST/store/default/$name"
    else mode_prefix > "$ST/store/default/$name"; fi
    u="$(new_uid "$name")"
    # 응답 유실: 객체는 만들어졌는데 클라이언트는 실패로 본다
    if fault_at create_ambiguous create_b; then echo "Error: timeout waiting for response" >&2; exit 1; fi
    if wants_uid; then echo -n "$u"; fi
    exit 0 ;;
  "delete --raw")
    # kubectl delete --raw /api/v1/namespaces/default/secrets/<name> -f -  (본문 = DeleteOptions.preconditions.uid)
    path="${args[2]}"; name="${path##*/}"; body="$(cat)"
    echo "raw $name $body" >> "$ST/delete-raw.log"
    want="$(echo "$body" | sed -n 's/.*"preconditions":{"uid":"\([^"]*\)"}.*/\1/p')"
    have="$(cat "$ST/uid/$name" 2>/dev/null)"
    # GET 과 DELETE 사이에 외부가 같은 이름·다른 UID 로 교체한 상황
    if [ "${FAULT:-}" = uid_replaced ] && [ "$step" = cleanup ] && [[ "$name" == *-b ]]; then have="uid-foreign"; fi
    if [ "${FAULT:-}" = delete_fail_unknown ] && [[ "$name" == *-b ]]; then echo "Unable to connect to the server: connection refused" >&2; exit 1; fi
    if [ ! -f "$ST/store/default/$name" ]; then echo "Error from server (NotFound): secrets \"$name\" not found" >&2; exit 1; fi
    if [ -z "$want" ] || [ "$want" != "$have" ]; then echo "Error from server (Conflict): Precondition failed: UID in precondition: $want, UID in object meta: $have" >&2; exit 1; fi
    # cleanup_fail: 삭제 요청은 성공(rc=0)이지만 객체가 남아 있는 상황
    if [ "${FAULT:-}" = cleanup_fail ] && [[ "$name" == *-b ]]; then :; else rm -f "$ST/store/default/$name"; fi
    echo '{"kind":"Status","apiVersion":"v1","status":"Success"}'; exit 0 ;;
  "delete secret")
    for n in "${args[@]:2}"; do case "$n" in --*) ;; *) echo "delete $n" >> "$ST/delete.log"; if [ "${FAULT:-}" = cleanup_fail ] && [[ "$n" == *-b ]]; then :; else rm -f "$ST/store/default/$n"; fi;; esac; done
    exit 0 ;;
  "exec etcd-"*)
    all="${args[*]}"
    case "$all" in
      *"endpoint health"*) if [ "${FAULT:-}" = etcd_unhealthy ]; then echo '[{"endpoint":"https://10.1.93.102:2379","health":true},{"endpoint":"https://10.1.93.103:2379","health":false},{"endpoint":"https://10.1.93.104:2379","health":true}]'
        else echo '[{"endpoint":"https://10.1.93.102:2379","health":true},{"endpoint":"https://10.1.93.103:2379","health":true},{"endpoint":"https://10.1.93.104:2379","health":true}]'; fi ;;
      *"endpoint status"*) echo "ENDPOINT ID LEADER"; echo "a 1 false" ;;
      *"get /registry/secrets/"*)
        # etcd 조회 결함 주입: 조회 실패(rc=1) / 성공이지만 빈 값 / 알 수 없는 형식
        if fault_at etcd_query_fail_b prefix_b || fault_at etcd_query_fail_c prefix_c; then echo "Error: context deadline exceeded" >&2; exit 1; fi
        if fault_at etcd_empty_b prefix_b; then exit 0; fi
        if fault_at etcd_unknown_b prefix_b; then printf 'GARBAGE-NOT-A-K8S-OBJECT-0123456789'; exit 0; fi
        if [ "$step" = audit_after ]; then
          case "${FAULT:-}" in
            rv_query) exit 42 ;;
            rv_empty) exit 0 ;;
            rv_unknown) printf UNKNOWN; exit 0 ;;
            rv_encrypted) printf 'k8s:enc:secretbox:v1:key1:'; exit 0 ;;
            rv_plain|rv_new|rv_foreign_prefix|rv_recreated|rv_bail) printf 'k8s\0SENSITIVE-PAYLOAD-MUST-NOT-LEAK'; exit 0 ;;
          esac
        fi
        p="${all#*get /registry/secrets/}"; p="${p%% *}"; cat "$ST/store/$p" 2>/dev/null ;;
    esac; exit 0 ;;
esac
echo "stub: unhandled: ${args[*]}" >&2; exit 1
S
  mv "$FX/bin/kubectl" "$FX/bin/kubectl-base"
  cat > "$FX/bin/kubectl" <<'WRAP'
#!/usr/bin/env bash
all=" $* "
step=$(cat "$STEP_FILE" 2>/dev/null)
fault="${FAULT:-}"
case "$fault:$all" in
  readyz_rc:*"get --raw=/readyz"*) printf ok; exit 42 ;;
  nodes_rc:*"get nodes"*|pods_rc:*"get pods"*|status_rc:*"endpoint status"*|config_rc:*"config view"*)
    "$(dirname "$0")/kubectl-base" "$@"; exit 42 ;;
  secrets_rc:*"get secrets"*|default_rc:*"get secret --no-headers"*)
    "$(dirname "$0")/kubectl-base" "$@"; exit 42 ;;
  rv_bail:*"get secrets"*|rolled_list_rc:*"get secrets"*)
    if [ "$step" = rolled_list ]; then "$(dirname "$0")/kubectl-base" "$@"; exit 42; fi ;;
  health_rc:*"endpoint health"*) "$(dirname "$0")/kubectl-base" "$@"; exit 42 ;;
  health_malformed:*"endpoint health"*) printf '[{"health":true}'; exit 0 ;;
  health_duplicate:*"endpoint health"*) printf '[{"endpoint":"https://10.1.93.102:2379","health":true},{"endpoint":"https://10.1.93.102:2379","health":true},{"endpoint":"https://10.1.93.104:2379","health":true}]'; exit 0 ;;
  health_unexpected:*"endpoint health"*) "$(dirname "$0")/kubectl-base" "$@" | sed s/10.1.93.103/10.1.93.199/; exit 0 ;;
  health_missing:*"endpoint health"*) printf '[{"health":true},{"health":true},{"health":true}]'; exit 0 ;;
esac
exec "$(dirname "$0")/kubectl-base" "$@"
WRAP
  cat > "$FX/bin/tar" <<'WRAP'
#!/usr/bin/env bash
[ "${FAULT:-}" = seal_tar ] && exit 42
/usr/bin/tar "$@" || exit $?
if [ "${FAULT:-}" = seal_sidecar ]; then
  # czf archive: obstruct sidecar creation without root permission assumptions.
  mkdir "$2.sha256"
fi
WRAP
  cat > "$FX/bin/sha256sum" <<'WRAP'
#!/usr/bin/env bash
if [ "${FAULT:-}" = seal_checksum ] && [[ "$*" == *".evidence.tar.gz"* ]]; then exit 42; fi
exec /usr/bin/sha256sum "$@"
WRAP
  cat > "$FX/bin/python3" <<'WRAP'
#!/usr/bin/env bash
[ "${FAULT:-}" = no_parser ] && exit 127
exec "$FIXTURE_PYTHON" "$@"
WRAP
  chmod +x "$FX/bin/"*
}

run_case() {   # $1 name, $2 mode, rest = env assignments
  if [ -n "${ONLY:-}" ] && [[ ",$ONLY," != *",$1,"* ]]; then SKIPPED=1; CASE_FX=/nonexistent; return 0; fi; SKIPPED=0
  local name="$1" mode="$2"; shift 2
  local FX; FX="$(mktemp -d)"; make_fixture "$FX"
  export FX; export MANIFEST="$FX/etc/manifests/kube-apiserver.yaml"; export ENC="$FX/etc/enc/encryption-config.yaml"
  ORIG_ENC_SHA="$(sha256sum "$ENC" | awk '{print $1}')"
  # 대상 CP 의 설정 사본. BK=mismatch(해시 다름) / BK=none(미지정) 으로 결함 주입
  local BKF="$FX/root/enc-backup.yaml"; cp -p "$ENC" "$BKF"; [ "${BK:-}" = mismatch ] && echo changed >> "$BKF"; chmod 600 "$BKF"
  [ "${BK:-}" = none ] && BKF=""
  local envs=(ENC_BACKUP="$BKF" FIXTURE=1 ENC="$ENC" MANIFEST="$MANIFEST" PARK="$FX/etc/manifests-parked" ADMIN="$FX/etc/admin.conf" OUT_BASE="$FX/root" \
    STEP_FILE="$FX/state/step" HOST=k8s-control03 SELF_IP=10.1.93.104 POLL_SEC=1 DOWN_WAIT_SEC=3 READY_WAIT_SEC=4 APISERVER_POD_WAIT_SEC=6 CALL_TIMEOUT_SEC=2 \
    PEER_SHAS="$ORIG_ENC_SHA $ORIG_ENC_SHA" FX="$FX" PATH="$FX/bin:$PATH" CONFIRM=YES)
  env "${envs[@]}" "$@" bash "$SCRIPT" "$mode" > "$FX/out.log" 2>&1
  RC=$?
  RUNDIR="$(ls -d "$FX"/root/p1-sec01-secretbox-"$mode"-* 2>/dev/null | head -1)"
  LOGF="$FX/out.log"   # final result is emitted only after archive seal
  CASE_FX="$FX"
}
check() {   # $1 case, $2 description, $3 condition result(0=ok)
  [ "${SKIPPED:-0}" = 1 ] && return 0
  if [ "$3" -eq 0 ]; then PASS=$((PASS+1)); RESULTS+=("  PASS  $1: $2"); else FAILN=$((FAILN+1)); RESULTS+=("  FAIL  $1: $2"); fi
}
has() { grep -q -- "$1" "$LOGF" 2>/dev/null; }
enc_same() { [ "$(sha256sum "$ENC" | awk '{print $1}')" = "$ORIG_ENC_SHA" ]; }
no_leftover() { [ -z "$(ls "$CASE_FX"/state/store/default 2>/dev/null)" ]; }
manifest_ok() { [ -f "$MANIFEST" ]; }
store_has() { ls "$CASE_FX"/state/store/default 2>/dev/null | grep -q -- "$1"; }
was_deleted() { grep -q -- "$1" "$CASE_FX/state/delete.log" 2>/dev/null; }   # 이름 기반 삭제(v4.1 에서는 어떤 경우에도 호출되면 안 됨)
no_name_delete() { [ ! -s "$CASE_FX/state/delete.log" ]; }
raw_attempted() { grep -qE "^raw enc-rb-test-[0-9a-f-]+-$1 " "$CASE_FX/state/delete-raw.log" 2>/dev/null; }   # $1 = a|b|c : 서버 측 UID precondition 삭제 요청을 보냈는가
raw_body_ok() {   # 소유 기록(이름 UID)마다 삭제 본문에 정확한 preconditions.uid 가 실렸는가
  local n u ok=0; while read -r n u; do [ -n "$n" ] || continue
    grep -qF "raw $n {\"apiVersion\":\"v1\",\"kind\":\"DeleteOptions\",\"preconditions\":{\"uid\":\"$u\"}}" "$CASE_FX/state/delete-raw.log" 2>/dev/null || ok=1; done < "$RUNDIR/10-owned-secrets.txt"; return $ok; }
not_pass() { ! has "RESULT: PASS"; }

# S1 정상
run_case S1 rollback-test
check S1 "정상 경로가 PASS(rc=0, RESULT: PASS)" $([ "$RC" -eq 0 ] && has "RESULT: PASS"; echo $?)
check S1 "원래 설정 해시 복원" $(enc_same; echo $?)
check S1 "시험 Secret 잔존 0" $(no_leftover; echo $?)
check S1 "재기동 관측 기록(container ID 변경)" $(has "재기동 관측"; echo $?)
check S1 "LB 와 다른 CP 를 각각 읽음" $(has "LB 와 다른 CP"; echo $?)
check S1 "원복 후 읽기: 대상 CP 직접·LB·다른 CP" $(has "복원 후 읽기: 대상 CP 직접(a, b), LB(b), 다른 CP(b)"; echo $?)
check S1 "rollback/restore 재기동 증거가 별도 파일(6개)" $( (cd "$RUNDIR" && for l in rollback restore; do for k in cid-before cid-after observed; do [ -s "restart-$l-$k.txt" ] || exit 1; done; done); echo $?)
check S1 "rollback 과 restore 의 before container ID 가 서로 다름(덮어쓰기 없음)" $( [ "$(cat "$RUNDIR/restart-rollback-cid-before.txt")" != "$(cat "$RUNDIR/restart-restore-cid-before.txt")" ]; echo $?)
check S1 "소유 Secret 3개가 UID 와 함께 기록" $( [ "$(wc -l < "$RUNDIR/10-owned-secrets.txt")" -eq 3 ] && grep -q 'uid-' "$RUNDIR/10-owned-secrets.txt"; echo $?)
check S1 "삭제는 서버 측 UID precondition(--raw DELETE + DeleteOptions.preconditions.uid)으로, 본문 UID 가 생성 응답 UID 와 일치" $(raw_body_ok; echo $?)
check S1 "이름 기반 삭제(fallback)는 한 번도 호출되지 않음" $(no_name_delete; echo $?)
check S1 "50-cleanup.txt 에 삭제 본문(preconditions.uid) 기록" $(grep -q '"preconditions":{"uid":"uid-' "$RUNDIR/50-cleanup.txt"; echo $?)
check S1 "시험 Secret 이름에 실행 고유 ID 포함" $(grep -q 'enc-rb-test-[0-9]\{14\}-[0-9a-f]\{8\}-a' "$RUNDIR/10-owned-secrets.txt"; echo $?)
check S1 "etcd 접두어 파일에 class 기록(SECRETBOX/PLAIN/SECRETBOX)" $( [ "$(grep -h '^class=' "$RUNDIR"/13-prefix-before.txt "$RUNDIR"/33-prefix-new-secret-rolled.txt "$RUNDIR"/40-prefix-after-restore.txt | tr '\n' ' ')" = "class=SECRETBOX class=PLAIN class=SECRETBOX " ]; echo $?)
ARC="$(ls "$CASE_FX"/root/*.evidence.tar.gz 2>/dev/null | head -1)"
mkdir -p "$CASE_FX/x"; tar xzf "$ARC" -C "$CASE_FX/x" 2>/dev/null
check S1 "아카이브에 .orig/.rolled/.bak/.db 없음" $([ -z "$(find "$CASE_FX/x" \( -name '*.orig' -o -name '*.rolled' -o -name '*.bak' -o -name '*.db' \) | head -1)" ]; echo $?)
check S1 "아카이브 전체에 키 원문(FAKEKEY) 없음" $(! grep -rq FAKEKEY "$CASE_FX/x"; echo $?)
check S1 "아카이브에 etcd 원문 임시 파일(.etcd-raw.*) 없음" $([ -z "$(find "$CASE_FX/x" -name '.etcd-raw.*' | head -1)" ]; echo $?)
check S1 "run.log 에 스크립트 SHA-256 기록" $(has "sha256="; echo $?)
rm -rf "$CASE_FX"

# 재기동 미관측
run_case S2 rollback-test FAULT=restart_not_observed
check S2 "listener 가 안 내려가면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
check S2 "재기동 미관측 사유 기록" $(has "재기동 미관측"; echo $?)
check S2 "PASS 로 끝나지 않음" $(not_pass; echo $?)
check S2 "설정 원복·manifest 복원" $( (enc_same && manifest_ok); echo $?)
check S2 "시험 Secret 잔존 0" $(no_leftover; echo $?)
rm -rf "$CASE_FX"
run_case S2b rollback-test FAULT=cid_unchanged
check S2b "container ID 가 안 바뀌면 FAIL" $([ "$RC" -ne 0 ] && has "container ID 가 바뀌지 않음"; echo $?)
check S2b "설정 원복·시험 Secret 0" $( (enc_same && no_leftover); echo $?)
rm -rf "$CASE_FX"

# B1(listener): 관측 오류를 down 으로 보지 않는다
for spec in "S2c:ss_error" "S2d:ss_empty"; do
  IFS=: read -r cs fault <<<"$spec"
  run_case "$cs" rollback-test FAULT="$fault"
  check "$cs" "$fault: 관측 오류면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
  check "$cs" "$fault: '관측 오류'로 기록, down 으로 오인하지 않음" $( (has "listener 관측 오류" && ! has "재기동 관측("); echo $?)
  check "$cs" "$fault: PASS 로 끝나지 않음" $(not_pass; echo $?)
  check "$cs" "$fault: 설정 원복·manifest 복원" $( (enc_same && manifest_ok); echo $?)
  check "$cs" "$fault: 시험 Secret 잔존 0" $(no_leftover; echo $?)
  rm -rf "$CASE_FX"
done

# AC-critical 실패 주입 -> 모두 FAIL
for spec in "S3:lb_read_fail:LB(" "S4:peer_read_fail:다른 CP(" "S5:restored_prefix_plain:복원 후 신규 Secret 이 secretbox" "S6:post_read_fail:복원 후 평문 b 읽기 실패" "S7b:read_old_fail:기존 암호화 Secret 을 읽지 못함"; do
  IFS=: read -r cs fault msg <<<"$spec"
  run_case "$cs" rollback-test FAULT="$fault"
  check "$cs" "$fault 주입 시 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
  check "$cs" "사유 기록: $msg" $(has "$msg"; echo $?)
  check "$cs" "PASS 로 끝나지 않음" $(not_pass; echo $?)
  check "$cs" "설정 원복(복원 경로)" $(enc_same; echo $?)
  check "$cs" "시험 Secret 정리" $(no_leftover; echo $?)
  rm -rf "$CASE_FX"
done

# B2: 대상 CP 직접 endpoint 에서만 실패해도(LB 는 정상) FAIL 이어야 한다. v3 는 LB 로 검증해 PASS 했다.
for spec in "S5b:post_self_write_plain:복원 후 신규 Secret 이 secretbox" "S6b:post_self_read_fail:복원 후 평문 b 읽기 실패" "S6c:post_self_read_a_fail:복원 후 암호화 a 읽기 실패" "S6d:post_lb_read_fail:복원 후 LB(" "S6e:post_peer_read_fail:복원 후 다른 CP("; do
  IFS=: read -r cs fault msg <<<"$spec"
  run_case "$cs" rollback-test FAULT="$fault"
  check "$cs" "$fault(LB 등 다른 경로는 정상) 주입 시 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
  check "$cs" "사유 기록: $msg" $(has "$msg"; echo $?)
  check "$cs" "PASS 로 끝나지 않음" $(not_pass; echo $?)
  check "$cs" "설정 원복" $(enc_same; echo $?)
  check "$cs" "시험 Secret 정리" $(no_leftover; echo $?)
  rm -rf "$CASE_FX"
done

# B1(etcd): 조회 실패·빈 값·알 수 없는 형식은 평문으로 인정하지 않는다
for spec in "S13:etcd_query_fail_b:etcd 조회 실패 또는 값 없음(class=QUERY_FAILED)" "S13b:etcd_empty_b:etcd 조회 실패 또는 값 없음(class=EMPTY)" "S13c:etcd_unknown_b:평문(identity)으로 확인하지 못함(class=UNKNOWN_FORMAT)" "S13d:etcd_query_fail_c:복원 후 c: etcd 조회 실패"; do
  IFS=: read -r cs fault msg <<<"$spec"
  run_case "$cs" rollback-test FAULT="$fault"
  check "$cs" "$fault 주입 시 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
  check "$cs" "사유 기록: $msg" $(has "$msg"; echo $?)
  check "$cs" "PASS 로 끝나지 않음" $(not_pass; echo $?)
  check "$cs" "설정 원복·manifest 복원" $( (enc_same && manifest_ok); echo $?)
  check "$cs" "시험 Secret 정리" $(no_leftover; echo $?)
  rm -rf "$CASE_FX"
done

# B1(precheck): 조회 실패가 0건 GO 로 바뀌지 않는다
run_case S14 precheck FAULT=secret_list_fail
check S14 "default Secret 조회 실패면 NO-GO(rc!=0)" $([ "$RC" -ne 0 ] && has "NO-GO"; echo $?)
check S14 "사유: default Namespace Secret 조회 실패" $(has "default Namespace Secret 조회 실패"; echo $?)
check S14 "GO 로 끝나지 않음" $(! has "RESULT: GO"; echo $?)
rm -rf "$CASE_FX"
run_case S14b precheck FAULT=secrets_all_fail
check S14b "전체 Secret 목록 조회 실패면 NO-GO" $([ "$RC" -ne 0 ] && has "NO-GO" && has "Secret 목록 조회 실패"; echo $?)
rm -rf "$CASE_FX"
run_case S14c rollback-test FAULT=secret_list_fail
check S14c "rollback-test 도 게이트에서 중단, 변경 없음" $( ([ "$RC" -ne 0 ] && enc_same && ! has "재기동 관측"); echo $?)
rm -rf "$CASE_FX"

# 설정 변경 전 실패에도 시험 Secret 정리, 재기동 없음
run_case S7 rollback-test FAULT=prefix_before_plain
check S7 "변경 전 실패는 FAIL" $([ "$RC" -ne 0 ]; echo $?)
check S7 "시험 Secret a 정리(잔존 0)" $(no_leftover; echo $?)
check S7 "재기동 안 함(설정 변경 전 복원 불필요 기록)" $(has "설정 변경 전이므로 복원 불필요"; echo $?)
check S7 "설정 파일 그대로" $(enc_same; echo $?)
check S7 "정리 확인(NotFound) 기록" $(has "시험 Secret 정리 확인"; echo $?)
rm -rf "$CASE_FX"

# B3: 정리 실패는 ERROR/FAIL 로 상향
run_case S8 rollback-test FAULT=cleanup_fail
check S8 "정리 실패(잔존)는 FAIL" $([ "$RC" -ne 0 ] && has "ERROR: 시험 Secret 잔존"; echo $?)
check S8 "PASS 로 끝나지 않음" $(not_pass; echo $?)
check S8 "이름 기반 삭제 fallback 없음" $(no_name_delete; echo $?)
rm -rf "$CASE_FX"

# B3: 소유하지 않은 객체는 삭제하지 않는다
# (1) AlreadyExists: 게이트 이후 외부가 같은 이름의 Secret 을 먼저 만듦 -> 생성 실패, 외부 객체 보존
run_case S15 rollback-test FAULT=race_exists
check S15 "AlreadyExists 면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
check S15 "소유 불명확·삭제하지 않음 기록" $(has "소유 불명확, 삭제하지 않음"; echo $?)
check S15 "외부 객체가 보존됨(삭제 요청 자체를 보내지 않음, 이름 삭제 없음)" $( (store_has "-a" && ! raw_attempted a && no_name_delete); echo $?)
check S15 "PASS 로 끝나지 않음" $(not_pass; echo $?)
check S15 "설정 파일 그대로(변경 전 중단)" $(enc_same; echo $?)
rm -rf "$CASE_FX"
# (2) UID 교체: 정리 직전 외부가 같은 이름으로 다시 만든 b -> 삭제하지 않고 FAIL, a·c 는 정상 정리
run_case S16 rollback-test FAULT=uid_replaced
check S16 "UID 불일치면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
check S16 "UID precondition conflict·삭제하지 않음·fallback 하지 않음 기록" $(has "UID precondition conflict" && has "fallback 하지 않음"; echo $?)
check S16 "외부 b 보존: 서버가 Conflict 로 거부했고 이름 삭제로 fallback 하지 않음" $( (store_has "-b" && raw_attempted b && no_name_delete); echo $?)
check S16 "이번 실행의 a, c 는 UID precondition 으로 삭제됨" $( (! store_has "-a" && ! store_has "-c" && raw_attempted a && raw_attempted c); echo $?)
check S16 "PASS 로 끝나지 않음" $(not_pass; echo $?)
check S16 "원래 설정 해시는 복원됨" $(enc_same; echo $?)
rm -rf "$CASE_FX"
# (3) 응답 유실: 객체는 만들어졌지만 클라이언트는 실패로 봄 -> 소유 불명확, 삭제하지 않고 수동 확인
run_case S17 rollback-test FAULT=create_ambiguous
check S17 "응답 유실이면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
check S17 "소유 불명확·수동 확인 기록" $(has "소유 불명확, 삭제하지 않음. 수동 확인 필요"; echo $?)
check S17 "응답 유실 객체 b 는 삭제 요청도 보내지 않음(소유 미확정)" $( (store_has "-b" && ! raw_attempted b && no_name_delete); echo $?)
check S17 "소유가 확인된 a 는 UID precondition 으로 삭제됨" $( (! store_has "-a" && raw_attempted a); echo $?)
check S17 "원래 설정 복원·manifest 복원" $( (enc_same && manifest_ok); echo $?)
check S17 "PASS 로 끝나지 않음" $(not_pass; echo $?)
rm -rf "$CASE_FX"

# B3(v4.1): 삭제 요청이 실패하거나 결과가 불명확하면 이름 삭제로 fallback 하지 않고 FAIL
run_case S19 rollback-test FAULT=delete_fail_unknown
check S19 "삭제 요청 실패면 FAIL(rc!=0)" $([ "$RC" -ne 0 ]; echo $?)
check S19 "삭제 요청 실패·결과 불명확·fallback 하지 않음 기록" $(has "삭제 요청 실패 또는 결과 불명확" && has "fallback 하지 않음"; echo $?)
check S19 "이름 기반 삭제 fallback 이 호출되지 않음" $(no_name_delete; echo $?)
check S19 "PASS 로 끝나지 않음" $(not_pass; echo $?)
check S19 "삭제 실패한 b 는 남고, a·c 는 정리됨" $( (store_has "-b" && ! store_has "-a" && ! store_has "-c"); echo $?)
rm -rf "$CASE_FX"

# kubeconfig 서버가 LB 가 아니면 NO-GO / 변경 안 함
run_case S9 precheck FX_KUBECONFIG_SERVER=https://10.1.93.103:6443
check S9 "precheck NO-GO(rc!=0)" $([ "$RC" -ne 0 ] && has "NO-GO"; echo $?)
check S9 "사유: 승인 LB 가 아님" $(has "승인 LB"; echo $?)
rm -rf "$CASE_FX"
run_case S9b rollback-test FX_KUBECONFIG_SERVER=https://10.1.93.103:6443
check S9b "rollback-test 도 게이트에서 중단, 변경 없음" $( ([ "$RC" -ne 0 ] && enc_same && ! has "재기동 관측"); echo $?)
rm -rf "$CASE_FX"

# 3CP 설정 해시 동일 확인
run_case S10 precheck PEER_SHAS=""
check S10 "PEER_SHAS 없으면 NO-GO" $([ "$RC" -ne 0 ] && has "3CP 동일 Config 미확인"; echo $?)
rm -rf "$CASE_FX"
run_case S10b precheck PEER_SHAS="deadbeef deadbeef"
check S10b "다른 CP 해시 불일치 NO-GO" $([ "$RC" -ne 0 ] && has "다른 CP 설정 해시"; echo $?)
rm -rf "$CASE_FX"
run_case S10c precheck
check S10c "정상 precheck 는 GO" $([ "$RC" -eq 0 ] && has "RESULT: GO"; echo $?)
rm -rf "$CASE_FX"

# 복구 수단: 이 CP 에 실제 있는 설정 사본(ENC_BACKUP)이 없거나 해시가 다르면 NO-GO, 변경 없음
BK=none run_case S18 precheck
check S18 "ENC_BACKUP 미지정이면 NO-GO" $([ "$RC" -ne 0 ] && has "ENC_BACKUP 미지정"; echo $?)
rm -rf "$CASE_FX"
BK=mismatch run_case S18b precheck
check S18b "ENC_BACKUP 해시 불일치면 NO-GO" $([ "$RC" -ne 0 ] && has "ENC_BACKUP 해시가 현재 설정과 다름"; echo $?)
rm -rf "$CASE_FX"
BK=none run_case S18c rollback-test
check S18c "ENC_BACKUP 없으면 rollback-test 도 게이트에서 중단, 변경 없음" $( ([ "$RC" -ne 0 ] && enc_same && ! has "재기동 관측"); echo $?)
rm -rf "$CASE_FX"
BK=
run_case S18d precheck ENC_BACKUP=/nonexistent/enc-backup.yaml
check S18d "ENC_BACKUP 경로가 이 CP 에 없으면 NO-GO" $([ "$RC" -ne 0 ] && has "ENC_BACKUP 파일 없음"; echo $?)
rm -rf "$CASE_FX"

# etcd health 구조 판정
run_case S11 precheck FAULT=etcd_unhealthy
check S11 "etcd unhealthy 이면 NO-GO" $([ "$RC" -ne 0 ] && has "etcd"; echo $?)
rm -rf "$CASE_FX"

# 재실행 안전: 같은 fixture 에서 정상 실행 2회(이름이 실행마다 달라 충돌하지 않음)
run_case S12 rollback-test
FX1="$CASE_FX"; ENC_AFTER1="$(sha256sum "$ENC" | awk '{print $1}')"
env FIXTURE=1 ENC_BACKUP="$FX1/root/enc-backup.yaml" ENC="$ENC" MANIFEST="$MANIFEST" PARK="$FX1/etc/manifests-parked" ADMIN="$FX1/etc/admin.conf" OUT_BASE="$FX1/root" STEP_FILE="$FX1/state/step" HOST=k8s-control03 SELF_IP=10.1.93.104 POLL_SEC=1 DOWN_WAIT_SEC=3 READY_WAIT_SEC=4 APISERVER_POD_WAIT_SEC=6 CALL_TIMEOUT_SEC=2 PEER_SHAS="$ORIG_ENC_SHA $ORIG_ENC_SHA" FX="$FX1" PATH="$FX1/bin:$PATH" CONFIRM=YES bash "$SCRIPT" rollback-test > "$FX1/out2.log" 2>&1; RC2=$?
check S12 "재실행도 PASS(재실행 안전)" $([ "$RC" -eq 0 ] && [ "$RC2" -eq 0 ] && [ "$ENC_AFTER1" = "$ORIG_ENC_SHA" ]; echo $?)
rm -rf "$FX1"

# S20: plausible stdout must never hide a failed command. Gate failures do not mutate ENC/manifest.
for fault in health_rc health_malformed health_duplicate health_unexpected health_missing readyz_rc nodes_rc status_rc pods_rc config_rc secrets_rc default_rc no_parser; do
  name="S20_$fault"
  run_case "$name" rollback-test FAULT="$fault"
  check "$name" "조회 실패/잘못된 JSON: nonzero, GO/PASS 없음" $([ "$RC" -ne 0 ] && ! has "RESULT: GO" && not_pass; echo $?)
  check "$name" "Gate 거부: ENC 동일, manifest 존재, 재기동/Secret 생성 없음" $(enc_same && manifest_ok && [ "$(cat "$CASE_FX/state/gen" 2>/dev/null)" = 1 ] && [ ! -e "$CASE_FX/state/uidseq" ]; echo $?)
  rm -rf "$CASE_FX"
done

# S21: failures after actual fixture mutation must still restore and perform UID cleanup.
for fault in seal_tar seal_checksum seal_sidecar rolled_list_rc; do
  name="S21_$fault"
  run_case "$name" rollback-test FAULT="$fault"
  check "$name" "nonzero, 전체 PASS 없음" $([ "$RC" -ne 0 ] && not_pass; echo $?)
  check "$name" "ENC/manifest 복구 및 UID cleanup 계속" $(enc_same && manifest_ok && no_leftover && raw_attempted a && no_name_delete; echo $?)
  if [ "$fault" != rolled_list_rc ]; then
    check "$name" "봉인 실패가 완료 기록과 stdout에 INCOMPLETE" $(has "EVIDENCE_STATUS=INCOMPLETE" && grep -q 'EVIDENCE_STATUS=INCOMPLETE' "$RUNDIR.completion.txt"; echo $?)
  fi
  rm -rf "$CASE_FX"
done
for mode in precheck postcheck; do
  name="S22_$mode"
  run_case "$name" "$mode" FAULT=seal_tar
  check "$name" "읽기 전용 모드도 봉인 실패 시 nonzero/INCOMPLETE, GO/PASS 없음" $([ "$RC" -ne 0 ] && has 'EVIDENCE_STATUS=INCOMPLETE' && ! has 'RESULT: GO' && not_pass; echo $?)
  rm -rf "$CASE_FX"
done

# S23: non-test Secret updates/creation and every audit failure fail closed.
for fault in rv_plain rv_new rv_foreign_prefix rv_recreated rv_query rv_unknown rv_empty rv_after_rc rv_after_bad rv_bail; do
  name="S23_$fault"
  run_case "$name" rollback-test FAULT="$fault"
  check "$name" "B59-3 검사 실패 nonzero/PASS 없음" $([ "$RC" -ne 0 ] && not_pass && has B59-3; echo $?)
  check "$name" "원복 및 UID cleanup 유지" $(enc_same && manifest_ok && no_leftover && no_name_delete; echo $?)
  check "$name" "Secret payload가 원본·archive에 노출되지 않음" $(mkdir -p "$CASE_FX/audit-extract"; tar xzf "$RUNDIR.evidence.tar.gz" -C "$CASE_FX/audit-extract"; ! grep -rq SENSITIVE-PAYLOAD "$RUNDIR" "$CASE_FX/audit-extract"; echo $?)
  if [[ "$fault" == rv_plain || "$fault" == rv_new || "$fault" == rv_foreign_prefix || "$fault" == rv_recreated || "$fault" == rv_bail ]]; then
    check "$name" "실제 후보에 PLAIN 기록" $(grep -q 'class=PLAIN' "$RUNDIR/62-secret-audit.txt"; echo $?)
  fi
  rm -rf "$CASE_FX"
done
run_case S23_before rollback-test FAULT=rv_before_rc
check S23_before "사전 snapshot 실패: 변경/재기동0, 소유 a 정리" $([ "$RC" -ne 0 ] && enc_same && manifest_ok && no_leftover && [ "$(cat "$CASE_FX/state/gen")" = 1 ]; echo $?)
rm -rf "$CASE_FX"
for fault in none rv_encrypted; do
  name="S24_$fault"
  run_case "$name" rollback-test FAULT="$fault"
  check "$name" "변경 없음/변경 후 secretbox면 PASS" $([ "$RC" -eq 0 ] && has 'RESULT: PASS'; echo $?)
  if [ "$fault" = none ]; then
    check "$name" "자기 소유 UID만 제외하여 후보0" $([ ! -s "$RUNDIR/61-secret-candidates.txt" ] && has 'candidates=0'; echo $?)
  else
    check "$name" "갱신된 실제 Secret도 암호화 확인" $(grep -q 'fds-app/harbor-pull.*class=SECRETBOX' "$RUNDIR/62-secret-audit.txt"; echo $?)
  fi
  rm -rf "$CASE_FX"
done

# ---- v4.4: post 점검의 apiserver mirror Pod 제한 시간 반복 확인 ----
run_case P1_transient rollback-test FAULT=pods_transient
check P1_transient "방금 재기동한 Pod 의 일시적 NotReady 는 deadline 안에서 회복되면 PASS(rc=0)" $([ "$RC" -eq 0 ] && has "RESULT: PASS"; echo $?)
check P1_transient "시도 기록에 NotReady 시도와 마지막 OK 가 함께 남음" $(grep -q 'NotReady' "$RUNDIR/post-apiserver-pods-attempts.txt" && tail -1 "$RUNDIR/post-apiserver-pods-attempts.txt" | grep -q ' OK$'; echo $?)
check P1_transient "회복 전 일시 실패는 FAIL 로 집계되지 않음(로그에 apiserver Pod 확인 실패 없음)" $(! has "apiserver Pod 확인 실패"; echo $?)
check P1_transient "시험 Secret 정리·원설정 복원은 그대로" $(enc_same && no_leftover; echo $?)
rm -rf "$CASE_FX"
run_case P2_never rollback-test FAULT=pods_never
check P2_never "끝까지 NotReady 면 FAIL(기존 검사 약화 없음, rc≠0, RESULT: PASS 아님)" $([ "$RC" -ne 0 ] && not_pass; echo $?)
check P2_never "실패 사유에 시도 횟수·deadline·마지막 rc 가 남음" $(has "apiserver Pod 확인 실패(post)" && has "시도 " && has "deadline 6초" && has "마지막 rc=0"; echo $?)
check P2_never "마지막 관측 원본(NotReady 행)과 rc 보존" $(grep -q 'kube-apiserver-k8s-control03 false' "$RUNDIR/post-apiserver-pods.txt" && [ "$(cat "$RUNDIR/post-apiserver-pods.rc")" = 0 ]; echo $?)
check P2_never "시도가 deadline 안에서 여러 번 반복됨(2회 이상)" $([ "$(wc -l < "$RUNDIR/post-apiserver-pods-attempts.txt")" -ge 2 ]; echo $?)
check P2_never "점검 실패여도 복원·정리는 완료" $(enc_same && no_leftover; echo $?)
rm -rf "$CASE_FX"
run_case P3_missing rollback-test FAULT=pods_missing_row
check P3_missing "CP Pod 행이 2개뿐이면 FAIL(정확한 행 수)" $([ "$RC" -ne 0 ] && not_pass && has "행 수 2!=3"; echo $?)
rm -rf "$CASE_FX"
run_case P4_rcfail rollback-test FAULT=pods_rc_fail
check P4_rcfail "조회 실패(rc=1)는 FAIL 이고 rc 와 stderr 가 보존됨" $([ "$RC" -ne 0 ] && not_pass && [ "$(cat "$RUNDIR/post-apiserver-pods.rc")" = 1 ] && grep -q 'request timed out' "$RUNDIR/post-apiserver-pods.stderr"; echo $?)
rm -rf "$CASE_FX"
run_case P5_timeout rollback-test FAULT=pods_timeout
check P5_timeout "개별 호출 timeout 은 rc=124 로 기록되고 FAIL" $([ "$RC" -ne 0 ] && not_pass && [ "$(cat "$RUNDIR/post-apiserver-pods.rc")" = 124 ]; echo $?)
rm -rf "$CASE_FX"
run_case P6_pre precheck FAULT=pods_pre_notready
check P6_pre "pre 점검은 단회(대기 없음): NotReady 면 NO-GO, 시도 1회" $([ "$RC" -ne 0 ] && [ "$(wc -l < "$RUNDIR/pre-apiserver-pods-attempts.txt")" = 1 ]; echo $?)
rm -rf "$CASE_FX"
run_case P7_names rollback-test APISERVER_POD_NAMES="kube-apiserver-k8s-control01 kube-apiserver-k8s-control02 kube-apiserver-k8s-control03"
check P7_names "APISERVER_POD_NAMES 가 정확히 일치하면 PASS" $([ "$RC" -eq 0 ] && has "RESULT: PASS"; echo $?)
rm -rf "$CASE_FX"
run_case P8_badnames rollback-test APISERVER_POD_NAMES="kube-apiserver-k8s-control01 kube-apiserver-k8s-control02 kube-apiserver-k8s-control09"
check P8_badnames "기대 이름 집합과 다르면 FAIL" $([ "$RC" -ne 0 ] && not_pass && has "기대 Pod 없음:kube-apiserver-k8s-control09"; echo $?)
rm -rf "$CASE_FX"

# ---- B2: 판정 파서 자체의 실패를 OK 로 처리하지 않는다 (PR #66 독립 리뷰 B2) ----
run_case P9_parser_pre rollback-test FAULT=parser_pre
check P9_parser_pre "pre 파서 오류(rc42·빈 stdout)는 NO-GO(rc≠0, 변경 0)" $([ "$RC" -ne 0 ] && not_pass && enc_same && manifest_ok && [ ! -e "$CASE_FX/state/was_down" ] && no_leftover; echo $?)
check P9_parser_pre "파서 rc(42)와 stderr 보존" $([ "$(cat "$RUNDIR/pre-apiserver-pods-parser.rc")" = 42 ] && grep -q 'injected parser failure' "$RUNDIR/pre-apiserver-pods-parser.stderr"; echo $?)
check P9_parser_pre "시도 기록에 parser_rc=42 와 사유, OK 로 기록되지 않음" $(grep -q 'parser_rc=42 파서 실행 실패(rc=42)' "$RUNDIR/pre-apiserver-pods-attempts.txt" && ! grep -q ' OK$' "$RUNDIR/pre-apiserver-pods-attempts.txt"; echo $?)
check P9_parser_pre "단회 즉시 실패(재시도로 덮지 않음, 시도 1회)" $([ "$(wc -l < "$RUNDIR/pre-apiserver-pods-attempts.txt")" = 1 ]; echo $?)
rm -rf "$CASE_FX"
run_case P10_parser_post rollback-test FAULT=parser_post
check P10_parser_post "post 파서 오류는 FAIL(rc≠0, RESULT: PASS 아님)" $([ "$RC" -ne 0 ] && not_pass && has "파서 실행 실패(rc=42)"; echo $?)
check P10_parser_post "원 설정 복원·시험 Secret 정리·봉인 유지" $(enc_same && manifest_ok && no_leftover && [ -s "$RUNDIR.evidence.tar.gz" ]; echo $?)
check P10_parser_post "파서 rc(42)와 stderr 보존, 시도 기록에 parser_rc=42" $([ "$(cat "$RUNDIR/post-apiserver-pods-parser.rc")" = 42 ] && grep -q 'injected parser failure' "$RUNDIR/post-apiserver-pods-parser.stderr" && grep -q 'parser_rc=42' "$RUNDIR/post-apiserver-pods-attempts.txt"; echo $?)
check P10_parser_post "조회 자체는 정상(kubectl rc 0)이라 조회 실패와 구분됨" $([ "$(cat "$RUNDIR/post-apiserver-pods.rc")" = 0 ]; echo $?)
rm -rf "$CASE_FX"

# ---- B2: Pod 파서 nonzero/빈 stdout 은 GO/PASS 로 바꾸지 않는다 ----
parser_evidence_ok() {
  local phase="$1"
  [ "$(cat "$RUNDIR/$phase-apiserver-pods.rc")" = 0 ] &&
  [ "$(cat "$RUNDIR/$phase-apiserver-pods-parser.rc")" = 42 ] &&
  grep -q 'awk: injected parser failure' "$RUNDIR/$phase-apiserver-pods-parser.stderr" &&
  grep -q 'parser_rc=42' "$RUNDIR/$phase-apiserver-pods-attempts.txt" &&
  [ "$(wc -l < "$RUNDIR/$phase-apiserver-pods-attempts.txt")" -eq 1 ] &&
  ! grep -q ' OK$' "$RUNDIR/$phase-apiserver-pods-attempts.txt"
}
parser_seal_ok() {
  local phase="$1"
  [ -s "$RUNDIR.evidence.tar.gz" ] &&
  [ -s "$RUNDIR.evidence.tar.gz.sha256" ] &&
  (cd "$(dirname "$RUNDIR")" && sha256sum -c "$(basename "$RUNDIR").evidence.tar.gz.sha256" >/dev/null) &&
  grep -q 'EVIDENCE_STATUS=COMPLETE' "$RUNDIR.completion.txt" &&
  tar tzf "$RUNDIR.evidence.tar.gz" | grep -q "/$phase-apiserver-pods-parser.rc$"
}
for spec in "P9_parser_precheck:precheck" "P10_parser_gate:rollback-test"; do
  IFS=: read -r name mode <<<"$spec"
  run_case "$name" "$mode" FAULT=parser_pre
  check "$name" "파서 rc42/빈 stdout: nonzero, GO/PASS 금지" $([ "$RC" -ne 0 ] && ! has 'RESULT: GO' && not_pass; echo $?)
  check "$name" "파서 실패 원인 보존" $(has '파서 실행 실패(rc=42)'; echo $?)
  check "$name" "조회 rc0와 파서 rc42·stderr·단회 시도 구분" $(parser_evidence_ok pre; echo $?)
  check "$name" "Gate 실패: 설정/manifest 보존, 재기동·Secret 생성 0" $(enc_same && manifest_ok && [ "$(cat "$CASE_FX/state/gen")" = 1 ] && [ ! -e "$CASE_FX/state/uidseq" ] && no_leftover; echo $?)
  check "$name" "실패 증적 봉인·SHA256 검증과 파서 기록 포함" $(parser_seal_ok pre; echo $?)
  rm -rf "$CASE_FX"
done
run_case P11_parser_post rollback-test FAULT=parser_post
check P11_parser_post "사후 파서 rc42: nonzero/RESULT FAIL, PASS 금지" $([ "$RC" -ne 0 ] && has 'RESULT: FAIL' && not_pass && has '파서 실행 실패(rc=42)'; echo $?)
check P11_parser_post "조회 rc0와 파서 rc42·stderr·단회 시도 구분" $(parser_evidence_ok post; echo $?)
check P11_parser_post "변경 후 ENC 원복/manifest 복구와 restore 재기동 증거" $(enc_same && manifest_ok && [ -s "$RUNDIR/restart-restore-observed.txt" ] && [ -s "$RUNDIR/00-orig-sha256.txt" ]; echo $?)
check P11_parser_post "소유 Secret a/b/c 모두 UID precondition 정리, 이름 삭제 금지" $(no_leftover && raw_attempted a && raw_attempted b && raw_attempted c && raw_body_ok && no_name_delete; echo $?)
check P11_parser_post "복구·정리 후 실패 증적 봉인/SHA256 검증" $(parser_seal_ok post; echo $?)
rm -rf "$CASE_FX"
run_case P12_parser_postcheck postcheck FAULT=parser_post
check P12_parser_post "읽기 전용 사후 점검도 nonzero/FAIL, PASS 금지" $([ "$RC" -ne 0 ] && has 'RESULT: FAIL' && not_pass; echo $?)
check P12_parser_post "사후 파서 rc42·stderr·단회 시도 보존" $(parser_evidence_ok post; echo $?)
check P12_parser_post "읽기 전용 상태 유지, 변경/재기동/Secret 생성 0" $(enc_same && manifest_ok && [ "$(cat "$CASE_FX/state/gen")" = 1 ] && [ ! -e "$CASE_FX/state/uidseq" ]; echo $?)
check P12_parser_post "실패 증적 봉인/SHA256 검증" $(parser_seal_ok post; echo $?)
rm -rf "$CASE_FX"

echo "=== fixture 시험 결과"
printf '%s\n' "${RESULTS[@]}"
echo "=== PASS=$PASS FAIL=$FAILN"
[ "$FAILN" -eq 0 ]
