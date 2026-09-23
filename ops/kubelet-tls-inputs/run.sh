#!/usr/bin/env bash
# ansible01 only. Downloads fixed source into a new controller result directory.
set -euo pipefail
umask 077
test "$(hostname -s)" = ansible01
test "$#" -eq 1
TLS_INPUT_SHA=$1
[[ "$TLS_INPUT_SHA" =~ ^[0-9a-f]{40}$ ]]
cd /home/server01_ansible/p1-ans-01
source /home/server01_ansible/.config/ansible/p1-ans-01.env
test -r inventory/hosts.yml
test "${ANSIBLE_DEBUG:-0}" != 1
command -v gh >/dev/null
command -v ansible-playbook >/dev/null
command -v openssl >/dev/null
TLS_INPUT_DIR=$(mktemp -d /home/server01_ansible/MON_TLS_INPUTS.XXXXXXXX)
printf 'RESULT_DIR=%s\n' "$TLS_INPUT_DIR"
mkdir -p "$TLS_INPUT_DIR/source/filter_plugins" "$TLS_INPUT_DIR/results" "$TLS_INPUT_DIR/local-tmp"
printf '%s\n' "$TLS_INPUT_SHA" > "$TLS_INPUT_DIR/source-commit.txt"
for TLS_INPUT_FILE in collect.yml remote.sh filter_plugins/tls_inputs.py README.md run.sh; do
  gh api "repos/321Team/infra-kubernetes/contents/ops/kubelet-tls-inputs/$TLS_INPUT_FILE?ref=$TLS_INPUT_SHA" \
    -H 'Accept: application/vnd.github.raw+json' > "$TLS_INPUT_DIR/source/$TLS_INPUT_FILE"
done
cmp -- "$0" "$TLS_INPUT_DIR/source/run.sh"
python3 -c 'import yaml; print("CONTROLLER_YAML=AVAILABLE")'
TLS_INPUT_RC=0
# no -v, ANSIBLE_DEBUG, debug dump, --tree or registered raw result printing.
# no_log protects raw transfer and parsing. Result files contain selected findings only.
ANSIBLE_CONFIG=/home/server01_ansible/p1-ans-01/ansible.cfg \
ANSIBLE_LOCAL_TEMP="$TLS_INPUT_DIR/local-tmp" \
ANSIBLE_DEBUG=0 ANSIBLE_VERBOSITY=0 \
PYTHONDONTWRITEBYTECODE=1 \
ansible-playbook -i /home/server01_ansible/p1-ans-01/inventory/hosts.yml \
  "$TLS_INPUT_DIR/source/collect.yml" \
  -e "tls_result_dir=$TLS_INPUT_DIR/results" \
  -e '{"ansible_ssh_use_tty":false,"ansible_become":false}' 2>&1 | tee "$TLS_INPUT_DIR/run.txt" || TLS_INPUT_RC=$?
printf 'ANSIBLE_RC=%s\n' "$TLS_INPUT_RC" > "$TLS_INPUT_DIR/result.txt"
(
  cd "$TLS_INPUT_DIR"
  # Do not archive Ansible temp files, inventory, env, raw configs or credentials.
  shopt -s nullglob
  TLS_INPUT_HASH_FILES=(source-commit.txt source/collect.yml source/remote.sh
    source/filter_plugins/tls_inputs.py source/README.md source/run.sh
    run.txt result.txt results/*.json)
  sha256sum "${TLS_INPUT_HASH_FILES[@]}" > SHA256SUMS
)
tar -czf "$TLS_INPUT_DIR.tar.gz" -C "$TLS_INPUT_DIR" \
  source-commit.txt source/collect.yml source/remote.sh source/filter_plugins/tls_inputs.py \
  source/README.md source/run.sh run.txt result.txt results SHA256SUMS
sha256sum "$TLS_INPUT_DIR.tar.gz" > "$TLS_INPUT_DIR.tar.gz.sha256"
printf 'ANSIBLE_RC=%s\nARCHIVE=%s.tar.gz\n' "$TLS_INPUT_RC" "$TLS_INPUT_DIR"
exit "$TLS_INPUT_RC"
