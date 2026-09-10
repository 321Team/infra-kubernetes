# Worker 결합 변경 및 CP2·CP3 방화벽 후속 절차

2026-09-10 / 상태: **구현·검토용, 실서버 미실행**. 주관: baseline #14 이권욱. Kubernetes 재개 판단: #10 이하영. Cockpit/보호 인계: #19 연계. PR #2 CP1 소스와 PR #3 수집기 소스는 수정하지 않는다.

## 변경 범위

| 대상 | public runtime와 permanent 각각의 변경 | 최대 변경 수 |
|---|---|---:|
| Worker01·02·03 | CP 3개 주소 각각 /32 → TCP 10250 rich rule 추가, `cockpit` service 허용 제거 | 노드당 3 추가 + 1 제거 × 2 = 8 |
| CP2·CP3 | CP 3개 주소 각각 /32 → TCP 2379–2380 및 10250 추가 | 노드당 6 추가 × 2 = 12 |

Worker 전체는 추가 후보 18개, 제거 후보 6개다. 이미 목표 상태인 항목은 보존으로 기록한다. CP1은 PR #2 전용 도구를 사용한다. SSH·dhcpv6-client, CP의 기존 LB→6443 예외, NM 연결과 public binding, SELinux Enforcing을 보존한다. Worker cockpit.socket/service의 mask·inactive 상태를 확인하며 unit을 변경하지 않는다. join/reset, NTP 변경, Calico 정책 변경, 전체 firewalld 정지·reload·runtime-to-permanent는 수행하지 않는다.

## 실행 조건과 순서

1. PR #2 고정 HEAD `068661e076de02669170d3582f3d596d6e1a8eb3`의 이하영 리뷰 지적 해결 및 CP1 canary 실제 적용·운영 증적 인수. 기존 jvgkgfm9는 **inspect** 증적이며 apply 영수증으로 사용할 수 없다.
2. 본 변경의 정확한 HEAD를 독립 리뷰한다. 현재 Draft 코드는 운영 적용 승인 상태가 아니다. 검토 항목은 `NODE_FIREWALL_VALIDATION.md`에 정리했다.
3. 평일 실습 가능 시간에 대상 한 대, 최대 2시간의 변경 창을 기록한다. 다른 firewall/NM/Calico 설정 변경과 kubeadm join/reset/member 변경을 중지하고 복구 콘솔을 확보한다. 각 노드에서 다음 노드로의 진행은 직전 결과 검토 후 판단한다. 2026-09-10 이하영 부재를 고려해 준비 작업을 병행하며 타인의 독립 승인을 대신 기록하지 않는다.
4. 실제 창에서 대상 inspect와 CP1 topology를 수집한다. 입력은 15분 이내이며 source·node·plan·snapshot·topology 해시가 묶인다. 지금 만료될 입력을 미리 반복 생성할 필요는 없다.
5. CP2 방화벽 적용·검토 → CP2 잔여 상태 판단 및 별도 join 검증 → CP3의 새 inspect·단계 적용·별도 join 검증. 이후 Worker01→02→03을 각 새 topology 기준으로 진행한다. Worker 변경 준비는 CP 작업과 병행할 수 있다.

## ansible01 사용법

배포 ZIP의 게시된 외부 SHA-256을 먼저 대조하고 새 디렉터리에 푼다. `server01_ansible` 사용자로 실행하며 controller에 sudo를 붙이지 않는다. 승인된 infra-ansible HEAD `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`, 고정 inventory·키·known_hosts·송신 IP를 검사한다. 원격은 `svc_ansible`과 `sudo -n /usr/bin/python3`이다.

```bash
sha256sum --check NODE_FIREWALL_SOURCE_SHA256SUMS
python3 scripts/run-node-firewall.py --help
```

실제 변경 창의 Worker01 예시:

```bash
python3 scripts/run-node-firewall.py inspect --node k8s-worker01
```

출력된 `OUTPUT` 아래 `INSPECTION.json`과 TARGET/CP1 결과를 검토한다. nft 결과는 원시 증적이며 독자 nft 규칙의 영향을 사람이 확인한다. 허용된 public 외 추가 active zone/source binding, direct rule/passthrough, 예상 밖 public 서비스·rich rule, Cockpit unit 상태 차이는 도구가 중단한다. 다른 비활성 zone/정책/서비스 정의는 snapshot에 넣고 변경 전후 동일성을 확인한다.

`policies/icr-d-net-01-01/node-firewall-authorization.template.json`을 별도 작업 파일로 복사한다. 실제 리뷰 링크·검토자·HEAD·CP1 인수 결과서 SHA·시간 창·대상·모드를 채우고 SHA-256을 구한다. null을 임의 승인값으로 채우지 않는다. 이 파일은 **운영자의 실제 검토 기록**이며 오프라인 도구가 GitHub 승인을 인증하는 수단은 아니다. 실행자는 링크의 리뷰 상태와 검토 HEAD가 일치하는지 확인한다.

```bash
python3 scripts/run-node-firewall.py apply --node k8s-worker01 \
  --inspection /absolute/path/INSPECTION.json \
  --inspection-sha256 INSPECTION_FILE_SHA256 \
  --authorization /absolute/path/worker01-apply-authorization.json \
  --authorization-sha256 AUTHORIZATION_FILE_SHA256 \
  --cp1-receipt /absolute/path/CP1_APPLY_TARGET.json \
  --cp1-receipt-sha256 CP1_APPLY_TARGET_FILE_SHA256
```

위 대문자 SHA 값은 실제 파일 해시로 교체한다. `CP1_APPLY_TARGET.json`은 PR #2 apply 결과 중 `result.receipt`를 포함하는 CP1 노드 JSON이다. 압축파일 해시와 혼용하지 않는다. 노드 이름만 바꿔 기존 입력·승인 파일을 재사용하면 거부된다. CP2/CP3는 동일 인터페이스로 각자의 새 inspect/승인 파일을 사용한다. `--all` 모드는 없다.

## 결과와 복구

`APPLIED_REVIEW_REQUIRED`는 이 도구의 방화벽 변경·새 SSH 연결·snapshot·CP1 API/readiness와 membership 전후 검사를 통과했다는 뜻이다. Node Ready, 전체 etcd health, 애플리케이션·Calico·ICR 전체 인수 완료를 뜻하지 않는다. 사전 TCP 10250 연결 실패는 아직 kubelet 설정이 없는 노드에서 예상할 수 있으므로, 방화벽 검증과 join 후 서비스 연결 검증을 구분한다.

영수증은 원격 `/var/lib/fds/icr-d-net-01-01/node-firewall-v1/`에 root 소유로 fsync 저장된다. 각 변경 전 intent, 실행 결과, 사후 query와 소유권을 기록한다. 동일 노드의 미해결 이전 영수증이 있으면 새 run ID 적용도 차단한다. 통신 중단 시 자동 재시도 대신 출력된 원래 RUN_ID로 확인한다.

```bash
python3 scripts/run-node-firewall.py receipt --node k8s-worker01 \
  --run-id ORIGINAL_RUN_ID
```

부분 실패여도 확실히 변경된 항목만 자동 복구할 수 있다. timeout, ALREADY_ENABLED/NOT_ENABLED 경고, intent 이후 결과 저장 실패, snapshot drift 또는 membership 변경은 자동 복구 중단 조건이다. 이 경우 원래 영수증과 현 상태를 검토하고 별도 복구 결정을 기록한다. 원래 상태를 모른 채 규칙을 삭제하거나 state 파일을 지우고 다시 실행하지 않는다.

복구는 별도 `mode=rollback` 기록과 원격 영수증을 포함한 노드 결과 JSON 및 그 파일 해시를 사용한다. 입력 영수증의 원격 해시도 별도로 검증한다.

```bash
python3 scripts/run-node-firewall.py rollback --node k8s-worker01 \
  --receipt /absolute/path/TARGET.json \
  --receipt-sha256 TARGET_FILE_SHA256 \
  --authorization /absolute/path/worker01-rollback-authorization.json \
  --authorization-sha256 ROLLBACK_AUTHORIZATION_FILE_SHA256
```

복구는 새로 추가한 rule만 제거하고 이 실행이 제거한 cockpit service만 원복한다. 원복 후에도 cockpit unit mask는 유지된다. 가입한 노드의 필수 통신을 제거하지 않도록 가입 이후에는 이 사전 join 복구 절차를 사용하지 못하게 한다.

항상 출력된 `ARCHIVE`와 `SIDECAR`를 함께 제출한다. controller는 실패 시에도 가능한 증적을 보존한다. 전송 실패 뒤 결과를 알 수 없는 경우 `runtime_applied=attempted_requires_receipt_review`이며 무변경으로 판정하지 않는다.

## 검증의 한계

로컬 테스트는 모의 backend/프로세스로 안전 분기와 복구를 확인한다. RHEL 계열 실서버 firewalld/SSH의 실행 검증은 아직 없다. node-local lock과 snapshot 대조는 다른 관리자나 D-Bus/nft 변경을 원자적으로 잠그지 못한다. CP1 topology도 대상 변경 전후에 별도 조회하므로, 유지보수 중 join/configuration 동결을 실제로 지켜야 한다. raw nft는 수집되지만 변화하는 counter/handle을 자동 ownership 판정에 쓰지 않는다. 최종 runtime/permanent 유효성과 reboot 후 지속성은 별도 실제 증적이 필요하다.

공식 동작 근거: [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html). runtime/permanent는 별도 설정이므로 개별 명령을 각각 실행한다. 성공 rc만으로 소유권을 단정하지 않으며 query와 경고를 함께 확인한다.
