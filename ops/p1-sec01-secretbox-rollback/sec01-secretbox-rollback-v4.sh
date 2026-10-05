#!/usr/bin/env bash
# P1-SEC-01 SEC-AC-05 Secretbox rollback 증적 도구 v4  (DRAFT - 리뷰 승인 전 운영 실행 금지)
# v3 에 대한 GWONUK 재리뷰(2026-10-05, #19 comment 5986339672) 반영:
#   B1 조회 실패를 정상 판정으로 바꾸지 않는다.
#      - etcd_prefix: 조회 rc, 값 존재, 형식(k8s:enc: / k8s\0)을 분리해 판정. 실패·빈 값·알 수 없는 형식은 모두 FAIL
#      - 잔여 시험 Secret 조회 실패는 0건이 아니라 NO-GO. Secret 목록 조회도 rc 로 판정
#      - listener 관측: 조회 오류(ss 실패·빈 출력)와 실제 down 을 구분. 오류는 재기동 미관측으로 FAIL
#   B2 원복 후 생성·읽기를 대상 CP 직접 endpoint($SELF_IP)에서 필수 검증. LB·다른 CP 읽기는 별도 유지
#   B3 시험 Secret 은 실행별 고유 이름 + 생성 응답의 UID 로 소유를 확인한 것만 삭제(UID 불일치·소유 불명확은 보존하고 FAIL)
#   +  재기동 증거를 단계별 파일로 분리(restart-rollback-*, restart-restore-*)
# v4.1 (2026-10-05, GWONUK 재리뷰 #19 comment 5986833302 반영):
#   B3 삭제는 GET→이름 DELETE 가 아니라 **서버 측 UID precondition** 으로 원자적으로 수행한다.
#      kubectl delete --raw /api/v1/namespaces/default/secrets/<name> -f -  (본문 DeleteOptions.preconditions.uid = 생성 응답 UID)
#      · 409 Conflict(UID 불일치)·NotFound·조회/삭제 실패·결과 불명확이면 **이름 삭제로 fallback 하지 않는다**
#      · 성공 후 명시적 NotFound 를 GET 으로 확인. cleanup 미완료는 ACFAILS 로 세어 최종 FAIL
#      (v4 의 "kubectl 은 UID precondition 을 노출하지 않는다"는 서술은 틀렸다. 같은 방식이 infra-kubernetes ac-net-run01 cleanup 에 이미 있다)
#
# 모드
#   precheck       읽기 전용. GO/NO-GO 판정
#   rollback-test  이 CP 1대의 kube-apiserver 만 재기동하는 변경 시험. CONFIRM=YES 필요
#   postcheck      읽기 전용. 복원 후 상태 확인(ORIG_SHA 지정 시 설정 해시 비교)
#
# 사용 (대상 CP 콘솔, root)
#   ENC_BACKUP=<이 CP 에 둔 설정 사본(mode 600, 해시 동일)> PEER_SHAS="<다른 CP 2대의 enc 설정 sha256>" bash sec01-secretbox-rollback-v4.sh precheck
#   ENC_BACKUP=... PEER_SHAS="..." CONFIRM=YES bash sec01-secretbox-rollback-v4.sh rollback-test
#   ORIG_SHA=<시험 전 sha256> bash sec01-secretbox-rollback-v4.sh postcheck
#
# FIXTURE=1 은 가짜 클러스터(스텁) 시험 전용이다. 운영에서 사용하지 않는다.
set -uo pipefail
REV="v4.3-20261005"
MODE="${1:-}"
FIXTURE="${FIXTURE:-0}"
ENC="${ENC:-/etc/kubernetes/enc/encryption-config.yaml}"
MANIFEST="${MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
PARK="${PARK:-/etc/kubernetes/manifests-parked}"
ADMIN="${ADMIN:-/etc/kubernetes/admin.conf}"
OUT_BASE="${OUT_BASE:-/root}"
LB_URL="${LB_URL:-https://10.1.93.109:6443}"
CP_IPS="${CP_IPS:-10.1.93.102 10.1.93.103 10.1.93.104}"
EXPECT_NODES="${EXPECT_NODES:-6}"
READY_WAIT_SEC="${READY_WAIT_SEC:-300}"
DOWN_WAIT_SEC="${DOWN_WAIT_SEC:-90}"
POLL="${POLL_SEC:-5}"
STEP_FILE="${STEP_FILE:-/dev/null}"
KC="kubectl --kubeconfig=$ADMIN"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_BASE/p1-sec01-secretbox-$MODE-$TS"
HOST="${HOST:-$(hostname)}"
SELF_IP="${SELF_IP:-$(hostname -I | awk '{print $1}')}"
# B3: 실행별 고유 이름. 이름만으로 다른 실행의 객체와 구분하지 않고, 아래 UID 로 소유를 다시 확인한다.
RUN_ID="$(date -u +%Y%m%d%H%M%S)-$(printf '%04x%04x' "$RANDOM" "$RANDOM")"
CHANGED=0
EXPOSURE=0
AUDITED=0
FINISHED=0
FAILS=0
ACFAILS=0
OWN_NAMES=()
OWN_UIDS=()
ETCD_CLASS=""
SSTATE=""
SUID=""
umask 077

if [ "$FIXTURE" != 1 ] && [ "$(id -u)" -ne 0 ]; then echo "root 로 실행하세요."; exit 2; fi
case "$MODE" in precheck|rollback-test|postcheck) ;; *) echo "사용: $0 precheck | rollback-test | postcheck"; exit 2;; esac
OUT="$(mktemp -d "$OUT.XXXXXX")" || { echo "출력 폴더 생성 실패" >&2; exit 2; }
LOG="$OUT/run.log"
say() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$LOG"; }
mark() { echo "$1" > "$STEP_FILE" 2>/dev/null; }
fail() { say "FAIL: $*"; FAILS=$((FAILS+1)); }
acfail() { say "FAIL(AC): $*"; ACFAILS=$((ACFAILS+1)); }
SCRIPT_SHA="$(sha256sum "$0" | awk '{print $1}')"
say "script=$(basename "$0") sha256=$SCRIPT_SHA rev=$REV mode=$MODE host=$HOST run_id=$RUN_ID"
{ echo "script_sha256=$SCRIPT_SHA"; echo "rev=$REV"; echo "mode=$MODE"; echo "host=$HOST"; echo "run_id=$RUN_ID"; echo "started_utc=$TS"; } > "$OUT/00-identity.txt"

sname() { echo "enc-rb-test-$RUN_ID-$1"; }   # $1 = a|b|c

etcdctl_in_pod() {
  $KC -n kube-system exec "etcd-$HOST" -- etcdctl --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key "$@"
}
# B1: etcd 조회 성공 여부와 결과 의미를 분리한다. $1=ns $2=name $3=결과 파일(class만 기록, 실제 Secret 바이트 금지)
# 반환 0 = 조회 성공 + 값 존재. ETCD_CLASS = QUERY_FAILED | EMPTY | SECRETBOX | ENC | PLAIN | UNKNOWN_FORMAT
etcd_prefix() {
  local raw="$OUT/.etcd-raw.$$" rc magic
  etcdctl_in_pod get "/registry/secrets/$1/$2" --print-value-only > "$raw" 2> "$OUT/etcd-get-stderr.txt"; rc=$?
  if [ "$rc" -ne 0 ]; then ETCD_CLASS=QUERY_FAILED; rm -f "$raw"; { echo; echo "class=$ETCD_CLASS rc=$rc"; } > "$3"; return 1; fi
  if [ ! -s "$raw" ]; then ETCD_CLASS=EMPTY; rm -f "$raw"; { echo; echo "class=$ETCD_CLASS (조회 성공, 값 없음)"; } > "$3"; return 1; fi
  magic="$(head -c 8 "$raw" | od -An -tx1 | tr -d ' \n')"
  case "$magic" in
    6b38733a656e633a) ETCD_CLASS=ENC ;;          # "k8s:enc:" 암호화 저장
    6b387300*)        ETCD_CLASS=PLAIN ;;        # "k8s\0" 평문 protobuf 저장
    *)                ETCD_CLASS=UNKNOWN_FORMAT ;;
  esac
  if [ "$ETCD_CLASS" = ENC ] && head -c 24 "$raw" | grep -aq 'k8s:enc:secretbox:v1:'; then ETCD_CLASS=SECRETBOX; fi
  printf 'class=%s\n' "$ETCD_CLASS" > "$3"
  rm -f "$raw"
  return 0
}
# B1: 조회 실패는 빈 목록이 아니다. 실패·빈 출력이면 1 을 반환한다.
secret_names() { local out; out="$($KC get secrets -A --no-headers 2>/dev/null)" || return 1; [ -n "$out" ] || return 1; echo "$out" | awk '{print $1"/"$2}' | sort; }
first_provider() { grep -E '^\s*- (secretbox|identity|aescbc|aesgcm|kms)' "$1" | head -1 | sed -E 's/^\s*- ([a-z0-9]+).*/\1/'; }
# B1: 0=listening, 1=listener 없음(관측 성공), 2=관측 오류(ss 실패·헤더 없는 출력). 오류를 down 으로 보지 않는다.
listening() {
  local out
  out="$(ss -ltn 2>&1)" || return 2
  echo "$out" | head -1 | grep -qi 'state' || return 2
  echo "$out" | grep -q ':6443 ' && return 0
  return 1
}
cid_of() { $KC -n kube-system get pod "kube-apiserver-$HOST" -o jsonpath='{.status.containerStatuses[0].containerID}' 2>/dev/null; }

wait_ready() {
  local i max=$((READY_WAIT_SEC/POLL))
  for i in $(seq 1 "$max"); do
    $KC --server="https://$SELF_IP:6443" get --raw=/readyz >/dev/null 2>&1 && return 0
    sleep "$POLL"
  done
  return 1
}

# 재기동을 실제로 관측해야 성공. $1 = rollback|restore (증거 파일 이름 분리). 반환 0=관측 성공, 2=재기동 미관측(manifest 복원함), 1=그 외 실패
restart_apiserver() {
  local lbl="$1" before after i down=0 oerr=0 lrc maxd=$((DOWN_WAIT_SEC/POLL)) maxc=$((60/POLL))
  mark "restart_begin_$lbl"
  before="$(cid_of)"; echo "$before" > "$OUT/restart-$lbl-cid-before.txt"
  [ -n "$before" ] || { say "FAIL: 재기동 전 container ID 확인 불가($lbl)"; return 1; }
  mkdir -p "$PARK"
  mv "$MANIFEST" "$PARK/kube-apiserver.yaml" || return 1
  for i in $(seq 1 "$maxd"); do
    listening; lrc=$?
    if [ "$lrc" -eq 1 ]; then down=1; break; fi
    [ "$lrc" -eq 2 ] && oerr=$((oerr+1))
    sleep "$POLL"
  done
  echo "listener_down=$down observe_errors=$oerr" > "$OUT/restart-$lbl-observed.txt"
  mv "$PARK/kube-apiserver.yaml" "$MANIFEST" || return 1
  if [ "$down" != 1 ]; then
    if [ "$oerr" -gt 0 ]; then say "FAIL: listener 관측 오류 ${oerr}회로 재기동 여부를 판정할 수 없음(재기동 미관측으로 처리). manifest 를 복원했다"
    else say "FAIL: 6443 listener 가 ${DOWN_WAIT_SEC}초 안에 내려가지 않음(재기동 미관측). manifest 를 복원했다"; fi
    return 2
  fi
  wait_ready || { say "FAIL: ${READY_WAIT_SEC}초 안에 Ready 가 되지 않음($lbl)"; return 1; }
  for i in $(seq 1 "$maxc"); do
    after="$(cid_of)"
    [ -n "$after" ] && [ "$after" != "$before" ] && break
    sleep "$POLL"
  done
  echo "$after" > "$OUT/restart-$lbl-cid-after.txt"
  if [ -z "$after" ] || [ "$after" = "$before" ]; then say "FAIL: container ID 가 바뀌지 않음(재생성 미관측, $lbl)"; return 1; fi
  say "재기동 관측($lbl): listener down + container ID 변경 ($before -> $after)"
  return 0
}

# B3: 존재 상태 확인. SSTATE = EXISTS | NOTFOUND | UNKNOWN, SUID = EXISTS 일 때 UID
secret_state() {
  local out rc
  out="$($KC -n default get secret "$1" -o jsonpath='{.metadata.uid}' 2>&1)"; rc=$?
  SUID=""
  if [ "$rc" -eq 0 ] && [ -n "$out" ]; then SSTATE=EXISTS; SUID="$out"
  elif echo "$out" | grep -q NotFound; then SSTATE=NOTFOUND
  else SSTATE=UNKNOWN; fi
}
# B3: 생성 응답으로 UID 를 확보한 것만 "이번 실행 소유"로 추적한다. $1 = a|b|c, 나머지 = kubectl 명령(서버 지정 포함)
# 생성 실패(AlreadyExists·응답 유실 포함)로 소유를 확정하지 못했는데 동명 객체가 있으면 삭제 대상으로 삼지 않고 오류로 남긴다.
make_secret() {
  local key="$1" name uid rc; shift
  name="$(sname "$key")"
  uid="$("$@" -n default create secret generic "$name" --from-literal=probe=dummy -o jsonpath='{.metadata.uid}' 2>> "$OUT/create-errors.txt")"; rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$uid" ]; then
    OWN_NAMES+=("$name"); OWN_UIDS+=("$uid"); echo "$name $uid" >> "$OUT/10-owned-secrets.txt"
    return 0
  fi
  secret_state "$name"
  case "$SSTATE" in
    EXISTS)  say "ERROR: 시험 Secret 생성 실패(rc=$rc)인데 동명 객체 존재($name uid=$SUID) - 소유 불명확, 삭제하지 않음. 수동 확인 필요"
             echo "$name $SUID" >> "$OUT/10-unowned-secrets.txt"; ACFAILS=$((ACFAILS+1)) ;;
    UNKNOWN) say "ERROR: 시험 Secret 생성 실패(rc=$rc)이고 존재 여부를 확인하지 못함($name). 수동 확인 필요"
             echo "$name UNKNOWN" >> "$OUT/10-unowned-secrets.txt"; ACFAILS=$((ACFAILS+1)) ;;
  esac
  return 1
}
# B3(v4.1): 이번 실행이 생성 응답으로 UID 를 받은 것만, **서버 측 UID precondition** 으로 삭제한다.
#   kubectl delete --raw <secret 경로> -f -   본문 = DeleteOptions{preconditions:{uid:<생성 응답 UID>}}
#   GET→UID 비교→이름 DELETE 는 두 요청 사이의 교체를 막지 못하므로 쓰지 않는다. 어떤 실패에서도 이름 삭제로 fallback 하지 않는다.
#   - rc=0 : 삭제 요청 성공. 이어서 GET 으로 명시적 NotFound 를 확인한다(아니면 잔존/확인 불가 ERROR)
#   - Conflict(409, "Precondition failed"): UID 가 달라 외부 객체로 교체됨. 보존하고 ERROR
#   - NotFound: 이미 없음. GET 으로 NotFound 를 다시 확인한 경우에만 정상
#   - 그 외 rc!=0·결과 불명확: ERROR(삭제 여부를 단정하지 않고 상태만 기록). 이름 삭제 재시도 없음
# 삭제 요청 본문·응답 앞부분은 50-cleanup.txt 에 남겨 precondition 이 실제로 전달됐음을 증적으로 남긴다(Secret 값 아님).
delete_owned() {  # $1=name $2=uid → 0=삭제 요청 성공, 1=Conflict, 2=NotFound, 3=그 외 실패
  local n="$1" u="$2" body out rc
  body="$(printf '{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"%s"}}' "$u")"
  out="$(printf '%s\n' "$body" | $KC delete --raw "/api/v1/namespaces/default/secrets/$n" -f - 2>&1)"; rc=$?
  { echo "## delete $n uid=$u rc=$rc"; echo "body=$body"; echo "$out" | head -c 400; echo; } >> "$OUT/50-cleanup.txt"
  [ "$rc" -eq 0 ] && return 0
  if echo "$out" | grep -qiE 'Conflict|Precondition failed'; then return 1; fi
  if echo "$out" | grep -q NotFound; then return 2; fi
  return 3
}
cleanup_tests() {
  local i n u bad=0 drc left_names=() left_uids=()
  mark cleanup
  if [ "${#OWN_NAMES[@]}" -eq 0 ]; then say "정리할 소유 시험 Secret 없음"; return 0; fi
  for i in "${!OWN_NAMES[@]}"; do
    n="${OWN_NAMES[$i]}"; u="${OWN_UIDS[$i]}"
    delete_owned "$n" "$u"; drc=$?
    case "$drc" in
      1) say "ERROR: 시험 Secret UID precondition conflict($n 생성 uid=$u) - 같은 이름의 다른 객체로 교체됨. 이번 실행 소유가 아니므로 삭제하지 않음(이름 삭제로 fallback 하지 않음)"
         bad=$((bad+1)); continue ;;
      3) say "ERROR: 시험 Secret 삭제 요청 실패 또는 결과 불명확($n) - 이름 삭제로 fallback 하지 않음"
         bad=$((bad+1)); left_names+=("$n"); left_uids+=("$u"); continue ;;
    esac
    secret_state "$n"
    case "$SSTATE" in
      NOTFOUND) : ;;
      EXISTS)   say "ERROR: 시험 Secret 잔존: $n"; bad=$((bad+1)); left_names+=("$n"); left_uids+=("$u") ;;
      *)        say "ERROR: 시험 Secret 정리 확인 불가($n)"; bad=$((bad+1)); left_names+=("$n"); left_uids+=("$u") ;;
    esac
  done
  OWN_NAMES=(${left_names[@]+"${left_names[@]}"}); OWN_UIDS=(${left_uids[@]+"${left_uids[@]}"})
  if [ "$bad" -gt 0 ]; then ACFAILS=$((ACFAILS+bad)); return 1; fi
  say "시험 Secret 정리 확인(서버 측 UID precondition 삭제 후 GET NotFound)"
  return 0
}

do_restore() {
  [ "$CHANGED" = 1 ] || { say "설정 변경 전이므로 복원 불필요(재기동 없음)"; return 0; }
  say "복원 시작"
  local ok=1 rc
  cp -p "$OUT/encryption-config.yaml.orig" "$ENC" && chmod 600 "$ENC" || { say "!!! 원본 설정 복사 실패"; ok=0; }
  [ -f "$PARK/kube-apiserver.yaml" ] && mv "$PARK/kube-apiserver.yaml" "$MANIFEST"
  restart_apiserver restore; rc=$?
  if [ "$rc" -ne 0 ]; then say "!!! 복원 중 재기동 실패(rc=$rc) - 즉시 수동 조치(runbook 7절)"; ok=0; fi
  sha256sum "$ENC" > "$OUT/99-restored-sha256.txt"
  if [ "$(awk '{print $1}' "$OUT/00-orig-sha256.txt")" = "$(awk '{print $1}' "$OUT/99-restored-sha256.txt")" ]; then say "복원 설정 해시가 원본과 일치"; else say "!!! 복원 설정 해시가 원본과 다름"; ok=0; fi
  if [ "$ok" = 1 ]; then
    CHANGED=0
    audit_secret_writes || :  # audit failure must not prevent cleanup
  fi
  [ "$ok" = 1 ]
}
# All gate inputs retain command status before any parsing/pipeline.
capture() {  # label command...; stdout/stderr/rc remain in OUT
  local label="$1" rc; shift
  "$@" > "$OUT/$label.txt" 2> "$OUT/$label.stderr"; rc=$?
  printf '%s\n' "$rc" > "$OUT/$label.rc" || { fail "$label rc 기록 실패"; return 1; }
  [ "$rc" -eq 0 ] || { fail "$label 조회 실패(rc=$rc)"; return 1; }
}
validate_health() {
  command -v python3 >/dev/null 2>&1 || { fail "etcd health JSON 파서 python3 없음"; return 1; }
  python3 - "$1" $CP_IPS <<'PYJSON'
import json, sys
from urllib.parse import urlsplit

def unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj
try:
    with open(sys.argv[1]) as f:
        data = json.load(f, object_pairs_hook=unique_keys)
    expected = set(sys.argv[2:])
    if not expected or len(expected) != len(sys.argv[2:]):
        raise ValueError("invalid expected endpoint set")
    if not isinstance(data, list) or len(data) != len(expected):
        raise ValueError("endpoint count/JSON structure mismatch")
    actual = []
    for item in data:
        if not isinstance(item, dict) or item.get("health") is not True:
            raise ValueError("missing/false health")
        ep = urlsplit(item["endpoint"])
        if (ep.scheme != "https" or ep.port != 2379 or ep.username or ep.password
                or ep.path not in ("", "/") or ep.query or ep.fragment):
            raise ValueError("invalid endpoint URL")
        actual.append(ep.hostname)
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError("duplicate/unexpected endpoint")
except (ValueError, TypeError, KeyError, OSError) as exc:
    print("etcd health invalid: " + str(exc), file=sys.stderr)
    sys.exit(1)
PYJSON
}
# B59-3: metadata only; UID catches delete/recreate, RV catches same-name updates.
secret_snapshot() {
  local label="$1"
  capture "$label" $KC get secrets -A --no-headers -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,UID:.metadata.uid,RV:.metadata.resourceVersion || return 1
  awk 'NF!=4 || $1!~/^[a-z0-9][a-z0-9.-]*$/ || $2!~/^[a-z0-9][a-z0-9.-]*$/ || $3=="<none>" || $4=="<none>" {bad=1} {k=$1"/"$2; if(seen[k]++) bad=1} END {exit (bad || NR==0)}' "$OUT/$label.txt" || { acfail "$label metadata 형식/중복/빈 목록 오류"; return 1; }
}
audit_secret_writes() {
  [ "$EXPOSURE" = 1 ] && [ "$AUDITED" = 0 ] || return 0
  AUDITED=1
  local ns name uid rv i=0 bad=0
  mark audit_after
  if ! secret_snapshot 60-secrets-after; then
    acfail "B59-3 사후 metadata 조회 실패: 암호화 잔여 확인 불가, 수동 점검 필요"; return 1
  fi
  # Exclude only exact creation-response UID owned by this run, never a name prefix.
  local owned="$OUT/10-owned-secrets.txt"
  [ -f "$owned" ] || owned=/dev/null
  awk 'FILENAME==ARGV[1] {own["default/"$1]=$2; next}
       FILENAME==ARGV[2] {before[$1"/"$2]=$3" "$4; next}
       {k=$1"/"$2; if(own[k]==$3) next; if(!(k in before) || before[k]!=$3" "$4) print}' \
    "$owned" "$OUT/21-secrets-before.txt" "$OUT/60-secrets-after.txt" > "$OUT/61-secret-candidates.txt" || { acfail "B59-3 metadata 비교 실패"; return 1; }
  : > "$OUT/62-secret-audit.txt" || { acfail "B59-3 audit 기록 실패"; return 1; }
  while read -r ns name uid rv; do
    [ -n "$ns" ] || continue
    i=$((i+1))
    if ! etcd_prefix "$ns" "$name" "$OUT/62-prefix-$i.txt"; then
      acfail "B59-3 $ns/$name 저장 형식 확인 불가(class=$ETCD_CLASS)"; bad=1
    elif [ "$ETCD_CLASS" != SECRETBOX ]; then
      acfail "B59-3 $ns/$name 비정상 저장(class=$ETCD_CLASS): 수동 재암호화/확인 필요"; bad=1
    fi
    printf '%s/%s uid=%s rv=%s class=%s\n' "$ns" "$name" "$uid" "$rv" "$ETCD_CLASS" >> "$OUT/62-secret-audit.txt" || { acfail "B59-3 audit 기록 실패"; bad=1; }
  done < "$OUT/61-secret-candidates.txt"
  say "B59-3 변경 Secret 검사: candidates=$i failure=$bad (값 원문 미기록)"
  [ "$bad" = 0 ]
}
finalize() {
  local arc="$OUT.evidence.tar.gz"
  tar czf "$arc" -C "$(dirname "$OUT")" --exclude='*.bak' --exclude='*.orig' --exclude='*.rolled' --exclude='*.db' --exclude='.etcd-raw.*' "$(basename "$OUT")" || return 1
  [ -s "$arc" ] || return 1
  sha256sum "$arc" > "$arc.sha256" || return 1
  [ -s "$arc.sha256" ] || return 1
  sha256sum -c "$arc.sha256" >/dev/null || return 1
}
finish() {  # requested success token; FAIL explicitly suppresses success
  [ "$FINISHED" = 1 ] && return "$FINAL_RC"
  local requested="${1:-FAIL}" operation=FAIL result=FAIL evidence=COMPLETE
  FINISHED=1; FINAL_RC=1
  audit_secret_writes || :  # also runs after bail/INT/TERM/EXIT restore attempts
  cleanup_tests || :  # cleanup increments ACFAILS; never skip seal on cleanup failure
  if [ "$requested" != FAIL ] && [ "$FAILS" -eq 0 ] && [ "$ACFAILS" -eq 0 ] && [ "$CHANGED" -eq 0 ]; then operation="$requested"; fi
  printf 'OPERATION_RESULT=%s\nEVIDENCE_STATUS=PENDING\n' "$operation" > "$OUT/99-operation-provisional.txt" || evidence=INCOMPLETE
  finalize || evidence=INCOMPLETE
  if [ "$operation" != FAIL ] && [ "$evidence" = COMPLETE ]; then result="$operation"; FINAL_RC=0; fi
  [ "$result" = FAIL ] && [ "$MODE" = precheck ] && result=NO-GO
  # External completion record: do not mutate archived files after sealing.
  if ! printf 'RESULT: %s\nOPERATION_RESULT=%s\nEVIDENCE_STATUS=%s\nEXIT_CODE=%s\nARCHIVE=%s\n' "$result" "$operation" "$evidence" "$FINAL_RC" "$OUT.evidence.tar.gz" > "$OUT.completion.txt"; then
    evidence=INCOMPLETE; FINAL_RC=1; result=FAIL
    [ "$MODE" = precheck ] && result=NO-GO
  fi
  printf 'RESULT: %s\nOPERATION_RESULT=%s EVIDENCE_STATUS=%s EXIT_CODE=%s\nCOMPLETION=%s\n' "$result" "$operation" "$evidence" "$FINAL_RC" "$OUT.completion.txt"
  return "$FINAL_RC"
}
bail() { say "ABORT: $*"; ACFAILS=$((ACFAILS+1)); do_restore || acfail "복원 실패"; finish FAIL; exit 1; }
trap 'say "신호 수신 - 중단"; do_restore || acfail "복원 실패"; finish FAIL; exit 1' INT TERM
on_exit() {
  local rc=$?
  trap - EXIT
  if [ "$FINISHED" = 0 ]; then
    say "비정상 종료 감지 - 정리"
    do_restore || acfail "복원 실패"
    finish FAIL
    [ "$rc" -ne 0 ] || rc=1
  fi
  exit "$rc"
}
trap on_exit EXIT

# 읽기 전용 점검. $1 = 파일 접두어
checks() {
  local p="$1" n ip r left srv lout lrc
  capture "$p-nodes" $KC get nodes -o wide || :
  if capture "$p-nodes-noheaders" $KC get nodes --no-headers; then
    n=$(awk '$2=="Ready" {n++} END {print n+0}' "$OUT/$p-nodes-noheaders.txt")
    say "nodes Ready: $n/$EXPECT_NODES"
    [ "$n" -eq "$EXPECT_NODES" ] || fail "Ready 노드 $n/$EXPECT_NODES"
  fi
  : > "$OUT/$p-readyz.txt"
  for ip in $CP_IPS; do
    if capture "$p-readyz-$ip" $KC --server="https://$ip:6443" get --raw=/readyz; then
      r=$(cat "$OUT/$p-readyz-$ip.txt")
      printf '%s readyz: %s\n' "$ip" "$r" >> "$OUT/$p-readyz.txt"
      [ "$r" = ok ] || fail "$ip /readyz 응답이 ok가 아님"
    fi
  done
  if capture "$p-etcd-health" etcdctl_in_pod endpoint health --cluster -w json; then
    validate_health "$OUT/$p-etcd-health.txt" 2> "$OUT/$p-etcd-health-validation.stderr" || fail "etcd health 구조/endpoint/상태 검증 실패"
  fi
  capture "$p-etcd-status" etcdctl_in_pod endpoint status --cluster -w table || :
  if capture "$p-apiserver-pods" $KC -n kube-system get pods -l component=kube-apiserver \
    -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount; then
    awk 'NR>1 {n++; if ($2 != "true") bad=1} END {exit (n==0 || bad)}' "$OUT/$p-apiserver-pods.txt" || fail "apiserver Pod 상태 비어 있음/NotReady"
  fi
  if capture "$p-secrets-raw" $KC get secrets -A --no-headers; then
    [ -s "$OUT/$p-secrets-raw.txt" ] || fail "Secret 목록 수집 실패(비어 있음)"
    awk '{print $1"/"$2}' "$OUT/$p-secrets-raw.txt" | sort > "$OUT/$p-secrets.txt" || fail "Secret 목록 변환 실패"
  else fail "Secret 목록 조회 실패"; fi
  { stat -c '%n mode=%a owner=%U:%G size=%s' "$ENC"; sha256sum "$ENC"; } > "$OUT/$p-enc-state.txt" 2>&1
  grep -nE '^\s*- (secretbox|identity|aescbc|aesgcm|kms)' "$ENC" > "$OUT/$p-provider-order.txt" 2>&1
  # B1: 잔여 시험 Secret 조회가 실패하면 0건이 아니라 NO-GO. 조회 성공 후에만 개수를 센다.
  if capture "$p-default-secrets" $KC -n default get secret --no-headers; then
    left=$(awk '/^enc-rb-test-/ {n++} END {print n+0}' "$OUT/$p-default-secrets.txt")
    [ "$left" -eq 0 ] || fail "시험용 Secret(enc-rb-test-*) $left 개가 남아 있음"
  else fail "default Namespace Secret 조회 실패"; fi
  if capture "$p-kubeconfig-server" $KC config view --minify -o jsonpath='{.clusters[0].cluster.server}'; then
    srv=$(cat "$OUT/$p-kubeconfig-server.txt")
    [ "$srv" = "$LB_URL" ] || fail "admin.conf 서버($srv)가 승인 LB($LB_URL)가 아님"
  fi
}
gate_extra() {
  [ -f "$ENC" ] || fail "암호화 설정 파일 없음"
  [ "$(first_provider "$ENC")" = "secretbox" ] || fail "현재 첫 provider 가 secretbox 가 아님: $(first_provider "$ENC")"
  [ "$(stat -c %a "$ENC")" = "600" ] || fail "암호화 설정 mode 가 600 이 아님"
  # 복구 수단: 이 CP(스크립트를 실행하는 서버)에 실제로 있는 설정 사본과 해시를 확인한다.
  # 다른 서버(control01 등)의 backup 경로는 이 CP 의 수동 복구 수단으로 인정하지 않는다.
  if [ -z "${ENC_BACKUP:-}" ]; then fail "ENC_BACKUP 미지정: 이 CP 에 둔 설정 사본 경로가 필요(수동 복구 수단)"
  elif [ ! -f "$ENC_BACKUP" ]; then fail "ENC_BACKUP 파일 없음(이 CP 에서 접근 불가): $ENC_BACKUP"
  elif [ "$(sha256sum "$ENC_BACKUP" | awk '{print $1}')" != "$(sha256sum "$ENC" | awk '{print $1}')" ]; then fail "ENC_BACKUP 해시가 현재 설정과 다름: $ENC_BACKUP"
  elif [ "$(stat -c %a "$ENC_BACKUP")" != "600" ]; then fail "ENC_BACKUP mode 가 600 이 아님: $ENC_BACKUP"
  else { echo "enc_backup=$ENC_BACKUP"; sha256sum "$ENC_BACKUP"; stat -c 'mode=%a owner=%U:%G' "$ENC_BACKUP"; } > "$OUT/pre-enc-backup.txt"; fi
  # 3CP 동일 Config 를 결과에 기계 판독 가능하게 포함. 다른 CP 해시(PEER_SHAS)가 필요하다.
  local me peers=($PEER_SHAS_LIST) p need=$(( $(echo $CP_IPS | wc -w) - 1 ))
  me="$(sha256sum "$ENC" | awk '{print $1}')"
  { echo "self=$me"; echo "peers=${PEER_SHAS:-}"; } > "$OUT/pre-3cp-config-sha.txt"
  if [ "${#peers[@]}" -ne "$need" ]; then fail "3CP 동일 Config 미확인: PEER_SHAS 에 다른 CP $need 대의 sha256 필요(현재 ${#peers[@]})"
  else for p in "${peers[@]}"; do [ "$p" = "$me" ] || fail "다른 CP 설정 해시($p)가 이 CP($me)와 다름"; done; fi
}
PEER_SHAS_LIST="${PEER_SHAS:-}"

case "$MODE" in
precheck)
  say "== precheck (읽기 전용)"
  checks pre; gate_extra
  finish GO; exit $?
  ;;

rollback-test)
  [ "${CONFIRM:-}" = YES ] || { echo "승인된 변경창에서만 실행. CONFIRM=YES 필요."; exit 2; }
  say "== rollback-test: host=$HOST ip=$SELF_IP (이 CP 의 kube-apiserver 만 재기동한다)"
  SKC="$KC --server=https://$SELF_IP:6443"

  say "[0] 시작 게이트(읽기 전용)"
  checks pre; gate_extra
  [ "$FAILS" -eq 0 ] || bail "게이트 실패 $FAILS 건 - 설정 변경 없이 종료"

  say "[1] 원본 보존과 재배치 설정 사전 검증(설정 변경 전)"
  cp -p "$ENC" "$OUT/encryption-config.yaml.orig" && chmod 600 "$OUT/encryption-config.yaml.orig" || bail "설정 사본 실패"
  sha256sum "$ENC" > "$OUT/00-orig-sha256.txt"
  IND="$(grep -E '^\s*- identity:' "$ENC" | sed -E 's/^(\s*)-.*/\1/' | head -1)"
  [ -n "$IND" ] || bail "identity provider 줄을 찾지 못함"
  awk -v ind="$IND" '/^[[:space:]]*- identity:/ {next} {print} /^[[:space:]]*providers:/ {print ind"- identity: {}"}' \
    "$OUT/encryption-config.yaml.orig" > "$OUT/encryption-config.yaml.rolled"
  chmod 600 "$OUT/encryption-config.yaml.rolled"
  grep -nE '^\s*- (secretbox|identity|aescbc|aesgcm|kms)' "$OUT/encryption-config.yaml.rolled" > "$OUT/20-rolled-provider-order.txt"
  cat "$OUT/20-rolled-provider-order.txt" | tee -a "$LOG"
  [ "$(first_provider "$OUT/encryption-config.yaml.rolled")" = "identity" ] || bail "재배치 설정의 첫 provider 가 identity 가 아님(재기동 안 함)"
  grep -q 'secretbox' "$OUT/encryption-config.yaml.rolled" || bail "재배치 설정에 secretbox 가 없음(재기동 안 함)"

  say "[2] 시험용 Secret 생성과 사전 접두어 확인"
  mark create_a
  make_secret a $KC || bail "시험 Secret a 생성 실패"
  mark prefix_before
  etcd_prefix default "$(sname a)" "$OUT/13-prefix-before.txt" || bail "사전: etcd 조회 실패 또는 값 없음(class=$ETCD_CLASS)"
  [ "$ETCD_CLASS" = SECRETBOX ] || bail "사전: secretbox 접두어 아님(class=$ETCD_CLASS)"
  say "사전: secretbox 접두어 확인"

  say "[3] provider 순서 변경 후 apiserver 재기동 (여기서부터 변경 시작)"
  mark snapshot_before
  secret_snapshot 21-secrets-before || bail "B59-3 사전 metadata 확인 실패 - 설정 변경 없음"
  EXPOSURE=1
  CHANGED=1
  cp -p "$OUT/encryption-config.yaml.rolled" "$ENC" && chmod 600 "$ENC" || bail "재배치 설정 적용 실패"
  restart_apiserver rollback; RC=$?
  [ "$RC" -eq 0 ] || bail "재배치 설정으로 재기동 관측 실패(rc=$RC)"

  say "[4] rollback 상태 검증"
  mark rolled_list
  capture "30-secrets-raw" $SKC get secrets -A --no-headers || bail "재배치 후 Secret 목록 조회 실패"
  cat "$OUT/30-secrets-raw.txt" | awk '{print $1"/"$2}' | sort > "$OUT/30-secrets-after-rollback.txt"
  [ -s "$OUT/30-secrets-after-rollback.txt" ] || bail "재배치 후 Secret 목록 수집 실패"
  diff <(grep -v 'default/enc-rb-test-' "$OUT/pre-secrets.txt") <(grep -v 'default/enc-rb-test-' "$OUT/30-secrets-after-rollback.txt") > "$OUT/31-secrets-diff.txt" 2>&1 \
    || bail "기존 Secret 목록 차이"
  say "기존 Secret 모두 읽힘(시험용 제외 차이 0)"
  mark read_a_rolled
  $SKC -n default get secret "$(sname a)" -o jsonpath='{.metadata.name}' > "$OUT/32-read-old-secret.txt" 2>&1 || bail "기존 암호화 Secret 을 읽지 못함"
  mark create_b
  make_secret b $SKC || bail "시험 Secret b 생성 실패"
  mark prefix_b
  etcd_prefix default "$(sname b)" "$OUT/33-prefix-new-secret-rolled.txt" || bail "rollback 후 b: etcd 조회 실패 또는 값 없음(class=$ETCD_CLASS) - 평문 여부를 판정할 수 없음"
  case "$ETCD_CLASS" in
    PLAIN) ;;
    SECRETBOX|ENC) bail "신규 Secret 이 아직 암호화됨 - rollback 효과 없음(class=$ETCD_CLASS)" ;;
    *) bail "신규 Secret 의 저장 형식을 평문(identity)으로 확인하지 못함(class=$ETCD_CLASS)" ;;
  esac
  say "신규 Secret 은 identity(평문)로 저장됨 - rollback 확인(class=PLAIN)"
  # LB 와 다른 CP 직접 읽기를 별도 명령으로 수행·기록(둘 다 필수)
  PEER_CP=""; for ip in $CP_IPS; do [ "$ip" = "$SELF_IP" ] || { PEER_CP="$ip"; break; }; done
  mark read_b_lb
  $KC --server="$LB_URL" -n default get secret "$(sname b)" -o jsonpath='{.metadata.name}' > "$OUT/34-read-b-via-lb.txt" 2>&1 || bail "LB($LB_URL) 경유 b 읽기 실패"
  mark read_b_peer
  $KC --server="https://$PEER_CP:6443" -n default get secret "$(sname b)" -o jsonpath='{.metadata.name}' > "$OUT/35-read-b-via-peer-cp.txt" 2>&1 || bail "다른 CP($PEER_CP) 직접 b 읽기 실패"
  say "평문 Secret 을 LB 와 다른 CP($PEER_CP)에서 각각 읽음"

  say "[5] 원래 설정으로 복원 - 원복 후 검증은 대상 CP($SELF_IP) 직접 endpoint 에서 필수, LB·다른 CP 는 별도"
  do_restore || bail "복원이 확인되지 않음(runbook 7절)"
  mark create_c
  make_secret c $SKC || bail "복원 후 시험 Secret c 생성 실패(대상 CP $SELF_IP 직접)"
  mark prefix_c
  etcd_prefix default "$(sname c)" "$OUT/40-prefix-after-restore.txt" || bail "복원 후 c: etcd 조회 실패 또는 값 없음(class=$ETCD_CLASS)"
  [ "$ETCD_CLASS" = SECRETBOX ] || bail "복원 후 신규 Secret 이 secretbox 로 저장되지 않음(class=$ETCD_CLASS)"
  say "복원 후 신규 Secret(대상 CP 직접 생성) 암호화 재확인"
  mark read_a_after
  $SKC -n default get secret "$(sname a)" -o jsonpath='{.metadata.name}' > "$OUT/42-read-a-after-restore-self.txt" 2>&1 || bail "복원 후 암호화 a 읽기 실패(대상 CP $SELF_IP 직접)"
  mark read_b_after
  $SKC -n default get secret "$(sname b)" -o jsonpath='{.metadata.name}' > "$OUT/41-read-plain-b-after-restore-self.txt" 2>&1 || bail "복원 후 평문 b 읽기 실패(대상 CP $SELF_IP 직접)"
  mark read_b_after_lb
  $KC --server="$LB_URL" -n default get secret "$(sname b)" -o jsonpath='{.metadata.name}' > "$OUT/43-read-b-after-restore-lb.txt" 2>&1 || bail "복원 후 LB($LB_URL) 경유 b 읽기 실패"
  mark read_b_after_peer
  $KC --server="https://$PEER_CP:6443" -n default get secret "$(sname b)" -o jsonpath='{.metadata.name}' > "$OUT/44-read-b-after-restore-peer-cp.txt" 2>&1 || bail "복원 후 다른 CP($PEER_CP) 직접 b 읽기 실패"
  say "복원 후 읽기: 대상 CP 직접(a, b), LB(b), 다른 CP(b)"

  say "[6] 시험 Secret 정리와 사후 점검"
  cleanup_tests || say "시험 Secret 정리 실패(ACFAILS 반영)"
  FAILS=0
  checks post
  diff "$OUT/pre-secrets.txt" "$OUT/post-secrets.txt" > "$OUT/51-secrets-diff-final.txt" 2>&1 || fail "시험 전후 Secret 목록 차이"
  [ "$(awk '{print $1}' "$OUT/00-orig-sha256.txt")" = "$(sha256sum "$ENC" | awk '{print $1}')" ] || fail "복원 설정 해시 불일치"
  say "원래 설정 sha256: $(awk '{print $1}' "$OUT/00-orig-sha256.txt")"
  finish PASS; exit $?
  ;;

postcheck)
  say "== postcheck (읽기 전용)"
  checks post
  if [ -n "${ORIG_SHA:-}" ]; then
    [ "$(sha256sum "$ENC" | awk '{print $1}')" = "$ORIG_SHA" ] || fail "암호화 설정 해시가 ORIG_SHA 와 다름"
  else say "ORIG_SHA 미지정 - 설정 해시 비교는 건너뜀"; fi
  [ "$(first_provider "$ENC")" = "secretbox" ] || fail "첫 provider 가 secretbox 가 아님"
  finish PASS; exit $?
  ;;
esac
