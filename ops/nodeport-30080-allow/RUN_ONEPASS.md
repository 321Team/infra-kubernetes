# 잔여 3건 원패스 — ansible01 점프만

사실: v4 host GNP Ingress SSH는 `10.250.10.10/32`(ansible01) 만 Allow.
CP/Worker 콘솔을 작업 PC에서 직접 열지 않는다. 이 문서는 **ansible01에 붙여 넣는 한 블록**이다.

READ-ONLY. apply/patch/delete, firewalld, HEP/GNP, SELinux 해제 없음.
#19·edge01 마이그레이션 재개 아님.

## 경로

```
작업 PC —(기존 관리 SSH)→ ansible01 10.250.10.10
 ansible01 —SSH:22→ k8s-control01 10.1.93.102   # A kubectl
 ansible01 —SSH:22→ worker .105/.107/.108        # B 회귀
 ansible01 —TCP→ 10.1.93.109:6443               # B API
 ansible01 —TCP→ worker:30080                   # C 음성 후보(소스는 .10, .50 아님)
 ansible01 —SSH→ edge01 10.1.93.50 있을 때만  # C 양성. 없으면 NOT_VERIFIED
```

계정: `SSH_USER`(basic `ansible` 또는 `svc_ansible`). kubeconfig는 control01 `/etc/kubernetes/admin.conf`, sudo.

## ansible01에서 전체

```bash
bash <<'EOF'
set +e
SSH_USER="${SSH_USER:-ansible}"
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

# A: kubectl on control01 via ansible01
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

# B: SSH workers + API LB from ansible01
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

# C-negative: ansible01(.10) -> Worker:30080. 승인 소스 아님.
# 리스너 없으면 정책 차단과 구분 불가.
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

# C-positive: edge01에 SSH가 열릴 때만. 안 열리면 NOT_VERIFIED.
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

`SSH_USER`가 다르면 ù 줄 앞에 `SSH_USER=svc_ansible bash <<'EOF'`.

## 판정

| 파일 | 의미 |
|---|---|
| `01-kubectl.out` | 현장 GNP spec. 후보 YAML과 대조. NotFound는 결과 |
| `02-ssh-workers.txt` | ansible01→Worker:22. rc=0이면 기존 SSH Allow 유지 |
| `03-api-lb.txt` | ansible01→.109:6443 |
| `04-neg-30080-from-ansible01.txt` | 비승인 소스(.10)→30080. 리스너 없으면 NOT_VERIFIED |
| `06-pos-30080-from-edge01.txt` | .50 양성. edge SSH 실패면 NOT_VERIFIED |

control01에서 Worker:22를 테스트하지 말 것. 9/22 기록대로 차단된다.

## 회수

`$OUT` 전체를 작업 PC로 가져온다. 합성 YAML 금지.
