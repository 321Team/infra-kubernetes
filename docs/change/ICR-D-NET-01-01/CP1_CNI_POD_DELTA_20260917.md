# CP1 Pod 변화·식별 경계 보완 — 2026-09-17

상태: **POD_DELTA_OBSERVER_AND_SOURCE_STAGING_IMPLEMENTED / OPERATIONAL_PRODUCER_INCOMPLETE / RUNTIME_NOT_RUN**.

## 이번 변경

- `cp1_cni_delta.py`: 실제 수집한 Calico fw/tw·namespace/ServiceAccount profile, dispatch tree, ARP verdict map, kube-proxy Service/endpoint chain을 API 및 peer-bound membership과 함께 검증한다. 검증한 Pod 종속 객체만 고정 비교에서 분리한다. Calico/KUBE 이름 전체를 제외하지 않는다.
- 저장된 iptables의 conntrack/MARK/DNAT/statistic 조건과 nft native 규칙을 모두 비교한다. namespace/ServiceAccount/Service/보호 정책·Calico 설정·kernel ipset은 고정 비교에 남는다. native의 알려진 객체에서 최상위 handle만 할당 메타데이터로 분리하며 사용자 필드를 재귀 삭제하지 않는다. 기존 activation의 엄격 비교를 암묵적으로 새 비교로 바꾸지 않는다.
- 같은 Service IP/protocol/port가 겹치지 않음을 확인한 뒤 Service dispatch 순서만 정규화한다. 마지막 NodePort dispatch의 위치는 고정한다. 세 endpoint의 statistic 31-bit 반올림까지 검사한다.
- Pod 0개와 Service Ready endpoint 0개 전환을 source-derived 모의 규칙으로 시험했다. Ready를 membership 선행조건으로 추가하지 않는다. zero endpoint의 native xt REJECT 표현은 아직 대상에서 수집한 결과가 아니다. 다른 표현이면 차단한다.
- 새 비교는 `fds.cp1-cni-pod-delta.v1` activation에서만 선택한다. 전체 observer에 실제 API 전후 조회와 raw CRI/peer capture 검사를 연결했다. 업로드 JSON의 complete/owned boolean만으로 권한을 만들지 않는다. 실제 호출자는 source-pinned 로컬 수집기와 root 보관 경계를 사용해야 한다.
- mutation 직전·직후 UID/IP/veth/ifindex/sandbox를 다시 확인한다. CRI collector도 후반 API/host 조회 뒤 실제 sandbox PID·netns·peer를 재조회한다. 초기 peer 표본을 복사해 후반 표본으로 쓰던 빈틈을 없앴다.
- 성공한 각 조작의 최신 관측을 journal에 반영한다. 여러 Pod의 조정이 30초를 넘더라도 각 조작은 신선한 검증으로 연결되고, 한 조작이 30초 상한을 넘으면 철회한다. 절대 lease 종료는 바꾸지 않는다. post-mutation identity 증거를 다시 해시하더라도 누락/변조된 generation은 거부한다.
- 빈 집합의 최초 관측을 저장한 뒤 watchdog을 시작할 수 있도록 seed 함수를 추가했다. startup 유예로 검사를 건너뛰지 않으며, 실제 guard가 없으면 추가는 계속 차단한다. timestamp만 있는 heartbeat도 거부한다.
- `cp1_cni_staging.py`: 검토된 외부 manifest hash에 맞는 바이트를 root 전용 경로에 새로 보관·검증한다. symlink/hardlink, writable ancestor, 변조·추가 파일, 권한 변경, 중단된 복사, 기존 run 덮어쓰기를 거부한다. 완료 marker는 마지막에 기록한다. 이 모듈은 service 설치 또는 실행 승인을 생성하지 않는다.

## 검증 결과

**279/279 PASS, 실패 0, 오류 0, skip 0.** 이전 229개에 신규 50개를 더했다. 한 test의 여러 subtest를 별도 개수로 부풀리지 않았다.

- Calico/ARP/Service 변화·숨겨진 인자·외부 규칙 변경, API/EndpointSlice identity, native/saved 상호 대조.
- Ready endpoint 없음·Pod 0개·Service dispatch 재정렬·UID/sandbox/ifindex 재사용·관측 시간 경계.
- API/CRI 실패·조작 도중 identity 변경·최초 guard 시작·수정된 receipt 거부.
- 실제 임시 파일시스템에서 root 보관·소유권/권한·링크·중단·변조 검사 10개.
- 실제 CAPTURE.json 재생과 기존 compatibility 반례 13/13 PASS. 새 Pod projection도 실제 3개 endpoint의 규칙 구조와 일치한다.

실제 자료에는 CRI sandbox/peer 검증이 없다. 오프라인 구조 재생은 명시적인 synthetic sandbox ID를 사용하며, 이를 실제 peer 소유 증거로 보고하지 않는다. 시험 fixture의 장비/Pod 원 UID와 sandbox 식별자는 synthetic 값으로 치환했다. 원본 archive는 게시하지 않는다.

## 지원 한계와 아직 남은 개발

1. 신규 NetworkPolicy/HEP/NetworkSet, remote dynamic Service endpoint, terminating endpoint fallback, 다른 Service 모드 및 알려지지 않은 backend 형식은 차단한다. 이 비교 모델은 Join 이후 모든 정상 변화의 지원 완료가 아니다.
2. 현재 대상에서 raw CRI peer layout·수집시간·zero endpoint REJECT 표현은 미검증이다. mock 입력으로 해당 사실을 확정하지 않는다.
3. root **소스 보관**은 구현했지만, 전체 원 영수증/before/after 기반 activation producer와 service 설치 entrypoint는 아직 없다.
4. 원 Runtime 6개 복원·전체 객체·관리 경로·unit 종료를 포함한 독립 전체 회수와 기존 controller의 새 gate 연결은 미완료다. 좁은 owned disable이 전체 회수 완료를 뜻하지 않는다.
5. 새 원본/후속 controller를 우회하거나 기존 receipt after를 바꾸지 않았다. CP1 인수·CP2/3 변경·Join을 이 중간 결과로 허가하지 않는다.

이 커밋은 개발 checkpoint이며 **통합 운영 묶음 독립 리뷰 준비 완료가 아니다**. 사용자 요청대로 중간 단계마다 리뷰/VM 실행 요청을 추가하지 않는다. 남은 producer·회수·consumer를 묶고, 현재 드러난 실제 검증 부족을 그 묶음의 시험 항목에 포함해야 한다.

## 재현

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

root 파일 소유권 시험 10개는 root 테스트 프로세스와 안전한 private HOME 아래 임시 디렉터리를 사용한다. 방화벽·systemd·실서버는 변경하지 않는다. non-root 환경에서는 해당 10개가 skip되므로 이번 279/279와 동일 검증으로 보고하면 안 된다.

## 알고리즘 근거

- [Calico profile ID 규칙](https://github.com/projectcalico/calico/blob/v3.32.1/libcalico-go/lib/hash/unique_id.go), [dispatch tree](https://github.com/projectcalico/calico/blob/v3.32.1/felix/rules/dispatch.go), [endpoint 규칙](https://github.com/projectcalico/calico/blob/v3.32.1/felix/rules/endpoints.go). 이 태그가 대상 digest의 설치 버전이라고 단정하지 않는다. 실제 수집 규칙의 모양도 별도로 대조한다.
- [대상 kube-proxy v1.36.4 Service/endpoint 및 zero endpoint 분기](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/proxy/iptables/proxier.go).
- [iptables 1.8.11 statistic 반올림·저장 표현](https://sources.debian.org/src/iptables/1.8.11-2/extensions/libxt_statistic.c/).
