# CP1 최소 CNI 변경 — 독립 리뷰용 실행 계약

이 문서는 새 코드의 검토·실행 계약이다. 현재 CP1 적용이나 인수를 증명하지 않는다.
`run-cp1-minimal-change.py`의 무인자 실행은 PLAN_ONLY이며, 실제 적용은 새 소스 리뷰와 명시된 작업창 입력이 필요하다.

## 대상과 변경

- 대상: `k8s-control01`, firewalld **2.4.3 / nftables / SELinux Enforcing**, 확인된 단일 local block `10.244.246.64/26`.
- 새 zone `fds-cp1-min`에 **Runtime `cali+`** 하나를 연결한다. `+`는 zone 인터페이스 조건에서 nft의 `*`로 변환된다.
- `fds-cp1-local`: ingress/egress 모두 새 zone, IPv4 source/destination 모두 위 `/26`, 모든 L4 허용. 새 cali veth도 범위에 들어간다. TCP8080 전용 진단 예외와 다른 운영 후보 범위이므로 별도 명시적 검토가 필요하다.
- 새 zone의 **forward=no**로 무제한 `oifname cali* ACCEPT` 생성을 방지한다. 양쪽 인터페이스와 양쪽 주소 조건의 교집합만 local 신규 허용이다.
- HOST에 쓰이던 public의 `ssh`, `dhcpv6-client`, 기존 7개 rich rule을 복제한다. public 자체와 Calico HEP/GNP/Felix는 수정하지 않는다.
- `fds-cp1-egress`: 기존 public의 cali 유입 → **oif ens160** 전달 허용을 보존한다. `hash:net,iface` 목적지 집합을 IPv4/IPv6 각각 두 `/1`로 구성하여 실제 `daddr . oifname` 조건을 사용한다. ANY egress 정책이라는 이유로 다른 oif를 모두 ACCEPT하지 않는다. HOST INPUT/OUTPUT에는 이 정책을 적용하지 않는다.
- 기존 Calico가 앞선 hook에서 DROP하는 패킷을 이 규칙으로 허용한다고 주장하지 않는다. full nft ruleset 전체 동치나 실제 HOST/외부 통신 보존은 아직 실측하지 않았다.

## 수명과 복원

작업창은 KST 평일, 최대 2시간이다. 종료 180초 전에 한 개의 transient systemd timer/service가 같은 root 보관 소스의 `recover --job`을 실행한다. 상시 watcher/부팅 service와 #13 서비스 확장은 추가하지 않는다.

새 객체를 생성하기 위해 **Permanent에 비활성 정의 5개를 잠시 쓰는 단계가 있다**. Runtime-only 활성화와 Permanent 무변경은 같은 뜻이 아니다. `inert_permanent_staging_acknowledged=true`를 포함한 검토·실행 결정 없이는 시작하지 않는다.

1. 원 CP1 receipt·fresh inspect·소스·실제 전후 대조, root 전용 journal/소스 보관, 독립 회수 예약.
2. 인터페이스·source binding 없는 새 zone/정책/ipset을 Permanent에 준비한다.
3. reload 후 기존 public Runtime 6개를 복원하고 전체 상태를 대조한다. 다른 Runtime 객체나 public service 정의가 Permanent와 다르면 **첫 변경 전에 중단**한다.
4. 새 Permanent 정의를 의존성 역순으로 삭제하고, Permanent가 원래 상태와 같은지 검증한다. 이후 reload 없이 Runtime `cali+`만 활성화한다.
5. 복원은 Runtime binding 제거 → 남은 소유 Permanent staging 삭제 → reload/원 6개 복원 → whole baseline 일치 → timer 해제 순서다.

부분 변경과 응답 유실은 같은 journal로 복원한다. foreign drift 시 맹목적으로 reload하지 않는다. 소유 Runtime 객체가 정확히 일치하면 그 binding만 먼저 내리고, 나머지는 콘솔 검토로 남긴다. 소유 객체가 변했거나 보호 상태 조회 자체가 실패하면 자동 복원 성공을 주장하지 않는다. 재부팅 시 cali binding은 활성화되지 않지만, boot identity가 바뀐 원 6개 복원/정리 판정은 콘솔 검토가 필요하다. 주기적 재시도/부팅 자동 복원은 이 최소 구현 범위에 없다.

## 실행 입력과 계정

일반 계정으로 접속하고 필요한 명령에 `sudo /usr/bin/python3 -I -B`를 사용한다. root 계정 로그인은 필요 없다. 원 receipt, 승인 JSON, 검사 JSON은 일반 파일과 그 SHA256을 함께 제공한다. 중복 JSON key·심볼릭 링크·해시 불일치는 차단한다.

검토 전 가능한 로컬 명령:

```bash
python3 -I -B scripts/run-cp1-minimal-change.py
python3 -m unittest discover -s tests -v
```

소스 승인 후 실행 순서:

- `inspect --original FILE --original-sha256 SHA`: root로 읽기만 수행, 원 receipt.after와 전체 현재 상태가 같은지 검사한다. 생성 시각 15분 이내 검사만 apply에서 수용한다.
- `apply`: 위 두 원본 옵션 + `--inspection FILE --inspection-sha256 SHA` + `--authorization FILE --authorization-sha256 SHA`. 빈 승인 양식은 실행되지 않는다. 새 `JOB_ID`와 root journal을 보존한다. 이전 미회수 작업이 있으면 새 작업을 차단한다.
- `status --job ID`, `recover --job ID`: 동일 보관 소스만 허용한다. 새 checkout으로 다른 버전의 작업을 회수하지 않는다.
- root 작업 경로: `/var/lib/fds/cp1-minimal/ID/`. `CHANGE.json`, `SOURCE.json`, `ORIGINAL.json`, `AUTHORIZATION.json`, `commands.jsonl`을 보존한다. 원문 NM/boot 식별자는 공개 GitHub에 게시하지 않는다.
- 적용 후 생성되는 `expected-firewall.json`을 기존 p3dmjivl trial kit의 **새 입력 사본**으로 사용한다. 기존 trial Python 코드는 유지하고 새 입력 해시를 기록한다. 과거 REQUEST/RESULT/receipt는 수정하지 않는다.
- 실제 기존 5개 흐름 종료·namespace 삭제 확인 후 `issue-receipt --job ID --trial ARCHIVE --trial-sha256 SHA`. 검증 완료 archive만 새 `HANDOFF.json`으로 연결한다. 출력은 인수 대기이며 자동 APPROVED/ACCEPTED가 아니다.

배포 ZIP은 코드·실행 범위 리뷰 후 고정 소스로 준비한다. 현재 문서의 검토 후보를 즉시 VM에 실행하도록 안내하는 패키지는 만들지 않았다.

## 새 receipt와 후속 소비자

원 CP1 생산자 `cp-canary-node.py`, 기존 `node-firewall.py`/`run-node-firewall.py`는 동일 바이트다.

`fds.cp1-minimal-handoff.v1`은 원 receipt hash, 새 변경 before/after/hash, 검증된 trial report와 원 wrapper hash를 연결한다. 실제 probe stdout/HTTP 본문/DNS/Pod UID/Service/정리 시각/전후 일치를 재계산하고, 두 Pod 주소가 검토된 `/26` 안에 있어야 한다. 과거 3/5 증적으로 발급하지 않는다.

새 정식 인수는 별도 `fds.cp1-minimal-acceptance.v1`이다. 독립 리뷰어, source bundle, 새 handoff hash, positive/negative/regression 증적 hash, 인수 시각과 만료 시각을 결합한다. 체크섬과 JSON의 이름/boolean은 GitHub 승인 사실의 인증 수단이 아니다. 운영자와 리뷰어가 실제 댓글·증적을 확인해야 한다.

`run-node-firewall-cni.py`는 기존 controller의 별도 어댑터다. `--cp1-receipt`, `--cp1-acceptance`에 더해 새 `--cp1-handoff`, `--cp1-trial`과 각각 SHA256을 받는다. 원본 receipt validator와 기존 대상별 승인·정책·SSH identity·global lock·topology·whole CP1 fingerprint 검사를 유지한다.

적용 전/후에 새 인수와 원본 trial을 연결한 계약을 확인하고, CP1 root 보관 소스의 read-only status에서 **전체 after-state·job/source/record hash·timer·만료**를 대조한다. 적용 전 최소 900초가 남아 있어야 한다. 적용 중 CP1 보호·소유 객체·수명이 바뀌면 최종 성공 판정을 중단한다. CP2/CP3 작업의 원래 PR4 승인 입력, Join 금지, Permanent promotion 미구현 조건도 유지한다. 이 어댑터가 후속 작업을 자동 시작하지 않는다.

## 현재 검증 범위

- 관련 130/130 STATIC/MOCK PASS: 기존 94 + 새 36. 불완전/응답 유실 변경 지점 12개, 복원·drift·window·receipt·실제 어댑터 함수 흐름을 대역으로 실행.
- firewalld v2.4.3 hash-pinned 함수 실행 + 로컬 패킷 모델 21조건 PASS. 실제 XML parser/D-Bus daemon/nft kernel/서비스 timer는 미실행이다.
- 과거 실제 p3dmjivl 자료 재생: **3/5, INCOMPLETE_REVIEW_REQUIRED**, consistency issue 0. 새로운 실측 결과가 아니다.
- 실제 CP1 적용·5/5·HOST/외부 회귀·새 인수·CP2/CP3 적용·Join·CI PASS·병합은 미수행이다.

필요한 다음 검토는 이 최소 변경의 소스/범위/임시 staging/유지·복원/새 인수 계약 한 묶음이다. 전체 #11 qualification이나 #13 확장을 새 필수조건으로 추가하지 않는다.
