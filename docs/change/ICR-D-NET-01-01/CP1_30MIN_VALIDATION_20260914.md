# CP1 30분 입력 유효기간 검증

2026-09-14 Linux/Python unittest: `python3 -m unittest discover -s tests -p 'test_*.py'` — 134 tests PASS (CP1 55, 후속 노드 58, Worker 21).

새 시험은 컨트롤러 1,800초 경계 허용/초과 차단, 원격 경계 허용, 미래·시간대 없는 입력 차단, 전송 중 만료 시 무변경, 30분 입력의 현재 상태 drift 차단, 동일 저장소 후속 PR 리뷰 링크 허용을 검증한다. Fake backend를 사용하며 실서버 변경은 실행하지 않았다.

CP1 고정 실행 소스 `264b273ab8ad79209bc0fec4a85c4497c946dbe3`, helper SHA-256 `a09efb38dbbc313124f6d42858c3ee3e7d676e933abe8ed0550bf8937fb18dcc`. 소비자 계약·정책·실제 생산자 fixture를 함께 갱신했다. 기존 실행창 종료·복구·상태 변동·소스/입력 결합 회귀시험도 통과했다.

19:00 KST는 사용자가 요청한 새 종료시간이다. 새 HEAD 독립 코드 리뷰와 새 소스/입력/작업창을 연결하는 Runtime 실행 승인, 최신 현장 점검은 별개로 남아 있다. 이전 승인과 입력의 시각을 바꾸어 재사용하지 않는다.
