# CP1 최소 수정 경로: 기존 5개 흐름 증적 재사용

기준: PR #11 `ed53205fb142c86715281cbe4eca6a44b0171c90`.
상태: **OFFLINE_ANALYSIS_IMPLEMENTED / OPERATIONAL_CHANGE_NOT_IMPLEMENTED**.

[Lead의 최소 수정 요청](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5706886333)을 수용한다.
PR #13은 `a1d76e9`에서 보류한다. CP1 인수의 순서는 최소 CNI 변경 → 기존 5개 흐름 → 실제 인수이며,
커널 캡처·fault injection·전체 진단 qualification·#13 service 확장을 그 공통 선행조건으로 추가하지 않는다.
운영 변경의 HOST/외부 경로 보존과 유효한 새 after/인수 연결은 여전히 해당 변경 자체의 검토 대상이다.

## 이번에 구현한 작은 도구

`scripts/analyze-cp1-cni-trial.py`는 이미 반출한 `p3dmjivl` 형식의 압축파일을 로컬에서 읽는다.
추가 VM 수집, SSH, Kubernetes 명령, 방화벽 변경, service 설치를 실행하지 않는다.
별도 Python 패키지도 필요 없다. 일반 계정으로 실행하며 `sudo`가 필요하지 않다.

- 외부 SHA256 및 내부 REQUEST/RESULT 해시, 두 문서의 요청 연결을 검사한다. 압축파일을 디스크에 풀지 않는다.
- 기록된 probe 코드 해시, 실행 인수, Pod/Service 관측과 원본 stdout을 대조한다.
- HTTP 200 + 대상 응답 본문, 정확한 Service DNS 답으로 5개 흐름을 재계산한다.
- 명령 실패·잘린 출력·stderr·시각, 전후 방화벽/health snapshot 차이, probe/삭제 후 namespace 부재를 따로 확인한다.
- 선택적으로 원 CP1 영수증을 기존 고정 소비자의 `validate_cp1_receipt()`로 검증한 뒤,
  영수증 after와 시험 후 firewall의 전체 차이 경로를 표시한다. 원 영수증은 수정하지 않는다.
- 결과에는 식별 UUID 원문이나 방화벽 값 전체를 내보내지 않는다. 집계·오류 이유·해시·달라진 JSON 경로만 출력한다.

입력 SHA256은 바이트 무결성 기준이다. 서명·독립 승인·실제 명령 실행의 진위까지 증명하지 않는다.
health 비교는 기록된 snapshot의 일치 검사이며 별도의 전체 건강 판정기가 아니다.
원 영수증 after 비교도 PR #4의 전체 실시간 preflight/topology/authorization 검사를 대체하지 않는다.

```bash
python3 scripts/analyze-cp1-cni-trial.py /path/to/FDS_CP1_CNI_Trial_RUN.tar.gz \
  --sha256 ARCHIVE_SHA256
```

필요하면 `--cp1-receipt /path/to/ORIGINAL.json --cp1-receipt-sha256 ORIGINAL_SHA256`를 함께 지정한다.
출력은 stdout JSON이다. 종료코드 0은 `FIVE_FLOWS_REVIEW_REQUIRED`, 1은 미완료/차이/검토 필요,
2는 입력 검증 실패다. **0도 CP1 ACCEPTED 또는 후속 적용 허용이 아니다.**
인수·CP2·Join 권한 필드는 항상 false이고 실제 소비자 연결도 false다.

## 기존 실제 자료로 재현

실제 CP1 `FDS_CP1_CNI_Trial_p3dmjivl` 아카이브
SHA256 `81a4210a2aaddf69d508fd9d166589630b17d87f6209658dc919a1bb1b18cb39`를 그대로 재분석했다.

| 흐름 | 원본 응답과 대조한 결과 |
|---|---|
| A → B Pod IP HTTP | FAIL |
| B → A Pod IP HTTP | FAIL |
| A → Service HTTP | PASS |
| A Service DNS | PASS |
| B Service DNS | PASS |

3/5, 전체 미완료다. 원본 명령 rc=0을 5/5로 바꾸지 않는다. 내부 파일 2개 해시가 일치하고
새 분석기의 증적 일관성 오류는 없다. 이것은 과거 실패 자료 재생이며 새 실서버 시험 결과가 아니다.
[재현 출력](CP1_MINIMAL_ANALYSIS_REFERENCE_20260917.json)에 비식별 집계를 보관한다.

## 최소 배포안에서 아직 끝내야 할 부분

다음은 구현 완료 또는 실행 승인으로 표시하지 않는다. 새 수집 반복 대신 같은 변경안에 묶는다.

| 항목 | 구체적인 처리와 남은 차이 |
|---|---|
| 허용 조건 | CP1 소유 /26의 같은 노드 트래픽, source/destination과 양쪽 cali 조건을 동시에 적용. cali zone 이동이나 전체 public forwarding만 켜는 명령은 이 조건과 같지 않다 |
| 기존 후보 재사용 | #13의 public→public, CONTINUE, priority -1, `hash:net,iface` source/destination 후보는 zone/HOST binding을 보존하는 대안이다. 현재 확인한 veth 목록 방식이므로 미래 `cali+` 전부를 자동 지원하지 않는다. 이 범위 차이를 숨기고 운영 완료로 처리하지 않는다 |
| 수명과 회수 | 상시 watcher를 공통 선행조건으로 만들지 않는다. 단일 작업창의 명시적 유지 종료 시각·그 창의 원복 경로를 적용 전에 확정한다. 기존 20분 진단을 /26 운영 유지 승인으로 전용하지 않는다 |
| 적용 영향 | 새 firewalld 객체에 Permanent 정의와 reload가 필요하면 기존 Runtime 6개 복원과 전체 보호 상태를 같은 절차에서 다룬다. 성공 여부가 불명확한 명령을 반복하거나 타인의 drift를 덮어쓰지 않는다 |
| 기존 시험 키트 | 원 키트의 expected-firewall.json은 변경 전 상태에 고정돼 있다. 새 after를 검토한 별도 키트 입력/source 연결이 필요하다. 과거 REQUEST/RESULT/원 영수증의 after를 고쳐 새 결과로 만들지 않는다 |
| CP1 인수 | 실제 새 상태에서 기존 5개 흐름을 확인하고 변경의 HOST/외부 경로 보존을 검토해 인수 기록을 남긴다. 이 분석기의 집계는 인수 결정이 아니다 |
| CP2/CP3 | 원 PR #4는 원 receipt.after 전체와 현재 CP1 상태를 비교하므로 CNI 변경이 남으면 차단된다. 새 변경 영수증·인수를 명시적으로 지원하는 좁은 소비자 연결이 필요하며 아직 구현하지 않았다 |

현재 이 도구의 source 기준은 기존 `b3f3374` 키트와 고정 probe다. 다른 생산자/변경 키트의 기록은
`ENVELOPE_SOURCE_OR_TRANSPORT`로 표시된다. 미래 키트를 무조건 통과시키는 옵션은 없다.
새 변경안이 검토되면 그 source/expected-after 연결을 같은 작은 수정에 포함해야 한다.
오늘 Join 완료 시각은 이 분석기 개발 완료로 확정하지 않는다.

## 단위시험과 자체 점검

`python3 -m unittest discover -s tests -p 'test_cp1_cni_trial_analysis.py' -v`

신규 33개와 기존 node firewall/contract 61개, 합계 **94/94 STATIC/MOCK PASS**, skip/error/failure 0.
의미 있는 거부 사례에는 거짓 passed 표시, HTTP 본문·DNS 답 불일치, summary/stdout 불일치,
중복·누락 흐름, 다른 노드/hostNetwork/Ready 아닌 Pod, 관측 시각 역전, 잘린 명령,
삭제 전 부재의 정리 완료 오인, 원 영수증과 새 CNI after 불일치가 포함된다.
archive 해시·경로·중복·링크 거부 및 실제 CLI 입력/출력도 확인했다.
fixture의 probe 원문은 기존 제출 자료에서 가져온 공개 시험 코드이며 테스트에서 실행하지 않는다.

[시험 로그](CP1_MINIMAL_ANALYSIS_TEST_20260917.txt)와 [검증 범위](CP1_MINIMAL_ANALYSIS_VALIDATION_20260917.json)를 함께 보관한다.
기존 node helper/controller/CP1 생산자 실행 바이트는 그대로다. #13 코드·R6 진단 코드·운영 설정을 변경하지 않았다.
새 VM 시험·CI·실제 CP1 인수·CP2/CP3 적용·Join·병합은 수행하지 않았다.
