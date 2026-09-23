# infra-kubernetes

321Team FDS 프로젝트의 Kubernetes 구축·구성·운영 형상관리 저장소다. P1-K8S-01 Gate 1 이후의 런타임 설치, 클러스터 구성, Calico 네트워크·호스트 보호 전환, 검증 및 복구 절차를 관리한다.

## 시작 기준

- 연계 티켓: **P1-K8S-01**
- 초기 범위: CentOS Stream 10 기반 Control Plane 3대와 Worker 3대
- 정본: [fds-project-baseline CURRENT.md](https://github.com/321Team/fds-project-baseline/blob/main/CURRENT.md). 초기 구성 시 확인한 RELEASED 버전은 **v3.1.17**이다.
- Gate 0 선행조건: **운영 6대 PASS**, 승인 소스의 제어 서버 반영 완료
- Gate 0 소스: [infra-ansible PR #3](https://github.com/321Team/infra-ansible/pull/3), 병합 커밋 `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`
- Gate 0 인수 근거: [GATE0_HANDOFF.md](docs/GATE0_HANDOFF.md), [상위 소스 고정값](docs/UPSTREAM_LOCK.json)

초기 파일은 저장소 관리 문서와 PR 양식이다. Gate 1/2 실행 코드, 실제 배포, Runtime PASS는 아직 이 초기 구성에 포함되지 않는다. Gate 0 PASS는 후속 설치의 선행조건 충족을 뜻하며 설치 완료나 개별 변경의 실행 승인을 대신하지 않는다.

## 저장소별 책임

| 저장소 | 관리 대상 | 연결 원칙 |
|---|---|---|
| `infra-ansible` | 공통 OS 구성, SSH/SFTP 접근, DNS Resolver, Inventory, Kubernetes 사전 요구사항, Gate 0 검증 | 기존 소스와 PR·증적 이력을 유지한다. 이 저장소에 동일 역할이나 Inventory의 독립 사본을 만들지 않는다. |
| `infra-kubernetes` | Gate 1 런타임·Kubernetes 도구 설치 및 관련 설정, Gate 2 클러스터 초기화·join·Calico·호스트 보호 전환, 검증·복구 | 작업별 승인 커밋과 실행 입력을 고정하고 변경 전후 결과를 기록한다. Ansible로 작성한 Kubernetes 전용 역할도 이 저장소에서 관리한다. |
| `fds-project-baseline` | 승인된 요구사항, 설계, WBS 및 변경 결정 | 저장소 분리를 이유로 승인 범위나 정본을 자동 변경하지 않는다. |
| 관련 기능 저장소·티켓 | API LB, DNS 서버, Harbor, 애플리케이션, DB, 모니터링 및 보안 기능의 각 담당 범위 | 인터페이스와 인수 조건을 연결하고 동일 기능을 중복 구현하지 않는다. |

공통 Inventory는 검토된 `infra-ansible` 커밋의 파일을 실행 시 참조한다. 실제 실행에서는 두 저장소의 전체 커밋 SHA와 Inventory 출처를 함께 기록한다. 상위 저장소의 `main` 변경을 자동 적용하거나, 기존 실행 디렉터리를 새 저장소로 덮어쓰지 않는다.

## 변경 검토 및 승인

1. Lead는 `p1-k8s-01/<작업명>` 브랜치에서 작업하고 `main` 대상으로 PR을 작성한다. 연계 티켓·Gate, 변경 이유·영향, 검증 결과·증적, 복구 절차와 검토 대상 전체 HEAD SHA를 기록한다.
2. 작성자와 다른 Reviewer가 해당 HEAD를 검토하고 자신의 계정으로 **PASS 또는 HOLD**, 판단 근거와 잔여 조치를 남긴다. AI가 작성한 검토 초안이나 Bot 승인만으로 독립 기술 검토를 완료하지 않는다.
3. 새 커밋이 추가되면 변경 영향과 관련 회귀 결과를 확인하고 **새 HEAD에 대한 판정**을 기록한다. 이전 HEAD의 PASS를 새 HEAD 승인으로 전용하지 않으며, 변경과 관계없는 검증을 일률적으로 반복하지 않는다.
4. 최종 승인자 **이권욱**은 동일 HEAD의 독립 검토·검증·잔여 조치를 확인하고 **APPROVED 또는 HOLD**를 기록한다. 명시적 최종 승인 전에는 병합하지 않는다.
5. 병합 직전에 HEAD 일치를 확인하고 승인된 절차에 따라 이권욱이 병합한다. PR, 승인 HEAD와 병합 커밋을 기록한다.
6. 실서버 반영은 대상·순서·영향·중단 조건·복구 절차·고정 커밋이 명시된 Runbook과 별도 실행 승인에 따른다. PR 병합이나 Gate 0 PASS를 실행 승인으로 전용하지 않는다.

위 1~5의 Reviewer PASS·최종 승인 절차에는 아래 사전 검증(Pre-check) 코드 예외가 적용된다.

### 사전 검증(Pre-check) 코드 예외 — 2026-09-23

적용 기준: 프로젝트 중간 결과 회고에 따른 2026-09-23 KST 사용자 정책 변경 안내. 상세 기준은 [중앙 리뷰 권한 정책 §3.2](https://github.com/321Team/fds-project-baseline/blob/49278c189dacb620e81d282c6401faa2a9c677a6/governance/REVIEW_AUTHORITY_POLICY.md)를 따른다. 중앙 [PR #93](https://github.com/321Team/fds-project-baseline/pull/93)이 2026-09-23 15:02 KST에 병합(`49278c1`)되어 이 절을 적용한다.

| 단계 | 검토와 병합 |
|---|---|
| Pre-check 코드 병합 | 사전 검증 단계의 점검·수집·판정용 스크립트·검증기와 그 시험 코드는 별도 코드 리뷰 없이 책임 티켓의 담당 Lead 권한으로 즉시 병합할 수 있다. |
| 운영 서버의 순수 검증기 실행 | 아래 조건을 모두 충족하면 Runtime 적용으로 보지 않는다. 리뷰 없이 담당 Lead 판단으로 운영 서버에서 실행할 수 있다. |
| 운영(Production) 서버 Runtime 적용 | 운영 서버에서 실행·설치·배포·영구 반영하거나 운영 배포 경로에 편입하는 시점부터 코드 리뷰가 필수다. 순수 검증기 실행은 제외한다. |

- **Pre-check 병합 기록.**
  - PR은 계속 사용한다. main 직접 push·force push·자동 병합은 금지다.
  - PR에 `Pre-check` 분류, 책임 티켓, 병합 SHA, 담당 Lead, KST 시각을 남긴다.
  - 리뷰 상태는 `REVIEW=NOT PERFORMED (Pre-check)`로 표기한다. 이 병합을 리뷰 완료, Runtime PASS, AC 인수로 표시하지 않는다.
- **운영 적용 전 절차.**
  - 적용할 SHA를 고정한다.
  - 직전 독립 리뷰 SHA 이후 전체 diff를 작성자 외 적격 검토자가 리뷰한다. 리뷰 이력이 없으면 적용 대상 코드 전체가 범위다.
  - Lead는 리뷰 완료 SHA와 적용 SHA의 일치를 실행 기록에 남긴다.
  - 리뷰 후 수정 PR은 일반 코드 PR 절차를 따른다.
- **순수 검증기 실행(운영 서버 완화).** 다음을 모두 충족해야 한다. 하나라도 벗어나거나 확인할 수 없으면 Runtime 적용이다.
  1. 대상 서버의 설정·파일·패키지·서비스·방화벽/네트워크·데이터·계정/권한을 변경하지 않는다. 재시작·reload·캐시 초기화도 변경에 포함한다.
  2. 서버에 설치·상주하거나 cron/systemd 등에 등록하지 않는다. 1회 실행 후 종료한다.
  3. 결과는 실행자 작업 디렉터리나 지정된 수집 경로에만 남긴다. 그 결과를 운영 설정·배포 입력으로 소비하지 않는다.
  4. 이미 승인된 접근 계정·권한 범위 안에서 실행한다. 새 계정·권한·Secret을 만들거나 수집 자료를 승인 범위 밖으로 반출하지 않는다.
  5. 운영 가용성·성능에 영향을 줄 수 있는 점검은 제외한다. 예: 부하 시험, 장애 주입, 대량 조회·스캔.
  - 실행 전 Lead가 코드에 쓰기·재시작·설치·등록 명령이 없는지 확인한다. 이 확인은 독립 코드 리뷰가 아니다.
  - 대상 서버, 실행 SHA, 명령, 계정, KST 시각, 판단 근거, 결과 경로를 기록한다.
  - 결과는 사전 검증 판정이며 운영 적용·AC 인수가 아니다.
- **대상 외(기존 독립 리뷰 유지).**
  - Pre-check 코드와 운영 Runtime 코드·manifest·IaC·배포 설정이 섞인 혼합 PR
  - 병합·Release 게이트 CI workflow
  - 시크릿·무결성 등 보안 통제 코드
  - fds-project-evidence의 모든 PR
- **유지 통제.** 비밀정보 커밋 금지, 필수 AC·시험·보안 기준, 운영 적용 시 선행조건·중단·복구 기준은 완화하지 않는다.

이 저장소에서 Pre-check로 병합한 코드도 실서버 적용 전에 위 2~4의 독립 검토·최종 승인을 해당 SHA에 대해 기록한다. 순수 검증기 실행은 예외다. 6의 Runbook·실행 승인은 그대로 적용한다.

`main` 직접 push와 force push는 변경 관리 절차에서 금지한다. GitHub에서 README를 포함해 새 저장소를 생성하는 초기화 이후, 이 초기 관리 문서도 작업 브랜치와 PR로 편입한다.

현재 프로젝트의 GitHub Free 조직·Private 운영 기준에서는 브랜치 보호와 Ruleset을 통한 필수 리뷰 강제를 전제로 하지 않는다. 위 절차는 사람이 확인하는 운영 통제이며, 실제 설정 완료나 기술적 강제 상태로 표시하지 않는다. `Write` 역할에는 push와 병합 권한이 있으므로 최종 병합 담당자 제한도 운영 규칙으로 준수한다. [브랜치 보호 지원 범위](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches), [Ruleset 지원 범위](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets), [역할별 권한](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization).

## 변경 영향과 검증 범위

| 변경 영역 | 검토할 영향과 증적 |
|---|---|
| 저장소 문서·경로·분리 관리 | 링크, 상위 소스 SHA, 관리 책임, 실행 진입점 참조. 저장소 분리 자체만으로 운영 설정을 재적용하지 않는다. |
| 런타임·Kubernetes 패키지·서비스 | 대상 노드의 패키지·서비스·설정 변경, 재실행 시 동작, SELinux, 사전 요구사항과 복구 절차 |
| 공통 OS·SSH·DNS·Inventory | `infra-ansible`에서 원본 변경을 검토하고 두 저장소의 호출·연결 영향을 함께 평가한다. 전체 노드 간 조건이 바뀌면 해당 6대 검증을 수행한다. |
| 클러스터·Calico·방화벽 | 관리 접속, API·DNS·이미지 공급 경로, 보호 정책 유지, 노드별 전환·중단·복구, 재부팅 후 상태 |

전체 Gate 0를 다시 실행하는 경우 승인된 6대를 대상으로 한다. 일부 노드 접속 확인은 별도 점검으로 기록하며 전체 Gate 0 PASS로 합산하지 않는다. 재시험은 관련 변경이나 현재 상태 의심 등 근거가 있는 범위에서 수행한다.

## 소스와 증적

- 소스·검토된 설정·실행 및 복구 문서·검증 코드·민감정보를 제거한 결과 요약을 관리한다.
- 암호, 토큰, 개인키, 자격증명이 포함된 kubeconfig, kubeadm join 토큰·certificate key, 환경별 비밀 입력과 원본 운영 로그는 커밋하지 않는다.
- 원본 증적은 승인된 보관 위치에 유지하고 PR에는 식별자·SHA-256·검토 결과·접근 가능한 참조를 기록한다. 별도 증적 저장소에 편입할 때도 해당 검토 절차를 따른다.
- 리드 전달안 v4.0은 문서·코드 검토 입력이다. 편입 시 원본 출처·해시와 수정 범위를 기록하고, 미확정 실측값을 임의로 채우거나 자동 실행하지 않는다.
- 이 초기 구성에는 Actions workflow나 실서버 실행기가 없다. CI/배포 자동화는 목적·권한·실행 조건을 검토하는 후속 PR에서 추가한다.

## 초기 파일

| 경로 | 용도 |
|---|---|
| `README.md` | 관리 범위·승인·영향 검토 기준 |
| `.github/PULL_REQUEST_TEMPLATE.md` | HEAD, 영향, 검증, 승인 및 실행 범위를 기록하는 PR 양식 |
| `.gitignore` | 로컬 인증·원본 증적·생성 파일의 기본 제외 규칙 |
| `docs/GATE0_HANDOFF.md` | Gate 0 완료 근거와 후속 인수 범위 |
| `docs/UPSTREAM_LOCK.json` | 검토된 상위 저장소 및 정본 참조 |

실제 구현 PR에서 필요한 `playbooks/`, `roles/`, `manifests/`, `scripts/`, `tests/`, `docs/runbooks/` 경로를 추가한다. 빈 실행 파일이나 승인되지 않은 설치 명령을 초기 구성에 넣지 않는다.
