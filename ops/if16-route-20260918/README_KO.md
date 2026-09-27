# IF-16 누락 경로 보완 — db01 NIC 바인딩 수정 후보

PR #18의 호스트 공통 `ens160` 가정을 수정한다. db01의 `10.1.93.55/24`는 `ens192`에 있고, `ens160`에는 별도 `10.250.40.20/24`가 있다는 현장 자료에 따라 호스트별 NIC를 고정한다. 이 수정 후보의 독립 코드 리뷰·운영 배포·서버 실행은 아직 미수행이다. 기존 승인본/배포 묶음은 덮어쓰지 않는다. ICR Lead의 실제 실행·인수 결정과 코드 리뷰를 구분한다.

## 범위

| 호스트 | IPv4 | 보완할 경로 |
|---|---|---|
| edge01 | 10.1.93.50/24 | 10.250.10.0/24 via 10.1.93.58 dev ens160 |
| harbor01 | 10.1.93.54/24 | 10.250.10.0/24 via 10.1.93.58 dev ens160 |
| db01 | 10.1.93.55/24 | 10.250.10.0/24 via 10.1.93.58 dev ens192 |

현재 호스트의 이름·위 표의 NIC·그 NIC의 단일 IPv4 /24·활성 NetworkManager ethernet 프로필이 일치해야 한다. NIC 자동 탐색이나 다른 NIC로의 fallback은 없다. db01의 storage `ens160` 주소·프로필·경로는 대상에 포함하지 않는다. 대상 경로가 없을 때만 추가한다. 기존 경로가 다른 gateway/device/table을 사용하거나 중복·중첩 경로·복수 프로필·정책 라우팅이 있으면 변경 전에 중단한다. 이미 정확한 경로가 존재하면 변경하지 않는다. 신규 경로의 metric은 두 상태가 모두 없을 때 100이며, 한쪽이 존재하면 확인 가능한 기존 metric을 사용한다.

새 Persistent 경로에만 `table=254`를 명시하여 NetworkManager `ipv4.route-table=0`의 전역 기본값에 의존하지 않는다. 프로필 전체 route-table·gateway·DNS·기존 route는 변경하지 않는다. 기존 Persistent 경로의 table이 암묵적이고 프로필 table도 0이면 해석이 모호하므로 중단한다. 이를 해결하기 위해 프로필 전역값을 임의로 변경하지 않는다.

`nmcli -g ipv4.routes`의 upstream non-PRETTY 출력(예: `10.250.10.0/24 10.1.93.58 100 table=254`)을 파싱한다. PRETTY 중괄호 출력, 알 수 없는 속성·형식은 거부한다. 실서버 NetworkManager 버전의 출력 검증은 아직 미수행이다.

## db01 현장 결과와 SSH/SFTP의 구분

제공된 현장 출력에서 db01의 Runtime `10.250.10.0/24 via 10.1.93.58 dev ens192 metric 101`과 Ansible `.10` 방향 `src 10.1.93.55` route-get이 이미 확인됐다. 이 사실만으로 Persistent 프로필 저장·재부팅 지속성·SSH/SFTP 접속 성공·AC13 전체 PASS를 선언하지 않는다. 기존 정상 Runtime 경로는 보존하며, Runtime과 Persistent 모두 검증 가능한 상태로 존재하면 네트워크 명령 없이 `ALREADY_CORRECT`로 끝난다. 기존 Persistent table이 암묵적이고 프로필 table도 0이면 이 수정 후에도 중단한다.

원본 PR #18 소스(`4c4190e…42c8ec`)에 현장 두 NIC 주소를 넣은 MOCK에서는 첫 `ens160` 주소 조회 후 `Expected single host IPv4 /24 on UP ens160`으로 중단하며 route 변경 명령이 발생하지 않았다. 이는 db01 적용 대상 식별 결함의 재현이며, 과거 실제 실행 결과를 대신하지 않는다. 기존 도구가 SSH 장애를 만들었다고 이 재현으로 결론 낼 수 없다.

이 도구는 firewalld source allow·zone·sshd·SSH 계정/키·SFTP 설정을 읽거나 변경하지 않는다. NIC 수정은 SSH/SFTP 차단 원인을 확인하거나 해결한 결과가 아니다. 방화벽 문제는 실제 유입 source와 서비스/정책 상태를 별도로 확인해야 한다.

## 실행 인터페이스

기본 동작은 로컬 호스트 읽기 전용 plan이다. SSH나 계정·키 설정은 포함하지 않는다. 각 호스트에 이미 승인된 접속 수단으로 로그인한 운영자가 실행한다. 계정명을 추정하지 않는다.

```bash
python3 -I -B if16_route.py
```

독립 코드 리뷰와 실제 실행 결정 이후의 적용 진입점:

```bash
sudo -n /usr/bin/python3 -I -B if16_route.py --apply
```

별도의 inspect 파일 업로드·authorization 생성·타이머 입력은 없다. `--apply`가 같은 실행에서 현재 상태를 읽고, 원본 영수증을 저장하고, 직전 상태를 대조한 뒤 필요한 추가만 수행한다. 같은 호스트에 다른 네트워크 변경을 병행하지 않는다.

1. root 소유 0600 JSONL 영수증에 preimage와 변경 의도를 fsync한다.
2. 누락된 kernel 경로를 `ip route add`로 추가한다.
3. 누락된 활성 프로필 경로를 `nmcli connection modify +ipv4.routes`로 저장한다.
4. 관련·비관련 설정 전후 동일성과 `.30` route-get의 gateway/device/source를 확인한다.
5. 실패하면 현재 상태가 예상한 본인 추가 상태와 일치할 때만 Persistent → Runtime 순으로 본인 추가를 되돌린다.

`connection up/down`, `device reapply`, NetworkManager/firewalld reload/restart, default route 변경, 전체 routes 교체, SNAT, Worker/CP Join, Kubernetes·HEP/GNP 변경은 실행하지 않는다. Worker AC14 기존 PASS를 반복 수집하지 않는다.

## 결과와 제한

- `APPLIED`: 해당 한 호스트의 runtime 경로와 저장된 프로필 경로 및 route-get 확인.
- `ALREADY_CORRECT`: 해당 상태를 확인했고 네트워크 변경 없음.
- `FAILED_RESTORED`: 실패 후 수집한 상태가 preimage와 일치.
- `PARTIAL_REVIEW_REQUIRED`: 명령 timeout 후 소유권 불명확, 타 작업 drift, 회수 명령 실패 또는 복구 확인 실패. 임의 삭제하지 않으며 자동 회수 성공으로 표시하지 않음.

영수증: `/var/lib/fds/if16-routes/<timestamp>_<uuid>.jsonl`. 경로는 변경 시작 전에 출력하며, 원본·추가/보상 의도·결과가 누적된다. 프로필의 선택된 네트워크 속성만 수집하고 Secret·키·비밀번호·전체 연결 설정을 덤프하지 않는다.

이 도구는 외부 NetworkManager 변경을 원자적으로 잠그지 않는다. 자체 실행끼리만 advisory lock으로 겹침을 방지하고, 외부 변경은 전후 비교로 탐지한다. SIGKILL·전원 장애·영수증 저장장치 장애에 자동복구 성공을 보장하지 않는다. 영수증으로 마지막 확정 상태와 미완료 의도를 확인한다.

Persistent 프로필 저장은 실제 재부팅 지속성 성공을 뜻하지 않는다. packet source preservation·mgmt-gw forwarding/NAT·IF06B Receiver/TLS/Auth/Payload·전체 AC 또는 독립 인수를 PASS로 생성하지 않는다. AC13의 세 호스트 결과는 해당 실제 결과를 모아 판정한다.

## 검증

로컬 STATIC/MOCK 32개 통과: 정상·멱등성, 기존 route 보존, Persistent 실패 보상, probe 실패 역순 보상, timeout 후 side effect 소유권 미확정, drift 시 회수 거부, 회수 실패, preimage 저장 실패, 명령 에러 마스킹, 파서 형식·속성, main table 바인딩, db01 두 NIC의 ens192 프로필 선택, 기존 metric 101 경로 보존, ens192 추가/보상, 잘못된 NIC/probe 선중단, edge/harbor ens160 유지 등. 원본 소스의 db01 선중단 MOCK 재현도 별도로 확인했다. 실제 NetworkManager·Kernel·서버 Runtime은 미실행이다.

```bash
python3 -B -m unittest discover -s . -p 'test_*.py' -v
```

## 출처

- [원본 PR #18](https://github.com/321Team/infra-kubernetes/pull/18), 코드 HEAD `11cfd36cff489fe1351afc4f5c5dd97eb3a111e4`
- db01 NIC/Runtime 경로: 2026-09-18 사용자 제공 현장 출력(독립 서버 재조회 미수행)

- [v3.1.19 Interface Register, IF-16](https://github.com/321Team/fds-project-baseline/blob/966fc5365874b1d259a9810fc876b57c3bfa0e08/releases/v3.1.19/FDS_Interface_Decision_CR_Register_v3.1.19.md)
- [ICR #14: AC13 edge/harbor/db 잔여](https://github.com/321Team/fds-project-baseline/issues/14)
- [NetworkManager nmcli — append/remove와 profile/device 동작](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html)
- [NetworkManager ipv4.routes — metric와 table 속성](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html)
- [NetworkManager nmcli route 문자열 직렬화 소스, 58864ef](https://github.com/NetworkManager/NetworkManager/blob/58864ef92d3241787c311fad9a16a28524b206e1/src/libnmc-setting/nm-meta-setting-desc.c)
