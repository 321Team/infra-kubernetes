# 기존 CP1 세션을 유지하고 CP2·CP3 재개

PR #15의 수정 HEAD `06286f31735a80982e50e5f94fd0e1f4a027eb8d`는 이하영님이 2026-09-18 10:22:52 KST에 APPROVED했다.

승인: https://github.com/321Team/infra-kubernetes/pull/15#pullrequestreview-5243182569

이 패키지는 해당 컨트롤러와 기존 CP1 소스·node helper·실행 흐름을 사용한다. 운영 진입점 `RUN_CP_RESUME.py`는 별도 버전/해시이며, 그 새 진입점 코드 자체가 위 수정 소스 리뷰에 포함됐다고 표시하지 않는다. 실행 시 기존 PR14 소스/실행 위임, PR15 최신 수정 소스 승인, 실제 기존 운영자 결정 댓글을 읽어 대조한다.

## 사용

두 파일을 ansible01의 `/home/server01_ansible`에 놓고 `server01_ansible` 계정에서 실행한다. 앞서 생성한 `ICR_CP_Session_20260918T010834Z_72393be0.partial.tar.gz`도 같은 경로에 그대로 둔다. 해당 파일은 이미 제출한 성공·중단 증적이며 새로 만들 필요가 없다.

```bash
cd /home/server01_ansible &&
sha256sum --check FDS_CP_Resume_06286f3_v1.0.tar.gz.sha256 &&
if [ ! -e FDS_CP_Resume_06286f3_v1.0 ]; then
    tar -xzf FDS_CP_Resume_06286f3_v1.0.tar.gz --no-same-owner --no-same-permissions
fi &&
python3 -I -B FDS_CP_Resume_06286f3_v1.0/tools/RUN_CP_RESUME.py --execute
```

시간이나 승인 문구를 다시 입력하지 않는다. 기존 실제 운영자 결정 #5723463564의 당일 종료 15:30 KST, 복구 여유900초, CP1 회수15:15 KST를 유지한다. 유효기간이 지났거나 실제 상태가 바뀌면 자동 연장하지 않고 종료한다.

## 자동 처리

1. 승인된 패키지와 제출 archive SHA256 검증. CP2가 변경 전에 멈췄다는 하위 RESULT 및 기존 5-flow·회귀·handoff·세션 결정의 일치 확인.
2. GitHub 실제 소스/실행 위임/수정 리뷰/기존 운영자 작업창 확인.
3. 같은 CP1 Job `c564ae97530dfe80`의 현재 frozen status와 타이머·전체 상태·원 결정 대조.
4. CP2 fresh inspect/authorization/apply/전후 검증. CP2 실제 receipt를 연결해 CP3 처리.
5. 이전 증적과 새 노드 결과를 통합한 archive, 체크섬 및 K8S_HANDOFF.json 생성.

CP1 apply, 5-flow/회귀 시험, 세션 결정 생성, 타이머 변경은 재실행하지 않는다. 원 SESSION_DECISION 바이트와 기존 CP1 grant/operator URL을 유지하며, 새 컨트롤러의 승인 근거는 별도로 기록한다. CP2·CP3의 source_commit은 실제 승인된 수정 HEAD와 일치한다.

기존 전체 실행 패키지의 EXECUTION_ATTEMPT를 삭제하지 않는다. 재개 진입점도 Job별 잠금과 CP2 적용 명령 시도 기록을 별도 유지하므로 새 폴더에 다시 풀어도 중복 실행하지 않는다. CP2 실행이 실패하면 CP3를 수행하지 않는다.

## 제출 결과

성공 출력: `STATUS=CP_FIREWALL_HANDOFF_REVIEW_PENDING`. 출력된 `EVIDENCE_ARCHIVE`와 `SIDECAR` 한 쌍을 제출한다. 중단 시 출력된 `.partial.tar.gz`와 `.sha256`을 제출한다.

CP1 독립 인수는 `false`, 독립 결과 검토는 `PENDING`을 유지한다. Join/reset/영구화는 실행하지 않는다. CP2·CP3 실제 Runtime 성공 및 Kubernetes Join readiness는 현장 결과가 나오기 전까지 확정하지 않는다.

## 검증과 출처

- 수정 컨트롤러: 기존208+누락요청 회귀8 = 216/216 STATIC/MOCK PASS.
- 재개 진입점: 별도28/28 PASS. 원 증적, 정확한 승인, 만료/변조/중복/이전변경 상태 거부 및 CP1 재실행 부재 확인.
- 기존 CP1 bundle: `d229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41`.
- 제출 증적 SHA256: `7b0799cf8117a10371204e8265db8dc31e9bf325bb984f4dac1080d510ec3aed`.
- 패키지 source lock에서 기존 대비 바뀐 제품 파일은 `run-node-firewall-cni.py` 하나다. 패키지 binding/source lock 메타데이터는 수정 HEAD로 갱신한다.
- 이 준비 환경에서 실제 VM 작업은 실행하지 않았다.

시험 재현에는 위 원본 archive와 resume-kit이 필요하다. 시험 소스의 WORKSPACE 경로는 제출 증적을 보관한 검증 환경 기준이며, 현장 실행에는 tests 디렉터리를 실행할 필요가 없다.
