# CP1 inspect 중단 수정 — 비활성 정본 정책 보존

대상 부모 HEAD: `4de08a9fd4b8398154ec8bf7c4cfd2760a3812be`. 새 bundle: `c79a4d27260bc894c528e8c6286b04b6cd2580ef72e3c7199fe4d6a7f3047fe8`.

## 관측과 점검 누락

현장 `inspect`는 `additional policies need explicit preservation analysis` / rc=2로 중단됐다. 첨부 PLAN의 bundle은 승인된 소스와 일치한다. 중복 제출된 두 오류 로그도 동일하다. inspect 경로는 방화벽 변경/job 생성/timer 예약 전에 끝났으며, CP1 적용·새 통신 시험은 수행하지 않았다. ansible01 사전 점검 완료 여부는 이번 첨부로 확인하지 않는다.

검증된 원 fcjv61h8 receipt에는 활성 `allow-host-ipv6` 외에 아래 다섯 정책의 정의가 이미 있다. 기존 단위시험은 정책 한 개만 있는 대역을 사용하여 실제 환경의 비활성 정의를 놓쳤다. 기존 '실행 코드 결함 없음' 점검 결론은 이번 증거로 정정한다.

| 정책 | Runtime / Permanent | 활성 목록 |
|---|---|---|
| gateway-dmz-to-HOST | disable: yes / yes | 없음 |
| gateway-lan-to-HOST | disable: yes / yes | 없음 |
| gateway-lan-to-work | disable: yes / yes | 없음 |
| gateway-lan-to-world | disable: yes / yes | 없음 |
| gateway-world-to-HOST | disable: yes / yes | 없음 |

## 제한된 수정과 보존 논증

- 허용 가능한 추가 이름은 위 다섯 개뿐이다. 양쪽 plane의 disable=yes, Runtime `(disabled)` 표기, 원 receipt의 raw 정책/파싱 결과, 유일한 활성 정책 `allow-host-ipv6`를 모두 대조한다.
- 전체 firewall 원 receipt.after 일치, public 외 Runtime/Permanent 정의 일치, 매 변경 후 전체 상태 비교, 복원 후 whole baseline 일치를 그대로 유지한다. 비활성 정책을 비교 대상에서 빼거나 이름만으로 신뢰하지 않는다. 알 수 없는 추가 정책, 활성화, 삭제, 외부 변경은 중단한다.
- firewalld v2.4.3 [공식 activation 선택 함수](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_policy.py)는 disable된 정책을 zone 활성 여부 검사 전에 제외한다. 보존된 양쪽 disable 값과 신규 정책 활성 집합의 제한이 근거다. 새 zone 바인딩으로 기존 비활성 gateway 정책을 활성화하는 변경은 허용하지 않는다.
- inspect의 완료된 snapshot 검증이 실패하면 stdout에 별도 `fds.cp1-minimal-inspect-diagnostic.v1`과 실패 이유·whole snapshot을 남기고 non-zero를 유지한다. apply는 이 진단 schema를 거부한다. snapshot 수집 자체가 불완전하면 완전한 검사 결과를 만들지 않는다. 원문 진단은 VM에서 보존하며 boot/NM 식별자를 공개 댓글로 게시하지 않는다.
- 정책 변경 명령, /26 양쪽 범위, ALL L4/HOST/ens160 보존, staging 순서, 복원 timer, 원 receipt validator/controller, 새 trial·인수 계약은 변경하지 않았다.

## 검증

- 145/145 단위·모의시험 PASS = 기존 130 + 신규 15, 실패/오류/skip 0. 기존 transaction의 변경 12위치 실패/복원 시험도 실제 증적에서 투영한 정책 6개 및 zone 10개를 포함한다.
- 원 receipt의 불변 validator를 실행한 로컬 재생에서 부모 코드의 같은 오류를 재현하고 수정본 통과를 확인했다. 보호 상태·서비스·boot/IPAM은 대역이며 현재 CP1 전체 live snapshot 재검증으로 주장하지 않는다.
- 선택된 공식 활성화 함수 8조건 PASS: 전/새 zone 연결/모든 참조 zone 활성에서 gateway 5개 비활성 유지, 각 정책의 disable을 해제한 5개 positive control에서 검출. 이는 모델 context에서 원 함수를 실행한 검사이며 실제 reload/daemon/kernel 시험이 아니다.
- XML parser/config/formatter 18조건, 기존 nft builder/패킷 모델 21조건 PASS. 실제 CP1 적용·새 5/5·HOST/외부 회귀·timer 실측·새 인수·CI PASS·병합은 미수행이다.
- fixture에는 정본의 정책/public 정의 및 QP5Lan의 zone 출력만 투영했다. 원 archive/member hash와 경로를 기록하며 UUID/transport 원문을 포함하지 않는다. 원 증적은 수정하지 않았다.

재현: `python3 -m unittest discover -s tests -v`; `python3 tests/probe_cp1_preserved_policies.py --upstream-dir <pinned-v2.4.3-source-directory>`; 기존 XML/packet probe 명령은 앞선 문서와 동일하다.

## 다음 단계

리뷰 요청 범위는 이번 비활성 정책 보존 검사와 실패 진단 출력의 증분이다. 이전 APPROVED는 부모 bundle 대상이므로 새 bundle의 승인으로 바꾸지 않는다. 전체 #11 qualification/#13 확장을 새 선행조건으로 추가하지 않는다. 변경 소스 확인 후 새 패키지의 fresh inspect → 기존 작업창/백업/콘솔/동결 조건 → CP1 최소 변경 → 기존 5개 흐름 → 새 인수 순서를 유지한다. 추가 VM 재수집, 수동 정책 삭제·활성화, 검사 우회는 요청하지 않는다.
