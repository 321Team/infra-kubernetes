#!/usr/bin/env bash
#
# create-secrets-safe.sh
#
# P1-SEC-01 / SEC-02 (High) 수정 — Harbor Pull Secret / DB / Grafana 관리자
# 자격증명을 CLI 인자·stdout 노출 없이 생성한다.
#
# 대응 리뷰: fds-project-baseline#19 (issuecomment-5627308483), SEC-02
#   - TokenRequest stdout, `--docker-password`, `--from-literal` 비밀번호가
#     ps/bash history/로그에 노출되는 경로였음 → 파일·stdin 기반으로 교체
#   - 실행 전 xtrace(set -x) 비활성 강제, 임시 파일 chmod/umask, 중단 시 정리(trap)
#
# PR #7 리뷰(c9ec46d) 반영:
#   - Harbor Robot 이름을 파일에 고정하지 않고 인자로 받는다(운영 기준 robot$worker).
#   - DB Secret을 transaction-api가 실제로 참조하는 transaction-api-db-credentials /
#     DATABASE_URL로 맞춘다(예전 fds-app-db-credentials는 참조하는 워크로드가 없었다).
#   - 값 파일 끝의 줄바꿈 1개를 떼어 Secret 값이 달라지지 않게 한다.
#
# 실행 위치: K8s Control Plane (kubectl 접속 가능한 관리 호스트)
#
# 사용법:
#   ./create-secrets-safe.sh harbor-pull <robot-name> <harbor-secret-file>   # 운영 기준 robot$worker (pull-only)
#   ./create-secrets-safe.sh db-url <database-url-file>                        # transaction-api-db-credentials / DATABASE_URL
#   ./create-secrets-safe.sh grafana-admin <grafana-password-file>
#   ./create-secrets-safe.sh cicd-token <audience> [duration]   # TokenRequest, stdout 미출력, 파일로만 저장
#
# 비밀번호 파일은 실행 전 별도로(예: 비밀번호 관리자에서) 준비하고,
# 이 스크립트가 끝나면 즉시 폐기(shred)한다.

set -euo pipefail
umask 077

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

# xtrace가 켜져 있으면 평문 비밀번호가 stderr/로그에 그대로 남으므로 강제로 끈다.
if [[ "${-}" == *x* ]]; then
  die "이 스크립트는 'bash -x' 등 xtrace 모드로 실행할 수 없습니다 (비밀번호 노출 위험)."
fi

TMP_FILES=()
cleanup() {
  local f
  for f in "${TMP_FILES[@]:-}"; do
    [[ -n "$f" && -f "$f" ]] && shred -u "$f" 2>/dev/null || rm -f "$f" 2>/dev/null
  done
}
trap cleanup EXIT INT TERM

require_pwfile() {
  local f="$1"
  [[ -f "$f" && ! -L "$f" ]] || die "비밀번호 파일 없음 또는 symlink: $f"
  local perm
  perm="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f")"
  [[ "$perm" == "600" || "$perm" == "400" ]] || die "비밀번호 파일 권한이 600/400이 아닙니다: $f ($perm)"
}

# --from-file은 파일 바이트를 그대로 Secret 값으로 넣는다. `echo pass > file`로 만든
# 파일은 끝에 줄바꿈이 붙어 DB/Grafana 인증이 실패한다. 끝 줄바꿈 1개(LF 또는 CRLF)를
# 뗀 사본을 임시 파일로 만들고 그 경로를 STAGED에 넣는다. 비었거나 여러 줄이면 거부한다.
# (호출 측 $(...) 서브셸에서 만들면 TMP_FILES 등록이 부모에 남지 않으므로 전역 변수로 돌려준다.)
STAGED=""
stage_value() {
  local src="$1"
  require_pwfile "$src"
  STAGED="$(mktemp)"
  TMP_FILES+=("$STAGED")
  python3 -I - "$src" "$STAGED" <<'PY' || die "값 파일 형식 오류: 비어 있거나 여러 줄입니다 ($src)"
import sys
data = open(sys.argv[1], 'rb').read()
if data.endswith(b'\r\n'):
    data = data[:-2]
elif data.endswith(b'\n'):
    data = data[:-1]
if not data or b'\n' in data or b'\r' in data:
    sys.exit(1)
open(sys.argv[2], 'wb').write(data)
PY
}

# ── harbor-pull: Worker Pull-only Robot Account Secret ──────────────
cmd_harbor_pull() {
  local robot="${1:?robot-name 인자 필요 (운영 기준: robot\$worker)}"
  local pwfile="${2:?harbor-secret-file 인자 필요}"
  # Robot 이름을 파일에 고정하지 않는다. 형식만 확인하고, pull-only 권한은 Harbor에서 확인한다.
  [[ "$robot" =~ ^robot\$[A-Za-z0-9._-]+$ ]] || die "Robot 이름 형식 오류: $robot"
  stage_value "$pwfile"

  # --docker-password는 argv에 남으므로 사용하지 않는다.
  # kubectl create secret docker-registry는 --docker-password-stdin을 지원하지 않으므로
  # docker config.json 형태를 직접 구성해 --from-file로 생성한다.
  local dockerconfig
  dockerconfig="$(mktemp)"
  TMP_FILES+=("$dockerconfig")

  local password
  password="$(cat "$STAGED")"
  local auth
  auth="$(printf '%s' "${robot}:${password}" | base64 -w0)"
  unset password

  cat > "$dockerconfig" <<EOF
{"auths":{"10.1.93.54":{"username":"${robot}","auth":"${auth}"}}}
EOF
  unset auth

  kubectl create secret generic harbor-pull-secret \
    --namespace fds-app \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$dockerconfig" \
    --dry-run=client -o yaml | kubectl apply -f -

  log "harbor-pull-secret 생성 완료 (robot=${robot}, argv에 비밀번호 노출 없음)."
}

# ── db-url: transaction-api가 실제로 참조하는 Secret ──────────────────
# 02-securitycontext.yaml의 transaction-api는 transaction-api-db-credentials/DATABASE_URL
# 하나만 읽는다(app/db.py). 예전 fds-app-db-credentials(DB_HOST/PORT/USER/PASSWORD)는
# 참조하는 워크로드가 없어 생성해도 앱 기동에 쓰이지 않았다.
cmd_db_url() {
  local urlfile="${1:?database-url-file 인자 필요}"
  stage_value "$urlfile"
  grep -Eq '^postgres(ql)?(\+[a-z0-9]+)?://[^[:space:]@]+@10\.1\.93\.55:5432/[^[:space:]/]+$' "$STAGED" \
    || die "DATABASE_URL 형식/대상 오류: postgresql[+driver]://<user>:<password>@10.1.93.55:5432/<db> 형식이어야 합니다 (값은 출력하지 않음)"

  kubectl create secret generic transaction-api-db-credentials \
    --namespace fds-app \
    --from-file=DATABASE_URL="$STAGED" \
    --dry-run=client -o yaml | kubectl apply -f -

  log "transaction-api-db-credentials 생성 완료 (key DATABASE_URL, argv에 값 없음)."
}

# ── grafana-admin ────────────────────────────────────────────────
cmd_grafana_admin() {
  local pwfile="${1:?grafana-password-file 인자 필요}"
  stage_value "$pwfile"

  kubectl create secret generic grafana-admin-credentials \
    --namespace monitoring-api \
    --from-literal=GF_SECURITY_ADMIN_USER=admin \
    --from-file=GF_SECURITY_ADMIN_PASSWORD="$STAGED" \
    --dry-run=client -o yaml | kubectl apply -f -

  log "grafana-admin-credentials 생성 완료."
}

# ── cicd-token: TokenRequest 결과를 stdout에 찍지 않고 파일로만 저장 ──
cmd_cicd_token() {
  local audience="${1:?audience 인자 필요}"
  local duration="${2:-1h}"
  local outfile
  outfile="$(mktemp)"
  chmod 600 "$outfile"
  # Deep Review 6276ade 지적 #6: 예전 코드는 "성공 후 인계"를 위해 이 파일을 처음부터
  # TMP_FILES에서 뺐는데, 그러면 kubectl 자체가 실패/중단돼도 trap이 정리하지 않아
  # 빈 파일이나 부분 노출된 데이터가 남는다. 일단 TMP_FILES에 등록해 실패 시
  # 자동 폐기되게 하고, 성공을 확인한 뒤에만 아래에서 제외해 인계용으로 남긴다.
  TMP_FILES+=("$outfile")

  kubectl create token cicd-runner-sa -n ci-cd \
    --duration="$duration" --audience="$audience" > "$outfile" \
    || die "TokenRequest 실패 — 임시 파일은 trap으로 자동 폐기됩니다."

  [[ -s "$outfile" ]] || die "TokenRequest 결과 파일이 비어 있음 — 임시 파일은 trap으로 자동 폐기됩니다."

  local -a kept=()
  local f
  for f in "${TMP_FILES[@]}"; do
    [[ "$f" == "$outfile" ]] || kept+=("$f")
  done
  TMP_FILES=("${kept[@]:-}")

  log "TokenRequest 완료. 토큰은 화면에 출력하지 않았습니다."
  log "저장 위치: $outfile (권한 600) — P1-CICD-01 소관 주입 절차로 즉시 이관 후 이 파일을 폐기하십시오."
  log "이 스크립트 종료 시 trap이 자동 폐기하지 않도록, 주입 완료 전까지는 별도 터미널에서 수동 관리하십시오."
}

case "${1:-}" in
  harbor-pull)      cmd_harbor_pull "${2:-}" "${3:-}" ;;
  db-url)           cmd_db_url "${2:-}" ;;
  grafana-admin)    cmd_grafana_admin "${2:-}" ;;
  cicd-token)       cmd_cicd_token "${2:-}" "${3:-1h}" ;;
  *)
    cat <<EOF
사용법: $0 <command> [args]

  harbor-pull <robot-name> <harbor-secret-file>   (운영 기준 robot\$worker)
  db-url <database-url-file>
  grafana-admin <grafana-password-file>
  cicd-token <audience> [duration=1h]

모든 비밀번호는 파일(권한 600)로만 전달하며 argv/stdout에 평문을 남기지 않습니다.
EOF
    exit 1
    ;;
esac
