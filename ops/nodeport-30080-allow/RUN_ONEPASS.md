# 잔여 3건 원패스 수집 — 2026-09-24

동작 환경에서 10.1.93.0/24·ansible01에 접속할 수 없다. 이 문서은 격리 VM 붙여넣기용이다.
READ-ONLY. apply/patch/delete, firewalld, HEP/GNP 변경, 테스트 Pod/Service 생성 없음.
#19 승인·edge01 마이그레이션 재개 아님.

## 대상·권한·경로

| 단계 | 호스트 | 계정 | 성공 | 중단 |
|---|---|---|---|---|
| A | k8s-control01 | root 또는 sudo, kubeconfig `/etc/kubernetes/admin.conf` | rc와 파일이 남는다. GNP NotFound는 결과 | apply 계열 명령 |
| B | ansible01 `10.250.10.10` | 기존 SSH 계정 | Worker 22, API `.109:6443` TCP 가부 | 새 SSH 키·firewalld 해제 |
| C | edge01 `10.1.93.50` 또는 A에서 NodePort 존재 확인 후 | 기존 계정 | 리스너+EndPoint 있을 때만 curl | 리스너 없으면 NOT_VERIFIED 종료. 생성 금지 |

저장소 clone이 안 되면 아래 블록만 붙인다.

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
  echo kubeconfig=$KUBECONFIG
} > "$OUT/00-meta.txt"
kubectl get globalnetworkpolicy nodeport-30080-allow -o yaml \
  >"$OUT/01-nodeport-gnp.yaml" 2>"$OUT/01-nodeport-gnp.err"
echo rc=$? > "$OUT/01-nodeport-gnp.rc"
kubectl get globalnetworkpolicy -o wide >"$OUT/02-gnp-list.txt" 2>"$OUT/02-gnp-list.err"
kubectl get hostendpoint -o yaml >"$OUT/03-hep-full.yaml" 2>"$OUT/03-hep-full.err"
kubectl get hostendpoint -o wide >"$OUT/04-hep-list.txt"
kubectl get svc -A -o wide >"$OUT/05-svc-all.txt" 2>"$OUT/05-svc-all.err"
kubectl get svc -A -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
rows=[]
for s in d.get("items",[]):
    ports=s.get("spec",{}).get("ports") or []
    np=[p for p in ports if p.get("nodePort")==30080]
    if np or s.get("spec",{}).get("type")=="NodePort":
        rows.append("%s/%s type=%s nodePorts=%s" % (
            s["metadata"].get("namespace"), s["metadata"].get("name"),
            s.get("spec",{}).get("type"),
            ",".join(str(p.get("nodePort")) for p in ports if p.get("nodePort"))))
open("'