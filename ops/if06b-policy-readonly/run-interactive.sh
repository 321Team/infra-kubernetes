#!/usr/bin/env bash
# Run as server01_ansible on ansible01. Passwords are read by SSH/sudo on TTY only.
set -euo pipefail
set +x
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(hostname -s)" = ansible01
sha256sum -c SHA256SUMS
RESULT=$(mktemp -d "$HOME/if06b-receipt.XXXXXXXX")
SOCKET="$RESULT/ssh-control"
TARGET=server01_mgmt-gw@10.1.93.58
SSH=(-o StrictHostKeyChecking=yes -o ConnectTimeout=8 -o ControlPath="$SOCKET")
finish() { ssh "${SSH[@]}" -O exit "$TARGET" >/dev/null 2>&1 || true; }
trap finish EXIT
printf 'SSH 비밀번호를 터미널에 직접 입력하십시오. 결과 경로: %s\n' "$RESULT"
# Do not combine ControlMaster=yes with -M: OpenSSH switches it to ask mode.
ssh "${SSH[@]}" -o ControlMaster=yes -o ControlPersist=300 -Nf "$TARGET"
# Subsequent operations must reuse this session; never prompt repeatedly on fallback.
SSH+=(-o BatchMode=yes)
ssh "${SSH[@]}" "$TARGET" true
REMOTE=$(ssh "${SSH[@]}" "$TARGET" 'umask 077; mktemp -d /tmp/if06b-code.XXXXXXXX')
[[ "$REMOTE" =~ ^/tmp/if06b-code\.[A-Za-z0-9]+$ ]] || exit 2
scp "${SSH[@]}" collect.py SOURCE_SHA256SUMS "$TARGET:$REMOTE/"
cat > "$RESULT/remote-command.sh" <<'REMOTE_SCRIPT'
set -uo pipefail
set +x
umask 077
cd "$1" || exit 2
test "$(hostname -s)" = mgmt-gw01 || exit 2
sha256sum -c SOURCE_SHA256SUMS || exit 2
sudo -v || exit 2
date -u +%FT%TZ > started-utc.txt
sudo python3 "$PWD/collect.py" > collector.stdout 2> collector.stderr
rc=$?
printf '%s\n' "$rc" > collector.rc
cat collector.stdout
cat collector.stderr >&2
OUT=$(sed -n 's/^OUT=//p' collector.stdout | head -1)
[[ "$OUT" =~ ^/root/if06b-policy-[A-Za-z0-9_]+$ ]] || exit 2
# Redirect is performed by the original user into this private staging folder.
# No ownership/mode change to root evidence or system configuration.
sudo -n tar -C /root -czf - "${OUT##*/}" > capture.tar.gz || exit 12
sha256sum capture.tar.gz > capture.tar.gz.sha256 || exit 12
date -u +%FT%TZ > finished-utc.txt
printf 'COLLECTOR_RC=%s (raw results retained even on STOP)\n' "$rc"
REMOTE_SCRIPT
scp "${SSH[@]}" "$RESULT/remote-command.sh" "$TARGET:$REMOTE/"
set +e
ssh -t "${SSH[@]}" "$TARGET" "bash '$REMOTE/remote-command.sh' '$REMOTE'"
remote_rc=$?
set -e
printf '%s\n' "$remote_rc" > "$RESULT/remote-command.rc"
printf '%s\n' "$REMOTE" > "$RESULT/remote-stage.txt"
for name in collector.stdout collector.stderr collector.rc capture.tar.gz capture.tar.gz.sha256 started-utc.txt finished-utc.txt; do
  scp "${SSH[@]}" "$TARGET:$REMOTE/$name" "$RESULT/"
done
(cd "$RESULT" && sha256sum -c capture.tar.gz.sha256)
printf '회수 완료: %s\n' "$RESULT"
printf 'collector.rc가 0이어도 정책 PASS/과거 8443 차단 귀속을 의미하지 않습니다.\n'
