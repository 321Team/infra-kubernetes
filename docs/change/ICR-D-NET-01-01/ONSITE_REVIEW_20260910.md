# PR #2 현장 Linux·읽기 전용 점검 검토 — 2026-09-10

**판정: 현장 Linux 모의 테스트와 읽기 전용 수집·증적 무결성 PASS. 실제 Runtime 적용·기능 인수·독립 승인 미완료.**

## 실행과 증적

| 항목 | 확인 결과 |
|---|---|
| 제출본 | FDS_PR2_Onsite_mvoxv1pm.tar.gz |
| 외부 SHA-256 | e3e2d9c40f4fd57f8a445ca52e6756f81c455c4ad1ddbc9aa00689009857966b, sidecar 일치 |
| 외부 내부 manifest | 13/13 일치 |
| 포함 inspect | FDS_CP1_Canary_8b86bj66.tar.gz |
| inspect SHA-256 | f50bf0c2da85de698b1276e40de38d4396d617ad07255e3e009062cb296036e5, sidecar 및 내부 5/5 일치 |
| 실행 장소 | ansible01, server01_ansible, Linux 6.12.0-260.el10 / Python 3.12.13 |
| 실행 시각 | 2026-09-10 19:15:56–19:16:09 KST (10:15:56–10:16:09 UTC) |
| 실행 source | afc246d2db041c68de5014972107484dad122558 |
| source/helper | SOURCE_LOCK 27개 파일의 SHA-256과 실제 Git blob 모두 일치. helper SHA 25b497f7c300c03b76975cee554c1c926e6775674f97ed11254cb1625b635e8d |
| 수집기 | 배포 wrapper SHA c954d15e3fd69ecc883ac6ca41bb5daaeaa3456e7294e1dbca5b8c44ec580264와 일치 |
| 실행 모드 | inspect, Runtime not_attempted / wrapper runtime_applied=false |

원시 운영 로그는 제출 원본으로 보존한다. Git에는 검토서·비밀정보 없는 현장 테스트 기록·파생 점검 요약과 해시를 편입한다. 이번 문서를 포함한 증적 편입 커밋을 위 실제 실행 source로 소급 표시하지 않는다.

## Linux 리뷰 요청 처리

[이하영의 Linux 실행 요청](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5615889673)에 대해 **현장 실행 증적 확보·검증 완료**로 판단한다. 원문 테스트 출력 37개 모두 ok, Ran 37 tests / OK, 종료 코드 0이다. 실행 도구 26개와 기존 오프라인 계획 11개이며 구 소스에서 요청한 19개 테스트 식별자가 새 실행 결과에 모두 포함됨을 대조했다.

[현장 실행 기록](ONSITE_TEST_20260910.json)에 실제 환경·명령·시각·종료 코드·전체 테스트 출력·출력 SHA·실행 파일 SHA를 기록했다. 과거 Codex Linux의 19/37개 기록은 그대로 보존하며 이번 ansible01 기록과 구분한다. 리뷰어의 정식 APPROVED는 별도다.

## CP 점검

| 항목 | 결과 |
|---|---|
| CP 3대 전송·질의 | 전송 모두 PASS, 일반 37 + snapshot 78 = 115개 조회 rc=0, 실패·gaps·byte truncation 0 |
| snapshot·topology | 노드별 snapshot 3개 및 CP1 topology 해시 일치 |
| 입력 완전성 | CP1 true. CP2/3 false는 CP1 전용 canary 입력 설계에 따른 값 |
| CP1 | Node Ready 1대, etcd voter 1개, learner 없음. API readyz 37항목 ok, etcd status 성공 |
| CP1 자체 TCP | 2379/2380/10250 3/3 성공 |
| CP2/3→CP1 TCP | 같은 포트 0/6, errno 113. 쿼리 수집 성공과 통신 인수 실패를 구분 |
| CP 3대 방화벽 | firewalld active, public Runtime/Permanent에 기존 SSH 서비스·LB 6443 허용만 있으며 대상 CP 허용 추가 전 |
| CP1 보호 | calico-node 1/1 Running, HEP 0개, GNP 0개. 보호 인계 미완료 |
| Calico IPPool | IPIP Never, VXLAN CrossSubnet, natOutgoing=true. BGP Disabled·MTU·전체 D34 인수를 이 조회로 확정하지 않음 |
| CP2 잔여 | API/controller/scheduler manifest 3개 존재. 표준 etcd.yaml·kubelet 설정·/var/lib/etcd와 member는 부재. 원래 join 단계·별도 data 경로는 미확인 |

이전 jvgkgfm9(13:56 KST)와 비교하면 관측된 세 노드 firewall/NM snapshot, CP1 Node/member 식별, TCP 결과, CP2 잔여 파일 메타데이터는 같다. 이는 두 관측 시점의 비교이며 사이 시간의 모든 변경 부재를 증명하지 않는다. 이번 일반 조회는 Calico 5개 추가로 기존 110개에서 115개가 됐다.

errno 113과 허용 누락은 통신 보완이 필요함을 뒷받침하지만 원래 kubeadm join 실패의 단일 원인을 완전히 입증한 결과는 아니다. 실제 허용 후 통신·TLS/Auth·기능·Negative·회귀 시험으로 확인한다.

## 다음 조건과 정본 관계

1. 최신 증적을 포함한 PR #2 HEAD를 이하영이 독립 검토한다. 이번 증적 편입은 코드·테스트·실행 정책 변경이 없는 문서 변경이며 실행 source afc246d의 27개 파일은 별도 고정했다.
2. 실제 Runtime은 보호·백업·콘솔·복구·평일 변경창과 실행 결정을 기록한 뒤 판단한다. CP1 수집 시각 기준 15분 한도는 **2026-09-10 19:30:57.964101 KST**다. 만료 또는 승인/실행 source 변경 시 최신 inspect가 필요하다.
3. CP1 Runtime 6개 후보만으로 canary를 수행한 뒤 Positive/Negative/Regression·독립 리뷰 결과를 확인한다. 이번 제출은 apply/rollback 영수증이 아니다.
4. PR #4의 구 v1 12개 receipt 및 Permanent 선반영 계약은 후속 보완 전 적용 보류다. CP2 정리·join/reset, NTP 결정, HEP/GNP 인수도 미완료다.
5. ADV-20260910의 정당한 선행 준비 허용을 유지하고 v18 후보에 최신 현장 증적·남은 조건을 편입한다. 정본 발행·기존 AC/Done·6대 firewalld OFF 및 named HEP/GNP 최종 목표를 이번 수집 PASS로 변경하지 않는다.

재수집은 Linux 기록 부족 때문에 반복할 필요가 없다. 실제 apply 입력의 시각·source 조건을 충족하기 위한 새 inspect는 별개다.
