#!/usr/bin/env bash
# Shared lifecycle. Source only; no server action on load.
# /proc/uptime is monotonic on the target Linux host. Integer rounding makes
# budgets conservative. Reserve one second for TERM -> KILL and one for dispatch.
mono() { local up rest; read -r up rest < /proc/uptime || return 1; printf '%s\n' "${up%%.*}"; }
phase_begin() { PHASE="$1"; PHASE_DEADLINE=$(( $(mono) + $2 )); }
budget_ok() { [ "$(mono)" -lt "$PHASE_DEADLINE" ]; }
observation_ok() { budget_ok && [ "$FAIL" -eq 0 ]; }
timeout() {
  local requested rc remaining duration
  # Compatibility with the existing scripts; all calls use integer seconds.
  while [[ "${1:-}" == --* ]]; do shift; done
  requested="${1%s}"; shift
  [[ "$requested" =~ ^[0-9]+$ ]] || return 125
  remaining=$(( PHASE_DEADLINE - $(mono) - 2 ))
  [ "$remaining" -gt 0 ] || return 124
  duration="$requested"; [ "$duration" -le "$remaining" ] || duration="$remaining"
  command timeout --signal=TERM --kill-after=1s "${duration}s" "$@"; rc=$?
  budget_ok || return 124
  return "$rc"
}
pause_bounded() {
  local seconds="$1" remain=$(( PHASE_DEADLINE - $(mono) ))
  [ "$remain" -gt 0 ] || return 124
  [ "$seconds" -le "$remain" ] || seconds="$remain"
  sleep "$seconds"
  budget_ok
}
# Never reenter cleanup. Further INT/TERM signals only affect terminal output;
# no evidence file can be changed by the handler during/after sealing.
on_signal() {
  if [ "$FINISHED" -eq 1 ]; then printf '%s\n' 'Signal during finalization; cleanup continues.' >&2
  else printf '%s\n' 'Signal: stopping observation and finalizing.' >&2; finish 3; fi
}
seal_out() {
  phase_begin seal 30
  timeout 28 python3 "$LIB_DIR/checks.py" seal "$OUT" > "$OUT.sealcheck.txt" 2>&1 || FAIL=1
}
completion() {
  local rc="$1"
  # Atomic sidecar is outside the sealed folder. A failed write is never rc0.
  printf 'EXIT_CODE=%s REV=%s MODE=%s OUT=%s SEALCHECK=%s\n' "$rc" "$REV" "$MODE" "$OUT" "$OUT.sealcheck.txt" > "$OUT.completion.tmp" &&
    mv -- "$OUT.completion.tmp" "$OUT.completion.txt" || return 12
  printf '종료 EXIT_CODE=%s out=%s\n' "$rc" "$OUT"
  return "$rc"
}
