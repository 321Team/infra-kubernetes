#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ ( $# != 2 && $# != 3 ) || ! ${2-} =~ ^[0-9a-f]{40}$ || ( $# == 3 && ${3-} != --counters-only ) ]]; then
  echo 'usage: bash run.sh /absolute/path/to/infra-ansible/inventory/hosts.yml SOURCE_SHA [--counters-only]' >&2
  exit 2
fi
ICR_MODE=source
ICR_PLAYBOOK=collect.yml
ICR_PREFIX=ICR_Scrape_Source_
ICR_EXPECTED=(k8s-control01-guard-before k8s-control01-guard-after k8s-worker01-prepare k8s-worker01-observe k8s-worker03-prepare k8s-worker03-observe)
if [[ ${3-} == --counters-only ]]; then
  ICR_MODE=counters-only
  ICR_PLAYBOOK=counters.yml
  ICR_PREFIX=ICR_Scrape_Counters_
  ICR_EXPECTED=(k8s-control01-guard-before k8s-control01-guard-after k8s-worker01-counters k8s-worker03-counters)
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
ICR_OUT=$(mktemp -d "$ICR_ROOT/${ICR_PREFIX}XXXXXX")
ICR_COMMIT=$(git rev-parse HEAD)
ICR_DIRTY=false
git diff --quiet HEAD -- . || ICR_DIRTY=true
export ICR_OUT ICR_SOURCE_SHA ICR_COMMIT ICR_DIRTY ICR_INVENTORY ICR_MODE
python3 - <<'PY'
import datetime, hashlib, json, os, pathlib, getpass
e=os.environ
record={'started_kst':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
        'controller_account':getpass.getuser(),'source_sha':e['ICR_SOURCE_SHA'],
        'infra_ansible_commit':e['ICR_COMMIT'],'tracked_changes':e['ICR_DIRTY'],
        'inventory':e['ICR_INVENTORY'],
        'inventory_sha256':hashlib.sha256(pathlib.Path(e['ICR_INVENTORY']).read_bytes()).hexdigest(),
        'collection_mode':e['ICR_MODE'],
        'scope':'pure pre-check; approved existing access only; no Runtime AC verdict'}
pathlib.Path(e['ICR_OUT'],'run-metadata.json').write_text(json.dumps(record,indent=2)+'\n')
pathlib.Path(e['ICR_OUT'],'execution-input.json').write_text(json.dumps(
    {'icr_output_dir':e['ICR_OUT'],'icr_source_sha':e['ICR_SOURCE_SHA']})+'\n')
PY
(
  cd -- "$ICR_SCRIPT_DIR"
  sha256sum collect.py collect.yml counters.yml stage.yml run.sh
) > "$ICR_OUT/source-SHA256SUMS"
ICR_RC=0
ansible-playbook -i "$ICR_INVENTORY" "$ICR_SCRIPT_DIR/$ICR_PLAYBOOK" --syntax-check \
  -e "@$ICR_OUT/execution-input.json" \
  > "$ICR_OUT/syntax-check.log" 2>&1 || ICR_RC=$?
if [[ $ICR_RC == 0 ]]; then
  ansible-playbook -i "$ICR_INVENTORY" "$ICR_SCRIPT_DIR/$ICR_PLAYBOOK" \
    -e "@$ICR_OUT/execution-input.json" \
    > "$ICR_OUT/ansible.log" 2>&1 || ICR_RC=$?
fi
for ICR_FILE in "${ICR_EXPECTED[@]}"; do
  [[ -s "$ICR_OUT/$ICR_FILE.json" ]] || ICR_RC=2
done
if [[ $ICR_RC == 0 ]]; then
  # Finish both sender guards before translating partial reads into controller RC=3.
  python3 - <<'PY' || ICR_RC=$?
import json, os, pathlib, sys
try:
    for p in pathlib.Path(os.environ['ICR_OUT']).glob('k8s-*.json'):
        e=json.loads(p.read_text()); r=json.loads(e['stdout'])
        if e['rc'] != 0 or r['result'] != 'COLLECTED_PENDING_ANALYSIS' or r.get('read_failures', 0):
            sys.exit(3)
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(3)
PY
fi
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
