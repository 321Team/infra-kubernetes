# R5 — 관측 증적과 종료 처리 일괄 보완

기준 HEAD: `c6792d408ca660f8ca7055a161d5e90b865d1bca`.
검토 근거: [진단 도구 복잡도·일정 개선 의견](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5694971716).
변경 대상은 커널 컴포넌트 실행기, 관련 회귀시험, 실행 안내와 검증 기록이다.
공통 Transaction·운영 node/controller·방화벽 helper의 바이트는 기준 HEAD와 같다.

## 확인한 실행 결과

Job `3c11f64a9e41c9f0`의 원본 결과는 `COMPONENT_FAILED_REVIEW_REQUIRED`로 유지한다.
`FDS_CNI_Kernel_Component_mzfwot39.tar.gz`의 SHA256은
`ba06f1c346964c8016248b7e0072dc864c1f7f049e1b31ac823ec12b2ec31e9d`이며,
외부 및 내부 24개 파일 체크섬, 소스 4개를 대조했다.

| 항목 | 확인된 결과 |
|---|---|
| A→B TCP8080 | TCP 연결, HTTP 200, job/role 응답 일치 |
| 같은 5-tuple의 nft trace | 진단 policy allow chain의 accept 관측 |
| PCAP | 24바이트 헤더만 존재, packet record 0개 |
| 나머지 5개 흐름 | 첫 실패 이후 미실행, NOT_TESTED |
| 공통 트랜잭션 원복 | 성공; baseline/expected 일치 |
| 시험 자원 | namespace 부재, 수집된 호스트 전후 파일 동일 |
| 복구 예약 | 수동 recover 1회 뒤 해제 확인; 반출 시 pending=false |

첫 HTTP 통신과 trace는 관측됐다. PCAP 필수 증적이 없으므로 전체 시험 PASS나 CP1 인수로 바꾸지 않는다.
기존 tcpdump stderr·통계·종료코드는 보존되지 않아 빈 PCAP의 직접 원인은 확정하지 못했다.
work journal의 timeout과, 실행 중인 자신의 unit을 삭제한 뒤 reload하는 기존 코드 순서는 확인했다.
이 순서의 위험은 회귀시험에서 재현했지만 대상 VM timeout의 단일 원인까지 재현한 것은 아니다.

## 이번에 반영한 개선

1. 통신 기대값 판정(`communication_status`)과 증적 상태(`evidence_status`)를 분리했다.
   증적 누락·수집 오류는 여전히 실패이며, 차단 판정에는 실제 ingress/trace 검토가 필요하다.
   `COMPONENT_SUMMARY.json`에서 6개 흐름과 원복·격리 자원 정리·예약 해제·unit 정리를 함께 본다.
   시도하지 않은 흐름은 NOT_TESTED이며, 구버전 결과를 새로운 판정으로 덮어쓰지 않는다.
2. tcpdump `--immediate-mode` 지원을 inspect에서 확인하고 버전을 기록한다.
   캡처 중 고정 0.2초 대기 대신 최대 3초 동안 packet/trace 파일의 기록을 기다린다.
   파일 증가는 대기 종료 조건일 뿐이다. 종료 후 tcpdump로 같은 BPF tuple을 읽어야 증적이 존재한다고 판단한다.
   프로세스 생존·전체 작업 기한을 계속 검사한다.
3. 흐름별 `*.tcpdump.log`, `*.nft.log`, `*.observation.json`을 보존한다.
   시작 오류, 종료코드, 강제 종료 여부, captured/received/dropped 통계, readback 오류를 남긴다.
   한 observer의 종료 오류가 다른 observer 회수를 건너뛰지 않으며, 비정상 종료는 파일이 있어도 실패다.
4. 자원 정리 뒤 timer 정지·비활성화 및 boot recovery 비활성화를 확인하고 `recovery_disarmed=true`를 저장한다.
   실행 중인 work/recover unit 정의는 그대로 두고 `unit_cleanup_pending=true`로 기록한다.
5. 기존 `collect JOB_ID`에서 **해당 job의 검증된 보관 소스**로 종료 처리를 마무리한다.
   모든 소유 unit이 inactive/failed, MainPID=0, queued Job 없음인 경우에만 unit 파일을 삭제하고 reload한다.
   종료 직후의 짧은 간격은 최대 5초 기다린다. collect가 서비스나 프로세스를 중지하지 않는다.
   실제 exit 상태는 `UNIT_EXIT_STATE.json`에 보존한다.
6. 타 unit·symlink·변경된 보관 소스·실행 중인 unit은 삭제하지 않는다.
   중간 실패는 `unit_cleanup_error`와 pending 상태를 반출하고 동일 collect로 재시도할 수 있다.
   예약이 다시 켜져 있거나 해제 상태 확인이 실패하면 recovery_disarmed도 false로 되돌려 재검토한다.
   새 start는 이전 job의 unit 정리까지 완료돼야 한다.

`-U`는 처리한 packet의 파일 출력을 flush하는 옵션이며 즉시 수신 모드와 구분된다.
공식 옵션 근거: [tcpdump 매뉴얼](https://github.com/the-tcpdump-group/tcpdump/blob/tcpdump-4.99.5/tcpdump.1.in).
이 설명은 이전 빈 PCAP의 원인을 확정하는 증거가 아니다.

## 복잡도와 리뷰 반복을 줄이는 운영 원칙

- 추가 서비스·새 VM·복구 프레임워크·시험 시나리오를 만들지 않는다. 기존 collect 한 단계에 종료 확인을 포함한다.
- 이번 증적 수집과 종료 처리 보완을 한 변경 묶음으로 검증·기록한다. 증상별 새 VM 반입/시험을 반복하지 않는다.
- 실제 CLI 결과·관측 파일·실패 경로를 검증한다. 모의시험 수만으로 대상 VM Runtime 성공을 주장하지 않는다.
- 이미 검토한 격리와 최소 독립 복구는 유지한다. 본 변경에서 수동 진단으로 전면 교체하거나 승인 조건을 완화하지 않는다.
- Worker 진행은 이 진단의 선행 조건이 아니다. 이번 변경에서 Worker 설정은 조작하지 않았다.
- 일정을 위해 진단을 보류할 수는 있으나, 미측정 항목을 완료/인수로 바꾸지 않는다.
- 변경 소스의 정확한 HEAD/digest, 로컬 검증, 실행 범위와 종료 조건을 한 번에 검토할 수 있도록 남긴다.
  추가 리뷰 요청 메시지나 새 실행 결정은 자동 생성하지 않는다.

## 검증과 남은 확인

- Python AST, 기본 PLAN_ONLY, 소스 체크섬 통과.
- 로컬 회귀 **246/246 PASS**, skip 0. 기존 220개와 추가 26개다.
- 실제 로컬 Python child가 지연 기록하는 파일을 기다리고 SIGINT로 회수하는 검사를 포함한다.
  firewall/namespace/systemd 연동은 mock이다. target tcpdump 및 systemd Runtime은 미실행이다.
- 빈 PCAP+HTTP 성공, negative 연결, 미실행 probe, observer 조기 종료·강제 종료,
  readback/trace 정리 실패, 실행 중 unit, 외부 unit 변경, staged source 변경,
  reload 중단 후 재시도, 보관 소스 collection, pending 실패 archive를 검사했다.
- 같은 unit 보존 회귀시험은 c679에서 실패하고 R5에서 통과한다.
- 상세: [검증 JSON](CNI_CAPTURE_R5_VALIDATION_20260916.json),
  [회귀 출력](CNI_CAPTURE_R5_TEST_OUTPUT_20260916.txt),
  [기존 코드 재현](CNI_CAPTURE_R5_RED_REPRODUCTION_20260916.txt).

새 커널 bundle: `3184ef3ebdaea8f1ca12843055510cacdfd212e65db41ce1829df5cb1723b619`.
운영 executor bundle은 `d6b2ab46297e8892ac966225f0e9a5ecb37724e1ceee0d94af4468a0d83bb74d`로 동일하다.

새 소스 독립 검토·VM inspect·VM Runtime은 대기다. c679의 이미 수행된 1회 실행 결정을 새 digest에 재사용하지 않는다.
로컬 회귀 완료 후에만 검토 가능한 변경 묶음을 반영한다. 실제 추가 시험은 별도 확정된 실행 범위에서 수행한다.
그때의 완료 조건은 6개 흐름과 PCAP/trace, 공통 원복·호스트 전후 일치,
recovery_disarmed 및 unit_cleanup_complete를 함께 확인하는 것이다.
실패 시 같은 JOB_ID의 증적과 종료 상태를 보존하고 새 start를 반복하지 않는다.
CP1 최종 인수·CP2 확대·병합·AC 완료 및 운영 복구 qualification은 계속 보류다.
