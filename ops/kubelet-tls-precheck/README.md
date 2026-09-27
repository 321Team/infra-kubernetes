# kubelet serving TLS 입력 사전 조회

책임: ICR #14 Source/host 경로 검증 지원, K8S #10·SEC #19 설정/인증서, MON #17 scrape.
분류: **순수 Pre-check**. 운영 manifest·설정·인증서 변경 없음.
운영 정책 후보 [PR27](https://github.com/321Team/infra-kubernetes/pull/27)과 독립된 수집 코드다.

## 목적과 범위

이미 받은 6대 인증서(IP SAN 없음)·firewalld 비활성·SYN 수신 결과는 재수집하지 않는다.
기존 ansible01 → svc_ansible/control01 → sudo kubectl 경로에서 다음 고정 GET 4개만 수행한다.

1. worker03 Node의 이름/InternalIP=.108 확인. 불일치·실패면 즉시 중단.
2. worker03 kubelet configz의 실제 설정 중 serving bootstrap/rotation/TLS 파일 경로/인증·인가/featureGates.
3. kube-system/kubelet-config의 관련 최상위 설정 줄. 누락은 NOT_EXPLICIT로 기록하고 false로 추정하지 않는다.
4. CP3대 kube-controller-manager Pod spec의 선택된 controller/signing flags. CP 집합 불일치는 실패.

각 GET은 kubectl 15초 및 subprocess 22초 제한이다. 첫 identity 통과 후 나머지는 실패를 각각 기록하며, 하나라도 실패하면 전체 rc=1이다. kubectl 원본 응답·stderr 전체를 출력하지 않는다.
서명 **개인키 파일 경로**는 출력할 수 있지만 키/토큰/Secret/환경변수/전체 명령행은 읽어 출력하지 않는다.
CA 신뢰·키 파일 가용성·signer 실제 발급·Prometheus 로드 설정·SA 인가·TLS 성공을 이 수집으로 증명하지 않는다.

## 실행 위치와 전제

- ansible01, 기존 server01_ansible 계정, /home/server01_ansible/p1-ans-01.
- 기존 inventory/hosts.yml, 환경 파일, StrictHostKeyChecking와 svc_ansible SSH/기존 sudo 권한 유지.
- ansible_ssh_use_tty=false 사용.
- control01의 hostname, python3, sudo -n kubectl 사용. 새 패키지/권한을 만들지 않는다.
- 고정 commit의 collect.sh를 gh api로 ansible01의 새 결과 폴더에 받는다.
- Ansible raw의 -a 인자로 파일 내용을 전달한다. 대상에는 파일을 설치하지 않으며 설치/등록/변경/재시작/CSR 승인이 없다.
- 결과는 ansible01 실행자 결과 폴더에 --tree와 tee로 보관한다. 사용한 전체 commit·파일 SHA256·KST 시각을 함께 기록한다.

기존 승인 계정·권한으로 수행하는 한 현행 REVIEW_AUTHORITY_POLICY §3.2 4-1의 순수 검증 범위다.
실행 여부는 해당 Lead 판단이며 새 독립 운영 manifest 리뷰나 상시 이중 서명을 추가하지 않는다.

## 정상·실패·이후

정상 수집: 네 CHECK의 RC=0, TLS_PRECHECK_FAILED=0, Ansible rc=0.
이는 COLLECTION 성공이며 serverTLSBootstrap=true 또는 TLS/AC PASS라는 뜻이 아니다.
Forbidden/timeout/NotFound/불일치도 결과 원본을 보존하고 중단한다. sudo/RBAC 확대 또는 정책 임시 해제로 우회하지 않는다.
조회만 하므로 서버 복구 작업은 없다. 실패 원본과 기존 결과 폴더를 삭제하지 않는다.

결과를 통해 worker03 인증서 변경 후보의 유효 설정·kubeadm 보관 설정·signer 입력을 대조한다.
설정 변경, kubelet restart, CSR 승인, CA/Trust 변경은 이 스크립트 범위 밖이며 별도 검토된 운영 변경으로 진행한다.

## 검증 상태

STATIC: Bash/Python 구문 검사, 고정 GET·선택 출력·실패 rc 검토.
MOCK/UNIT: NOT RUN.
CI: PR 실제 run을 별도 기록.
FIELD COLLECTION: NOT RUN.
REVIEW: NOT PERFORMED (Pre-check). MERGE: PR 상태 참조.
AI 사용: CODEX_ASSISTED.

공식 참고:
- https://kubernetes.io/docs/tasks/administer-cluster/kubelet-config-file/
- https://kubernetes.io/docs/reference/access-authn-authz/kubelet-tls-bootstrapping/
