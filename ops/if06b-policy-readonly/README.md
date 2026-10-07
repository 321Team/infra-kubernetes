# IF-06B gateway 정책 원본 수집

책임: baseline #14 / GWONUK 생산 → #17 / MON 소비·재판정, #19 입력 정정.
순수 읽기 전용 Pre-check. CODEX_ASSISTED. 독립 REVIEW 미수행.

기존 자료의 공백은 `if06b-p2m` runtime/permanent 원본과 실효 nft 규칙이다.
이번 수집은 10/6 canary나 8443 음성 시험을 다시 실행하지 않는다.
현재 설정만으로 과거 8443 차단 주체·source preservation·AC 충족을 확정하지 않는다.

## 실행 조건과 범위

- mgmt-gw01의 기존 승인 계정/root 권한, Python3와 기존 firewalld/nft/ip/sysctl.
- 사용자 지정 경로는 `server01_mgmt-gw@10.1.93.58`이다. ansible01에서 TCP/SSH는
  도달하지만 현재 관리 키·기본 SSH 인증 모두 거부된다. PC 직접 경로는 TCP22 timeout이다.
  기존 승인 키/별칭 또는 사용자 콘솔 접근을 확인하기 전 수집하지 않는다.
- ansible01의 과거 전용 inventory는 `server01_mgmt-gw@10.250.10.1` 및
  `/home/server01_ansible/if06b-ssh-trust/known_hosts`를 지정한다.
  현 관리 키 인증은 거부됐다. 접속 복구를 위해 키·계정·권한·host-key 검증을 변경하지 않는다.
- 새 root 전용 `/root/if06b-policy-*`에만 결과를 쓴다. 설치·reload·규칙 변경·접속 probe 없음.
- 22개 고정 조회, 각 15초/전체 조회 180초. 실패 rc와 stdout/stderr를 보존한다.
  time budget 후 명령은 실행하지 않고 rc124로 남긴다. 자동 재시도 없음.
- 실행 전 전체 코드 SHA를 확인하고 `sudo -n python3 /절대경로/collect.py`로 1회 실행한다.
  입력이나 승인되지 않은 인자로 대상·명령을 확대할 수 없다.
- rc0: 필수 조회 완료·설정 원문 전후 동일·manifest 생성. 정책 적합 판정이 아니다.
  rc2: 조회 실패/변경 감지. rc12: 기록 실패/중단, 미봉인 원본 보존.
  호스트/권한 부적합은 출력 디렉터리 생성 전에 중단한다.
- 중단 시 운영 복구할 변경은 없다. 생성한 원본은 삭제하지 않고 실행 rc와 함께 제출한다.
  서버 SSH 전송 실패와 collector 실패를 구분한다.

## 검토·전달

수집 폴더 전체와 실행 source SHA/명령/시각/외부 rc를 보존한다. 전송 후 모든 내부
SHA256SUMS를 확인한다. tar sidecar는 수신 bytes의 hash이며 서버 tar와의 동일성을
확인하지 않았다면 서버 hash라고 쓰지 않는다. 결과에는 방화벽 설정·내부 주소가
포함되므로 기존 비공개 evidence 저장소에서 검토한다. 인증 비밀은 조회하지 않는다.

후속 리뷰는 exact allow(`10.1.93.108/32 → 10.250.10.30/32 tcp443`),
physical→management/target DROP, 다른 정책·zone·nft NAT/forward 영향과 원본을 대조한다.
전후 byte 일치는 원자적 snapshot/중간 무변경 증명이 아니다. nft counter는 자연히
변할 수 있어 byte 비교로 PASS/FAIL하지 않는다. 8443 실제 drop 귀속은 별도 증적이다.

Backup #71 및 기존 Probe 승인 번들을 변경하지 않는 별도 수집기다.

## 10/8 실제 수집 및 인증 반복 수정

사용자가 비밀번호/콘솔 경로로 00:54:41~43 KST 수집을 완료했다.
collector SHA `d8c6fdeffbf2bf9585306b7f20118277bc99f757d31e139e7e8b9e9bb7440f4b`,
22조회 rc0, 내부67개 hash 일치, `/root/if06b-policy-1x2k0nr2` 원본을 회수했다.
따라서 위 접근 차단 기록은 당시 비대화형 키 인증의 한계이며 원본 미수집 상태는 해소됐다.

초기 임시 wrapper는 `-o ControlMaster=yes -MNf`를 함께 써서 master를 ask 모드로
전환했다. ansible01의 실제 `ssh -G`에서 기존 `controlmaster ask`, 수정 `true`를
확인했다. [OpenSSH option parser](https://github.com/openssh/openssh-portable/blob/master/ssh.c)의
case M과 일치한다. 서버 SSH 제한 때문이라고 단정하지 않는다.
인증 반복과 master 종료 거부가 발생했지만 각 별도 연결로 조회/전송은 완료됐다.
원본 collector·기존 실패/실행 이력은 바꾸지 않는다.

새 `run-interactive.sh`는 ansible01에서 전체 디렉터리/SHA256SUMS 확인 후 실행한다.
중복 -M을 제거하고 최초 인증 뒤 master 재사용 조회를 먼저 확인한다.
후속 호출은 BatchMode=yes여서 재사용 실패 시 비밀번호 반복 대신 STOP한다.
SSH/sudo 암호는 TTY에만 입력하고 저장하지 않는다. strict host-key를 유지한다.
동일 수집을 재실행할 필요는 없다. 수정 wrapper의 전체 현장 수집은 NOT_RUN이며,
shell 문법/실제 ssh -G 옵션 회귀 검사만 수행했다. 이 스크립트를 쓰는 새 수집은
별도 필요성이 있는 경우에만 실행한다.
