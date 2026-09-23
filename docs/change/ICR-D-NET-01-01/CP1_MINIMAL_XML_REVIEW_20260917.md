# CP1 최소 변경 — 실제 XML 파서 사전 점검 보충

검사 대상 실행 소스는 `c2efad4309cce7f3cad990979ddefb4383520447` 그대로다.
CP1 실행 bundle SHA256은 `e2b98fdc22cd15d8577b0df8b45f9d841e1beb1b41e953cbe2e7dfd42979ac33`이다.
이 보충은 검사 코드·결과·문서만 추가한다. 새 실행 범위나 별도 승인 요청을 만들지 않는다.
현재 소스에 대한 독립 리뷰는 기존 요청에서 대기 중이다.

## 왜 추가했는가

기존 130개 STATIC/MOCK와 선택된 nft 생성 함수 검사는 XML을 실제 firewalld가 해석한 결과까지 확인하지 않았다.
과거 반복된 VM 반입·실패·재수집을 줄이기 위해 생성 XML을 대상 버전의 실제 파서와 설정 참조 검사에 통과시켰다.

## 결과

**18/18 PASS**이며, 실행 코드 수정이 필요한 결함은 발견되지 않았다.

| 검사 | 결과 |
|---|---|
| zone 1개·policy 2개·ipset 2개: 실제 파싱, 참조 검사, 저장 후 재읽기 | 5/5 |
| 실제 Settings 데이터 클래스·CLI formatter 출력과 실행기의 전체 객체 비교 모델 일치 | 5/5 |
| 비활성 staging: 인터페이스/source 없음, forward=false | 1/1 |
| cali+ 연결 후 설정 유효성·출력 비교 | 1/1 |
| ssh, IPv4/IPv6 ipset, zone 의존성 누락 거부 | 4/4 |
| 잘못된 ipset 네트워크·두 zone의 동일 인터페이스 거부 | 2/2 |

기존 관련 단위·모의시험도 **130/130 PASS**, skip/failure/error 0을 재확인했다.
18개는 외부 버전 고정 계약 검사이며 단위시험 개수에 합산하지 않는다.

## 재현

공식 [firewalld v2.4.3](https://github.com/firewalld/firewalld/tree/v2.4.3) 소스를 별도 경로에 준비한 뒤 저장소 루트에서 실행한다.
검사 스크립트의 `UPSTREAM`에 열거된 20개 원본 파일을 모두 SHA256으로 대조한다.
운영 VM 또는 root 권한은 필요 없다.

```bash
python3 tests/probe_cp1_minimal_xml.py /absolute/path/to/firewalld-2.4.3
python3 -m unittest discover -s tests -v
```

`config/__init__.py.in`의 `@PACKAGE_VERSION@`만 임시 디렉터리에서 `2.4.3`으로 치환한다.
전체 XML reader/writer/check_config_dict는 원본 그대로 실행한다.
Settings 데이터 클래스와 CLI 출력 메서드는 AST로 선택해 실행하며, D-Bus 예외 처리 decorator만 identity로 대체한다.
이 경계 때문에 실제 D-Bus 호출이나 firewalld daemon 검증으로 표시하지 않는다.
검사 중 외부 프로세스 생성은 차단한다. 임시 파일은 종료 시 삭제한다.

## 남은 실행 경계

이 결과는 실제 CP1 적용, firewalld daemon/nft kernel/systemd timer 실행, 새 5/5 통신 또는 HOST/외부 회귀 증적이 아니다.
원 p3dmjivl 실측은 계속 3/5·미완료다. 새 인수, CP2/CP3 적용, Join, CI PASS, 병합을 증명하지 않는다.
기존 리뷰 완료 후 고정 소스 반입 → fresh inspect와 실행 입력 확정 → CP1 최소 변경 → 기존 5개 흐름 → 새 정식 인수 순서를 따른다.
새 VM 재수집이나 #11 전체 qualification, #13 확장을 공통 선행조건으로 추가하지 않는다.
