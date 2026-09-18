# 승인 리뷰와 실행 위임 댓글을 함께 사용하는 현장 진입점

2026-09-18 09:48:11 KST에 이하영님의 정확한 소스 실행 위임 댓글이 등록됐다.

- 소스 승인: https://github.com/321Team/infra-kubernetes/pull/14#pullrequestreview-5242208554
- 실행 위임: https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723302935
- 승인된 Runtime HEAD: `cfae027fad62a36ee228da3217011fa0787c7486`
- 승인된 CP1 bundle: `d229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41`

기존 편의 실행기는 위임 문구를 리뷰 본문에서만 읽는다. 실제 소스 승인 리뷰 본문은 비어 있고, 위임은 별도 실제 댓글에 있다. 승인된 하위 CLI는 실제 issuecomment URL을 지원한다. 이 별도 진입점은 두 GitHub 기록을 각각 검증하고 기존 CLI와 실행 흐름에 연결한다. 리뷰 본문을 만들거나 승인 기록을 수정하지 않는다.

`RUN_CP_SESSION_APPROVED.py`는 별도 운영 진입점 `fds.cp-approved-comment-entry.v1`이다. 이 새 코드가 위 HEAD의 독립 소스 리뷰를 받았다고 표시하지 않는다. 승인된 Runtime 패키지 30개 파일, 소스, 정책, 타이머, 시험 코드는 바이트 그대로다. `ENTRY_PROVENANCE.json`에 새 진입점의 해시와 승인 소스의 해시를 따로 기록한다.

## 현장 실행 한 번

새 `.tar.gz`와 `.sha256`을 ansible01의 `/home/server01_ansible`에 놓고 해당 계정에서 실행한다.

```bash
cd /home/server01_ansible &&
sha256sum --check FDS_CP_Approved_Entry_v1.tar.gz.sha256 &&
if [ ! -e FDS_CP_Approved_Entry_v1 ]; then
    tar -xzf FDS_CP_Approved_Entry_v1.tar.gz --no-same-owner --no-same-permissions
fi &&
python3 -I -B FDS_CP_Approved_Entry_v1/RUN_CP_SESSION_APPROVED.py --execute
```

기존 `/home/server01_ansible/FDS_CP_Session_cfae027_v4.0` 폴더가 있으면 자동으로 그 패키지와 실행 이력을 사용한다. 없으면 새 압축에 포함한 동일한 승인 패키지를 사용한다. 기존 실행 시도 파일을 지우거나 덮어쓰지 않는다.

실행기는 GitHub의 실제 GWONUK 계정, 현재 PR HEAD, 최신 이하영님 소스 승인, 정확한 위임 댓글을 확인한다. 당일 작업·인계·복구를 마칠 KST 종료시각과 실제 복구 대응 여유(초)를 입력하고, 기존 CP1·CP2·CP3 복구 조건 및 해당 시각까지 운영자 대응 가능함을 한 번 확인한다. 이전의 `04:00/06:00`을 재사용하지 말고 현재 시각보다 뒤인 실제 유인 종료시각을 입력한다. 고정 90분 작업창은 사용하지 않는다.

이후 기존 흐름대로 실제 이전 Job 복구 상태 → CP1 inspect/apply → 5-flow·회귀 → 세션 지속 결정 → CP2 → CP3 → 통합 증적을 처리한다. 별도 리뷰 본문 재작성이나 노드별 재승인 요청은 하지 않는다. GitHub 작업창 결정 댓글은 실제 운영자 확인 후 한 번 등록된다.

현재 이전 Job의 복구 완료는 업로드 시각만으로 판단하지 않는다. 현장 명령 안에서 실제 root 기록과 현재 상태를 대조한다. 변경 전 실패는 원인을 해결한 뒤 재실행할 수 있다. 변경을 시도했으면 생성된 증적부터 확인한다.

## 결과

정상 출력은 `STATUS=CP_FIREWALL_HANDOFF_REVIEW_PENDING`이다. 출력된 `EVIDENCE_ARCHIVE`와 `SIDECAR` 한 쌍을 제출한다. 중단 시 `.partial.tar.gz`와 `.sha256`을 제출한다.

실행 성공과 독립 결과 인수는 별개다. 결과는 `cp1_accepted=false`, `independent_acceptance=PENDING`을 유지한다. Kubernetes 담당자에게 실제 노드 결과와 유효시각을 인계한다. Join/reset·영구화·교차 노드 연결 시험을 이 진입점이 수행했다고 표시하지 않는다.

무인자 실행은 네트워크 없는 PLAN_ONLY, `--check`는 실제 GitHub 승인 읽기 전용 확인이다. 이 패키지를 준비한 환경에서는 VM 적용을 수행하지 않았다.
