# P1-K8S-01 Gate 0 인수 기록

기준일: **2026-09-09**

**Gate 0 선행조건 충족 / 승인 소스 반영 완료.** 후속 Kubernetes 작업은 `infra-kubernetes`에서 관리하고 Gate 0 소스·원본 증적·PR 이력은 기존 위치에 보존한다.

## 승인 소스와 GitHub 기록

| 항목 | 고정 근거 |
|---|---|
| 원본 PR | [infra-ansible #3](https://github.com/321Team/infra-ansible/pull/3) |
| 코드 승인 HEAD | `e6c65d6c04615b17bd9be950d572f96abe351027` |
| 코드 승인 기록 | [APPROVED 리뷰](https://github.com/321Team/infra-ansible/pull/3#pullrequestreview-5150001943) |
| 병합·운영·제어 서버 반영 커밋 | `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab` |
| 소스 트리 | `0817d18fcbf74b3826f3ee31b1ad8f275daedc8b` |
| 운영 6대 검토 | [Gate 0 운영 PASS](https://github.com/321Team/infra-ansible/pull/3#issuecomment-5598425695) |
| 리드 요청 종결 | [후속 검사 PASS·승인 소스 반영 완료](https://github.com/321Team/infra-ansible/pull/3#issuecomment-5599485126) |

## 제출 증적과 확인 범위

| 증적 | 확인 범위와 결과 |
|---|---|
| 운영 6대 결과 v1.1, 마지막 성공 제출본 | 2026-09-09 16:49~16:50 KST. check/run 각각 전체 6대 PASS, 변경·실패·접속 실패 0. 노드 간 통신·이름 해석 및 승인 IP 일치 각각 30개 방향 통과. |
| 후속 결과 v1.0 | 2026-09-09 18:08 KST. 제어 서버의 site·Gate 0 문법 검사와 요청 범위 lint 성공, 승인 커밋 반영 완료. 이 실행은 노드 재검증을 수행하지 않았다. |
| 수동 접속 및 Gate 0 재확인 콘솔 | 서버 로그 시각 2026-09-09 18:46경. 사용자가 가동 상태를 의심한 두 노드의 SSH·Python pong 확인, 이어서 전체 6대 PASS. 모든 노드의 changed·unreachable·failed·rescued·ignored=0, 노드 식별자와 이전 증적 일치. |

| 원본 식별 | SHA-256 |
|---|---|
| `FDS_Gate0_6Node_Result_v1.1.tar(2).gz` | `6814a0d23d77432cfe744c1678971b0c68bae1d077c836a4fe7785cb046ab4a5` |
| `FDS_Gate0_Followup_Result_v1.0.tar.gz` | `848cc354cb739756695a36dd654d02b5350e6e216cecc10d96aaae9d9f9d0802` |
| `붙여넣은 텍스트 (1)(20260909-094748).txt` | `181364e10a0366e6e1a6a2ec3bed9318c50c3d9f5b6feed01f9da1458535c778` |

앞의 두 압축 증적은 제출된 sidecar 및 내부 파일 체크섬을 대조해 검토했다. 마지막 콘솔은 첨부 파일 수신 후 계산한 식별 해시이며, 실행기 봉인 아카이브나 서버 측 서명으로 표현하지 않는다. 위 판정은 제출 자료 검토 결과이고 리뷰 환경에서 운영 VM에 직접 접속한 결과가 아니다. 실행 당시 PASS를 이후 시점의 가동 상태 증명으로 전용하지 않는다.

## 인수 범위와 다음 단계

1. 공통 OS·SSH·DNS·Inventory·사전 요구사항과 Gate 0 소스는 `infra-ansible`에서 계속 관리한다. 검증된 파일을 이동하거나 새 저장소에서 독립 수정하지 않는다.
2. 후속 PR은 Gate, 고정 커밋·입력, 변경 영향과 필요한 시험 범위를 기록한다. 저장소 분리만으로 동일 OS 설정을 재적용하거나 Gate 0 전체 검증을 반복할 필요는 없다.
3. 런타임 설치 후 얻을 수 있는 실측값과 설치 전 결정할 수 있는 값을 구분해 Gate 1·2 순서에 맞춰 수집한다. 클러스터 생성 후에만 존재하는 값을 Gate 1 설치의 선행조건으로 순환 요구하지 않는다.
4. 리드 전달안 `FDS_P1-K8S-01_Lead_Final_v4.0`은 후속 코드·문서 편입 시 검토할 입력이다. 원본 ZIP SHA-256은 `f7a50e2ccbbbd5537bf6969baa451d699db284438c10760ece2b03f98df8de5f`다. 이 초기 패키지는 해당 코드 편입·승인·배포를 수행하지 않았다.
5. Gate 1/2 적용은 해당 소스·실행 계획의 검토와 실행 승인에 따라 수행한다. 미확정 운영 입력은 근거 없이 채우지 않는다.

원본 실행기의 `REVIEW_REQUIRED` 상태를 수정하지 않고 별도 검토 판정과 연결한다. 기존 비차단 P3 표시 개선사항의 분류는 유지한다. Gate 0 완료를 P1-K8S-01 전체 완료나 Kubernetes 설치 완료로 표시하지 않는다.
