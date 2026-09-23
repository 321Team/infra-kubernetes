# ICR scrape Source 사전점검

책임: ICR-D-NET-01-01 Lead 이권욱. 2026-09-24 01:21 KST에 수신한
[MON 사전점검](https://github.com/321Team/fds-project-evidence/pull/23)을 잇는다.
운영 HEP/GNP에 9100 허용이 없고 10250은 CP3개만 허용된 상태에서, 실제 scrape 패킷의
Source와 양쪽 관측·기존 커널 규칙을 수집한다. 새 연결을 생성하거나 정책을 적용하지 않는다.

## 순수 Pre-check 분류

중앙 REVIEW_AUTHORITY_POLICY §3.2의 순수 검증기다. **REVIEW=NOT PERFORMED (Pre-check).**
기존 ansible01/server01_ansible → inventory svc_ansible 접근·sudo 권한만 사용하며,
설정/서비스/방화벽/route/NAT/패키지/권한을 바꾸지 않는다. 임시 Python은 stdin에서 종료한다.
결과는 ansible01 새 작업 디렉터리만 기록하고 운영 설정 입력으로 소비하지 않는다.
대량 조회·부하·장애 시험·probe·TLS 우회·kubectl exec는 없다.

1. control01에서 고정 Prometheus의 UID/PodIP/hostIP/node/SA/imageID/Ready를 전후 대조한다.
   변경되면 STOP, 같은 이름의 새 Pod를 자동 채택하지 않는다. Pod env/args/annotations는 저장하지 않는다.
2. worker03(10.1.93.108)과 worker01(.105)의 ens160 주소·UP 상태, 도구/기존 권한을 확인한다.
   worker03의 PodIP 10.244.39.219 경로가 직접 연결 Calico veth여야 한다.
3. 두 Worker를 동시 실행한다. worker03 ens160 및 해당 veth(2개), worker01 ens160(1개)에서
   인터페이스당 최대35초/64패킷 TCP SYN/RST 헤더를 관측한다. 목적/반환 대상 .105의
   TCP9100/10250만 필터링하며 destination 관측 Source를 미리 제한하지 않는다.
4. tcpdump는 `-p`(promiscuous mode 요청 없음), `-s 96`; -A/-X/-w 없이 텍스트 헤더만 출력한다.
   pcap 또는 요청/응답 payload를 저장하지 않는다. `sudo -n timeout`이35초 후 INT,
   2초 후 필요 시 KILL로 종료한다. 도구 미설치·sudo 거부 시 설치/권한 변경 없이 STOP한다.
5. 기존 ss listener·ip route get·iptables-save filter/nat counter를 읽는다. counter reset은 없다.
   iptables-save가 없거나 조회가 실패하면 그 한계를 기록한다. 다른 dataplane의 모든 정책을
   iptables 출력으로 대체하지 않으며 eBPF/nft 상태 전체를 수집했다고 표시하지 않는다.

## 실행

기존 `/home/server01_ansible/p1-ans-01` checkout에서 기존 env·ansible.cfg를 사용한다.
동일 source SHA의 collect.py/collect.yml/stage.yml/run.sh/SHA256SUMS를 내려받아 해시 확인 후:

```bash
bash /가져온경로/run.sh "$PWD/inventory/hosts.yml" SOURCE_SHA
```

run.sh는 Ansible syntax-check 성공 후에만 원격 실행한다. 결과는
`ICR_Scrape_Source_XXXXXX.tar.gz`와 `.sha256`이다. 실패 묶음도 보존한다.
controller rc=0 및 전후 guard 통과 여부를 확인한 뒤 내용을 분석한다.
관측창은 노드별 시각을 기록하지만 완전 동기화 또는 서로 겹친 구간은 실행 전 보장하지 않는다.
패킷 tuple/sequence/시각과 veth→ens160→destination 관측을 대조해야 한다.

## 판정 한계와 후속

- 0 packets는 정상 통신/차단/Source 부재의 증거가 아니다. 누락·구간 불일치·도구 오류를 먼저 확인한다.
- counter 증가만으로 해당 Prometheus 패킷이 원인이라고 단정하지 않는다. 패킷과 실제 rule 경로를 대조한다.
- veth에서 실제 Pod의 SYN과 ens160의 동일 연결을 확인하면 SNAT 유무를 대조할 수 있다.
  worker IP를 관측한 사실만으로 worker의 모든 workload를 승인 Prometheus로 취급하지 않는다.
- source/정책 보완 후에도 kubelet serving 인증서 IP SAN과 CA+SA 인증은 별도 검증한다.
- 이 묶음은 **IF-06(9100/10250) 진단**이다. IF-06B monitor01:443의 수신 Source/return path,
  Remote Write·TLS/Auth·Payload·내부 SNAT 금지 또는 AC PASS를 입증하지 않는다.
- 정책 변경은 K8S/SEC·MON과 최소 범위를 정한 뒤 독립 코드 리뷰한 SHA로 별도 적용한다.

## 검증

로컬 Python AST·Bash syntax·YAML parse, guard/비밀필드 제외/인터페이스·route 불일치/
capture 시간·출력 제한 및 오류 보존에 대한 MOCK unittest7개와 controller 정상/구문 실패 중단 MOCK2개를 확인했다. 로컬에 Ansible이 없으므로
실제 Ansible syntax-check·운영 capture는 NOT RUN. 현장 실행기에서 syntax-check를 강제한다.
MOCK는 패킷 수집이나 실제 sudo 권한의 검증이 아니다.
