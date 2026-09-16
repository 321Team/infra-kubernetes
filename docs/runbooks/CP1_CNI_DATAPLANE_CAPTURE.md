# CP1 CNI dataplane의 누락 근거 수집

대상은 실제 `k8s-control01`입니다. `cp1-cni-lab`의 합성 namespace 시험 결과로 대체하지 않습니다.
이 수집은 호스트/API 읽기와 로컬 결과 파일 작성만 수행합니다. policy 설치, reload,
Pod 생성/삭제, 재시험, systemd 변경은 없습니다. root 접근은 기존 콘솔/관리 절차를 사용합니다.

## 필요한 이유

2026-09-15 실제 CP1 증적에서 `iptables-nft-1.8.11-16.el10.x86_64`와
Calico의 `xt match "set"` 규칙을 확인했습니다. nft 출력은 해당 확장의 완전한 내용을
표현하지 못하며, 당시 묶음에는 kernel `ipset save`가 없습니다.
이미지 digest와 iptables 규칙은 재사용합니다. 누락된 kernel ipset과 같은 수집 구간의
nft JSON·iptables-save·Calico/API 정보를 함께 확보해야 동적 Pod 교체 판정을 구현할 수 있습니다.

## 실행

전달 폴더에는 `scripts/collect-cp1-cni-dataplane.py`, 고정 helper
`scripts/node-firewall.py`, `CAPTURE_SHA256SUMS`가 있습니다. 아래는 전달 폴더의 루트에서 실행합니다.
Windows/공유 폴더를 사용하는 경우 폴더를 VM 홈의 새 경로로 먼저 복사합니다.

```bash
(
  set -euo pipefail
  umask 077
  sha256sum --check CAPTURE_SHA256SUMS
  sudo -v
  icr_out=$(mktemp -d "$HOME/FDS_CP1_CNI_Dataplane.XXXXXX")
  icr_rc=0
  sudo /usr/bin/python3 -I -B scripts/collect-cp1-cni-dataplane.py \
    --output "$icr_out/CAPTURE.json" || icr_rc=$?
  printf '%s\n' "$icr_rc" > "$icr_out/EXIT_CODE.txt"
  sudo test -s "$icr_out/CAPTURE.json"
  sudo tar -czf - -C "$icr_out" . > "$icr_out.tar.gz"
  (
    cd "$(dirname "$icr_out")"
    sha256sum "$(basename "$icr_out").tar.gz" \
      > "$(basename "$icr_out").tar.gz.sha256"
  )
  printf 'EXIT_CODE=%s\nARCHIVE=%s.tar.gz\nSIDECAR=%s.tar.gz.sha256\n' \
    "$icr_rc" "$icr_out" "$icr_out"
)
```

생성된 `.tar.gz`와 `.sha256` 두 파일을 기존 증적 전달 경로로 제출합니다.

조회 제한은 전체 240초, 개별 조회 최대 10초입니다. 한 조회가 실패해도 나머지 자료를 보존합니다.
`READ_ONLY_CAPTURE_INCOMPLETE` 또는 경고가 있어도 반복 실행하지 않고 생성된 결과부터 전달합니다.
`complete=true`는 조회 종료·누락 여부만 뜻합니다. 전후 상태 일치, 원자적 snapshot, 정상 Pod 교체,
통신 성공, 운영 승인 또는 실제 방화벽 의미 검증을 뜻하지 않습니다.

원본에는 내부 네트워크와 Pod 메타데이터가 있습니다. GitHub 댓글에 원본 전체를 게시하지 않고
기존 증적 전달 경로로 제출합니다. 제품 UUID와 kubeconfig/Secret 원문을 조회하는 명령은 없습니다.
