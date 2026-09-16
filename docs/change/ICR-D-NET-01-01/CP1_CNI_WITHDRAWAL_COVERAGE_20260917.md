# CP1 CNI 회수 경계 수정과 실제 dataplane 근거의 차단 사항

기준 HEAD: `fb9d01c8bc80c1b12fca2f105ad59bbe6af130b0`. 상태는
**DEVELOPMENT_BLOCKED_ON_TARGET_DATAPLANE_COVERAGE**이며 PR #13은 Draft를 유지한다.
완성된 통합 묶음에 대한 독립 리뷰 요청 단계에 도달하지 않았다.

## 이번에 수정한 오류

1. `disable()`이 신규 허용용 observer를 호출해 lease 만료·guard 장애·API 장애·boot 변경 때
   소유 policy의 허용 철회도 막히는 연결 오류를 수정했다.
   Permanent description, Runtime/Permanent 정의, inet/firewalld의 compiled 규칙과 요소를
   두 번 조회하는 독립적인 소유권 관측으로 disable을 검증한다.
   이 결과의 fixed-state digest는 `null`, guard는 false이며 추가 허용의 입력으로 사용할 수 없다.
2. disable 이후 membership 제거는 같은 boot와 전체 고정 설정 대조를 계속 요구한다.
   lease/guard 생존 여부만 제외하며, 다른 boot·foreign drift·opaque xt가 남으면 제거와
   전체 회수 완료 판정은 계속 차단한다. 전역 reload나 타인 설정 복원은 실행하지 않는다.
3. 고정 `node-firewall.py` helper는 정상 결과에서 `stdout_truncated`/`stderr_truncated` 필드를
   생략한다. observer 및 unit adapter가 이를 실패로 오인하던 버그를 수정했다.
   missing/false만 정상으로 처리하고 true/null·경고·비정수 종료코드는 거부한다.
4. 전체 nft JSON의 `xt` 확장을 고정 상태가 완전히 관측된 것으로 처리하지 않도록
   `XT_COMPAT_COVERAGE_REQUIRED`를 추가했다. 정상 Pod 변화와 보호 변경의 구분을
   전체 Calico 규칙 제외로 우회하지 않는다.

## 재사용한 실제 CP1 증적

| 묶음 | 외부 SHA256 | 검증 |
|---|---|---|
| `FDS_CP1_CNI_Diagnose_7aqtz7d4.tar.gz` | `e24ab6d535cedc12cbe59014b4e452a56292c15100227ef2e3ce3f9e3bab1857` | 외부·내부 일치 |
| `FDS_CP1_CNI_Supplement_kav4i4cv.tar.gz` | `3e3ac43556035e5895c4040cdbf3a5ecec6cd3bdbc24de50b11965372fc0d3c2` | 외부·내부 일치 |
| `FDS_CP1_CNI_Trial_p3dmjivl.tar.gz` | `81a4210a2aaddf69d508fd9d166589630b17d87f6209658dc919a1bb1b18cb39` | 외부·내부 일치 |
| `FDS_CP1_CNI_Prepare_v7nd98wj.tar.gz` | `dcb1fb8b6c48ad3aa40058ce445d27cf8e148d211d3053a4805a9c6e78c4d2e3` | 외부·내부 일치 |
| `FDS_CP1_CNI_Prepare_75ewn8ke.tar.gz` | `3d34b4546ab722bc026bfe781c48e7accb101a52d046fe818891f4a29fbd23b0` | 외부·내부 일치 |

실제 9월 15일 진단에서 다음을 확인했다. 최신 서버 상태라는 의미는 아니다.

- Calico node desired image와 container status imageID가
  `quay.io/calico/node@sha256:c061070a27292f8152ae6a0582078eb9059d1b6ed5e57c2052e5c22534734240`로 일치.
  이 digest를 확인한 사실과 버전 태그를 추정하는 것은 구별한다.
- 실제 iptables 소유 패키지는 `iptables-nft-1.8.11-16.el10.x86_64`.
  `iptables-save` 헤더는 `v1.8.11 (nf_tables)`.
- Calico endpoint chain은 `ip filter`의 `cali-fw-*`/`cali-tw-*`에 있다.
  이전 observer 시험의 `ip calico` 테이블은 모형이며 실제 대상 구조가 아니다.
- nft 출력에 여러 `xt match "set"`과 `xt target "MASQUERADE"`가 남아 있다.
  supplement의 iptables 원본은 실제 참조 set 이름을 보여 주지만 set 내용은 없다.
  진단/보완 수집 시각도 서로 달라 하나의 원자적 상태로 합치지 않는다.
- 재사용한 다섯 묶음에서 kernel `ipset save`와 짝지어진 전체 nft JSON을 찾지 못했다.

Netfilter 공식 설명도 xt 구문이 불완전한 번역의 fallback이며 nft로 복원되지 않는다고 명시한다.
[nft manual: XT statement](https://netfilter.org/projects/nftables/manpage.html)

## 한 번의 수집으로 보완할 근거

새 `collect-cp1-cni-dataplane.py`는 nft JSON/text, IPv4/IPv6 iptables-save,
kernel ipsets, link/route, Calico 이미지·Felix·IPAM·보호 정책, Pod/namespace/service account,
Service/EndpointSlice, kube-proxy 설정을 전후 한 묶음에 남긴다.
마지막 boot까지 대조하며 모든 조회 결과·경고·실패를 보존한다.
multicall 실행 파일을 resolve해도 `argv[0]`을 `iptables-save`로 보존한다.
이미 존재하는 출력 파일은 host 조회 전에 거부한다.

[실행·반출 절차](../../runbooks/CP1_CNI_DATAPLANE_CAPTURE.md)

이는 읽기 전용 수집 도구다. 실제 CP1에서 아직 실행하지 않았으며, 출력 완전성은
정책 의미·변화 모델·운영 인수의 검증과 별개다. 자료가 있어도 새로운 namespace/profile,
kube-proxy 변경 등 모든 종류의 변화가 자동 허용된다고 약속하지 않는다.

## 검증과 남은 순서

전체 로컬 회귀 **216/216 PASS**. 이전 197개 + 회수 9개 + 수집 10개.
회수 시험에는 Manager → adapter → 실제 query-record observer 연결에서 guard 상실 뒤 disable 검증이 포함된다.
기존 실제 파일시스템/프로세스 9개를 재실행했고, 새 collector의 argv0 보존·출력 제한·timeout은
로컬 실제 shell 프로세스로도 확인했다. 실제 kernel/firewalld/systemd/CP1 적용은 하지 않았다.

아직 구현되지 않은 범위:

1. 실제 backend에 연결된 정상 Pod 교체의 native/xtables/ipset 변화 모델.
2. 모델을 반영한 전체 before/after를 검증하는 root staging 및 activation/설치.
3. 원 Runtime 6개·관리 경로·Runtime/Permanent 객체·unit을 포함한 독립 전체 회수.
4. 실제 CP1 인수와 인증된 관측을 후속 controller의 전후 gate에 연결.

원 생산자·원 영수증 검증기·후속 controller는 보존했다. CP1 인수·CP2/3 변경·Join은 계속 보류다.
누락 근거 확보 후 위 통합과 실패 시험까지 끝내고 하나의 리뷰 묶음으로 제출한다.
