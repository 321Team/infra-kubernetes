# CP1 CNI 소유 서비스 기동·종료 확인

상태: `SERVICE_LIFECYCLE_PRIMITIVES_IMPLEMENTED / OPERATIONAL_INTEGRATION_INCOMPLETE / RUNTIME_NOT_RUN`.

기존 소유 unit 게시 모듈은 정의를 적재하는 단계까지 구현돼 있었다. 이번 변경은 별도 journal에 회수 예약 링크 설치와 1회 기동 의도를 기록하고, 실제 기동 여부 및 하위 cgroup 종료를 확인하는 구성 요소를 추가한다. 운영 root entrypoint와 인증된 외부 finalizer는 아직 연결하지 않았다.

## 구현한 경계

| 영역 | 구현 및 실패 시 동작 |
|---|---|
| 회수 예약 | boot recover 링크를 먼저 만들고 timer 링크를 만든다. 매 링크 전 intent와 생성 후 directory/journal을 fsync한다. 기존 파일·링크는 내용이 같아도 채택하지 않는다. |
| 1회 기동 | 원본·staged source·loaded unit argv·비활성 상태·기한을 검사한다. 인증된 호출자의 activation/전체 before/초기 journal 확인 후 `LAUNCH_INTENT`를 먼저 저장하고 정확한 timer/work 쌍에만 start를 요청한다. |
| 불확실한 시작 | timeout·오류·경고·출력 잘림·journal 장애·프로세스 중단 뒤에는 같은 작업의 start를 반복하지 않는다. 알려진 intent 이후는 회수 대상으로 남긴다. |
| 기동 확인 | start 접수와 실제 work 활성 상태를 별도로 기록한다. 같은 boot, 활성 독립 guard, MainPID와 pending job, 최초 dispatch의 고정 5초 창을 확인한다. 재조회로 창을 연장하지 않는다. |
| 준비 중 guard | 최초 start 대기 허용은 pristine transition과 활성 guard가 있는 5초에 한정한다. 진행한 transition은 durable dispatch 접수 뒤 기존 준비 guard로 검증하며, 접수가 불확실한 진행은 회수한다. |
| 부분 예약 회수 | 완전히 적재한 unit 정의와 launch journal의 알려진 링크 생성 prefix를 묶는다. 미생성 또는 생성 중이던 링크의 부재만 인정하며, 나중에 등장한 동일 링크도 임의 삭제하지 않는다. |
| 종료 확인 | 실제 cgroup2 파일시스템의 소유 service 경로만 읽는다. `populated`는 자손을 포함한다. 정확한 service 디렉터리 부재만 부재 증거로 인정하며, 상위 경로·events 누락, symlink, 권한 실패, 다른 파일시스템은 오류다. |
| 로컬 명령 어댑터 | 원본 helper 바이트 해시와 root 권한을 검사한다. exact unit 조회와 daemon-reload, owned start/stop 조합만 허용한다. 일반 enable/restart, 다른 service, shell 명령은 거부한다. |

`cp1_cni_cgroup.quiesced`는 같은 절대 기한 안에 새 unit 관측 → 커널 cgroup 읽기 → 새 unit 관측을 수행한다. loaded ownership과 liveness가 바뀌면 종료 완료로 인정하지 않는다. 호출자는 global lock 획득 후 이 확인을 다시 해야 한다. PID 0만으로 종료를 판정하지 않는다.

새 어댑터의 전체 호출 기한은 최대 150초이며 매 명령은 남은 시간과 5초 중 작은 값으로 제한한다. unit 조회는 최대 10초와 호출자가 넘긴 절대 기한 중 작은 값을 사용한다. 이 수치는 대상 VM에서 성능 검증을 마쳤다는 뜻이 아니다.

소스 의존성 manifest는 24개에서 27개로 확장했다. 원본 4개 파일과 기존 영수증/controller 계약은 변경하지 않았다. `UnitFiles`는 운영 entrypoint가 없는 manifest를 계속 거부한다.

## 실행 전 반드시 연결할 부분

이 코드는 라이브러리 구성 요소이며 운영 실행 CLI가 아니다. root 호출자는 source staging, 운영 결정의 인증, 별도 잠금 journal과 global lock을 실제로 확보해야 한다. 단순 `approved=true` 입력은 없다.

work entrypoint는 방화벽을 변경하기 전에 durable `START_REQUESTED`/`WORK_OBSERVED`와 활성 guard를 직접 읽어야 한다. systemd가 journal의 접수 기록보다 먼저 work를 실행할 수 있으므로 이 초기 대기는 불변 launch 시각으로 제한해야 한다. `LAUNCH_INTENT`만으로 변경을 허용하지 않는다. 해당 entrypoint는 이번 변경에 구현하지 않았다.

외부 finalizer는 모든 소유 service 밖에서 stop → recursive cgroup 종료 → 전체 방화벽 원복 → 새 인증 관리 연결을 확인한 뒤 unit 파일을 삭제해야 한다. 부분 unit **정의 게시** 중단의 회수, guard/membership 인계, 새 SSH 관리 세션 transport, 삭제 후 not-found 관측, 후속 controller 연결도 남아 있다. 이번 구현은 부분 **예약 링크** 설치 회수까지 포함한다.

## 검증

전체 단위·회귀 455/455 PASS, 실패·오류·skip 0. 직전 414개 대비 고유 시험 41개를 추가했다. 시험 출력과 검증 메타데이터를 함께 보존한다.

- 실제 private 임시 파일시스템에 root 소유 source, unit, wants 링크, 잠금 journal을 만들고 fsync 전후 중단을 주입한다.
- 각 링크 생성 전후 중단, journal/reload 실패, 같은 링크의 사전 충돌, 변경된 source·argv·pending job을 검사한다.
- 기동 요청 전후 중단, 알 수 없는 명령 결과, 두 번째 기동, 만료·boot 변경, 접수/활성 확인 구분을 검사한다.
- 부분 예약 회수 중 생성하지 않은 링크가 등장하는 경합과 proof 이후 링크 변경을 거부한다.
- cgroup 파일의 부재/누락/권한/symlink/다른 파일시스템/FIFO/크기 초과/경로 inode 교체를 검사한다. 자손 프로세스가 남거나 unit이 재시작하면 종료로 인정하지 않는다.
- pinned local systemd 어댑터 → 실제 unit 파일 읽기 → typed D-Bus 결과 파싱 → service arm/start journal을 연결한 모의 통합시험을 포함한다. 파일 변조 후 추가 기동도 차단한다.
- 실행기 커널의 `/sys/fs/cgroup` 파일시스템 종류를 읽는 시험 1건이 포함된다. CP1 대상 커널의 검증은 아니다.

systemd와 방화벽 명령, 대상 CRI·관리 SSH 동작은 mock이다. 실제 service start/stop, CP1 방화벽 변경, Runtime 인수, 재부팅 회수는 수행하지 않았다. 별도 리뷰 요청이나 VM 재수집 요청을 추가하지 않았으며 운영 연결이 갖춰진 통합 묶음을 준비한다.

## 원리 확인에 사용한 공식 자료

- [systemd v257 systemctl 문서](https://github.com/systemd/systemd/blob/v257/man/systemctl.xml): enablement 링크와 start는 별도이며 `--no-block` 접수는 실행 완료를 의미하지 않는다.
- [Linux cgroup v2 문서](https://docs.kernel.org/admin-guide/cgroup-v2.html): cgroup2 파일시스템 식별과 `cgroup.events`의 재귀 populated 의미.

원리 확인에 사용한 문서 버전은 대상 VM 버전의 실행 검증을 대신하지 않는다. CP1 인수·CP2/3 변경·Join은 계속 보류한다.
