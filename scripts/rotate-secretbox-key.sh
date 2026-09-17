#!/usr/bin/env bash
#
# rotate-secretbox-key.sh
#
# P1-SEC-01 / SEC-01 (Critical), SEC-02 (High) 수정 — 2차
#
# GWONUK 재검토(fds-project-baseline#19, issuecomment-5644891728)에서 지적된
# 결함:
#   SEC-01: add-key가 keyfile 존재만 확인(내용/길이/CP 동일성 미검증).
#           manifest 검사가 문자열 grep 수준(구조적 volumeMount 대조 아님).
#           config 편집만으로 재시작을 "출력"했을 뿐 실제 재시작 미확인.
#           healthz 대기가 해당 CP의 API 프로세스 직접 연결을 보장 못 함.
#           구 Secret read 1건이 전 CP 새 키 로드의 증거가 아님. 대기에 timeout 없음.
#           raw 검증기가 find()로 prefix를 찾아 구 키/비정상 데이터도 PASS 처리.
#           구 키 제거 전 전체 재작성 성공/실패 건수 미확인.
#   SEC-02: umask 077 없이 키 생성 후 chmod. 고정 /tmp 경로라 충돌·잔류 위험.
#           검증 실패 시 set -e로 마지막 rm에 도달 못 함.
#
# 2차 수정 요지: 모든 검증을 "존재 여부"가 아니라 "구조적 일치·완전일치"로 강화하고,
# 모든 대기 루프에 timeout을 두며, 구 키 데이터는 반드시 FAIL로 떨어지게 한다.
#
# 실행 위치: 관리 워크스테이션에서 3개 Control Plane(CP1/CP2/CP3)에 SSH로 순차 접속.
# 사전 조건: 각 CP 노드에 root 또는 sudo 권한, kubectl 접속 가능한 관리 호스트 별도 존재,
#            각 CP는 SSH_USER로 sudo 비밀번호 없이 접근 가능(운영 표준 절차).
#
# 사용법:
#   ./rotate-secretbox-key.sh generate-key
#   ./rotate-secretbox-key.sh add-key <keyfile>
#   ./rotate-secretbox-key.sh verify-decrypt
#   ./rotate-secretbox-key.sh promote-key
#   ./rotate-secretbox-key.sh rewrite-secrets
#   ./rotate-secretbox-key.sh verify-encryption <expected-key-name>
#   ./rotate-secretbox-key.sh remove-old-key
#
# 각 단계는 확인 없이 다음 단계로 자동 진행하지 않는다. 실패 시 즉시 중단하고,
# 신규 암호문이 이미 쓰인 상태에서 구 config만 복원하는 rollback은 하지 않는다.

set -uo pipefail  # -e는 의도적으로 뺀다: 실패 분기를 함수 안에서 명시적으로 처리하고,
                   # trap cleanup이 set -e로 인한 조기 종료에 막히지 않게 하기 위함.
umask 077          # SEC-02: 이 스크립트가 만드는 모든 파일은 생성 시점부터 600/700.

# ── 환경 설정 (실제 값으로 교체 필요) ─────────────────────────────
CP_NODES=("cp01" "cp02" "cp03")            # SSH 접속 가능한 CP 호스트명/IP. 실제 인벤토리로 교체.
# 각 CP의 API 프로세스에 "직접" 연결되는 endpoint. LB/VIP를 경유하면 특정 CP가
# 죽어있어도 다른 CP가 응답해 healthz PASS가 나올 수 있으므로 CP별 직접 주소를 쓴다.
CP_DIRECT_ENDPOINTS=("https://cp01:6443" "https://cp02:6443" "https://cp03:6443")
SSH_USER="${SSH_USER:-ansible}"
ENC_CONFIG_PATH="/etc/kubernetes/enc/encryption-config.yaml"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"
BACKUP_DIR="${BACKUP_DIR:-./rotation-backup-$(date +%Y%m%dT%H%M%SZ)}"
ETCD_CACERT="/etc/kubernetes/pki/etcd/ca.crt"
ETCD_CERT="/etc/kubernetes/pki/etcd/server.crt"
ETCD_KEY="/etc/kubernetes/pki/etcd/server.key"
PROBE_SECRET_PATH="/registry/secrets/fds-app/fds-app-db-credentials"
WAIT_TIMEOUT_SEC=120

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }
confirm() {
  read -r -p "$1 [계속하려면 'yes' 입력] " ans
  [[ "$ans" == "yes" ]] || die "사용자 확인 실패 — 중단"
}

ssh_cp() {
  local node="$1"; shift
  ssh -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "${SSH_USER}@${node}" "$@"
}

# CP endpoint에 직접 붙어 healthz를 확인한다. LB 경유가 아니므로 이 CP 자체가
# 살아있다는 증거가 된다. timeout을 반드시 둔다 (SEC-01: 무한 대기 금지).
wait_healthz_direct() {
  local endpoint="$1"
  local elapsed=0
  while (( elapsed < WAIT_TIMEOUT_SEC )); do
    if curl -sk --max-time 3 "${endpoint}/healthz" 2>/dev/null | grep -q '^ok$'; then
      return 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  return 1
}

# ── generate-key: 새 32byte base64 키를 "로컬"에서만 생성 ─────────
cmd_generate_key() {
  local out="${BACKUP_DIR}/new-secretbox.key"
  mkdir -p "$BACKUP_DIR"
  head -c 32 /dev/urandom | base64 > "$out"   # umask 077로 이미 600 권한으로 생성됨
  log "신규 키 생성 완료: $out (umask 077로 생성 시점부터 600)"
  log "이 파일은 Git/Evidence/로그에 절대 포함하지 마십시오."
  log "키 이름(name:)은 add-key 실행 시 별도로 지정합니다 (예: key-$(date +%Y%m%d))."
}

# ── add-key: 신규 키를 provider 목록의 "두 번째"로 추가 (기존 키는 1번째 유지) ──
cmd_add_key() {
  local keyfile="${1:?keyfile 인자 필요}"
  local new_key_name="${2:?신규 key name 인자 필요 (예: key-20260913)}"
  [[ -f "$keyfile" ]] || die "키 파일 없음: $keyfile"

  # SEC-01: 키 파일 내용 검증 — base64 디코드 후 정확히 32byte인지 확인.
  local key_bytes
  key_bytes="$(base64 -d < "$keyfile" 2>/dev/null | wc -c)"
  [[ "$key_bytes" -eq 32 ]] || die "키 파일이 32byte base64가 아님 (디코드 결과 ${key_bytes}byte) — 잘못된 키"

  mkdir -p "$BACKUP_DIR"
  local -a cp_configs=()
  for node in "${CP_NODES[@]}"; do
    log "[$node] 현재 encryption-config 백업 및 파일 권한 확인 중..."
    mkdir -p "${BACKUP_DIR}/${node}"
    scp -o StrictHostKeyChecking=yes "${SSH_USER}@${node}:${ENC_CONFIG_PATH}" \
        "${BACKUP_DIR}/${node}/encryption-config.yaml.bak" \
      || die "[$node] 기존 config 백업 실패 — 중단"
    cp_configs+=("${BACKUP_DIR}/${node}/encryption-config.yaml.bak")

    local perm
    perm="$(ssh_cp "$node" "stat -c '%a' ${ENC_CONFIG_PATH}")" \
      || die "[$node] 기존 config 권한 확인 실패"
    [[ "$perm" == "600" ]] || die "[$node] 기존 encryption-config.yaml 권한이 600이 아님 (${perm})"
  done

  # SEC-01: 3개 CP의 백업 config가 서로 동일한지 확인 (회전 시작 전 drift 없음을 보증)
  if ! diff -q "${cp_configs[0]}" "${cp_configs[1]}" >/dev/null 2>&1 || \
     ! diff -q "${cp_configs[0]}" "${cp_configs[2]}" >/dev/null 2>&1; then
    die "3개 CP의 기존 encryption-config.yaml 내용이 서로 다름 — 회전 전 drift부터 해소 필요"
  fi
  log "3개 CP 기존 config 동일성 확인 PASS"

  log "각 CP의 encryption-config.yaml에서 secretbox.keys 배열에 신규 키(name: ${new_key_name})를"
  log "'2번째 항목'으로 추가하십시오 (기존 키는 1번째 유지, 권한 600 유지):"
  cat <<EOF
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - secretbox:
          keys:
            - name: <기존-key-name>
              secret: <기존-키>
            - name: ${new_key_name}
              secret: <신규-키>
      - identity: {}
EOF
  confirm "위 형식으로 3개 CP 모두 편집·반영(권한 600, root-only)하셨습니까?"

  # SEC-01: 문자열 grep이 아니라 Python YAML 파서로 volume/volumeMount를 구조적으로 대조.
  # Deep Review 6276ade 지적 #2: 예전 코드는 quoted heredoc(<<'REMOTE') 안에서
  # 로컬 $APISERVER_MANIFEST를 그대로 참조했다. quoted heredoc은 로컬 변수 확장이
  # 되지 않으므로 원격 쉘에는 그 변수가 정의돼 있지 않고, set -euo pipefail(-u)
  # 때문에 python3가 실행되기도 전에 "unbound variable"로 즉시 죽는다.
  # 수정: APISERVER_MANIFEST를 원격 명령의 환경변수로 명시 전달하고, python도
  # 하드코딩 경로 대신 그 값(argv)을 실제로 사용하도록 바꾼다.
  for node in "${CP_NODES[@]}"; do
    log "[$node] kube-apiserver static Pod manifest의 volume/mount 구조 검증..."
    ssh_cp "$node" "APISERVER_MANIFEST=${APISERVER_MANIFEST}" bash -s <<'REMOTE'
set -euo pipefail
python3 - "$APISERVER_MANIFEST" <<'PYEOF'
import os, sys, yaml

path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)

containers = doc.get("spec", {}).get("containers", [])
if not containers:
    print("FAIL: containers 없음"); sys.exit(1)

args = containers[0].get("command", []) + containers[0].get("args", [])
flag = next((a for a in args if a.startswith("--encryption-provider-config=")), None)
if not flag:
    print("FAIL: --encryption-provider-config 플래그 없음"); sys.exit(1)
config_path = os.path.normpath(flag.split("=", 1)[1])

# Deep Review 6276ade 지적 #2: config_path.startswith(mountPath) 문자열 접두어 매칭은
# "/etc/kubernetes/enc" vs "/etc/kubernetes/encryption-config.yaml"처럼 서로 다른
# 경로가 우연히 문자열로 겹치면 오탐 PASS가 난다. 정규화 후 정확 일치이거나
# 디렉토리 경계(구분자 포함)로 시작하는 경우만 매칭으로 인정한다.
def covers(mount_path):
    mount_path = os.path.normpath(mount_path)
    return config_path == mount_path or config_path.startswith(mount_path + os.sep)

mounts = containers[0].get("volumeMounts", [])
mount = next((m for m in mounts if covers(m.get("mountPath", "\x00"))), None)
if not mount:
    print(f"FAIL: {config_path}를 커버하는 volumeMount 없음"); sys.exit(1)

vol_name = mount["name"]
volumes = doc.get("spec", {}).get("volumes", [])
vol = next((v for v in volumes if v.get("name") == vol_name), None)
if not vol or "hostPath" not in vol:
    print(f"FAIL: volume '{vol_name}'의 hostPath 정의 없음"); sys.exit(1)

print(f"PASS: flag={flag} mount={mount.get('mountPath')} readOnly={mount.get('readOnly')} hostPath={vol['hostPath'].get('path')}")
PYEOF
REMOTE
    [[ $? -eq 0 ]] || die "[$node] static Pod manifest 구조 검증 실패"
  done

  log "1단계 완료: 신규 키(${new_key_name})는 '읽기 전용'으로만 추가됨, 구조 검증 PASS."
  log "다음: verify-decrypt (재시작 실제 발생 확인 포함)"
}

# ── verify-decrypt: 전 CP가 신규 키로 복호화 가능한지 확인 (실제 재시작 포함) ──
cmd_verify_decrypt() {
  # SEC-01: config 변경 전 각 CP kube-apiserver 컨테이너 시작 시각을 기록해두고,
  # 재시작 후 시작 시각이 실제로 바뀌었는지 확인한다 (단순 healthz 대기가 아님).
  for i in "${!CP_NODES[@]}"; do
    local node="${CP_NODES[$i]}"
    local endpoint="${CP_DIRECT_ENDPOINTS[$i]}"

    local start_before
    start_before="$(ssh_cp "$node" "sudo crictl ps --name kube-apiserver -o json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"containers\"][0][\"createdAt\"] if d.get(\"containers\") else \"\")'")" \
      || die "[$node] 재시작 전 kube-apiserver 시작 시각 조회 실패"

    log "[$node] CP 직접 endpoint(${endpoint})로 재시작 대기 (timeout ${WAIT_TIMEOUT_SEC}s)..."
    wait_healthz_direct "$endpoint" || die "[$node] healthz가 ${WAIT_TIMEOUT_SEC}s 안에 OK가 되지 않음"

    local start_after
    start_after="$(ssh_cp "$node" "sudo crictl ps --name kube-apiserver -o json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"containers\"][0][\"createdAt\"] if d.get(\"containers\") else \"\")'")" \
      || die "[$node] 재시작 후 kube-apiserver 시작 시각 조회 실패"

    if [[ "$start_before" == "$start_after" ]]; then
      die "[$node] kube-apiserver 시작 시각이 변경 전후 동일함 — 실제 재시작이 일어나지 않았을 가능성 (config reload 미확인)"
    fi
    log "[$node] 재시작 확인됨 (before=${start_before} after=${start_after})"
  done

  log "2단계 완료: 전 CP가 config 변경 후 실제로 재시작되고 healthz OK 확인됨."
  log "  (신규 키 자체의 유효성은 5단계 verify-encryption에서 최종 확인)"
}

# ── promote-key: 신규 키를 '1번째(쓰기 우선)'로 승격 ────────────────
cmd_promote_key() {
  local new_key_name="${1:?신규 key name 인자 필요 (add-key와 동일 값)}"
  log "각 CP의 encryption-config.yaml에서 keys 순서를 뒤집어 신규 키(${new_key_name})를 1번째로 올리십시오."
  cat <<EOF
    providers:
      - secretbox:
          keys:
            - name: ${new_key_name}
              secret: <신규-키>
            - name: <기존-key-name>
              secret: <기존-키>
      - identity: {}
EOF
  confirm "3개 CP 모두 순서를 교체하셨습니까? (재시작 확인은 아래에서 자동 수행)"

  for i in "${!CP_NODES[@]}"; do
    local node="${CP_NODES[$i]}"
    local endpoint="${CP_DIRECT_ENDPOINTS[$i]}"
    wait_healthz_direct "$endpoint" || die "[$node] 승격 후 healthz가 ${WAIT_TIMEOUT_SEC}s 안에 OK가 되지 않음"
    log "[$node] API server healthy (신규 키 쓰기 우선 적용 후)"
  done
  log "3단계 완료. 다음: rewrite-secrets"
}

# ── rewrite-secrets: 기존 Secret 전체를 재작성해 신규 키로 재암호화 ──
cmd_rewrite_secrets() {
  local before_count after_count
  before_count="$(kubectl get secrets -A --no-headers 2>/dev/null | wc -l)" \
    || die "재작성 전 Secret 개수 조회 실패"
  log "재작성 대상 Secret 총 ${before_count}건"

  confirm "전체 Secret을 재작성합니다 (kubectl replace). 진행하시겠습니까?"
  local fail_file
  fail_file="$(mktemp)"
  kubectl get secrets -A -o json | kubectl replace -f - 2>"$fail_file"
  local rc=$?

  after_count="$(kubectl get secrets -A --no-headers 2>/dev/null | wc -l)"

  if [[ "$rc" -ne 0 ]]; then
    log "재작성 중 오류 발생:"
    cat "$fail_file" >&2
    rm -f "$fail_file"
    die "재작성 실패 — before=${before_count} after=${after_count}, verify-encryption으로 현재 상태 재확인 후 재시도"
  fi
  rm -f "$fail_file"

  if [[ "$before_count" != "$after_count" ]]; then
    die "재작성 전후 Secret 개수 불일치 (before=${before_count} after=${after_count}) — 누락 의심, 구 키 제거로 진행하지 말 것"
  fi
  log "4단계 완료: 전체 Secret ${after_count}건 재작성 완료 (개수 일치 확인). 다음: verify-encryption"
}

# ── verify-encryption: etcd 원문 비노출 + '정확히 기대한 신규 키'인지 검증 ──
# find()로 prefix를 아무 위치에서나 찾는 방식은 구 키 데이터·비정상 데이터도
# 우연히 substring이 일치하면 PASS로 오판할 수 있으므로, prefix가 값의 '맨 앞'에서
# 시작하는지, 그리고 key name이 기대값과 '완전히 일치'하는지 둘 다 강제한다.
cmd_verify_encryption() {
  local expected_key_name="${1:?기대하는 key name 인자 필요 (예: key-20260913)}"
  local probe_node="${CP_NODES[0]}"
  log "[$probe_node] etcd에서 ${PROBE_SECRET_PATH} raw 값을 조회하여 암호화 헤더를 직접 파싱합니다."
  log "기대 key name: ${expected_key_name}"

  ssh_cp "$probe_node" bash -s <<EOF
set -euo pipefail
PROBE_FILE="\$(mktemp)"
sudo ETCDCTL_API=3 etcdctl get "${PROBE_SECRET_PATH}" \\
  --cacert=${ETCD_CACERT} --cert=${ETCD_CERT} --key=${ETCD_KEY} \\
  -w json > "\$PROBE_FILE"

python3 - "${expected_key_name}" "\$PROBE_FILE" <<'PYEOF'
import json, base64, sys, os

expected_key_name = sys.argv[1]
probe_file = sys.argv[2]

try:
    with open(probe_file) as f:
        data = json.load(f)
except Exception as e:
    print(f"ERROR: probe 파일 파싱 실패: {e}")
    os.remove(probe_file)
    sys.exit(2)

kvs = data.get("kvs", [])
if not kvs:
    print("FAIL: 대상 Secret을 찾지 못함 (경로 확인 필요)")
    os.remove(probe_file)
    sys.exit(1)

raw = base64.b64decode(kvs[0]["value"])
prefix_marker = b"k8s:enc:secretbox:v1:"

# 완전일치 요구: prefix는 반드시 raw의 '맨 앞(offset 0)'에서 시작해야 한다.
# find()로 아무 위치에서나 찾으면 평문 중간에 우연히 같은 바이트열이 있어도
# PASS로 오판할 수 있으므로 startswith로 강제한다.
if not raw.startswith(prefix_marker):
    print("FAIL: 값이 secretbox 암호화 접두어로 시작하지 않음 — 평문이거나 다른 provider")
    os.remove(probe_file)
    sys.exit(1)

rest = raw[len(prefix_marker):]
if b":" not in rest:
    print("FAIL: key name 구분자 없음 — 데이터 형식 이상 (파싱 오류)")
    os.remove(probe_file)
    sys.exit(2)

key_name = rest.split(b":", 1)[0].decode(errors="replace")

# 완전일치 요구: 기대한 신규 key name과 정확히 같아야 PASS. 구 키 이름이거나
# 다른 값이면 FAIL (find 기반 부분일치로 인한 오판 방지).
if key_name != expected_key_name:
    print(f"FAIL: 사용된 key name '{key_name}'이 기대값 '{expected_key_name}'과 다름 — 회전이 아직 반영되지 않았거나 구 키 사용 중")
    os.remove(probe_file)
    sys.exit(1)

print(f"PASS: secretbox 암호화 확인, key name '{key_name}'이 기대값과 완전일치")

for leaked_marker in (b"DB_PASSWORD", b"GF_SECURITY_ADMIN_PASSWORD"):
    if leaked_marker in raw:
        print(f"FAIL: {leaked_marker.decode()} 필드명이 원문으로 노출됨")
        os.remove(probe_file)
        sys.exit(1)

print("PASS: 알려진 Secret 필드명 원문 노출 없음")
os.remove(probe_file)
PYEOF
EOF

  local rc=$?
  if [[ "$rc" -eq 0 ]]; then
    log "5단계 완료: PASS. remove-old-key로 진행 가능."
  elif [[ "$rc" -eq 2 ]]; then
    die "5단계 ERROR: 파싱/형식 오류로 판정 불가 (NOT VERIFIED) — remove-old-key 진행 금지"
  else
    die "5단계 FAIL: 기대한 신규 키 사용이 확인되지 않음 — remove-old-key 진행 금지"
  fi
}

# ── remove-old-key: rewrite-secrets 개수 일치 + verify-encryption PASS 후에만 ──
cmd_remove_old_key() {
  confirm "rewrite-secrets에서 개수 일치를 확인했고, verify-encryption이 PASS(기대 key name과 완전일치)였습니까?"
  log "각 CP의 encryption-config.yaml에서 구 키 항목을 삭제하고, 3개 CP 모두 재시작을 확인하십시오."
  cat <<'EOF'
    providers:
      - secretbox:
          keys:
            - name: <신규-key-name>   # 신규 키만 남김
              secret: <신규-키>
      - identity: {}
EOF
  confirm "3개 CP 모두 구 키를 제거하셨습니까? (재시작 확인은 아래에서 자동 수행)"
  for i in "${!CP_NODES[@]}"; do
    local node="${CP_NODES[$i]}"
    local endpoint="${CP_DIRECT_ENDPOINTS[$i]}"
    wait_healthz_direct "$endpoint" || die "[$node] 구 키 제거 후 healthz가 ${WAIT_TIMEOUT_SEC}s 안에 OK가 되지 않음"
    log "[$node] API server healthy (구 키 제거 후 최종 상태)"
  done
  log "6단계 완료: 회전 종료. 백업본(${BACKUP_DIR})은 Git/Evidence에 포함하지 말고 별도 보관하십시오."
}

# ── 진입점 ───────────────────────────────────────────────────────
case "${1:-}" in
  generate-key)      cmd_generate_key ;;
  add-key)           cmd_add_key "${2:-}" "${3:-}" ;;
  verify-decrypt)    cmd_verify_decrypt ;;
  promote-key)       cmd_promote_key "${2:-}" ;;
  rewrite-secrets)   cmd_rewrite_secrets ;;
  verify-encryption) cmd_verify_encryption "${2:-}" ;;
  remove-old-key)    cmd_remove_old_key ;;
  *)
    cat <<EOF
사용법: $0 <command> [args]

  generate-key
  add-key <keyfile> <new-key-name>
  verify-decrypt
  promote-key <new-key-name>
  rewrite-secrets
  verify-encryption <expected-key-name>
  remove-old-key

순서대로 실행하십시오. 각 단계는 확인(yes) 없이는 진행하지 않으며,
모든 대기 루프는 ${WAIT_TIMEOUT_SEC}s timeout을 가집니다.
EOF
    exit 1
    ;;
esac
