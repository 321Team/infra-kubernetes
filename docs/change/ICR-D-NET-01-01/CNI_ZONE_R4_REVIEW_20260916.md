# R4 — Permanent zone forwarding 생성 계약 보완

2026-09-16. 이전 HEAD `d015e54`의 normal은 실제 실행됐고 실패했다. 원인은 아래 증적으로 확정했으며 추가 VM 수집은 필요 없다. 수정본은 STATIC/MOCK 검증을 완료했고 독립 리뷰·새 inspect·VM Runtime은 대기다.

## 실제 증적

- Job `d6c7e1e672c418ca`, normal, firewalld `2.4.3-4.el10.noarch`.
- 실행 16:50:53~16:51:10 KST, 수집 17:00:09 KST. 작업 서비스의 실패 종료는 16:51:11 KST journal에 기록됐다.
- `FDS_CNI_Kernel_Component_cmr271f5.tar.gz`: SHA256 `b1b63bbda9172fb20c1a4389ae581da1955224abfb7e11bb7ac4143ec59bacaa`. 외부 sidecar·내부 **20/20** 파일·실행 소스 **4/4** 대조 통과.
- D-Bus/firewalld 기동과 전용 mount/net namespace·runtime 검증을 통과했다. non-root admission 실측을 의미하지 않는다.
- `COMMANDS.jsonl` zero-based 74: `--permanent --new-zone-from-file=... --name=fds-cni-trial`, rc=0/success. 67~73의 변경 전 snapshot은 `intent.before`와 일치한다.
- 75~81의 변경 후 snapshot과 `intent.after`를 전체 비교한 유일한 차이:

| 경로 | 예상 | 실제 |
|---|---|---|
| permanent.zones.fds-cni-trial.forward | no | yes |

- 82~88의 원복 시점 snapshot에도 같은 차이가 있어 expected/intent before/after와 일치하지 않았다. `shared_transaction_restored=false`는 실패로 유지한다.
- 오류의 `own policy disabled where verified`는 조건부 처리 설명이다. 이번에는 policy 생성까지 도달하지 않았고 실제 policy disable 명령도 기록되지 않았다.
- 전용 서비스 종료·소유 namespace 4개 삭제(각 rc=0), cleanup/disarm 완료, 수집한 HOST_BEFORE/HOST_AFTER 내용 일치, recovery_pending=false. 트랜잭션 원복 성공·독립 timer/reboot 회수 성공과 구분한다.
- 통신 결과 0개, pcap/trace 0개. CP1/CNI 통신 원인 판정·인수·CP2 변경·qualification·AC credit 없음. 반출 파일에 UUID 평문 패턴 없음.

## 원인과 공식 소스 대조

실행 코드와 기존 Simulator는 XML에서 `<forward/>`를 생략하면 CLI 생성 결과도 forward=no라고 가정했다. XML reader 자체는 생략 시 False다. 그러나 공식 firewalld v2.4.3의 CLI는 export tuple을 `addZone()`에 넘기고, client는 이를 16개 필드로 자른다. index 16의 forward가 빠져 daemon의 새 Zone 기본값 True가 남는 경로가 실제 관측과 일치한다. 대상 RPM의 패치 소스까지 비교한 결과는 아니다.

공식 근거: [CLI import](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall-cmd.in), [client addZone](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/client.py), [Zone reader/default](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/io/zone.py), [new_zone](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_config.py).

## 수정

1. 생성 XML에 `<forward/>`를 명시한다. 구형 tuple/전체 필드 전달 경로 모두 처음 생성되는 **Permanent-only·interface/source 없는 zone**의 예상 상태를 yes로 고정한다.
2. `disable-permanent-zone-forward`를 별도 `Transaction.alter()`로 추가한다. 변경 전에 intent를 저장하고 `--permanent --zone=fds-cni-trial --remove-forward` 이후 전체 snapshot을 엄격히 대조한다.
3. forward=no 확인 뒤에만 policy 생성·reload·바인딩·활성화로 진행한다. no로 바뀌지 않거나 다른 필드가 달라지면 중단한다. Runtime forward=yes는 허용하지 않는다.
4. 기존 guard·snapshot 비교·외부 drift 중단·원복 의도 대조는 유지한다. 기존 실패 job을 새 core로 수리하거나 재시작하지 않는다.

생성 직후 잠시 존재하는 yes는 바인딩 없는 Permanent 정의에만 한정된다. 중간 재부팅으로 로드되는 경우에도 interface/source가 없는 상태에서 원복하도록 모의 검증했다. 이 상태 전이는 독립 리뷰 대상이며 실환경 재부팅 검증을 완료한 것은 아니다.

이 수정은 **공통 cni_diag_core.py**를 변경하므로 격리 실행기와 생산 진단 실행기의 source digest 모두 달라진다. 생산 실행기는 배포·실행하지 않았으며 이전 qualification/승인을 새 bundle에 재사용할 수 없다. node-firewall.py, node executor, controller, daemon 격리 코드는 같은 바이트다.

## 검증 및 독립 리뷰 범위

- 기존 소스 + 실제 import 동작을 반영한 Simulator에서 `Post-mutation state differs: create-permanent-zone` 재현.
- 수정본 STATIC/MOCK **220/220 PASS** = 기존 211 + 25번째 변경 위치의 전/후 중단 2 + 신규 7. 전체 변경 위치 25개 × 전/후 = **50** fault-injection case.
- 신규 검사: 실제 zone 출력 replay, XML 생략의 한계, reload/binding 전 forward=no, remove 명령 오류, 성공을 반환하지만 변경 없는 경우, 외부 필드 변경 거부, 생성/disable 전후 재부팅 4 subcase.
- Python AST·PLAN_ONLY·소스 SHA256 검사 통과. 실제 firewalld/커널 통신 시험은 로컬 검증에 포함되지 않는다. 기존 D-Bus 프로세스 검사를 이번 변경의 검증으로 재계상하지 않는다.
- Kernel bundle: `1109fe8e2370fdfe09e49300f39d1e1e0f5b732c36ac7b8e55404996bffc507c`.
- Production executor bundle: `d6b2ab46297e8892ac966225f0e9a5ecb37724e1ceee0d94af4468a0d83bb74d`.
- 독립 리뷰: Permanent 임시 yes 상태의 범위, no 확인 전 reload/binding 차단, 중단·재부팅 원복, 외부 drift 보존, 공통 core 영향과 digest 갱신.
- 다음: 새 HEAD 독립 리뷰·새 inspect 대조 → 별도 추가 normal 실행 결정 → 실제 flow/pcap/trace/원복 검토. 기존 normal 1회는 이미 사용됐고 재실행하지 않는다. 병합·CP1/CP2·AC 완료는 보류다.
