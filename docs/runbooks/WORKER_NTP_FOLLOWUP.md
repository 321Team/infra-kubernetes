# Worker 방화벽 · 6대 NTP 후속 수집 v1.0

2026-09-10. 주관 #14 이권욱, 클러스터 연계 #10 이하영. PR #2의 CP1 변경 검토와 병행하는 읽기 전용 작업이다.

## 실행 범위

| 대상 | 수집 내용 |
|---|---|
| CP 3대 | SSH·호스트·ens160 IP identity, chrony tracking/sources, NTP source 설정, CP→Worker TCP 10250 각 3회 |
| Worker 3대 | 위 NTP 정보, SELinux, firewalld runtime/permanent 전체 zone/policy/direct, nft ruleset, NM 연결 UUID/zone, route/listener/service 상태, Worker→LB TCP 6443 각 1회 |

총 TCP 시험은 12회다. 실제 TCP 연결을 생성했다가 닫으며 애플리케이션 인증·API 요청을 보내지 않는다. NTP는 기존 chronyd의 로컬 상태만 조회한다. 신규 NTP 서버에 능동 시험을 하거나 현재 시간을 보정하지 않는다. 설정 쓰기, firewall reload/off, 서비스 재시작, join/reset, 패키지 설치 기능은 없다. 원격 파일 생성 없이 기존 sudo Python으로 수집하고 결과 파일은 ansible01에 저장한다.

CP는 .102/.103/.104, Worker는 .105/.107/.108, LB는 .109다. Inventory는 `/home/server01_ansible/p1-ans-01`의 기존 `inventory/hosts.yml`을 조회한다. 승인된 infra-ansible HEAD `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`, 두 그룹·IP·사용자 일치와 기존 SSH 키 지문·known_hosts를 검사한다. 다른 HEAD이면 임의 완화하지 않고 STOP 증적을 검토한다.

## 실행

배포 ZIP과 `.sha256`을 ansible01의 `/home/server01_ansible`에 두고 **server01_ansible 계정으로 sudo 없이** 실행한다. 바깥 체크섬을 확인한 후 압축을 해제한다. Python 표준 라이브러리와 이미 설치된 ssh, git, ansible-inventory만 사용하며 설치 작업은 없다.

```bash
(
  set -euo pipefail
  cd /home/server01_ansible
  sha256sum --check FDS_Worker_Followup_Kit_v1.0.zip.sha256
  python3 -m zipfile -e FDS_Worker_Followup_Kit_v1.0.zip .
  python3 FDS_Worker_Followup_Kit_v1.0/scripts/run-worker-followup.py inspect
)
```

기존 동명 키트에 사용자가 수정한 파일이 있으면 새 디렉터리에 해제한다. 보통 수분이 걸리며 노드별 진행을 출력한다. 실패·타임아웃도 가능한 범위에서 결과에 남기며 마지막 `ARCHIVE=`와 `SIDECAR=` 두 파일을 제출한다. Exit 2도 자동 재실행하지 말고 생성된 증적을 먼저 검토한다. 사전 검사에서 실패해도 결과서와 체크섬을 만든다.

## 판정

- `COLLECTED_REVIEW_REQUIRED`: 예정한 6대의 조회 결과가 빠짐없이 수집됨. 포트·NTP 정책 승인이나 운영 PASS를 의미하지 않는다.
- `INCOMPLETE_REVIEW_REQUIRED`: 조회 실패, 잘린 출력, 설정 참조 미해결, 누락 노드 등이 존재. 실패 기록을 근거로 필요한 부분만 보완한다.
- `STOP_REVIEW_REQUIRED`: 로컬 소스·Inventory·키 등 사전 검사 실패 또는 중단. 기존 설정을 수정하지 않는다.
- `tcp_connected`는 별도 판단한다. TCP 실패 시 같은 시각 Worker의 listener, kubelet 상태, 방화벽 zone/policy/nft 경로를 함께 본다. `ECONNREFUSED`/`EHOSTUNREACH`만으로 단일 원인을 확정하지 않는다. 연결 성공도 kubelet/API 인증·정상 서비스의 증거는 아니다.
- `chrony_sources_numeric`와 `chrony_sources_original`, `chrony_config`를 비교한다. 같은 pool이어도 노드별 선택 서버가 달라질 수 있으며 관측 IP를 승인된 고정 NTP로 간주하지 않는다. 수집은 순차 실행이므로 각 노드의 UTC 시각을 보존한다.

NTP 설정 읽기는 root 소유·안전한 권한의 `/etc/chrony.conf`, `/etc/chrony.d/*.{conf,sources}`, `/run/chrony-dhcp/*.{conf,sources}`에 한정한다. 출력에는 source/reference 지시어와 파일 해시만 남긴다. 다른 include는 따라가지 않고 미해결 참조로 기록한다. 키 파일·kubeconfig·개인키 본문을 수집하지 않는다. 원본 소스 옵션 전체를 보관하지 않으므로 NTS·인증 세부 설정의 최종 감사에는 추가 증적이 필요할 수 있다.

방화벽 active policy 출력은 원문으로 보존하며 `ANY`/`HOST`를 policy 이름으로 재해석하지 않는다. policy 목록은 runtime/permanent의 `--list-all-policies` 전체 원문으로 확보한다. 명령 미지원도 누락으로 남긴다.

## 제출 검토 순서

1. Archive/sidecar와 내부 SHA256SUMS 전체 범위, 6개 노드 identity, 고정 소스 HEAD를 대조한다.
2. Worker 방화벽 실제 적용 경로와 CP→Worker 9개, Worker→LB 3개 결과를 매핑한다.
3. 6대의 configured/selected NTP, Leap status·offset·stratum·reach를 표로 비교한다.
4. #14에 필요한 최소 정책 후보와 NTP 승인 공란을 정리하고 #10에 클러스터 잔여 조건을 연결한다. 자동 apply나 이슈 종료는 하지 않는다.

공식 조회 동작: [chronyc](https://chrony-project.org/doc/4.6/chronyc.html), [chrony.conf](https://chrony-project.org/doc/4.6/chrony.conf.html), [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html).
