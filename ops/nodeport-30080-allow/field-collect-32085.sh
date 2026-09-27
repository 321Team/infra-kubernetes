#!/bin/bash
# P1-SEC-01 / infra-kubernetes#45 read-only NodePort drift precheck.
# Run on ansible01. No kubectl apply/patch/delete and no firewall/policy changes.
set -euo pipefail
umask 077

SSH_USER="${SSH_USER:-svc_ansible}"
CP="${CP:-10.1.93.102}"
EDGE="${EDGE:-10.1.93.50}"
WORKERS=(10.1.93.105 10.1.93.107 10.1.93.108)
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)
STAMP="$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S%z)"
OUT="${OUT:-$HOME/nodeport-32085-precheck-$STAMP}"
mkdir -p "$OUT"

{
  echo "COLLECTED_AT_KST=$(TZ=Asia/Seoul date -Iseconds)"
  echo "HOST=$(hostname -s)"
  echo "SSH_USER=$SSH_USER"
  echo "CP=$CP"
  echo "EDGE=$EDGE"
} > "$OUT/00-meta.txt"

remote_kubectl() {
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$CP" --     sudo -n kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=15s "$@"
}

set +e
remote_kubectl -n fds-app get svc transaction-api -o yaml   >"$OUT/01-svc-transaction-api.yaml" 2>"$OUT/01-svc-transaction-api.err"
echo $? >"$OUT/01-svc-transaction-api.rc"

remote_kubectl -n fds-app get svc transaction-api-nodeport -o yaml   >"$OUT/02-svc-transaction-api-nodeport.yaml" 2>"$OUT/02-svc-transaction-api-nodeport.err"
echo $? >"$OUT/02-svc-transaction-api-nodeport.rc"

remote_kubectl -n fds-app get endpointslice   -l kubernetes.io/service-name=transaction-api -o yaml   >"$OUT/03-endpointslice-transaction-api.yaml" 2>"$OUT/03-endpointslice-transaction-api.err"
echo $? >"$OUT/03-endpointslice-transaction-api.rc"

remote_kubectl -n fds-app get endpointslice   -l kubernetes.io/service-name=transaction-api-nodeport -o yaml   >"$OUT/04-endpointslice-transaction-api-nodeport.yaml" 2>"$OUT/04-endpointslice-transaction-api-nodeport.err"
echo $? >"$OUT/04-endpointslice-transaction-api-nodeport.rc"

remote_kubectl get globalnetworkpolicy.crd.projectcalico.org nodeport-30080-allow -o yaml   >"$OUT/05-gnp-nodeport-30080-allow.yaml" 2>"$OUT/05-gnp-nodeport-30080-allow.err"
echo $? >"$OUT/05-gnp-nodeport-30080-allow.rc"

remote_kubectl -n fds-app get pods -l app=transaction-api -o wide   >"$OUT/06-transaction-api-pods.txt" 2>"$OUT/06-transaction-api-pods.err"
echo $? >"$OUT/06-transaction-api-pods.rc"

remote_kubectl get svc -A -o wide   >"$OUT/07-all-services.txt" 2>"$OUT/07-all-services.err"
echo $? >"$OUT/07-all-services.rc"
set -e

probe_local() {
  local ip="$1" port="$2"
  echo "=== $ip:$port from $(hostname -s) ==="
  if command -v nc >/dev/null 2>&1; then
    timeout 5 nc -zv -w 3 "$ip" "$port"
    echo "RC=$?"
  else
    timeout 5 bash -c "</dev/tcp/$ip/$port"
    echo "RC=$?"
  fi
}

set +e
: >"$OUT/08-ansible01-probes.txt"
for ip in "${WORKERS[@]}"; do
  for port in 30080 32085; do
    probe_local "$ip" "$port" >>"$OUT/08-ansible01-probes.txt" 2>&1
  done
done
set -e

set +e
ssh "${SSH_OPTS[@]}" "$SSH_USER@$EDGE" -- 'hostname -s; hostname -I'   >"$OUT/09-edge01-ssh.txt" 2>&1
EDGE_RC=$?
echo "$EDGE_RC" >"$OUT/09-edge01-ssh.rc"
set -e

if [ "$EDGE_RC" -eq 0 ]; then
  : >"$OUT/10-edge01-probes.txt"
  for ip in "${WORKERS[@]}"; do
    for port in 30080 32085; do
      set +e
      ssh "${SSH_OPTS[@]}" "$SSH_USER@$EDGE" -- bash -s -- "$ip" "$port"         >>"$OUT/10-edge01-probes.txt" 2>&1 <<'REMOTE'
ip="$1"
port="$2"
echo "=== $ip:$port from $(hostname -s) ==="
if command -v nc >/dev/null 2>&1; then
  timeout 5 nc -zv -w 3 "$ip" "$port"
  echo "RC=$?"
else
  timeout 5 bash -c "</dev/tcp/$ip/$port"
  echo "RC=$?"
fi
REMOTE
      set -e
    done
  done
else
  echo "NOT_VERIFIED: edge01 SSH unavailable from ansible01; no policy was changed."     >"$OUT/10-edge01-probes.txt"
fi

find "$OUT" -type f ! -name SHA256SUMS -print0   | sort -z   | xargs -0 sha256sum >"$OUT/SHA256SUMS"
sha256sum --check "$OUT/SHA256SUMS"

ARCHIVE="$OUT.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" >"$ARCHIVE.sha256"

echo "OUT=$OUT"
echo "ARCHIVE=$ARCHIVE"
echo "SIDECAR=$ARCHIVE.sha256"
