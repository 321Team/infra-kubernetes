#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
action=${1:-check}
[[ "$action" == check || "$action" == apply ]] || { echo 'usage: cockpit.sh check|apply' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'run with sudo' >&2; exit 2; }
failed=0
for unit in cockpit.socket cockpit.service; do
  details=$(systemctl show "$unit" -p Id -p LoadState -p UnitFileState -p ActiveState)
  printf '%s\n' "$details"
  load=$(awk -F= '$1=="LoadState"{print $2}' <<<"$details")
  [[ -n "$load" ]] || { echo "QUERY_ERROR: $unit" >&2; exit 2; }
  if [[ "$load" == not-found ]]; then
    echo "NOT_INSTALLED: $unit"
    continue
  fi
  if [[ "$action" == apply ]]; then
    systemctl disable --now "$unit"
    systemctl mask "$unit"
    details=$(systemctl show "$unit" -p Id -p LoadState -p UnitFileState -p ActiveState)
    printf '%s\n' "$details"
  fi
  state=$(awk -F= '$1=="UnitFileState"{print $2}' <<<"$details")
  active=$(awk -F= '$1=="ActiveState"{print $2}' <<<"$details")
  [[ "$state" == masked && "$active" == inactive ]] || failed=1
done
# Capture the return code before interpreting an empty result.
listeners=$(ss -H -ltn 'sport = :9090') || { echo 'SS_QUERY_ERROR' >&2; exit 2; }
printf '%s\n' "$listeners"
[[ -z "$listeners" ]] || failed=1
(( failed == 0 )) || { echo 'COCKPIT_HOLD'; exit 1; }
echo 'COCKPIT_STATE_OK; post-change reboot persistence is a separate recorded check'
