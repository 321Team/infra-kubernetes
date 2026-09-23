#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $# != 2 || ! $2 =~ ^[0-9a-f]{40}$ ]]; then
  echo 'usage: bash run.sh /absolute/path/to/infra-ansible/inventory/hosts.yml SOURCE_SHA' >&2
  exit 2
fi
ICR_INVENTORY=$(realpath -- "$1")
ICR_SOURCE_SHA=$2
ICR_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ICR_ROOT=$(git -C "$(dirname -- "$ICR_INVENTORY")" rev-parse --show-toplevel)
[[ $ICR_INVENTORY == "$ICR_ROOT/inventory/hosts.yml" ]] || { echo 'STOP: expected canonical inventory/hosts.yml' >&2; exit 2; }
for ICR_TOOL in ansible-playbook python3 sha256sum tar; do
  command -v "$ICR_TOOL" >/dev/null || { echo "STOP: missing existing $ICR_TOOL" >&2; exit 2; }
done
cd -- "$ICR_ROOT"
ICR_OUT=$(mktemp -d "$ICR_ROOT/ICR_MON_Precheck_XXXXXX")
ICR_COMMIT=$(git rev-parse HEAD)
ICR_DIRTY=false
git diff --quiet HEAD -- . || ICR_DIRTY=true
export ICR_OUT ICR_SOURCE_SHA ICR_COMMIT ICR_DIRTY ICR_INVENTORY
python3 - <<'PY'
import datetime, hashlib, json, os, pathlib, getpass
e=os.environ
record={'started_kst':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
        'controller_account':getpass.getuser(),'source_sha':e['ICR_SOURCE_SHA'],
        'infra_ansible_commit':e['ICR_COMMIT'],'tracked_changes':e['ICR_DIRTY'],
        'inventory':e['ICR_INVENTORY'],
        'inventory_sha256':hashlib.sha256(pathlib.Path(e['ICR_INVENTORY']).read_bytes()).hexdigest(),
        'scope':'pure pre-check; approved existing access only; no Runtime AC verdict'}
pathlib.Path(e['ICR_OUT'],'run-metadata.json').write_text(json.dumps(record,indent=2)+'\n')
pathlib.Path(e['ICR_OUT'],'execution-input.json').write_text(json.dumps(
    {'icr_output_dir':e['ICR_OUT'],'icr_source_sha':e['ICR_SOURCE_SHA']})+'\n')
PY
(
  cd -- "$ICR_SCRIPT_DIR"
  sha256sum collect.py collect.yml run.sh
) > "$ICR_OUT/source-SHA256SUMS"
ICR_RC=0
ansible-playbook -i "$ICR_INVENTORY" "$ICR_SCRIPT_DIR/collect.yml" --syntax-check \
  -e "@$ICR_OUT/execution-input.json" \
  > "$ICR_OUT/syntax-check.log" 2>&1 || ICR_RC=$?
if [[ $ICR_RC == 0 ]]; then
  ansible-playbook -i "$ICR_INVENTORY" "$ICR_SCRIPT_DIR/collect.yml" \
    -e "@$ICR_OUT/execution-input.json" \
    > "$ICR_OUT/ansible.log" 2>&1 || ICR_RC=$?
fi
[[ -s "$ICR_OUT/icr-monitoring-precheck.json" ]] || ICR_RC=2
printf '%s\n' "$ICR_RC" > "$ICR_OUT/controller-exit-code.txt"
(
  cd -- "$ICR_OUT"
  sha256sum ./*.json ./*.log source-SHA256SUMS controller-exit-code.txt > SHA256SUMS
)
ICR_ARCHIVE="$ICR_OUT.tar.gz"
tar -C "$(dirname -- "$ICR_OUT")" -czf "$ICR_ARCHIVE" "$(basename -- "$ICR_OUT")"
(
  cd -- "$(dirname -- "$ICR_OUT")"
  sha256sum "$(basename -- "$ICR_ARCHIVE")" > "$ICR_ARCHIVE.sha256"
)
printf 'RC=%s\nARCHIVE=%s\nSIDECAR=%s\n' "$ICR_RC" "$ICR_ARCHIVE" "$ICR_ARCHIVE.sha256"
exit "$ICR_RC"
