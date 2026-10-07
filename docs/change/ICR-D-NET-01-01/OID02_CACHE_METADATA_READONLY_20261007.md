# OID-02 control01 캐시 metadata 읽기 준비 — v2 후보

CODEX_ASSISTED. baseline#14의 동일 digest preloaded 보충안을 준비한다.
Owner 이하영 / 소비·검증 책임 이권욱. 현재 이미지 목록·complete 관측은 반복하지 않고 index→linux/amd64 manifest→config/platform→layer descriptor와 크기의 미수신 관계만 수집한다.
이 문서는 export/import·Runtime·AC 인수 승인이 아니다.

## 버전과 승인 경계

기존 승인 HEAD c2f543a16754926c3dd2ecc35773ba09fe1df3a1 및 독립 APPROVED5438296911은 그 버전에만 결속된다.
기존 OID02_Metadata_ReadOnly_20261007.zip의 SHA256 9564b58842d3af2ff5d479b6725eb809d9e9f2450d4c16c0e30af89c0c190202는 보존한다.
v2는 stderr 증적과 실행 파일 식별, 스트림 상한을 보완한 새 후보이다. 새 HEAD의 독립 검토 전에는 기존 승인을 v2로 확대하지 않는다.
전달 ZIP의 TRANSFER_RECEIPT.json은 정확한 source commit과 repository/archive 경로 매핑을 제공한다. 저장소 manifest는 OID02_READONLY_SOURCE_MANIFEST_20261007.json이며 ZIP 안의 SOURCE_MANIFEST.json과 동일 바이트다.

## 수집 범위와 제한

- 기존 control01(.102), Linux/root, 승인 hostname control01 또는 k8s-control01, 기존 /run/containerd/containerd.sock 및 k8s.io namespace만 사용한다.
- approved top digest는 #14/evidence#132의 kube-controllers df00967cbd6d88e1ff3123e1598895845622e2987928b4ebd9d8ac49aefe00c3 및 CoreDNS e7e6440cfd1e919280958f5b5a6ab2b184d385bba774c12ad2a9e1e4183f90d9로 고정한다.
- 실제 자식 명령은 ip -j -4 address show, ctr --version, ctr content get뿐이다. ip/ctr는 resolve한 절대 경로로 실행하며 경로·파일 크기·SHA256을 기록한다. 이는 바이너리 식별이며 패키지 신뢰성이나 공급 출처의 독립 인증은 아니다.
- registry 접속·fetch/pull·export/import/tag/push·Pod 변경·설치·권한 또는 보안 설정 변경은 없다. socket은 연결 검사를 위해 생성하지 않는다.
- 기본 linux/amd64 후보가 정확히 1개여야 하며 config의 os/architecture와 descriptor digest/size/rootfs 관계를 대조한다. variant v2/v3, nested index, foreign layer 등은 지원한다고 추정하지 않고 INCOMPLETE로 중단한다.
- 명시적 mediaType과 1~512 layers는 고정 두 서비스 이미지에 대한 수집 도구의 지원 범위다. OCI에서 생략 가능한 mediaType이나 zero-layer 문서를 보편적으로 사양 위반이라고 판정하지 않는다. 지원 밖 입력은 INCOMPLETE이며 프로젝트 AC를 확대하지 않는다.
- config 원문의 Env/Labels/History는 메모리 안에서만 처리하고 파일·stdout·stderr에 저장하지 않는다. layer 본체도 읽지 않는다.
- 각 명령의 argv·UTC 시작/종료·경과시간·실제 RC와 stdout/stderr의 수집 바이트 수·SHA256·capture_complete·오류 분류를 기록한다. stderr 원문 및 원문에서 유래한 자유 텍스트는 출력하지 않는다. timeout/상한 초과의 해시는 수집한 부분 바이트의 해시이며 전체 스트림 해시로 사용하지 않는다.
- stdout는 명령별 최대 4MiB, stderr는 최대 64KiB로 메모리 수집을 제한한다. 초과/timeout이면 자신의 읽기 자식 프로세스만 종료하고 INCOMPLETE로 반환한다. ip 명령 5초, 나머지 명령 10초, 수집 예산 45초이며 바이너리 해시·host gate·version을 포함한다. 각 자식 정리에는 별도 최대 2초를 허용하므로 엄격한 전체 벽시계 45초 상한으로 주장하지 않는다.
- descriptor 합은 linux/amd64 참조 content 입력이다. 최종 archive 크기·전체 플랫폼 크기·실제 layer 무결성이나 과거 공급 출처 증적이 아니다.
- 성공해도 plan_ready=false, oid02_ac_accepted=false, layer_bytes_verified=false, original_supply_provenance_verified=false이며 archive_size_bytes=null이다.

## 반입·실행 선행조건

새 후보의 exact source 독립 검토와 기존 Owner 읽기 수집 범위를 확인한 뒤 실행한다. PR 존재나 ZIP 전달만으로 현장 실행 GO가 되지 않는다.
격리 VM에는 기존 승인 파일 전달 경로로 ZIP과 sidecar를 전달한다. 새 계정·키·SSH 경로·네트워크 예외·패키지 설치를 추가하지 않는다. 기존 로그인 홈 아래 신규 디렉터리에만 푼다.
ZIP SHA256과 내부 SHA256SUMS, SOURCE_MANIFEST.json의 tools/tests 파일 해시, TRANSFER_RECEIPT.json의 commit·매핑을 확인한다. Python3/ctr/ip와 기존 sudo 권한이 있어야 한다. hostname·주소·socket·namespace가 다르면 중단한다.

## 실행과 원본 보존

실행 위치: control01 기존 로그인 터미널, 전달 ZIP을 푼 도구 디렉터리. reviewed_commit은 독립 검토된 TRANSFER_RECEIPT의 40자리 SHA로 입력한다.

~~~bash
reviewed_commit='<독립 검토된 40자리 commit>'
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

위 예시는 사용자 터미널의 set -e/-u/pipefail을 변경하지 않는다. source-commit은 선언이므로 실제 도구 파일 해시·전달 receipt·PR 검토가 함께 결속되어야 한다.
collector.stderr는 Python launcher 자체의 원본 오류 파일이며 ip/ctr stderr 원문 저장 파일이 아니다. 예기치 않은 traceback이나 민감값이 있으면 외부 공유 전 검토하고 원본을 임의 편집하지 않는다.

성공: RC0 및 두 이미지의 MANIFEST_METADATA_VERIFIED_REVIEW_REQUIRED. 이는 metadata 관계 검증이며 AC PASS가 아니다.
실패: RC2/INCOMPLETE 또는 launcher 실패. 부분 JSON·RC·stderr를 보존하고 중단한다. 자동 재실행·입력 덮어쓰기·보안 통제 해제를 하지 않는다.
복구: 캐시/운영 설정 변경이 없어 운영 원복 대상은 없다. 생성 증적만 신규 경로에 남긴다. 이미지/content/Pod를 삭제하지 않는다.
인계: metadata.json, collector.stderr, collector.rc, SHA256SUMS, host·계정·KST 시각, source/ZIP SHA를 기존 증적 경로로 전달한다.

## 완료 조건과 이번 검증

Owner는 source 출처 한계·유효 범위·동의·export/검증/용량·STOP/ref 관련 입력으로 #14 완료조건 2~5 초안을 보완한다. 최신 Owner 계획은 10/7 18:00이고 원 Due 10/7은 변경하지 않는다. OID03·renderer BLOCK·AC/종결은 자동 해소되지 않는다.
STATIC: Python AST 2파일 PASS. LOCAL/MOCK: Windows Python3.12.14 39건 OK/RC0. 이 중 4건은 로컬 Python 자식 프로세스의 별도 stderr/nonzero·timeout·stdout/stderr 상한을 확인한 합성 시험이다. Linux/root/containerd/SELinux 현장 수집은 NOT_RUN.
현재 main Actions workflow0이며 CI0을 PASS로 표기하지 않는다. 새 HEAD 독립 리뷰는 PENDING이다.

## 근거

- https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-6030614541
- https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-6031725527
- https://github.com/321Team/infra-kubernetes/pull/68
- https://github.com/containerd/containerd/blob/v2.3.5/cmd/ctr/commands/content/content.go
- https://github.com/opencontainers/image-spec/blob/v1.1.1/image-index.md
- https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md
- https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md
