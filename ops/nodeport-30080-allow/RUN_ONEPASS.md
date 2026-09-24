# 잔여 3건 원패스 — ansible01 점프만

사실: v4 host GNP Ingress SSH는 `10.250.10.10/32`(ansible01) 만 Allow.
원격 계정 정본: `svc_ansible` (`infra-ansible` `ansible.cfg` `remote_user`).

READ-ONLY. apply/patch/delete, firewalld, HEP/GNP, SELinux 해제 없음.

2026-09-24 14:35 수집(`ssh_user=ansible`) 판정: [`FIELD_20260924-143557.md`](FIELD_20260924-143557.md).

## ansible01에서 전체

```bash
SSH_USER=svc_ansible bash <<'EOF'
set +e
SSH_USER="${SSH_USER:-svc_ansible}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
CP=10.1.93.102
OUT=$HOME/nodeport-onepass-$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S%z)
mkdir -p "$OUT"
{
  echo collected_at_kst=$(TZ=Asia/Seoul date -Iseconds)
  echo jump=$(hostname)
  echo jump_ips=$(hostname -I 2>/dev/null)
  echo ssh_user=$SSH_USER
} > "$OUT/00-meta.txt"

ssh $SSH_OPTS "${SSH_USER}@${CP}" -- sudo bash -s <<'REMOTE' >"$OUT/01-kubectl.out" 2>"$OUT/01-kubectl.err"
set +e
export KUBECONFIG=/etc/kubernetes/admin.conf
echo HOST=$(hostname)
echo '---GNP---'
kubectl get globalnetworkpolicy nodeport-30080-allow -o yaml
echo GNP_RC=$?
echo '---GNP_LIST---'
kubectl get globalnetworkpolicy -o wide
echo '---HEP_LIST---'
kubectl get hostendpoint -o wide
echo '---HEP_WORKER---'
kubectl get hostendpoint -o yaml
echo '---SVC---'
kubectl get svc -A -o wide
echo '---NODEPORT_GREP---'
kubectl get svc -A -o wide | grep 30080 || true
REMOTE
echo kubectl_ssh_rc=$? > "$OUT/01-kubectl.rc"

: > "$OUT/02-ssh-workers.txt"
for ip in 10.1.93.105 10.1.93.107 10.1.93.108; do
  echo "=== $ip:22 ===" | tee -a "$OUT/02-ssh-workers.txt"
  timeout 8 ssh $SSH_OPTS "${SSH_USER}@${ip}" -- true >>"$OUT/02-ssh-workers.txt" 2>&1
  echo rc=$? ip=$ip | tee -a "$OUT/02-ssh-workers.txt"
done
{
  echo "=== 10.1.93.109:6443 from $(hostname) ==="
  if command -v nc >/dev/null; then
    timeout 5 nc -zv -w 3 10.1.93.109 6443
    echo nc_rc=$?
  elif command -v curl >/dev/null; then
    curl -sk --connect-timeout 5 -o /dev/null -w 'http_code=%{http_code}\n' https://10.1.93.109:6443/readyz
    echo curl_rc=$?
  else
    echo NOTOOL
  fi
} >"$OUT/03-api-lb.txt" 2>&1

: > "$OUT/04-neg-30080-from-ansible01.txt"
for ip in 10.1.93.105 10.1.93.107 10.1.93.108; do
  echo "=== $ip:30080 from ansible01 ===" | tee -a "$OUT/04-neg-30080-from-ansible01.txt"
  if command -v curl >/dev/null; then
    curl -sS -o /dev/null -w 'http_code=%{http_code}\n' --connect-timeout 3 "http://$ip:30080/readyz" \
      >>"$OUT/04-neg-30080-from-ansible01.txt" 2>&1
    echo curl_rc=$? ip=$ip | tee -a "$OUT/04-neg-30080-from-ansible01.txt"
  else
    echo NOTOOL >> "$OUT/04-neg-30080-from-ansible01.txt"
  fi
done

{
  echo "=== ssh edge01 10.1.93.50 ==="
  timeout 8 ssh $SSH_OPTS "${SSH_USER}@10.1.93.50" -- 'hostname; hostname -I'
  echo edge_ssh_rc=$?
} >"$OUT/05-edge01-ssh.txt" 2>&1
if grep -q 'edge_ssh_rc=0' "$OUT/05-edge01-ssh.txt"; then
  : > "$OUT/06-pos-30080-from-edge01.txt"
  for ip in 10.1.93.105 10.1.93.107 10.1.93.108; do
    echo "=== $ip:30080 from edge01 ===" | tee -a "$OUT/06-pos-30080-from-edge01.txt"
    timeout 20 ssh $SSH_OPTS "${SSH_USER}@10.1.93.50" -- \
      "curl -sS -o /dev/null -w 'http_code=%{http_code}\n' --connect-timeout 3 http://$ip:30080/readyz; echo curl_rc=\$? ip=$ip" \
      >>"$OUT/06-pos-30080-from-edge01.txt" 2>&1
  done
else
  echo 'NOT_VERIFIED: ansible01에서 edge01 SSH 실패. .50 양성은 이 경로로 실행 불가.' > "$OUT/06-pos-30080-from-edge01.txt"
fi

( cd "$OUT" && sha256sum * > SHA256SUMS )
echo OUT=$OUT
ls -l "$OUT"
EOF
```

먼저 `ssh -o BatchMode=yes svc_ansible@10.1.93.102 -- hostname`이 rc=0이어야 한다.
