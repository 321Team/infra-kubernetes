# CP1 운영 변경·인수 연계 구현 초안 — 2026-09-17

**OFFLINE_CONSISTENCY_IMPLEMENTED / OPERATIONAL_PRODUCER_AND_INTEGRATION_NOT_IMPLEMENTED / RUNTIME_NOT_RUN**

승인된 `ed53205` 양식 수정 뒤, 원 CP1 영수증에서 새 CNI 상태로 이어지는 입력 검사를 구현했다.
기존 `run-node-firewall.py`·`node-firewall.py`와 승인된 생산자 바이트는 변경하지 않는다.
새 검사는 로컬 파일의 정합성 검사이며 실제 방화벽 적용/회수기 또는 후속 노드 실행 승인이 아니다.
PR #11의 인수 양식 증분 승인과 이 초안의 승인을 구분한다.

## 1. 완성한 코드 범위

`scripts/check-cp1-cni-handoff.py`는 네 JSON 파일과 각각 외부에서 확인한 SHA256을 입력받는다.
파일 내부에 자기 해시를 적는 방식으로 파일 무결성을 증명하지 않는다. 해시 인자의 출처를 사람이 확인해야 한다.

| 입력 | 검사 |
|---|---|
| contract | 원 파일/영수증 해시, source/plan pin, CP1 같은 노드 /26·양쪽 cali 범위, 제한된 변경 집합, 검토 기록, 시간대가 있는 최대 2시간 창 |
| original | 기존 CP1 생산자 계약과 동일한 검증 함수를 바이트 해시로 고정하여 재사용. 원 영수증 편집·구 source·불완전 작업 거부 |
| bundle | 원 after→새 before, 계획의 변경 집합을 순서대로 계산한 after→기록 after, intent/result·시각·source·run ID, 인수 기록과 세 증적 분류 연결 |
| observation | 작업 전/후 동일 검증, 전체 상태·보호 상태·boot ID·현재 실행 상태·복구 예약, CP2/3 미가입, 현재 시각·정리 여유 확인 |

추가 가능한 상태는 `fds-cp1-cni` zone 및 `fds-cp1-cni-local` policy와 검토된 active-policy 출력 변경뿐이다.
기존 public Runtime/Permanent·NM·보호 상태·다른 객체를 변경 집합에 넣거나 기존 객체를 새 소유물로 주장하면 거부한다.
단, 전용 객체 **내용의 패킷 처리 의미**는 이 검사로 증명하지 않는다. 승인된 계획 생성기와 native rule 검사가 별도로 필요하다.
현재 초안은 zone/interface 변경에 따른 public 상태 표현이 실제로 이 투영과 호환되는지도 Runtime으로 입증하지 않았다.

SHA가 맞아도 SYNTHETIC_NETNS 또는 FAIL 증적, 다른 run/state의 증적, 누락된 Positive/Negative/Regression은 거부한다.
보고서에 ACTUAL_CP1_CALICO_RUNTIME/PASS를 쓰는 것만으로 실제 패킷을 입증할 수는 없다.
raw archive 해시는 연결 정보이며 이 도구가 archive를 열어 재판정하거나 GitHub 승인자를 인증하지 않는다.

성공 출력도 **CONSISTENT_REVIEW_REQUIRED**이며 `runtime_independently_verified`, `authorizes_cp1_acceptance`,
`authorizes_cp2_change`, `authorizes_join`, `production_consumer_connected`는 전부 false다.
기존 실행기는 이 출력을 인수 JSON으로 받아들이지 않고 CNI after drift를 계속 차단한다.
이 구분은 테스트에서도 확인한다. 기존 원 영수증을 새 after로 덮어쓰는 우회 경로는 없다.

사용 형식(개발/검토 환경의 로컬 파일 전용):

```bash
python3 -I -B scripts/check-cp1-cni-handoff.py \
  --contract CONTRACT.json --contract-sha256 VERIFIED_CONTRACT_SHA256 \
  --original ORIGINAL.json --original-sha256 VERIFIED_ORIGINAL_FILE_SHA256 \
  --bundle BUNDLE.json --bundle-sha256 VERIFIED_BUNDLE_SHA256 \
  --observation OBSERVATION.json --observation-sha256 VERIFIED_OBSERVATION_SHA256
```

PLACEHOLDER 인자는 실행값이 아니다. 빈 배포 template은 DRAFT_NOT_EXECUTABLE이며 실행 입력으로 채워 배포하지 않는다.
오류는 BLOCKED JSON과 non-zero로 반환한다. SSH/sudo/firewall/systemd 호출·입력 파일 수정은 없다.

## 2. 한 번에 결정·검토할 운영 변경안

다음은 [기존 후보](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5699610955)의 구체화이며 아직 실행 승인이 아니다.
이 초안의 소프트웨어 검사가 허용하는 범위와 실제 운영에 채택할 범위를 구분한다.

| 항목 | 검토안 및 해소 조건 |
|---|---|
| 허용 | CP1 소유 `10.244.246.64/26`의 같은 노드 Pod 간 L4 전체. ingress/egress 모두 cali. 실행창에서 IPAM·경로·실제 veth 소유 재확인. /16·물리·터널·교차 노드 자동 확대 없음 |
| 기본 메커니즘 후보 | 전용 cali zone + 해당 zone 양방향 policy, target CONTINUE, source/destination /26 교집합. zone target ACCEPT/trusted 및 무제한 intra-zone forward 금지 |
| HOST 보존 | 기존 public INPUT의 서비스·rich rule·ICMP 등 처리 의미를 전용 zone에서도 보존하는 설계가 필요. 단순 빈 zone 이동은 채택 불가. public 정의 불변만으로 패킷 의미 보존을 증명하지 않음 |
| 외부 통신 보존 | public forward=yes·ens160로의 경로, DNAT 선행 처리, 기존 policy priority를 포함해 DNS/API/Harbor 등 기존 경로와 비교. 전용 같은-zone policy만으로 해결됐다고 선언하지 않음 |
| native 검사 | 양쪽 cali와 두 /26이 모두 조건에 포함되는지, IPv6·물리·터널·HOST로 확대되지 않는지, 기존 Calico NetworkPolicy 집행을 우회하지 않는지 생성 규칙으로 확인 |
| 초기 창 | 관측·원복을 포함한 최대 2시간 후보. 사전 작업에는 기존 node 실행 예산 480초+정리 여유 180초, 사후 확인에는 180초 잔여 필요. 기존 20분 진단 승인과 별개 |
| Join 이후 존속 | 이 최대 2시간 PRE_JOIN_CANDIDATE는 Join을 허가하지 않음. Join을 포함하려면 유지 종료 시각·HEP 인계·가입 후 장애 시 대응을 별도로 구체화하고 구현해야 함. 기간만 늘리거나 자동 원복을 꺼서 해결하지 않음 |
| 회수 | 변경 전 독립 로컬 복구 확보, 자기 policy/binding 비활성·회수, 소유 Permanent 정의 삭제와 통제된 reload/원 Runtime 6개 복원, 전체 원상/관리 통신 확인. 남의 drift는 덮어쓰지 않음 |

firewalld 공식 소스 확인 근거: [v2.4.3 interface 문법](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/functions.py),
[nft backend](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/nftables.py).
`cali+`가 내부 iif/oif `cali*` 조건으로 변환됨과 zone-forward의 출력 interface ACCEPT 생성은 확인됐지만,
이를 완성된 정책·HOST/외부 통신 보존 증명으로 확대하지 않는다.

## 3. 실제 연결 전에 필요한 구현

| 순서 | 완료 조건 |
|---|---|
| 운영 명세 | 위 HOST/외부 보존 및 정책 존속·회수 종료 조건 확정, 실행창·담당자·독립 검토 기록 |
| 생산자 | 완전한 before 수집과 정확한 예상 after 생성, 변경 intent fsync, 소유권, 부분 실패 reconcile, 독립 복구 및 실제 상태 수집 구현 |
| 소비자 연결 | 고정된 생산자 source/plan·코드 해시를 검증하는 배포 계약을 확정한 후 기존 controller의 CP1 gate 전·후 호출과 승인 입력 결합. 기존 v1 경로/Worker 경로 보존 |
| 현장 인수 | 원래 5개 기능 + 새 범위의 Negative/Regression + native/패킷 증거 + 종료 상태를 같은 창에 수집. raw 증적과 독립 판정 연결 |
| CP2/3·Join | CP1 실제 인수와 후속 실행기 최신 검토/실행 결정 뒤 포트 적용. 가입 잔여 상태·보호·시간 동기화 등 Join 조건은 별도 확인 |

따라서 이번 초안만으로 **CP1 운영 패치 준비 완료·CP2 실행 가능·당일 Join 가능**이라고 보고하지 않는다.
새 VM 반입이나 이미 확인한 증적 재수집 없이 수행한 자체 검증이며, 운영 명세가 미확정인 상태에서 실서버용 명령은 배포하지 않는다.
새 리뷰 요청을 단계마다 추가하지 않고 운영 변경안과 실제 연결 구현을 한 묶음으로 만들 때 검토 범위를 고정한다.

## 4. 검증과 한계

신규 검사에서는 원 producer를 실제 바이트로 고정하고 모의 backend에서 원 영수증을 생성했다.
새 CNI producer·관측·승인·증적은 전부 모의 입력이며 실제 구현 또는 현장 관측으로 표현하지 않는다.
정상 연결, 재해시한 source/plan 변조, 원 영수증/after 변경, 순서·intent 오류, timeout/경고 결과,
소유 객체 충돌·범위 확대·만료·회수·boot 변경·전후 drift·증적 범주/주체·JSON 중복 키 등을 검사한다.
정확한 시험 수·원본 로그·바이트 검증은 동봉한 VALIDATION JSON과 TEST 로그를 따른다.

운영 목표는 정본 v3.1.18 §6.6/6.6.1과 최신 Lead 결정의 HEP/GNP 6/6 Ready 후 별도 실행창을 유지한다.
진단 실행기 전체 qualification을 모든 운영 대안의 공통 선행조건으로 추가하지 않는다.
