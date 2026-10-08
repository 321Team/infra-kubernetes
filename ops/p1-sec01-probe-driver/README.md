# SEC Probe 재시험 driver — 사전 리뷰용

관련: [baseline #19 후속 조건](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6052047912),
[실패 증적 #158](https://github.com/321Team/fds-project-evidence/pull/158),
[기동 경로 수정 #74](https://github.com/321Team/infra-kubernetes/pull/74).
정본은 `docs-v3.1.21 @ 979facb98de8ca1f1e2ce22f38b27ac3366f3fc3`.

이 PR은 지난 회차의 임시 `stage.py / precheck-launch.py / run-launch.py / job.py / fetch.py`를
그대로 재실행하지 않도록 실행 연결부를 소스에 편입한다. **새 현장 실행·실행창 예약·Runtime GO가 아니다.**
Harbor 복제본, VMware, 운영 Harbor, Edge, Runner를 이 준비 작업에서 사용하지 않는다.

## 범위와 신뢰 경계

- `prepare.py`: 로컬 Git의 정확한 40자리 commit에서 driver와 기존 Probe 실행 파일 4개를
  `git show`로 추출. dirty checkout이나 CRLF 변환을 공급물에 섞지 않는다. 원 source manifest를 검사한다.
  `BUNDLE.json` SHA는 코드 SHA를 결속하며, 그 자체는 독립 리뷰/승인을 증명하지 않는다.
- `driver.py execute`: **k8s-control01의 기존 승인 관리 계정 경로 → root**에서 실행.
  새 sudo 권한/계정/SSH 키/토큰을 만들지 않는다. 기존 `/etc/kubernetes/admin.conf`를 사용한다.
  `precheck`와 `run`을 별도 명령으로 제공하고 `backup.sh`를 실행하지 않는다.
  backup 파일은 기존 probe의 `source-sha256.txt` 기록에 필요하여 포함한다.
- 기존 `probe.sh` v2.2의 고정 범위(fds-app, worker01, cached digest, `/usr/local/bin/python3.12`,
  imagePullPolicy Never, 시험 Pod·Service 각 1개, UID 조건 정리)를 그대로 사용한다.
  운영 앱 HTTP Probe나 전체 AC의 인수 도구가 아니다.
- **permit은 운영자가 확인한 기록이지, 서명된 권한 토큰이 아니다.** 이 driver는 GitHub에 접속하지 않으며
  `go_body` 작성자/삭제/수정/철회/사람의 승인 의도를 인증하지 않는다.
  반드시 실행자가 아닌 승인 담당자가 #19에 해당 mode·commit·bundle SHA·대상·창·중단 조건을 명시한 GO를
  먼저 게시하고, 운영자가 실행 직전 GitHub 최신 원문·작성자·시각·철회 여부를 직접 대조한다.
  이 과정 없이 JSON의 true/null만 바꿔 실행하면 승인 절차를 이행한 것이 아니다.
  `go_body_sha256`는 UTF-8 원문 body의 바이트 해시(임의 trailing newline 추가 금지)다.
- root·sudo 관리자와 승인 기록을 전달하는 운영자는 신뢰 주체다. 악의적인 root를 격리하거나
  스크립트 직접 실행을 강제로 금지하는 보안 경계는 아니다. 자동 GH 승인이 필요한 도구로 해석하지 않는다.

## 준비: PC에서만 (운영 접속 없음)

최신 독립 리뷰/병합 commit을 확인한 뒤 새 출력 경로를 지정한다.
아래 `<...>`는 실제 값으로 바꿔야 하며 미확정 값을 예시 승인으로 사용하지 않는다.

```text
python -I ops/p1-sec01-probe-driver/prepare.py <repo> <40자리 승인된 commit> <새 bundle 경로>
python -I <bundle>/driver.py check-bundle <bundle> <출력된 bundle_sha256>
```

`prepare.py`는 네트워크 연결, 운영 디렉터리 쓰기, Git fetch, SSH 전달을 하지 않는다.
두 번째 명령은 `BUNDLE_BYTES=VERIFIED; GO=NOT_VERIFIED`여야 한다.
`permit.example.json`은 의도적으로 실행 불가한 템플릿이다. 실제 시각/창/승인은 이 PR에서 생성하지 않는다.
소스 변경 시 새 commit·bundle SHA와 새 리뷰 범위를 확정한다. 옛 회차 GO는 재사용하지 않는다.

## 현장 전제와 순서 (독립 리뷰·새 GO 이후에만)

1. 담당자끼리 Harbor/Edge/Backup/Runner/Probe/Ansible을 포함한 실제 Runtime 비중첩을 확인한다.
   이 driver의 host lock은 다른 호스트/수동 명령을 통제하지 않는다. 로컬 `/proc` Probe/Backup 검사만으로
   전역 작업창이 비었다고 판단하지 않는다. 단톡 사전 공지와 인수자를 확인한다.
2. 승인된 전달 경로로 bundle 전체를 control01의 **새 root 소유 0700 폴더**에 전달한다.
   source 폴더 0700, 파일 0600으로 제한하고 비신뢰 사용자의 쓰기 권한/링크가 없어야 한다.
   이 로컬 staging도 사전 승인된 범위 안에서만 한다. `/root/sec01-probe-driver-state`는
   영속 0700 상태 폴더이며, 세션/claim은 자동 삭제하지 않는다.
3. `precheck` 전용 permit을 작성한다. 승인 본문과 mode/commit/bundle/호스트/실행자/세션/시각을 대조하고,
   GitHub 원문·공지·비중첩을 확인한 시각을 `checked_at`에 기록한다(60초 이내).
   `approved_at <= window_start <= 현재 <= latest_start`여야 한다.
   `window_end - latest_start`는 precheck **240초 이상**, run **1380초 이상**으로 둔다.
   기존 lifecycle 예산(관측·실패 진단·정리·사후 확인·봉인)을 줄이지 않고 dispatch 여유를 포함한다.
4. control01 root의 유지되는 콘솔에서 아래를 실행한다. SSH 단절을 예상하면 기존 승인된 지속 콘솔을
   사용한다. 새 `nohup`/인증 우회 방법을 임의 도입하지 않는다.

```bash
python3 -I /root/<bundle>/driver.py check-bundle /root/<bundle> <bundle_sha256>
python3 -I /root/<bundle>/driver.py execute /root/<bundle> <bundle_sha256> /root/<precheck-permit>.json
```

5. precheck의 `script_rc=0 / driver_rc=0 / seal=VERIFIED` 및 실제 원본을 확인한 뒤,
   `run`에 대한 별도 사전 GO를 확인한다. 같은 GO가 양 mode를 명시적으로 포함한 경우에만 같은 URL을 사용할 수 있다.
   **같은 session/실행자/commit/bundle**, 같은 부팅의 **5분 이내** precheck를 요구한다.
   GO 확인이 늦어지면 실행하지 말고 새 precheck 승인·세션부터 조정한다. 이전 marker를 지우지 않는다.

```bash
python3 -I /root/<bundle>/driver.py execute /root/<bundle> <bundle_sha256> /root/<run-permit>.json
```

각 mode는 영속 claim `(GO URL, mode)`와 배타적인 세션 폴더로 **1회만** 시작한다.
새 session 이름으로 동일 GO를 재사용할 수 없다. 중간 실패도 claim을 소비한다.
시스템 전체 driver lock + 기존 probe/backup 프로세스 검사 뒤에 실행한다.
실행 환경은 고정 allowlist이며 `BASH_ENV`, 외부 `KC`, `PYTHONPATH`를 상속하지 않는다.

## 실패·정리·결과 해석

- 중단 시 driver의 INT/TERM/HUP는 Probe shell에 TERM 1회만 전달하고 종료를 기다린다.
  shell의 기존 handler가 제한 시간 안에서 UID 확인·삭제·사후 확인·봉인을 수행한다.
  outer timeout/KILL로 cleanup을 잘라내거나, driver가 `kubectl delete <이름>`으로 대신 정리하지 않는다.
- I/O hang·SIGKILL·전원 종료는 자동 복구가 보장되지 않는다. 미완료 세션/claim을 보존하고 다음 시험을 중단한다.
  수동 인수자가 실제 객체 UID·소유권·노드·기준선·진행 프로세스를 확인한 뒤 별도 복구를 결정한다.
  드라이버/기존 Probe 코드를 rollback한다는 이유로 객체를 이름 삭제하거나 claim을 초기화하지 않는다.
- 원 shell rc는 `result.json`의 `script_rc`에 보존한다. `driver_rc=0`도 AC PASS가 아니다.
  기존 rc2 사전 STOP, rc3 관측 실패, rc12 봉인 실패, rc13 정리 실패의 의미를 유지한다.
  driver의 원본/봉인 대조 실패는 driver_rc12(원 rc13은13)이며 실패를 성공으로 올리지 않는다.
- 결과 부재/미확인 seal은 `collect`가 성공 패키지로 내보내지 않는다. 원 폴더를 보존하여 기존 승인된
  별도 파일 전달 절차로 장애 조사한다. 무조건 재실행하지 않는다.

## 원본 회수 (서버 조회 없음)

```bash
python3 -I /root/<bundle>/driver.py collect /root/sec01-probe-driver-state/<session> /root/<새 receipt 폴더>
```

완료된 mode의 tar.gz·sidecar·completion·sealcheck, driver stdout/stderr·결과·permit·BUNDLE만
allowlist로 복사한다. 원 archive를 재압축/추출/수정하지 않는다. archive 내부 목록·파일 바이트와
폴더 manifest·completion rc를 대조하며, 새 회수 폴더에 SHA256SUMS를 쓴다.
사전 permit의 공개 GO 본문에는 비밀값을 넣지 않는다. kubeconfig/키/환경변수 전체는 수집하지 않는다.
PC 수신 후 SHA256SUMS를 다시 대조하고 bundle commit/해시·회수 원본을 새 evidence PR로 연결한다.
공유 저장소 게시 전 민감정보 점검과 독립 증적 리뷰는 별도다. 실패 원본의 rc/시각을 바꾸지 않는다.

## 검증 구분

```text
python -m unittest discover -s ops/p1-sec01-probe-driver/tests -v
```

- 로컬: 정상/거부 permit, 시간창·예산·독립 주체·precheck freshness/boot/source,
  Git blob 기반 공급, 변조·추가·중복·링크 archive 거부, 실패 rc/원 archive 보존.
- Linux CI: 실제 flock 충돌, 환경 allowlist, 신호 전달 후 finalization 대기,
  driver precheck→run 연결(MOCK child), 기존 Probe 실패 경로(가짜 kubectl)의 실제 seal 형식 호환.
- CI/fixture는 실제 Kubernetes/Harbor/etcd/운영 네트워크를 사용하지 않는다.
  실제 control01 권한/지속 콘솔·전달 경로·새 GO·T0~T3·정리/회복은 **RUNTIME NOT_RUN**이다.
