# 후속 변경 검토 및 검증 범위

2026-09-10. **31/31 로컬 테스트 PASS**. 로컬 표준 라이브러리 unittest로 transaction의 소유권·보존·부분 실패·복구와 controller gate를 검증한다. 결과는 모의 검증이며 실서버 적용 PASS가 아니다.

검토할 코드:

- `scripts/node-firewall.py`: 고정 plan, 사전 상태, 단일 변경 단위 journal, 반대 동작 복구, 불명확 상태 중단.
- `scripts/run-node-firewall.py`: 기존 승인 transport, source/inventory pin, 대상별 입력, CP1 apply 영수증, 유지보수 기록, topology 전후 및 새 SSH 확인.
- `tests/test_node_firewall.py`: Worker 8변경/노드와 CP 12변경/노드의 적용·역순 복구, 기존 rule/이미 없는 cockpit 보존, 상태 drift, 알려진 실패, 변경 후 timeout, 서비스 제거 후 timeout, 경고에 의한 소유 불명확, intent 저장 실패, 복구 실패, 위조/다른 대상 영수증, 가입/etcd peer 감지, 시간 만료, 승인 공백, SSH pin, 해시, 실패 증적 포장, root journal 중복 차단.

## 인수 항목

| 구분 | 현재 | 실제 증적/조건 |
|---|---|---|
| Worker full/supplement 수집 | 완료 | archive 및 내부 checksum PASS, 시간 속성 6/6, Worker mask 6 units와 kubelet 실패 원인 확인 |
| PR #2 독립 리뷰 | 미완료 | 이하영의 고정 HEAD 리뷰 결과 |
| CP1 canary 적용 | 미실행 | 적용 영수증과 API/etcd·관리 SSH·TCP·runtime/permanent 인수 |
| 본 소스 검토 | Draft 준비 | 정확한 HEAD의 독립 리뷰, 모의 검증 결과 검토 |
| Worker 결합 변경 | 미실행 | 3 CP /32→10250, cockpit service 제거, 기존 mask·SSH 보존, reboot 후 확인은 별도 |
| CP2·CP3 방화벽 | 미실행 | 단계별 apply 영수증·기존 LB rule·topology·SSH 및 복구 조건 |
| CP2/3 및 Worker join | 미실행 | #10 잔여 상태 판단 후 정상 join·Node·etcd/서비스 검증 |
| NTP 고정화 | 결정 대기 | 승인 endpoint·수치 기준과 별도 infra-ansible 후속 변경 |
| Calico/ICR 최종 | HOLD 유지 | HEP/GNP, Positive/Negative, 관리 경로, 독립 증적, rollback/reboot 등 기존 AC |

실행 기록은 `python3 -m unittest discover -s tests -v`로 재현한다. 소스 manifest는 `sha256sum --check NODE_FIREWALL_SOURCE_SHA256SUMS`로 확인한다. state 파일이나 root 구성 권한을 우회하지 않는다. 같은 머신에서 다른 관리자의 변경을 완전히 차단하는 분산 lock은 제공하지 않으므로 실제 유지보수 동결과 독립 리뷰가 필요하다.

## 증적 추적

- Worker full `FDS_Worker_Followup__fqs5007.tar.gz`: outer SHA `cfbc39b698b7198ec7c3853d8c783527aeb241a5347a0378cc1152ce0481d42e`. 실제 업로드 이름의 `(1)`은 사본 이름이며 해시로 대응했다. 방화벽·NTP 상태 근거.
- Worker supplement `FDS_Worker_Supplement_mse3803h.tar.gz`: outer SHA `38fa093a0ed2d28fe782932bf4ee694fa3d1007f5c0f3a1baee36097a1aa8b0b`. 내부 8/8, 24조회 성공, 6대 시간 속성 및 3 Worker kubelet 원인·Cockpit mask 확인. 추가 보완 재수집 요구는 해결됐다.
- CP1 inspect `FDS_CP1_Canary_jvgkgfm9.tar.gz`: outer SHA `45a23e3039a37edf4ee17a540263a1a3c558ab1e0d50b7aa2f783f0fa8f61b55`. 내부 5/5, 조회 110개 성공. 미적용·15분 입력 만료 상태.
- source 재사용 근거: PR #2 HEAD `068661e076de02669170d3582f3d596d6e1a8eb3`의 snapshot/topology/journal 기본 구조, PR #3 HEAD `3309866766236bbdbe20b9e2ae1d8fd6f9233bcd`의 승인 transport·root binary 검사·timeout 부분 출력 보존. 본 확장 코드는 별도 검토 대상이다.
- [baseline #14](https://github.com/321Team/fds-project-baseline/issues/14) 통신 변경 / [#10](https://github.com/321Team/fds-project-baseline/issues/10) Kubernetes 재개 / [#19](https://github.com/321Team/fds-project-baseline/issues/19) 보안 인계. 기존 AC 상태와 [Done #65](https://github.com/321Team/fds-project-baseline/issues/65)는 변경하지 않는다.
