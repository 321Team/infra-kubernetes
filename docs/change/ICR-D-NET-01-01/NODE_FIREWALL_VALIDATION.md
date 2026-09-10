# 후속 변경 검토 및 검증 범위 v2

2026-09-10. **74/74 로컬 Linux 모의 테스트 PASS**. PR #4 테스트는 55개(기존 보완 33 + v2 계약/실패 분기 22), 병합된 PR #3 Worker 회귀는 19개다. 테스트 ID 중복 없이 계산했다. 실서버 firewalld·SSH 적용 PASS가 아니다.

검토 소스는 scripts/node-firewall.py, scripts/run-node-firewall.py, node-firewall-v2.json 및 승인/CP1 인수 template이다. tests/fixtures/pr2_cp_canary_node.py는 PR #2 097ddb56d87b229a9eb465f72ea9f55d16524c51의 실제 생산자와 바이트가 같은 모의 시험 전용 파일이다. 영수증을 검증기에 맞춰 손으로 작성하지 않고 생산자의 apply_transaction을 fake backend로 실행한다. 운영 CP1 인수 기록을 대신하지 않는다.

| 검증 범위 | 확인한 분기 |
|---|---|
| Runtime transaction | CP 6·Worker 4후보, 기존 상태·Permanent 보존, durable intent, 부분 실패·경고·timeout·snapshot drift·역순 소유권 복구 |
| CP1 계약 | 실제 생산자 added/preserved 영수증, source/helper/plan/순서/범위/소유권/결과/전후 상태/시각 변조 거부, 인수 증적 결합 |
| 정책/승인 | 5대 실행 정책 대조, 새 inspection·잔여 메타데이터·zone controls·CP1 상태 해시 결합, 승인 공백 거부 |
| Controller | inspect→apply→새 SSH, 부분 실패 포장, 인수 누락 시 변경 전 중단, 새 inspect 기반 rollback |
| 상태/실행 통제 | Calico 준비 상태·재시작 감지, resourceVersion 제외, controller lock, 명령 시간/횟수 예산, legacy rollback 거부 |
| 입력/환경 | 중복 JSON·symlink 거부, env 제한, root /usr/local/bin 검사, redact 패턴, stderr truncation/경고 처리 |
| 회귀 | 병합된 PR #3 Worker 수집기 19개 및 해당 main 파일 12개 보존 |

## 실제 인수 상태

| 구분 | 상태 | 다음 증적/판단 |
|---|---|---|
| PR #2 현장 읽기 전용 검사 | 제출본 검토 완료 | 37개 현장 Linux 모의 테스트 + 실제 CP 조회 115개. apply 증적은 아님 |
| PR #2 독립 최종 리뷰·CP1 Runtime 인수 | 미완료 | 정확한 HEAD 리뷰, 실제 Runtime 영수증 및 Positive/Negative/Regression 인수 |
| PR #4 새 HEAD 독립 리뷰 | 재검토 필요 | 이하영의 보완 코드·문서 및 현장 검증 요구 확인 |
| PR #4 Runtime 적용·복구 | 미실행 | 한 대상 새 inspect, 실제 보호/백업/콘솔/복구 판단, 실행 시간·영수증·통신/관리 경로 검증 |
| Permanent 승격·재부팅 | 미구현/미인수 | Runtime 인수 후 별도 승인·구현 또는 보호 인계 회수 결정 |
| CP2/3·Worker join | 미실행 | #10의 잔여 상태·Runtime 종료 계획 검토 후 별도 창 |
| NTP 고정화 | 결정 대기 | 승인 endpoint·운영 주체·수치 기준과 별도 infra-ansible 변경 |
| Calico/ICR 최종 | 기존 HOLD 유지 | 정본 D34·named HEP/GNP·6대 firewalld 비활성화·기존 AC/독립 증적 |

원문 로그는 NODE_FIREWALL_TEST_RESULT.txt, 환경·코드/시험 파일 해시는 NODE_FIREWALL_TEST_ENVIRONMENT.json이다. 재현 명령은 `python3 -m unittest discover -s tests -v`, 소스 확인은 `sha256sum --check NODE_FIREWALL_SOURCE_SHA256SUMS`다. 상세 리뷰/CA 대응은 [보완 이력](NODE_FIREWALL_REMEDIATION_20260910.md), 실행·복구는 [runbook](../../runbooks/NODE_FIREWALL_FOLLOWUP.md)을 따른다.

## 증적 추적

- 최신 CP1 onsite `FDS_PR2_Onsite_mvoxv1pm.tar.gz`: SHA `e3e2d9c40f4fd57f8a445ca52e6756f81c455c4ad1ddbc9aa00689009857966b`. outer 13/13 + nested 5/5, 2026-09-10 19:15–19:16 KST, 37/37 현장 모의 테스트와 실제 조회 115/115 성공. 실행 source afc246d2db041c68de5014972107484dad122558; 증적 편입 HEAD 097ddb56d87b229a9eb465f72ea9f55d16524c51는 코드 동일. 미적용·입력 만료 상태.

- Worker full `FDS_Worker_Followup__fqs5007.tar.gz`: outer SHA `cfbc39b698b7198ec7c3853d8c783527aeb241a5347a0378cc1152ce0481d42e`. 실제 업로드 이름의 `(1)`은 사본 이름이며 해시로 대응했다. 방화벽·NTP 상태 근거.
- Worker supplement `FDS_Worker_Supplement_mse3803h.tar.gz`: outer SHA `38fa093a0ed2d28fe782932bf4ee694fa3d1007f5c0f3a1baee36097a1aa8b0b`. 내부 8/8, 24조회 성공, 6대 시간 속성 및 3 Worker kubelet 원인·Cockpit mask 확인. 추가 보완 재수집 요구는 해결됐다.
- CP1 inspect `FDS_CP1_Canary_jvgkgfm9.tar.gz`: outer SHA `45a23e3039a37edf4ee17a540263a1a3c558ab1e0d50b7aa2f783f0fa8f61b55`. 내부 5/5, 조회 110개 성공. 미적용·15분 입력 만료 상태.
- 최초 v1 구현의 source 재사용 근거: PR #2 HEAD `068661e076de02669170d3582f3d596d6e1a8eb3`의 snapshot/topology/journal 기본 구조, PR #3 HEAD `3309866766236bbdbe20b9e2ae1d8fd6f9233bcd`의 승인 transport·root binary 검사·timeout 부분 출력 보존. 본 확장 코드는 별도 검토 대상이다.
- [baseline #14](https://github.com/321Team/fds-project-baseline/issues/14) 통신 변경 / [#10](https://github.com/321Team/fds-project-baseline/issues/10) Kubernetes 재개 / [#19](https://github.com/321Team/fds-project-baseline/issues/19) 보안 인계. 기존 AC 상태와 [Done #65](https://github.com/321Team/fds-project-baseline/issues/65)는 변경하지 않는다.
