# Controller UTF-8 locale 보완 — 2026-09-14

CP2 첫 후속 inspect는 ansible-inventory 시작 단계에서 STOP_REVIEW_REQUIRED로 중단됐다. 원격 접속 전 단계이며 Runtime/Permanent 모두 not_attempted, TARGET/CP1_STATE/INSPECTION은 생성되지 않았다. 현장 CP2 상태나 통신 실패를 판정할 수 있는 수집물은 아니다.

현장 archive `FDS_Node_Firewall_dm8_apz3.tar.gz` SHA-256: `2e233102ea4fd1149f9737c6a0d3ef384c943d4faf89fd70490a2db9c32e8c7e`. 외부 및 내부 POLICY/RESULT 무결성 확인. 실패 소스는 승인된 PR #8 HEAD `27ff0fbabdd0df211813769951e49b307174ca38`이다. 오류는 `Ansible requires the locale encoding to be UTF-8; Detected None.`이다.

## 원인과 변경

run-node-firewall.py 및 run-worker-followup.py의 controller_env()가 LANG/LC_ALL을 C로 강제했다. [Ansible 2.16.19 initialize_locale](https://github.com/ansible/ansible/blob/v2.16.19/lib/ansible/cli/__init__.py)은 locale.setlocale(LC_ALL, '') 뒤 locale.getlocale()의 encoding이 UTF-8인지 검사하므로 C 환경은 중단된다. Python UTF-8 모드나 상위 셸 export만으로 이 강제 설정을 해결할 수 없다.

두 controller의 정제된 자식 프로세스 환경을 C.UTF-8로 변경한다. 기존 HOME/USER/LOGNAME/PATH 허용 목록과 ANSIBLE_CONFIG의 명시적 지정, Python 격리 옵션, inventory/source/key 검증은 유지한다. 원격 helper·방화벽 정책·CP1 생산자 계약·승인/작업창/인수 검증은 변경하지 않는다. C.UTF-8을 제공하지 않는 실행 환경에서는 오류를 검토하고, 비 UTF-8로 자동 하향하거나 Ansible 검증을 비활성화하지 않는다.

## 검증

| 검증 | 결과 | 환경·한계 |
|---|---|---|
| 수정 전 실제 Ansible 인벤토리 시작 | 두 controller 모두 동일 오류, rc=1 재현 | Linux, ansible-core 2.16.19, 임시 localhost inventory; 서버 접속 없음 |
| 수정 후 실제 자식 locale 및 인벤토리 시작 | 신규 2개 시험 PASS, 두 controller 각각 확인 | 상위 LANG/LC_ALL=C에서도 UTF-8 및 실제 inventory JSON 확인 |
| node firewall 회귀 | 58개 PASS | 모의 방화벽/계약 시험 |
| Worker 수집 회귀 | 21개 PASS | 모의 수집/보호 경계 시험 |
| 전체 관련 시험 | 81개 PASS, skip 없음 | 실제 Ansible 통합 시험 포함. 실서버 CP2 inspect 재실행은 별도 |

처음 로컬 Ansible 설치 전에는 CLI 통합 시험이 skip됐으나, 격리 환경에 2.16.19 설치 후 해당 시험을 다시 수행해 PASS를 확보했다. 과거 실서버 CP1 인수나 CP2 현장 수집 성공으로 확대하지 않는다.

## 현장 재개

새 PR의 고정 HEAD를 별도 디렉터리에 checkout하고 NODE_FIREWALL_SOURCE_SHA256SUMS를 검증한 뒤, ansible01/server01_ansible에서 `python3 -I scripts/run-node-firewall.py inspect --node k8s-control02`를 실행한다. 실패한 파일과 새 결과를 모두 보존하고 ARCHIVE/SIDECAR를 제출한다.

읽기 전용 inspect 재실행에는 새 방화벽 실행 승인을 만들지 않는다. 실제 apply에는 최종 CP1 독립 인수·새 작업창·직전 15분 이내 대상 입력 및 기존 실행 조건이 필요하다. CP1 30분 입력 한도와 혼동하지 않는다. 이번 수정은 19시 종료된 작업창을 연장하지 않는다.
