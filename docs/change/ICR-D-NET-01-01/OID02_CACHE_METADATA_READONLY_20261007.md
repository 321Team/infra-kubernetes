# OID-02 control01 캐시 metadata 읽기 보완

CODEX_ASSISTED. baseline#14의 동일 digest preloaded 보충안 준비를 지원한다.
기존 이미지 목록/content complete 관측을 반복하지 않고 index→linux/amd64 manifest→config/platform→layer descriptor와 크기의 미수신 관계만 수집한다.
Owner 이하영 / 추적·검토 이권욱. 이 자료는 새 export/import·배포·수용 승인이 아니다.

## 범위와 해석

- 기존 control01(.102), 기존 containerd socket /run/containerd/containerd.sock, k8s.io namespace만 사용한다.
- Linux/기존 root 권한, control01 hostname 및 .102 현재 주소·socket을 대조한다. 실패 시 명령을 확대하지 않는다.
- approved top digest는 #14/evidence#132의 kube-controllers df00967… 및 CoreDNS e7e644… 전체 SHA로 코드에 고정했다.
- ctr --version과 ctr content get만 호출한다. registry 연결·fetch/pull·export/import/tag/push·Pod 변경·서비스/보안 설정 변경은 없다.
- index는 기본 linux/amd64 후보가 정확히1개여야 한다. config 자체의 os/architecture와 digest/size도 대조한다. variant v2/v3 등 추가 CPU 조건이나 nested index/foreign layer는 지원한다고 추정하지 않고 INCOMPLETE로 중단한다.
- config는 메모리에서 digest/플랫폼/rootfs 관계를 확인하고 Env/Labels/History 원문을 파일·stdout·stderr에 남기지 않는다. CLI stderr도 폐기하며 오류는 분류 코드로만 출력한다.
- layer 본체는 읽지 않는다. descriptor 크기 합은 linux/amd64 참조 content의 추정 입력이며 실제 archive·전체 플랫폼 크기·공간 여유가 아니다.
- 원래 ref의 현재 연결, cache의 과거 반입 출처·원본 archive, 대상8조합의 실제 공급·잔여 allocatable/실효 Pod constraint 및 renderer 증적 path/hash는 별도 입력이다.
- 성공 출력도 plan_ready=false / oid02_ac_accepted=false / layer_bytes_verified=false / original_supply_provenance_verified=false다.

## 선행조건·전달

먼저 동일 항목을 담은 기존 원본이 있는지 확인한다. 충분한 기존 자료가 있으면 재수집하지 않고 인계한다.
새 도구는 exact source의 독립 코드 검토 후 기존 Owner의 읽기 수집 범위에서 사용한다. 이번 준비 PR 자체가 현장 실행 GO는 아니다.

격리 VM에는 배포 ZIP을 기존 허용된 파일 전달 경로(기존 WinSCP/Ansible 전달 등)로 보내고 양쪽 ZIP SHA256을 대조한다.
새 계정/키·SSH 경로·네트워크 예외·도구 설치를 추가하지 않는다. 붙여넣기 대신 ZIP과 이 실행 자료를 사용한다.
control01의 기존 관리자 계정 홈 아래 새 전용 폴더에서 해제하고 SOURCE_MANIFEST.json의 source 파일 hash를 대조한다.
Python3/ctr/ip 및 기존 sudo 권한이 이미 있어야 한다. 다른 endpoint/계정/호스트라면 중단한다.

## 독립 검토 뒤 실행 예시

실행 위치: control01 기존 계정 터미널, 새 전달 폴더.
독립 검토된 full commit은 배포 TRANSFER_RECEIPT.json / 실제 PR HEAD와 대조한다.
아래 reviewed_commit 자리에는 그40자리 SHA를 사용한다. old main이나 임의 값으로 채우지 않는다.

~~~bash
reviewed_commit='<독립 검토한40자리commit>'
bash -c '
umask 077
out=$(mktemp -d "$HOME/oid02-metadata.XXXXXXXX") || exit 1
printf "EVIDENCE_DIR=%s\n" "$out"
sudo -v || exit 1
sudo -n python3 tools/oid02-cache-metadata-readonly.py \
  --source-commit "$1" > "$out/metadata.json" 2> "$out/collector.stderr"
rc=$?
printf "%s\n" "$rc" > "$out/collector.rc"
cd "$out" || exit 1
sha256sum metadata.json collector.stderr collector.rc > SHA256SUMS || exit 1
printf "COLLECTOR_RC=%s EVIDENCE_DIR=%s\n" "$rc" "$out"
exit "$rc"
' _ "$reviewed_commit"
~~~

이 예시는 현재 터미널에 set -e/-u/pipefail을 적용하지 않는다.
source-commit은 선언값이고 자체 Git 승인 검증이 아니다. 실제 source 전달 hash·PR HEAD/리뷰가 함께 있어야 한다.
명령별 content digest·크기·RC/timeout·시각, 실제 host/주소/uid·ctr 버전·collector hash가 JSON에 남는다.
content 읽기는 명령별10초, 총45초 예산이다. IP조회5초는 별도이며 stdout는 metadata 문서만 허용한다.

## 정상·실패·복구·인계

정상: RC0, 두 이미지의 MANIFEST_METADATA_VERIFIED_REVIEW_REQUIRED, 승인 top→실제 child/config SHA와 플랫폼/descriptor 크기가 연결된다.
이는 metadata 수집 성공이며 이미지 전체 content·원본 출처·공급·AC PASS가 아니다.
실패: RC2/INCOMPLETE, host/socket/platform/JSON/size/hash/시간 오류. 부분 결과와 RC를 보존하고 중단한다. 재실행으로 원본을 덮어쓰지 않는다.
복구: 캐시/서비스 변경이 없으므로 운영 원복 대상은 없다. 기존 이미지/content·Pod를 삭제하지 않는다.

인계: metadata.json·collector.stderr·collector.rc·SHA256SUMS, 실행 계정·실제 시작/종료 KST, 검토된 source/배포 SHA를 기존 비공개 증적 경로에 연결한다.
Owner가 source 출처 한계·실효 스케줄 조건·export/전달/대상별 검증/STOP/ref 단위 복구안과 결합하여 기존 #14 완료조건2~5에 제출한다.
원Due10/7와 Owner 초안 계획10/8 12:00을 구분한다. OID03·renderer BLOCK·AC·일정은 자동 해제하지 않는다.

## 이번 검증

Windows Python3.12.14 MOCK26/26 OK·RC0, AST2파일 PASS. synthetic fixture이며 Linux/root/containerd/SELinux 현장 수집은 NOT_RUN.
Kubernetes 저장소 현재 main에 Actions workflow는 없고 신규 CI0건을 PASS로 표시하지 않는다.
SOURCE_MANIFEST.json은 정확한 소스와 실제 로컬 검증 범위를 기록하며 현장 증적으로 쓰지 않는다.

## 원본 기준

- https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-6030614541
- https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-6031201210
- https://github.com/containerd/containerd/blob/v2.3.5/cmd/ctr/commands/content/content.go
- https://github.com/opencontainers/image-spec/blob/v1.1.1/image-index.md
- https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md
- https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md
