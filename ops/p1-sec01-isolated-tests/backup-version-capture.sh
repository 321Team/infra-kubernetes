#!/usr/bin/env bash
# control01 root: operational state is read only; writes only a new evidence folder.
set -u -o pipefail
umask 077
[[ $(id -u) == 0 && $(hostname -s) == k8s-control01 ]] || exit 2
for c in timeout python3 kubectl sha256sum mktemp; do command -v "$c" >/dev/null || exit 2; done
OUT=$(mktemp -d /root/sec19-backup-version.XXXXXXXX) || exit 2
printf 'CAPTURE_DIR=%s\n' "$OUT"
cap() {
  local name=$1 rc
  shift
  printf '%q ' "$@" > "$OUT/$name.command"
  printf '\n' >> "$OUT/$name.command"
  timeout --signal=TERM --kill-after=2s 15s "$@" > "$OUT/$name.stdout" 2> "$OUT/$name.stderr"
  rc=$?
  printf '%s\n' "$rc" > "$OUT/$name.rc"
  return 0
}
seal() {
  local rc=$?
  trap - EXIT
  date -u +%FT%TZ > "$OUT/ended-utc.txt"
  printf '%s\n' "$rc" > "$OUT/capture-exit.rc"
  (cd "$OUT" && sha256sum -- *.command *.stdout *.stderr *.rc *.txt > SHA256SUMS) || return 2
  printf 'CAPTURE_COMPLETE rc=%s dir=%s\n' "$rc" "$OUT"
}
trap seal EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
date -u +%FT%TZ > "$OUT/started-utc.txt"
hostname > "$OUT/hostname.txt"
cap collector-sha sha256sum -- "$0"
K=(kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s)
cap pod-image "${K[@]}" -n kube-system get pod etcd-k8s-control01 -o 'jsonpath={.metadata.uid}{"\n"}{range .spec.containers[*]}{.name}={.image}{"\n"}{end}{range .status.containerStatuses[*]}{.name}={.imageID}{"\n"}{end}'
cap find-pid python3 -c '
from pathlib import Path
import os,sys
pids=[]
for p in Path("/proc").iterdir():
 if not p.name.isdigit(): continue
 try:
  if Path(os.readlink(p/"exe")).name != "etcd": continue
  args=(p/"cmdline").read_bytes().split(b"\0")
  if b"--data-dir=/var/lib/etcd" in args or any(a==b"--data-dir" and i+1<len(args) and args[i+1]==b"/var/lib/etcd" for i,a in enumerate(args)):
   pids.append(p.name)
 except (FileNotFoundError,ProcessLookupError,PermissionError): continue
if len(pids)!=1: sys.exit("expected exactly one production etcd PID: "+str(pids))
print(pids[0])'
[[ $(cat "$OUT/find-pid.rc") == 0 ]] || exit 2
PID=$(cat "$OUT/find-pid.stdout")
[[ $PID =~ ^[0-9]+$ ]] || exit 2
identity='from pathlib import Path; import os,sys; p=Path("/proc")/sys.argv[1]; s=(p/"stat").read_text(); print(sys.argv[1], s[s.rfind(")")+2:].split()[19]); print(os.readlink(p/"exe"))'
cap process-before python3 -c "$identity" "$PID"
[[ $(cat "$OUT/process-before.rc") == 0 ]] || exit 2
cap running-exe-sha sha256sum "/proc/$PID/exe"
for b in etcd etcdutl etcdctl; do
  p=/proc/$PID/root/usr/local/bin/$b
  arg=version; [[ $b == etcd ]] && arg=--version
  cap "$b-sha" sha256sum "$p"
  cap "$b-file" file -L "$p"
  cap "$b-elf" readelf -h -l "$p"
  cap "$b-host-version" "$p" "$arg"
  cap "$b-container-version" "${K[@]}" -n kube-system exec etcd-k8s-control01 -c etcd -- "/usr/local/bin/$b" "$arg"
done
cap process-after python3 -c "$identity" "$PID"
[[ $(cat "$OUT/process-after.rc") == 0 ]] || exit 2
cmp -s "$OUT/process-before.stdout" "$OUT/process-after.stdout" || exit 2
cap pod-image-after "${K[@]}" -n kube-system get pod etcd-k8s-control01 -o 'jsonpath={.metadata.uid}{"\n"}{range .spec.containers[*]}{.name}={.image}{"\n"}{end}{range .status.containerStatuses[*]}{.name}={.imageID}{"\n"}{end}'
[[ $(cat "$OUT/pod-image.rc") == 0 && $(cat "$OUT/pod-image-after.rc") == 0 ]] || exit 2
cmp -s "$OUT/pod-image.stdout" "$OUT/pod-image-after.stdout" || exit 2
# Individual command failures remain in their .rc; capture completion is not precheck PASS.
exit 0
