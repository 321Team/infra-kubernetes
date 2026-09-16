# R6 — 가속 장애 시험·운영 recovery 종료 처리 보완

기준 HEAD: `b930b07be8f0716b147585e5cdab04a757737aa8`. 사용자 착수 요청에 따라 코드 수정 전 사전 점검 결과를 구현했다. **STATIC/MOCK 286/286 PASS / R6 대상 VM Runtime·독립 리뷰 미완료**다. 기존 R5 normal 결과와 승인 범위를 변경하지 않는다.

## 문제와 근거

1. R5 normal job `3c42bc094b1c1ad7`은 ARMED 20:01:43.336 → 첫 흐름 준비 20:03:26.361 KST로 약 **103.025초**가 걸렸다. 기존 가속 kill/deadline/cleanup-retry timer 90초는 같은 준비 시간이 걸리면 목표 장애 지점 전에 회수할 수 있다. 이는 실제 normal 측정과 코드 분석에 따른 위험이며, 미실행 fault 시험을 실패로 보고한 것이 아니다.
2. 운영 node `disarm()`은 recovery service 자신이 실행 중인 경로에서도 unit 파일을 삭제한 뒤 reload했다. 실제 local 파일과 mock systemd를 사용하는 회귀시험에서 기존 소스의 unit 삭제를 재현했다. 이 결과를 대상 VM 또는 운영 장애 재현으로 확대하지 않는다.
3. 운영 controller는 기존 collect에서 status만 조회했다. node만 수정하면 pending unit을 마무리할 경로가 없으므로 controller·반출 계약도 함께 보완해야 했다.

R5의 6개 흐름·51개 해시·최종 정리 결과는 [완료 기록](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5697557472)에 보존했다. R5 승인은 해당 소스의 normal 1회에 한정되며 R6에 자동 승계되지 않는다.

## 변경

### Kernel wrapper

- kill/deadline/cleanup-retry 가속 timer를 **300초**로 변경했다. 독립 회수는 첫 변경 전에 계속 예약한다. normal/reboot 예약 1020초, 전체 작업창 1200초·정리 여유 180초는 유지한다.
- 목표 `PRIVATE_POLICY_ACTIVE` 도달·주입 의도·회수 관측을 `scenario_observation`에 기록한다. 목표 미도달은 `NOT_REACHED`와 component 실패로 보존한다.
- SIGKILL 의도와 recovery가 worker를 중지하기 **전** 조회한 실제 종료 상태를 구분한다. 최초 관측은 재시도에서도 유지한다. cleanup-retry의 첫 의도적 실패는 목표 도달과 SIGKILL 관측이 있을 때만 주입한다.
- 가속 deadline 경과는 같은 boot의 monotonic 시간으로 계산한다. 시간 경과가 실제 timer 발화의 단독 증명은 아니므로 verdict는 REVIEW_REQUIRED로 남기고 journal과 함께 검토한다. 300초가 모든 환경에서 충분하다는 보장은 없다.
- RESULT와 COMPONENT_SUMMARY에 같은 시나리오 증적을 포함한다. production qualification/PASS를 합성하지 않는다.

### 운영 node·controller

- 자원 정리, recovery 예약 해제, unit 파일 정리를 분리했다. timer inactive/failed·queued job 부재 및 timer/service disabled 상태를 readback한 뒤 disarm을 기록한다. 실행 중일 수 있는 recovery service 파일은 보존한다.
- controller는 새 job의 `unit_cleanup_protocol=1`을 확인하고 기존 collect/execute 최종 수집에서 해당 job의 **staged node** `collect`를 호출한다. 이전 job에 protocol이 없으면 status 조회만 유지하며 새 source를 이전 job에 주입하지 않는다.
- direct run/recover/collect는 staged source 해시와 실행 위치를 검사한다. STOP/systemctl 변경보다 먼저 검사하며, unit manifest는 소스 생성값과 일치해야 한다. 파일 변경·symlink·예상 밖 누락은 거절한다. mutation 전 partial install 또는 기록된 제거 중단만 별도 취급한다.
- work/recovery/timer의 terminal 상태·MainPID·queued job·fragment/drop-in을 확인한 뒤 소유 recovery unit 두 개만 제거한다. 활성 서비스 강제 중지는 collect에 포함하지 않는다. transient work unit은 종료 확인 대상이며 파일 삭제 대상이 아니다.
- 최초 종료 상태를 `UNIT_EXIT_STATE.json`에 보존한다. reload 실패 후 재시도에서 `not-found` 관측으로 원래 실패 종료 기록을 덮어쓰지 않는다.
- 정리 중 systemctl 호출은 **20초 예산**을 공유한다. controller의 30초 응답 제한과 맞추고, 상태 불명·reload 실패·종료 대기는 pending으로 반출한다. 삭제 intent 후 disarm 재확인이 실패한 경우도 같은 collect가 readback부터 재시도할 수 있다.
- 최종 정리 응답이 끊기면 먼저 받은 원본 증적과 `collection_error`를 보존한다. `--execute`는 unit 정리가 미완료면 non-zero이며, `--collect`의 증적 수집 성공과 구분한다.
- 새 install은 이전 job의 cleanup/disarm 및 protocol 1의 unit 정리가 끝나야 허용한다. 새 job의 systemd 이름 충돌도 설치 전에 검사한다.

공통 Transaction/parser(`cni_diag_core.py`)와 기존 `node-firewall.py`는 동일 바이트다. 운영 node가 바뀌므로 두 bundle 모두 변경된다.

| Bundle | SHA256 |
|---|---|
| Kernel | `22628cfbf5679fa008d2a48d43ff17d0b9f4ce1ca52542c63a389242b2daf99c` |
| Production | `7e53c034ca852ea3bccc83666f48684d1a8e9e547c7207585fd2cd37f6e87e26` |

## 검증

**기존 246개 + 추가 40개 = 286/286 PASS**, skip 0. [실행 로그](CNI_RECOVERY_R6_TEST_OUTPUT_20260916.txt)와 [검증 JSON](CNI_RECOVERY_R6_VALIDATION_20260916.json)을 함께 보관한다.

- 기존 R5 소스에서 동일 disarm 보존 assertion이 **FAIL**하는 것을 확인했다. R6에서는 PASS다. [변경 전 재현](CNI_RECOVERY_R6_RED_REPRODUCTION_20260916.txt)
- 실행 중 work/recovery·queued job·MainPID 누락·다른 checkout·source 변경·foreign manifest/drop-in·symlink·누락 unit을 검사했다.
- reload 실패 후 재시도·재활성화 전후 확인·최초 exit 기록 보존·중복 collect·이전 job pending 차단·mutation 전 partial install을 검사했다.
- 실제 로컬 Python child로 controller 종료코드와 archive를 검사했다. 종료 후 staged 수집, pending archive, 수집 응답 유실 시 기존 증적 보존, 구버전 status-only 경로를 포함한다. 원격 SSH adapter는 mock이다.
- 목표 미도달, kill intent/관측 분리, 재시도 관측 보존, wall-clock 변화, boot 변경, missing armed clock, 목표 guard 실패, cleanup-retry 주입 조건을 검사했다.
- Python AST 14개 파일, kernel manifest 4개와 두 실행기의 기본 PLAN_ONLY를 확인했다. qualification 및 실행 승인 요구는 유지된다.

검증 명령:

```bash
python3 -B -m unittest discover -s tests -p 'test_cni*.py' -v
sha256sum --check CNI_KERNEL_COMPONENT_SHA256SUMS
python3 -B scripts/run-cni-kernel-integration.py
python3 -B scripts/run-cp1-cni-diagnostic.py
```

실제 systemd·방화벽·namespace·Calico·물리 네트워크·reboot 경계는 mock이며 **R6 Runtime은 미실행**이다. CI/독립 REVIEW/병합 상태는 게시 후 GitHub에서 별도 확인한다. 이 문서의 로컬 PASS를 CI 또는 독립 APPROVED로 표현하지 않는다.

## 변경 소스 검토와 다음 실행의 조건

- 이번 제출은 반복 증상별 수정 대신 타이머·증적·운영 node/controller 수집 계약과 재시도를 함께 다룬다. 별도의 리뷰 요청 메시지나 신규 VM 작업을 발생시키지 않는다. PR의 기존 리뷰어 지정은 유지한다.
- 새 source 검토에서는 staged authority, protocol 없는 이전 작업 호환성, unit 종료/정리 순서, 제한시간·pending 반출, 목표 미도달과 실제 주입 구분을 함께 확인한다.
- 운영 qualification의 실제 Calico/physical/tunnel/existing Pod **7개 경로**, disconnect/kill/deadline/reboot/cleanup-retry/NM-ifcfg **6개 회수**는 여전히 미검증이다. 이번 수정본은 해당 qualification harness를 완성한 것이 아니다.
- 운영 hostname·원 영수증·source/qualification 계약을 완화하지 않았다. 새 VM 또는 운영 실행 없이 자체 점검과 코드 제출까지만 수행했다. CP1 인수·CP2 변경·AC 가산·병합은 보류하며 Worker 병행을 유지한다.
- R5 원본 job·v1.4 패키지를 보존한다. R6 변경 소스의 검토와 실제 시험 범위가 정해지기 전에 사용자에게 추가 반입·normal 반복을 요구하지 않는다.

공식 동작 근거: systemctl disable은 service 중지를 자동 의미하지 않으므로 실행 상태와 별도로 확인한다. [systemctl](https://www.freedesktop.org/software/systemd/man/systemctl.html). 가속 timer의 OnActiveSec는 timer 활성화 시점을 기준으로 하며 실제 deadline 시험 해석 시 준비시간을 포함한다. [systemd.timer](https://www.freedesktop.org/software/systemd/man/systemd.timer.html).
