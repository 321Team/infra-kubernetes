#!/bin/bash
# 읽기 전용. apply/patch/delete 없음.
# 대상: k8s-control01, kubeconfig=/etc/kubernetes/admin.conf
# 성공: 명령 rc=0, 출력을 증적 디렉터리에 저장.
# 중단: NotFound는 실패가 아니라 결과. 클러스터 변경 명령이 있으면 중단.
set -euo pipefail
KUBECONFIG="${KUBECONFIG:-/etc/kubernetes/admin.conf}"
OUT="${1:-./field-collect-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
export KUBECONFIG

{
  echo "# collected_at=$(date -Iseconds)"
  echo "# host=$(hostname)"
} | tee "$OUT/00-meta.txt"

kubectl get globalnetworkpolicy nodeport-30080-allow -o yaml \
  > "$OUT/01-nodeport-gnp.yaml" 2>"$OUT/01-nodeport-gnp.err" || true

kubectl get globalnetworkpolicy -o wide \
  > "$OUT/02-gnp-list.txt"

kubectl get hostendpoint -l 'fds.example/role==worker' -o yaml \
  > "$OUT/03-worker-hep.yaml"

kubectl get hostendpoint -o wide \
  > "$OUT/04-hep-list.txt"

( cd "$OUT" && sha256sum * > SHA256SUMS )
echo "wrote $OUT"
