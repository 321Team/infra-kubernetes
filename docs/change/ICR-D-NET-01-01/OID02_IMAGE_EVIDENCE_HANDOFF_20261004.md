# OID-02 이미지 증빙 인계 — 2026-10-04

범위: Phase 1 / baseline #14 OID-02. Owner 이하영, Lead/대조 이권욱, 기존 Due 2026-10-07 유지.
상태: 절차 준비, 현장 실행 및 이미지 인수 미완료.

## 현재 근거와 작업 분리

- [모드 결정](https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-5977963213): preloaded.
- [worker01 보완](https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-5977996917): Harbor 이름 이미지 관측; 전체 digest와 출처는 후속.
- [PR #54](https://github.com/321Team/infra-kubernetes/pull/54): renderer 입력 승인·병합 완료. Runtime 승인과 구분.
- #14 최신 댓글과 evidence 저장소의 경로명 검색에서는 6노드 전체 digest 원본을 확인하지 못했다. archive 내부 전체 검색 결과는 아니므로 증적 부재를 확정하지 않는다.

먼저 Owner가 이미 수집한 원본의 위치·SHA-256·노드·시각을 인계받는다.
해당 원본에 full repoDigests가 있으면 재수집하지 않는다. 누락 노드/항목만 아래 절차로 보완한다.
축약 digest나 화면 접두부를 64자리 digest로 복원하지 않는다.

## 최소 읽기 전용 보완

실행 위치: 누락된 Kubernetes 노드의 기존 관리자 터미널.
대상은 control01~03 및 worker01~03이며, 실제 hostname/IP를 기존 인벤토리와 대조한다.
기존에 정상 사용하던 crictl과 containerd endpoint를 사용한다. 도구 설치·설정 변경·이미지 pull/import/tag/push는 이 절차에 포함하지 않는다.

아래 예시는 기존 endpoint가 `unix:///run/containerd/containerd.sock`임이 확인된 노드에만 사용한다.
endpoint가 다르거나 hostname/IP가 예상과 다르면 실행하지 않고 확인된 값을 인계한다.
명령은 자식 bash에서 실행되며 현재 터미널의 shell option을 바꾸지 않는다.
sudo 암호는 터미널에만 입력한다.

```bash
bash -c '
umask 077
out=$(mktemp -d "$HOME/oid02-images.XXXXXXXX") || exit 1
printf "EVIDENCE_DIR=%s\n" "$out"
date -Is > "$out/captured-at.txt" || exit 1
hostname > "$out/hostname.txt" || exit 1
ip -j -4 address show > "$out/addresses.json" || exit 1
crictl --version > "$out/crictl-version.txt" 2>&1 || exit 1
sudo -v || exit 1
sudo -n test -S /run/containerd/containerd.sock || exit 1
timeout 30 sudo -n crictl \
  --runtime-endpoint unix:///run/containerd/containerd.sock \
  --image-endpoint unix:///run/containerd/containerd.sock \
  images -o json > "$out/images.json" 2> "$out/images.stderr"
capture_rc=$?
printf "%s\n" "$capture_rc" > "$out/images.rc"
cd "$out" || exit 1
sha256sum captured-at.txt hostname.txt addresses.json crictl-version.txt images.json images.stderr images.rc > SHA256SUMS || exit 1
if [ "$capture_rc" -ne 0 ]; then
  printf "STATUS=CAPTURE_FAILED rc=%s EVIDENCE_DIR=%s\n" "$capture_rc" "$out"
  exit 1
fi
printf "STATUS=CAPTURED_REVIEW_REQUIRED EVIDENCE_DIR=%s\n" "$out"
' || printf '수집이 중단됐습니다. 오류와 생성된 증적을 보존하며 현재 터미널은 유지됩니다.\n'
```

정상 명령 종료는 이미지 검증 PASS가 아니다. 인계 시 SHA256SUMS와 해당 파일들을 함께 전달한다.
images.json의 images 배열·repoTags·repoDigests, identity/시각/rc, stderr를 검토한다.
실패·timeout·빈 결과는 미확인으로 남긴다. 재실행으로 기존 출력 파일을 덮어쓰지 않는다.
서버 설정/이미지 변경이 없어 복구 명령은 필요 없다. 실패 시 증적만 보존하고 중단한다.
인계 자료는 기존 비공개 증적 경로로 전달하며 자격증명·kubeconfig·Secret은 추가하지 않는다.

공식 CLI 참고: [cri-tools crictl](https://github.com/kubernetes-sigs/cri-tools/blob/master/docs/crictl.md).
로컬 CLI 버전의 지원 옵션과 기존 정상 endpoint를 우선 확인한다.

## 대조 및 완료 판단

| 순서 | 필요한 근거 | 판정 경계 |
| --- | --- | --- |
| 1 | 노드별 image reference, full repoDigest, 이미지 ID, 수집 시각·해시 | 이미지 ID와 manifest/index digest는 서로 같은 값이라고 가정하지 않음 |
| 2 | worker01 Harbor 이름과 타 노드 quay 이름의 전체 digest | 동일 digest는 동일 참조 대상의 근거; 최초 반입 경위까지 증명하지 않음 |
| 3 | 원본 archive 위치·SHA-256, 이미지 manifest digest, 반입 기록·검증 방법 | 현재 캐시에서 새로 export해도 과거 반입 출처 증명이 되지는 않음 |
| 4 | kube-controllers/CoreDNS의 실제 scheduling 제약·후보 노드와 이미지 보유 | 모든 이미지를 무조건 6대에 복제하지 않고 실제 후보 노드 기준으로 공백 판단 |
| 5 | 부족 이미지 사전 적재 또는 스케줄 제약/위험 수용 결정 | 별도 검토·실행 기록 필요; 본 읽기 전용 수집에서 적용하지 않음 |

repoDigests가 없거나 manifest/index의 플랫폼별 관계가 불명확하면 추가 확인 대상으로 둔다.
현재 실행 중이라는 사실만으로 장애 후 재스케줄·오프라인 재구성을 PASS 처리하지 않는다.
경로 승인에서는 preloaded 선택으로 CP→Harbor 확대가 발생하지 않는다는 범위를 확인한다.
외부 egress 또는 신규 다운로드가 필요해지면 OID-03 판단에 연결하며 이 절차로 허용하지 않는다.

## 리뷰 후속 검증

PR #54의 비차단 권고를 반영해 PolicyTests에 빈 registry_nets 허용 1건과
6노드 각각의 registry_nets 추가 거부 1건(6 subtests)을 추가했다.
PolicyTests 11/11 통과. Runtime 코드·정책·입력 모드는 변경하지 않았다.
실제 노드 수집과 이미지 목록 대조, 운영 인수는 아직 미실행이다.
