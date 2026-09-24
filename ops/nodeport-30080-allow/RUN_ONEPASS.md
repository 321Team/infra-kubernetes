# 잔여 3건 원패스 수집 — 2026-09-24

동작 환경에서 10.1.93.0/24·ansible01에 접속할 수 없다. 격리 VM 붙여넣기용.
READ-ONLY. apply/patch/delete, firewalld, HEP/GNP 변경, 테스트 Pod/Service 생성 없음.
#19 승인·edge01 마이그레이션 재개 아님.

## 대상

| 단계 | 호스트 | 계정 | 성공 | 중단 |
|---|---|---|---|---|
| A | k8s-control01 | root/sudo, kubeconfig `/etc/kubernetes/admin.conf` | 파일+검증합. GNP NotFound는 결과 | apply 계열 |
| B | ansible01 `10.250.10.10` | 기존 SSH | Worker:22, `.109:6443` TCP 가부 | 새 키·firewalld 해제 |
| C | edge01 `10.1.93.50` | 기존 계정 | A에 NodePort 30080 리스너가 있을 때만 curl | 없으면 NOT_VERIFIED. 생성 금지 |

전달: 켘솔 붙여넣기. clone 불필.

## A — control01

```bash
sudo bash <<'EOF'
set +e
export KUBECONFIG=/etc/kubernetes/admin.conf
OUT=/root/nodeport-onepass-A-$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S%z)
mkdir -p "$OUT"
{
  echo collected_at_kst=$(TZ=Asia/Seoul date -Iseconds)
  echo host=$(hostname)
} > "$OUT/00-meta.txt"
kubectl get globalnetworkpolicy nodeport-30080-allow -o yaml \
  >"$OUT/01-nodeport-gnp.yaml" 2>"$OUT/01-nodeport-gnp.err"
echo rc=$? > "$OUT/01-nodeport-gnp.rc"
kubectl get globalnetworkpolicy -o wide >"$OUT/02-gnp-list.txt" 2>"$OUT/02-gnp-list.err"
kubectl get hostendpoint -o yaml >"$OUT/03-hep-full.yaml" 2>"$OUT/03-hep-full.err"
kubectl get hostendpoint -o wide >"$OUT/04-hep-list.txt"
kubectl get svc -A -o wide >"$OUT/05-svc-all.txt" 2>"$OUT/05-svc-all.err"
kubectl get svc -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name} type={.spec.type} ports={.spec.ports[*].port} nodePorts={.spec.ports[*].nodePort}{"\n"}{end}' \
  >"$OUT/06-svc-ports.txt" 2>"$OUT/06-svc-ports.err"
grep -n '30080' "$OUT/05-svc-all.txt" "$OUT/06-svc-ports.txt" >"$OUT/07-nodeport-30080-grep.txt" || true
( cd "$OUT" && sha256sum * > SHA256SUMS )
echo OUT=$OUT
ls -l "$OUT"
EOF
```

판정: `01-nodeport-gnp.yaml` spec을 후보 YAML과 대조. `07-nodeport-30080-grep.txt`가 비어 있으면 C는 NOT_VERIFIED.

## B — ansible01

```bash
bash <<'EOF'
set +e
OUT=$HOME/nodeport-onepass-B-$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S%z)
mkdir -p "$OUT"
{
  echo collected_at_kst=$(TZ=Asia/Seoul date -Iseconds)
  echo host=$(hostname)
  echo src=$(hostname -I 2>/dev/null)
} > "$OUT/00-meta.txt"
for ip in 10.1.93.105 10.1.93.107 10.1.93.108; do
  echo "=== ssh $ip:22 ===" | tee -a "$OUT/01-ssh-workers.txt"
  timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
    "$ip" -- true >>"$OUT/01-ssh-workers.txt" 2>&1
  echo rc=$? ip=$ip | tee -a "$OUT/01-ssh-workers.txt"
done
echo "=== tcp 10.1.93.109:6443 ===" > "$OUT/02-api-lb.txt"
if command -v nc >/dev/null; then
  timeout 5 nc -zv -w 3 10.1.93.109 6443 >>"$OUT/02-api-lb.txt" 2>&1
  echo nc_rc=$? >> "$OUT/02-api-lb.txt"
elif command -v curl >/dev/null; then
  curl -sk --connect-timeout 5 -o /dev/null -w 'http_code=%{http_code} err=%{errormsg}\n' https://10.1.93.109:6443/readyz \
    >>"$OUT/02-api-lb.txt" 2>&1
  echo curl_rc=$? >> "$OUT/02-api-lb.txt"
else
  echo NOTOOL >> "$OUT/02-api-lb.txt"
fi
echo "=== udp 4789 worker01 (판정 아님, 도달만) ===" > "$OUT/03-vxlan-note.txt"
echo 'VXLAN 성공/실패는 이 프로브로 단정하지 않는다. SSH/6443만 기록.' >> "$OUT/03-vxlan-note.txt"
( cd "$OUT" && sha256sum * > SHA256SUMS )
echo OUT=$OUT
ls -l "$OUT"
EOF
```

판정: Worker 3대 SSH rc=0 그리고 `.109:6443`가 도달되면 관리 경로 회귀 관측. 22 거부는 이 GNP(30080 전용)와 분리.

## C — edge01, A에 30080 리스너가 있을 때만

A `07-nodeport-30080-grep.txt`가 비어 있으면 실행하지 말 것. NOT_VERIFIED.

```bash
bash <<'EOF'
set +e
OUT=$HOME/nodeport-onepass-C-$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S%z)
mkdir -p "$OUT"
{
  echo collected_at_kst=$(TZ=Asia/Seoul date -Iseconds)
  echo host=$(hostname)
  echo src=$(hostname -I 2>/dev/null)
} > "$OUT/00-meta.txt"
if ! command -v curl >/dev/null; then
  echo NOTOOL > "$OUT/RESULT.txt"
  exit 3
fi
: > "$OUT/01-allow-workers.txt"
for ip in 10.1.93.105 10.1.93.107 10.1.93.108; do
  echo "=== $ip:30080 ===" | tee -a "$OUT/01-allow-workers.txt"
  curl -sS -o /dev/null -w 'http_code=%{http_code} rc_placeholder\n' --connect-timeout 3 "http://$ip:30080/readyz" \
    >>"$OUT/01-allow-workers.txt" 2>&1
  echo curl_rc=$? ip=$ip | tee -a "$OUT/01-allow-workers.txt"
done
echo 'negative: this host must be 10.1.93.50. other source is a separate run.' > "$OUT/02-source-note.txt"
( cd "$OUT" && sha256sum * > SHA256SUMS )
echo OUT=$OUT
ls -l "$OUT"
EOF
```

음성(비승인 Source)는 ansible01 또는 control01에서 같은 curl을 한 번 더 돌린다. 연결 실패만으로 PASS라 하지 말 것. 리스너가 없으면 정책 차단과 구분 불가(NOT_VERIFIED).

## 회수

원본 폴더 3개를 작업 PC로 옮긴다. 이 PR `REVIEW_PACK.md` 셑션 6에 SHA와 함께 붙인다.
합성 결과 파일을 만들지 말 것.
