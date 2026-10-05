#!/bin/bash
# READ-ONLY on ansible01. A successful Ansible transport and each remote gate are required.
# No Namespace creation, image pull, Secret creation, policy or permission changes.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); MAN=${1:-$HERE/manifests/ac-net-run01.yaml}
source ~/.config/ansible/p1-ans-01.env || exit 2
export ANSIBLE_PRIVATE_KEY_FILE
cd ~/infra-ansible || exit 2
umask 077
OUT=$(mktemp -d ~/ac-net-run01-precheck-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX) || exit 2
KC="kubectl --kubeconfig /etc/kubernetes/admin.conf"
fail=0
remote_check() {
  local label="$1" host="$2" command="$3" marker="$4" rc
  ansible -i inventory/hosts.yml "$host" -b -m shell -a "$command" > "$OUT/$label.stdout" 2> "$OUT/$label.stderr"; rc=$?
  printf '%s\n' "$rc" > "$OUT/$label.rc" || return 1
  cat "$OUT/$label.stdout" "$OUT/$label.stderr"
  [ "$rc" -eq 0 ] && grep -Fxq "$marker" "$OUT/$label.stdout"
}
main() {
  echo '# AC-NET-RUN01 PRECHECK (read-only)'; date -Is; hostname
  echo "manifest=$MAN"; sha256sum "$MAN" || return 1
  IMAGE=$(awk '/^[[:space:]]+image: / {print $2; exit}' "$MAN") || return 1
  # Strict token validation before using the reviewed image in a remote shell command.
  [[ "$IMAGE" =~ ^[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'FAIL: image is not a valid pinned reference'; return 1; }
  DIGEST=${IMAGE#*@}; echo "IMAGE=$IMAGE"; echo "DIGEST=$DIGEST"
  ns_cmd="$KC get ns ac-net-run01 --ignore-not-found -o name"
  remote_check namespace k8s-control01 "ns=\$($ns_cmd) || exit \$?; [ -z \"\$ns\" ] || { printf '%s\\n' \"\$ns\"; exit 1; }; echo ACNET_NAMESPACE_ABSENT" ACNET_NAMESPACE_ABSENT || { echo 'FAIL: namespace absence not confirmed'; fail=1; }
  gate_cmd=$(cat <<EOF
nodes=\$($KC get nodes --no-headers) || exit \$?
printf '%s\\n' "\$nodes"
printf '%s\\n' "\$nodes" | awk 'NF {total++; if (\$2=="Ready") ready++; if (seen[\$1]++) dup=1} END {exit !(total==6 && ready==6 && !dup)}' || exit 1
ready=\$($KC get --raw=/readyz) || exit \$?
printf 'readyz=%s\\n' "\$ready"
[ "\$ready" = ok ] || exit 1
echo ACNET_NODES_READYZ_OK
EOF
)
  remote_check nodes-readyz k8s-control01 "$gate_cmd" ACNET_NODES_READYZ_OK || { echo 'FAIL: nodes/readyz gate failed'; fail=1; }
  for w in k8s-worker01 k8s-worker02 k8s-worker03; do
    # stderr of the crictl calls goes to a remote temp file, never into the command's stdout: ansible ad-hoc output appends stderr
    # right after the last stdout line, which glued a crictl warning onto the success marker (false PRECHECK_FAIL, 2026-10-05).
    # The marker still has to be a whole line (grep -Fx) and the exit status still counts; the stderr is printed only on failure.
    image_cmd=$(cat <<EOF
e=\$(mktemp) || exit 1
images=\$(crictl images --digests --no-trunc 2>"\$e") || { rc=\$?; sed 's/^/crictl-images-stderr: /' "\$e"; rm -f "\$e"; exit \$rc; }
printf '%s\\n' "\$images" | grep -F -- '$DIGEST' || { rm -f "\$e"; exit 1; }
crictl inspecti '$IMAGE' >/dev/null 2>"\$e" || { rc=\$?; sed 's/^/crictl-inspecti-stderr: /' "\$e"; rm -f "\$e"; exit \$rc; }
rm -f "\$e"
echo ACNET_IMAGE_OK
EOF
)
    remote_check "$w-image" "$w" "$image_cmd" ACNET_IMAGE_OK || { echo "FAIL: $w exact image unresolved"; fail=1; }
  done
  [ "$fail" -eq 0 ]
}
main 2>&1 | tee "$OUT/run.log"
rcs=("${PIPESTATUS[@]}")
rc=1
if [ "${rcs[0]}" -eq 0 ] && [ "${rcs[1]}" -eq 0 ]; then
  (cd "$OUT" && sha256sum -- *.stdout *.stderr *.rc run.log > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null) && rc=0
fi
if [ "$rc" -eq 0 ]; then
  printf 'PRECHECK_PASS\nEXIT_CODE=0\n' > "$OUT/result.txt" || rc=1
fi
if [ "$rc" -eq 0 ]; then echo PRECHECK_PASS
else
  printf 'PRECHECK_FAIL\nEXIT_CODE=1\n' > "$OUT/result.txt" || :
  echo 'PRECHECK_FAIL (do NOT start; do not pull or create a secret to work around)'
fi
echo "EVIDENCE_DIR=$OUT"
exit "$rc"
