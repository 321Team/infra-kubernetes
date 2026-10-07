#!/usr/bin/env bash
# Only backup.sh launches this inside a fresh net + PID namespace.
set -euo pipefail
set +x
source "$LIB_DIR/common.sh"
phase_begin inner 285
EP=""
inner_finish() {
  local rc=$?
  trap '' INT TERM
  trap - EXIT
  if [ -n "$EP" ]; then
    kill -TERM "$EP" 2>/dev/null || true
    sleep 2
    kill -KILL "$EP" 2>/dev/null || true
    wait "$EP" 2>/dev/null || true
  fi
  exit "$rc"
}
trap inner_finish EXIT
trap 'exit 130' INT TERM
ip link set lo up
"$BIN/etcd" --name "$NAME" --data-dir "$DD" \
  --listen-client-urls "$CURL" --advertise-client-urls "$CURL" \
  --listen-peer-urls "$PURL" --initial-advertise-peer-urls "$PURL" \
  --initial-cluster "$NAME=$PURL" --initial-cluster-token "$TOKEN" \
  --log-outputs stderr > "$OUT/40-etcd.stdout" 2> "$OUT/40-etcd.stderr" &
EP=$!
ok=0
for ((i=0; i<30; i++)); do
  if timeout 5 "$BIN/etcdctl" --endpoints="$CURL" endpoint health > "$OUT/41-temp-health.txt" 2>&1; then ok=1; break; fi
  pause_bounded 1 || break
done
echo "temp_etcd_health_ok=$ok" > "$OUT/41-temp-health.rc"
[ "$ok" -eq 1 ] || exit 22
timeout 15 "$BIN/etcdctl" --endpoints="$CURL" endpoint status -w json > "$OUT/42-temp-status.json" 2> "$OUT/42-temp-status.stderr"
timeout 10 python3 "$LIB_DIR/checks.py" revision < "$OUT/42-temp-status.json" > "$OUT/42-temp-revision.txt"
# Preserve stdout, stderr and rc separately. An error string cannot be a count.
rc=0
timeout 15 "$BIN/etcdctl" --endpoints="$CURL" get "" --prefix --count-only --write-out=fields > "$OUT/43-temp-livekeys.txt" 2> "$OUT/43-temp-livekeys.stderr" || rc=$?
printf '%s\n' "$rc" > "$OUT/43-temp-livekeys.rc"
[ "$rc" -eq 0 ] || exit 23
timeout 10 python3 "$LIB_DIR/checks.py" count < "$OUT/43-temp-livekeys.txt" > "$OUT/43-temp-livekeys-count.txt"
# Values stay in the pipe and process memory. Neither stdout nor diagnostics
# from etcdctl may expose Secret values in evidence.
timeout 20 "$BIN/etcdctl" --endpoints="$CURL" get /registry/secrets/ --prefix -w json 2>/dev/null |
  timeout 20 python3 "$LIB_DIR/checks.py" secrets > "$OUT/44-secret-prefix-counts.txt" 2> "$OUT/44-secret-prefix-counts.stderr"
timeout 10 ss -ltn > "$OUT/45-listeners-in-netns.txt" 2> "$OUT/45-listeners-in-netns.stderr"
budget_ok
echo inner_done >> "$OUT/41-temp-health.rc"
