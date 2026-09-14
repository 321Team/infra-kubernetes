# PR #4 독립 리뷰 후속 보완

2026-09-14 / 제출 상태: **보완·Linux 검증 완료, 새 HEAD 독립 재리뷰 필요**.

대상 리뷰: [이하영의 리뷰](https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-5659926215). 보완 전 PR HEAD는 `e264e910a2fbc902a9ee6ea4cebf7f56d0e8181f`, 통합한 main은 `1c5483166f3e4d495feffd5408967bc45c60c0e4`다. 이 문서는 실서버 실행·운영 인수 승인 기록이 아니다.

## 승인 전 요청 대응

| 항목 | 보완과 확인 근거 |
|---|---|
| B1 — PR #2 미통합 | PR #2가 포함된 main의 파일과 이력을 merge 방식으로 통합한다. 기존 PR #4와 이하영의 수정 이력을 보존하며 force push/rebase로 재작성하지 않는다. 계약 시험은 통합된 실제 `scripts/cp-canary-node.py`를 고정 해시로 확인한 뒤 fake backend로 실행한다. 중복 `tests/fixtures/pr2_cp_canary_node.py`는 제거한다. |
| B2 — 생산자 커밋 추적 | 실제 main ref는 `1c54831`이며 두 부모는 `7d44afc`와 `f0388b1`이다. `f0388b1`과 `2911d62` 모두 이 main의 조상이다. 이전 PR base `7d44afc`와 현재 main을 구분한다. `CP1_CONTRACT`의 f0388b1 pin은 실제 승인된 생산자 영수증 호환성 조건이므로 유지한다. |
| B3 — 인가 하드코딩 | 기존 지정 인물 검사를 `EXPECTED_OPERATOR` / `EXPECTED_REVIEWER`로 명명하고 함수 설명과 [인가 기록 모델](../../runbooks/NODE_AUTHORIZATION_MODEL.md)에 의도·위조 한계·실제 승인 대조 절차를 명시한다. 인가 JSON이나 자체 해시만으로 신원·승인 진위를 인증하지 않으며 임의 역할 JSON으로 검사를 대체하지 않는다. |

당시 GitHub API 대조 결과:

- [main 병합 커밋과 부모](https://github.com/321Team/infra-kubernetes/commit/1c5483166f3e4d495feffd5408967bc45c60c0e4).
- `f0388b1...1c54831`: ahead=4, behind=0, merge-base=f0388b1.
- `2911d62...1c54831`: ahead=5, behind=0, merge-base=2911d62.
- `1c54831...e264e91`: diverged, ahead=6, behind=8, merge-base=7d44afc. PR #4 미통합은 사실이나 현재 main에서 두 생산자 커밋이 도달 불가능하다는 해석은 맞지 않는다.

저장소를 fetch한 환경에서는 다음 읽기 전용 확인으로 대조한다. 첫 두 명령은 exit 0이어야 한다.

```bash
git merge-base --is-ancestor f0388b1a372d19b5079e94ec6949ef443d774d2f 1c5483166f3e4d495feffd5408967bc45c60c0e4
git merge-base --is-ancestor 2911d625745d74eff035fa572b269df5445886fb 1c5483166f3e4d495feffd5408967bc45c60c0e4
git show --no-patch --format='%H %P' 1c5483166f3e4d495feffd5408967bc45c60c0e4
```

실제 CP1 실행 checkout은 기존 승인 f0388b1로 유지한다. PR #4에 CP1 코드가 통합됐다는 이유로 새 PR #4 HEAD를 CP1 실행 source로 전용하지 않는다. 생산자 helper의 SHA-256은 `0454a238d7451c86362dd98a54d3ed15cbf7f5d78c921cd6b93447581fc40ac2`로 동일하다.

## 리뷰어 수정과 권장 사항

- `d4f17be`: Worker subprocess 환경변수 제한과 두 원격 Python 호출의 `-I`를 보존한다. 새 회귀시험은 주입된 Python/Ansible/credential 환경이 전달되지 않는지, identity와 sudo 실행 양쪽이 격리 모드를 쓰는지 확인한다.
- `e264e91`: 정책을 한 번 읽는 `read_checked()`와 `load_checked()` 외부 계약을 보존한다. 체크섬 불일치·중복 JSON·symlink 거부, controller 적용/복구 모의 흐름이 통과했다.
- 사용하지 않는 `node-firewall-v1.json`은 현재 배포본에서 제거하고 과거 Git 이력에 보존한다. v1 journal 경로·영수증 조회 호환성과 v2만 허용하는 자동 rollback 조건은 유지한다.
- 공통 controller 모듈 추출은 **비블로커 구조 개선으로 별도 후속에 남긴다**. 격리 Python import, 원격 단일 파일 payload와 배포 source lock을 함께 바꾸는 별도 영향이 있어 이번 승인 전 보완과 결합하지 않는다. 이번 env/격리 누락에는 직접 회귀시험을 추가했다.

## Linux 검증

검증 입력은 두 원격 snapshot의 파일별 Git blob SHA를 대조해 통합하고 아래 보완을 적용한 소스다. 로컬 인덱스를 만들었으며 원격 HEAD인 것처럼 로컬 임시 commit을 기록하지 않았다. [실행 환경·입력 SHA](PR4_REVIEW_TEST_ENVIRONMENT_20260914.json)와 [전체 출력](PR4_REVIEW_TEST_RESULT_20260914.txt)에 실제 바이트와 실행 결과를 기록한다.

```bash
python3 -m unittest discover -s tests -v
```

| 범위 | 이번 실행 결과 |
|---|---:|
| CP1 기존 회귀 | 49/49 PASS |
| Node 계약·실행/복구 | 58/58 PASS |
| Worker 수집기 | 21/21 PASS |
| 합계 | **128/128 PASS, exit 0** |

기존 125개와 새 시험 3개다. 이전 57개 또는 74개 결과와 합산하지 않는다. pytest를 실행했다고 기록하지 않으며 같은 unittest 기반 테스트를 Linux의 unittest discovery로 실행했다. 서버 네트워크·SSH·방화벽·VM에 접근하지 않은 모의시험이다. 이후 문서·manifest 정리로 실행/시험 소스가 달라지지 않았는지 해시로 대조한다.

최신 Node/Worker 배포 manifest는 이 보완 소스를 기준으로 갱신한다. PR #2의 기존 manifest·CP1 실행 코드는 그대로 보존한다. 새 PR #4 HEAD 독립 판정, 실제 CP1 인수와 후속 대상별 실행 결정은 남아 있다.
