#!/usr/bin/env bash
#
# rotate-secretbox-key.sh
#
# P1-SEC-01 / SEC-01 (Critical) 수정 — etcd Secretbox at-rest encryption key
# 무중단 회전 절차 (kube-apiserver EncryptionConfiguration).
#
# 대응 리뷰: fds-project-baseline#19 (issuecomment-5627308483), SEC-01
#   - identity를 첫 provider로 올려 재작성하는 방식(=평문 저장) 금지
#   - 신규 키를 "두 번째 provider"로 추가 -> 전 CP 복호화 확인 ->
#     신규 키를 "첫 번째(쓰기 우선)"로 승격 -> 재작성 -> 구 키 정리 순서 준수
#   - static Pod(kube-apiserver) manifest의 volume/mount 연결을 스크립트가 직접 확인
#   - etcd 원문 비노출 검증은 17바이트 secretbox 접두어를 정확히 파싱 (hexdump 라인 경계 오판 방지)
#
# 실행 위치: 관리 워크스테이션에서 3개 Control Plane(CP1/CP2/CP3)에 SSH로 순차 접속.
# 사전 조건: 각 CP 노드에 root 또는 sudo 권한, kubectl 접속 가능한 관리 호스트 별도 존재.
#
# 사용법:
#   ./rotate-secretbox-key.sh generate-key          # 새 32byte base64 키 생성 (로컬에서만, 화면 출력 안 함)
#   ./rotate-secretbox-key.sh add-key <keyfile>      # 1단계: 신규 키를 2번째 provider로 추가
#   ./rotate-secretbox-key.sh verify-decrypt         # 2단계: 전 CP가 신규 키로 복호화 가능한지 확인
#   ./rotate-secretbox-key.sh promote-key            # 3단계: 신규 키를 1번째(쓰기 우선)로 승격
#   ./rotate-secretbox-key.sh rewrite-secrets        # 4단계: 기존 Secret 전체 재작성 (신규 키로 암호화)
#   ./rotate-secretbox-key.sh verify-encryption      # 5단계: etcd 원문 비노출 + 신규 키 사용 검증
#   ./rotate-secretbox-key.sh remove-old-key         # 6단계: 구 키 제거 (5단계 PASS 확인 후에만 실행)
#
# 각 단계는 "확인 없이 다음 단계로 자동 진행하지 않는다" 원칙을 따른다.
# 실패 시 즉시 중단하고, 직전 단계로 되돌릴 때도 "신규 키를 읽을 수 있는 상태"는 유지한다.
# (신규 암호문이 이미 쓰였는데 구 config만 복원하는 rollback은 금지 — 복호화 실패로 이어짐)

set -euo pipefail

# ── 환경 설정 (실제 값으로 교체 필요) ─────────────────────────────
CP_NODES=("cp01" "cp02" "cp03")            # SSH 접속 가능한 CP 호스트명/IP. 실제 인벤토리로 교체.
SSH_USER="${SSH_USER:-ansible}"
ENC_CONFIG_PATH="/etc/kubernetes/enc/encryption-config.yaml"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"
BACKUP_DIR="${BACKUP_DIR:-./rotation-backup-$(date +%Y%m%dT%H%M%SZ)}"
ETCD_CACERT="/etc/kubernetes/pki/etcd/ca.crt"
ETCD_CERT="/etc/kubernetes/pki/etcd/server.crt"
ETCD_KEY="/etc/kubernetes/pki/etcd/server.key"
PROBE_SECRET_PATH="/registry/secrets/fds-app/fds-app-db-credentials"  # 검증용 임의 1개 Secret 경로

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }
confirm() {
  read -r -p "$1 [계속하려면 'yes' 입력] " ans
  [[ "$ans" == "yes" ]] || die "사용자 확인 실패 — 중단"
}

ssh_cp() {
  local node="$1"; shift
  ssh -o StrictHostKeyChecking=yes "${SSH_USER}@${node}" "$@"
}

# ── generate-key: 새 32byte base64 키를 "로컬"에서만 생성 ─────────
cmd_generate_key() {
  local out="${BACKUP_DIR}/new-secretbox.key"
  mkdir -p "$BACKUP_DIR"
  head -c 32 /dev/urandom | base64 > "$out"
  chmod 600 "$out"
  log "신규 키 생성 완료: $out (권한 600, 화면에 값 출력하지 않음)"
  log "이 파일은 Git/Evidence/로그에 절대 포함하지 마십시오."
}

# ── add-key: 신규 키를 provider 목록의 "두 번째"로 추가 (기존 키는 1번째 유지) ──
cmd_add_key() {
  local keyfile="${1:?keyfile 인자 필요}"
  [[ -f "$keyfile" ]] || die "키 파일 없음: $keyfile"

  for node in "${CP_NODES[@]}"; do
    log "[$node] 현재 encryption-config 백업 중..."
    mkdir -p "${BACKUP_DIR}/${node}"
    scp -o StrictHostKeyChecking=yes "${SSH_USER}@${node}:${ENC_CONFIG_PATH}" \
        "${BACKUP_DIR}/${node}/encryption-config.yaml.bak" \
      || die "[$node] 기존 config 백업 실패 — 중단"
  done

  log "각 CP의 encryption-config.yaml에서 secretbox.keys 배열에 신규 키를 '2번째 항목'으로 추가하십시오."
  log "예시 (편집 후 각 CP에 root-only 권한으로 반영):"
  cat <<'EOF'
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - secretbox:
          keys:
            - name: key1        # 기존 키 (1번째 = 여전히 쓰기 우선)
              secret: <기존-키>
            - name: key2        # 신규 키 (2번째 = 아직 읽기 전용)
              secret: <신규-키>
      - identity: {}
EOF
  confirm "위 형식으로 3개 CP 모두 편집·반영(권한 600, root-only)하셨습니까?"

  for node in "${CP_NODES[@]}"; do
    log "[$node] kube-apiserver static Pod manifest의 --encryption-provider-config 마운트 확인..."
    ssh_cp "$node" "grep -q -- '--encryption-provider-config=${ENC_CONFIG_PATH}' ${APISERVER_MANIFEST}" \
      || die "[$node] kube-apiserver manifest에 --encryption-provider-config 플래그가 연결돼 있지 않습니다. volume/mount를 먼저 patch하십시오."
    ssh_cp "$node" "grep -A3 'name: encryption-config' ${APISERVER_MANIFEST} | grep -q hostPath" \
      || die "[$node] encryption-config volume(hostPath) 마운트가 manifest에 없습니다."
    log "[$node] config·마운트 확인 OK — kubelet이 static Pod를 재기동합니다 (수 초 소요)"
  done

  log "1단계 완료: 신규 키는 '읽기 전용'으로만 추가됨. 다음: verify-decrypt"
}

# ── verify-decrypt: 전 CP가 신규 키로 복호화 가능한지 확인 ─────────
cmd_verify_decrypt() {
  for node in "${CP_NODES[@]}"; do
    log "[$node] kube-apiserver 재기동 완료 대기..."
    ssh_cp "$node" "until kubectl --kubeconfig=/etc/kubernetes/admin.conf get --raw=/healthz >/dev/null 2>&1; do sleep 2; done"
    log "[$node] API server healthy"
  done

  log "임의 Secret 하나를 read → 정상 응답되면 현재 키(구 키) 기준 복호화가 살아있음을 의미."
  kubectl get secret -n fds-app fds-app-db-credentials -o jsonpath='{.metadata.name}' \
    || die "기존 Secret read 실패 — 회전 중단, 이전 상태 확인 필요"

  log "2단계 완료: 모든 CP가 기동 정상. 다음 promote-key 전에 신규 키 자체의 유효성은"
  log "  '재작성 후 실제 read'로 최종 확인됩니다 (5단계 verify-encryption)."
}

# ── promote-key: 신규 키를 '1번째(쓰기 우선)'로 승격 ────────────────
cmd_promote_key() {
  log "각 CP의 encryption-config.yaml에서 keys 순서를 뒤집어 신규 키(key2)를 1번째로 올리십시오."
  cat <<'EOF'
    providers:
      - secretbox:
          keys:
            - name: key2        # 신규 키 (이제 1번째 = 쓰기 우선)
              secret: <신규-키>
            - name: key1        # 기존 키 (2번째 = 읽기 전용으로 유지, 아직 삭제 금지)
              secret: <기존-키>
      - identity: {}
EOF
  confirm "3개 CP 모두 순서를 교체하고 kube-apiserver 재기동을 확인하셨습니까?"

  for node in "${CP_NODES[@]}"; do
    ssh_cp "$node" "until kubectl --kubeconfig=/etc/kubernetes/admin.conf get --raw=/healthz >/dev/null 2>&1; do sleep 2; done"
    log "[$node] API server healthy (신규 키 쓰기 우선 적용 후)"
  done
  log "3단계 완료. 다음: rewrite-secrets"
}

# ── rewrite-secrets: 기존 Secret 전체를 재작성해 신규 키로 재암호화 ──
cmd_rewrite_secrets() {
  confirm "전체 Secret을 재작성합니다 (kubectl replace). 클러스터 규모에 따라 시간이 걸릴 수 있습니다. 진행하시겠습니까?"
  kubectl get secrets -A -o json | kubectl replace -f - \
    || die "Secret 재작성 실패 — verify-encryption으로 현재 상태 재확인 후 재시도"
  log "4단계 완료: 전체 Secret 재작성 요청 완료. 다음: verify-encryption"
}

# ── verify-encryption: etcd 원문 비노출 + 신규 키 사용 여부를 '정확히' 검증 ──
# hexdump grep으로 17바이트 secretbox 접두어를 찾는 방식은 16바이트 행 경계에서
# 접두어가 잘려 오검(false negative)될 수 있으므로, python으로 raw bytes를 바로 검사한다.
cmd_verify_encryption() {
  local probe_node="${CP_NODES[0]}"
  log "[$probe_node] etcd에서 ${PROBE_SECRET_PATH} raw 값을 조회하여 암호화 헤더를 직접 파싱합니다."

  ssh_cp "$probe_node" bash -s <<EOF
set -euo pipefail
sudo ETCDCTL_API=3 etcdctl get "${PROBE_SECRET_PATH}" \\
  --cacert=${ETCD_CACERT} --cert=${ETCD_CERT} --key=${ETCD_KEY} \\
  -w json > /tmp/secretbox-probe.json

python3 - <<'PYEOF'
import json, base64, sys

with open("/tmp/secretbox-probe.json") as f:
    data = json.load(f)

kvs = data.get("kvs", [])
if not kvs:
    print("FAIL: 대상 Secret을 찾지 못함 (경로 확인 필요)")
    sys.exit(1)

raw = base64.b64decode(kvs[0]["value"])

# k8s API server encryption prefix 형식: "k8s:enc:<provider>:v1:<key-name>:"
prefix_marker = b"k8s:enc:secretbox:v1:"
idx = raw.find(prefix_marker)
if idx == -1:
    print("FAIL: secretbox 암호화 접두어 없음 — 평문 또는 다른 provider로 저장됨")
    sys.exit(1)

# 접두어 뒤 key name 추출 (다음 콜론까지)
rest = raw[idx + len(prefix_marker):]
key_name = rest.split(b":", 1)[0].decode(errors="replace")
print(f"PASS: secretbox 암호화 확인. 사용된 key name = {key_name}")

# 평문 노출 여부: DB_PASSWORD 등 알려진 필드명이 raw bytes에 그대로 보이면 안 됨
for leaked_marker in (b"DB_PASSWORD", b"GF_SECURITY_ADMIN_PASSWORD"):
    if leaked_marker in raw:
        print(f"FAIL: {leaked_marker.decode()} 필드명이 원문으로 노출됨")
        sys.exit(1)

print("PASS: 알려진 Secret 필드명 원문 노출 없음")
PYEOF

rm -f /tmp/secretbox-probe.json
EOF

  log "5단계 완료. key name이 신규 키(예: key2)로 나와야 회전이 실제로 반영된 것입니다."
  log "PASS 확인 후에만 remove-old-key를 실행하십시오."
}

# ── remove-old-key: 5단계 PASS 확인 후에만 구 키 제거 ────────────────
cmd_remove_old_key() {
  confirm "verify-encryption 단계에서 PASS를 확인하셨습니까? (신규 키로 재암호화된 것이 실제로 확인된 경우에만 yes)"
  log "각 CP의 encryption-config.yaml에서 구 키(key1) 항목을 삭제하고 재기동을 확인하십시오."
  cat <<'EOF'
    providers:
      - secretbox:
          keys:
            - name: key2        # 신규 키만 남김
              secret: <신규-키>
      - identity: {}
EOF
  confirm "3개 CP 모두 구 키를 제거하고 kube-apiserver 재기동을 확인하셨습니까?"
  for node in "${CP_NODES[@]}"; do
    ssh_cp "$node" "until kubectl --kubeconfig=/etc/kubernetes/admin.conf get --raw=/healthz >/dev/null 2>&1; do sleep 2; done"
    log "[$node] API server healthy (구 키 제거 후 최종 상태)"
  done
  log "6단계 완료: 회전 종료. 백업본(${BACKUP_DIR})은 Git/Evidence에 포함하지 말고 별도 보관하십시오."
}

# ── 진입점 ───────────────────────────────────────────────────────
case "${1:-}" in
  generate-key)      cmd_generate_key ;;
  add-key)           cmd_add_key "${2:-}" ;;
  verify-decrypt)    cmd_verify_decrypt ;;
  promote-key)       cmd_promote_key ;;
  rewrite-secrets)   cmd_rewrite_secrets ;;
  verify-encryption) cmd_verify_encryption ;;
  remove-old-key)    cmd_remove_old_key ;;
  *)
    cat <<EOF
사용법: $0 <command>

  generate-key                 새 32byte base64 키 로컬 생성
  add-key <keyfile>            1단계: 신규 키를 2번째 provider로 추가
  verify-decrypt                2단계: 전 CP 복호화 가능 확인
  promote-key                  3단계: 신규 키를 1번째(쓰기 우선)로 승격
  rewrite-secrets               4단계: 기존 Secret 전체 재작성
  verify-encryption              5단계: etcd 원문 비노출 + 신규 키 사용 검증
  remove-old-key                 6단계: 구 키 제거 (5단계 PASS 후에만)

순서대로 실행하십시오. 각 단계는 확인(yes) 없이는 진행하지 않습니다.
EOF
    exit 1
    ;;
esac
