# v4 배치 도구 정합화 준비 — 2026-10-09

상태: **PREPARATION / RUNTIME NOT RUN / 배포 GO 미확정**. 이 문서는 기존 승인 소스의 배치 불일치를 해소하기 위한 검토안이다. 코드 수정·자동 배포기·운영 교체 명령을 추가하지 않는다. 실행 전 Lead가 범위와 아래 미확인 입력을 확정해야 한다.

책임: K8S Lead 이하영. 준비 지원: 이권욱(CODEX_ASSISTED). [baseline#10](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-6063150237), [evidence#174](https://github.com/321Team/fds-project-evidence/pull/174) 현재 HEAD `fa75b46067585e1899496f2abb69e6facc13d1d5`의 독립 리뷰를 근거로 한다. 증적 편입 승인과 서버 교체 승인을 구분한다.

## 1. 고정 기준과 교체 후보

- 승인 소스 기준: `b4303f2df512a8791d2421203cf159567d076b2a`, [기존 승인](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-6009176490). 이 과거 승인 범위를 이번 5대 교체에 확대하지 않는다.
- 조회한 main: `81ad791d5a6a611d3c7f0068bd638e60ec8e28f8`.
- Git blob 직접 비교: 기준·main의 RUNBOOK_FINAL.md 및 code8개 **9/9 SHA256 동일**. 새 소스 수정이 아니라 배치본 정합화 후보이다.
- 배치 관측: **10/9 00:00 회차**의 5대×9개=45개, MATCH28 / DIFFERENT17. 현재 시각의 실행 직전 점검으로 재사용하지 않는다.

| 대상 | 교체 후보 | 유지할 비교 대상 |
|---|---|---|
| k8s-control01 | code/audit-window.py 1개 | 나머지8개 |
| k8s-control03 | audit-window.py, cluster-change.py, node-evidence.py, render.py 4개 | 나머지5개 |
| k8s-worker01 | 위4개 | 나머지5개 |
| k8s-worker02 | 위4개 | 나머지5개 |
| k8s-worker03 | 위4개 | 나머지5개 |

`manifest-20261009.json`에 17개 각각의 source/deployed 절대 경로, 관측/목표 SHA256, 관측 시각·uid/gid/mode/size/mtime, 보존할28개 해시를 적었다. JSON은 00:00 관측을 보존하며 당시 미수집한 SELinux/ACL·백업·GO를 미확인으로 둔다. 08:20 추가 메타데이터 수집은 §7의 별도 증적으로 연결한다. **이 JSON은 실행 가능한 승인 목록이 아니다.**

control02는 이번5대 수집에서 제외된 별도 검증 대상이다. 이번 계획으로 현재 control02의 일치를 재확인했다고 하지 않는다. Lead 확인 없이 이번 배포에 추가하지 않는다.

## 2. 불일치 영향과 제외 범위

| 파일 | 기존 증적에서 확인된 영향 |
|---|---|
| audit-window.py | 엄격한 ausearch 결과 분류·UTC 날짜 경계 보완 부재. 구버전으로 새 AVC 무발생 판정을 만들지 않음 |
| cluster-change.py | WorkloadEndpoint 부재 시 Pod fallback 및 rollback 전 Felix 검증 순서 보완 부재. 구버전으로 승인된 안전한 rollback이라 주장하지 않음 |
| node-evidence.py | 원 규칙 출력·상세 비교 보완과 차이 |
| render.py | 후속 CP/Worker 허용 규칙 생성 변경과 차이. 옛 입력/생성 YAML 재적용 금지 |

복사 대상은 위17개 도구 파일뿐이다. `/root/generated-gate2-attempt01`, runtime-input/config.lock, 생성 YAML, HEP/GNP/Felix, firewalld, NTP, SSH 설정, gate2-state, 기존 증적을 변경하지 않는다. RUNBOOK 및 일치28개를 묶음 압축 해제로 덮어쓰지 않는다. 현 정책의 nets 순서·CP01 UDP123 중복 허용은 별도 판단 대상이다.

저장소의 과거 `REVIEW_FIX_SHA256SUMS`는 현재 고정 Git blob의 전체 배포 manifest로 사용하지 않는다. 이번 목표 해시는 manifest의9개 기준과 승인 Git blob을 직접 대조한다. 오래된 검증 기록을 이번 배포 성공으로 소급하지 않는다.

## 3. 실행 전 Lead가 채울 항목

1. **대상·실행자·용도**: 5대의 정확한 배치 위치와 실제 호출 주체(수동·예약·다른 세션)를 확인한다. 제안 순서는 control01의 audit 파일1개를 첫 대상으로 확인한 뒤, Lead가 승인한 나머지4대를 한 대씩 처리하는 것이다. 이는 canary 확정 또는 실행 GO가 아니다.
2. **fresh 사전점검**: 각 호스트/정상 계정,17개 원본 및 비교대상28개의 SHA256, 일반 파일·symlink/hardlink·파일 크기·inode, 부모 디렉터리 소유/권한과 쓰기 가능한 filesystem을 확인한다. 관측 SHA가 manifest의 이전/목표 어느 쪽과도 다르면 STOP. 이미 목표 SHA이면 재교체하지 않고 상태를 기록한다.
3. **메타데이터·백업**: uid/gid/mode, SELinux 실제·기대 문맥, ACL/추가 xattr를 확인하고 변경 전 파일별 사본과 metadata를 새0700 증적/백업 위치에 보존한다. metadata가 없으면 임의 root:root/0750/context로 맞추지 않는다. 백업 bytes/해시와 복구 방법 검증 전 교체하지 않는다. 민감 ACL/사용자 정보의 공개 범위는 별도 제한한다.
4. **동시 사용 배제**: 교체할 도구를 실행하는 수동·예약·세션을 확인하고 전체 교체/검증/복귀 동안 호출이 없음을 담당자가 인수한다. `ps` 한 번이나 열린 Actions0건만으로 확정하지 않는다. 불명확하면 STOP.
5. **실행 창·복귀 인수**: 소스 SHA·manifest·호스트·교체17개 중 승인한 부분집합·실행자·콘솔/복구 담당·중단 시각을 기록한 이번 GO를 받는다. 사용자00~09시 단독 창, 기존 증적 승인, 과거 Runtime GO만으로 대신하지 않는다. 제한 시간 안에 검증/복귀할 수 없으면 시작하지 않는다.

위 입력이 채워진 뒤 실행 명령·정확한 staging/backup 경로를 고정해 검토한다. 현 문서는 그 입력 없이 복사 명령을 실행하도록 안내하지 않는다. SELinux·방화벽·SSH 통제 해제는 해결책에 포함하지 않는다.

## 4. 승인 후 수행 순서와 판정 (이번 PR에서 미실행)

1. 고정 Git blob의 승인 파일만 새 staging에 준비하고 목표 SHA를 대조한다. 운영 디렉터리에 전체 archive를 풀거나 현재 branch 전체를 덮어쓰지 않는다.
2. 대상별 fresh 사전점검과 백업을 수행한다. backup/staging/원본의 경로·실제 호스트·시각·해시·metadata를 기록한다. 출력 오류·권한 부족·드리프트는 정상/빈 결과로 바꾸지 않는다.
3. 호출이 배제된 한 호스트에서 승인한 파일집합 전체를 staging 검증 후 교체한다. 일부만 바뀐 중간 상태에서 audit/render/cluster-change를 실행하지 않는다. 소유/권한/SELinux/ACL은 승인된 기대값으로 유지하고 확인한다. 파일별 atomic rename만으로 다중파일 교체가 atomic하다고 주장하지 않는다.
4. 운영 도구 자체의 apply/rollback/validate 명령을 실행하지 않고, **읽기 검증**으로 목표 hash17 및 유지대상hash28, metadata, 파일 유형을 확인한다. Python 구문은 바이트를 `compile(..., 'exec')`만 하며 import·코드 실행·pycache 생성을 하지 않는다. 모의 시험은 별도 작업 디렉터리/격리 환경에서만 수행한다.
5. 대조표 전체와 원래 대상/실행 범위를 확인한 후 Lead가 도구 사용 재개를 판단한다. 파일 정합화 완료는 audit 무발생, 정책 보호, rollback 성공, AC4e/AC4 전체인수와 별개다. 승인된 추가 검증만 별도 회차로 수행한다.

성공 조건: 승인한 파일집합의 목표hash/metadata 일치, 비대상파일 불변, 혼합 버전 없음, 오류0, 원본·백업·변경·사후 자료가 연결됨. 일부 파일 일치 또는 구문 PASS로 전체 성공을 선언하지 않는다.

## 5. 중단과 복귀

드리프트·메타데이터/백업 실패·예상 밖 링크·동시 실행·부분 교체·비대상 변경·시간 초과이면 다음 호스트로 진행하지 않는다. 부분 교체 시 호출 차단을 유지하고 **그 호스트에서 이번에 바꾼 파일만** 확인된 백업 bytes와 uid/gid/mode/context/ACL로 되돌려 원래 해시·metadata를 다시 확인한다. 파일 복귀를 위해 cluster-change rollback, 정책 재적용, 서비스 재시작을 실행하지 않는다.

복귀한 구버전에는 알려진 결함이 남는다. 따라서 이전 bytes 복귀를 도구 안전성 또는 업무 복구 PASS로 표시하지 않고 **RESTORED_WITH_KNOWN_TOOL_DEFECT / 해당 도구 사용 보류**로 인계한다. 원본 백업의 불완전성이나 복귀 실패가 있으면 콘솔 담당에게 인수하고 더 넓은 설정 변경으로 해결하려 하지 않는다.

## 6. 이번 준비에서 검증한 것

- 승인commit/main Git blob9개씩 직접 SHA 대조:9/9 동일.
- evidence174 COMPARISON의45행과 manifest: 변경17/보존28,5호스트, 중복(host,path)0; 관측hash와 uid/gid/mode/size/mtime가 수신 원본과 일치.
- 운영 소스·기존 도구·manifest·워크플로 변경0. 이번 변경은 새 준비 문서/JSON2개뿐이다.
- STATIC 문서·입력 대조 완료. RUNTIME 배포/복귀·새 AVC 관측·AC 인수·독립 리뷰는 미완료다. 저장소 CI 적용 여부/결과는 PR에서 별도로 표시한다.

미확인 입력을 임의 값으로 채우거나 새로 생성한 계획을 실서버 증적으로 표시하지 않는다. 준비 문서의 리뷰를 요청하고 작업창 종료 후 회수한다.

## 7. 08:20 읽기 전용 수집 보완

[증적 PR182](https://github.com/321Team/fds-project-evidence/pull/182), 수신 원본 고정 HEAD `6d7728fcbb3c250646ce0822cf4f74fce7ea4a9f`의 [분석](https://github.com/321Team/fds-project-evidence/blob/6d7728fcbb3c250646ce0822cf4f74fce7ea4a9f/evidence/P1-K8S-01/v4-tool-metadata-20261009T082015/analysis.json). 수신 PC 2026-10-09 08:20:15~08:21:27 KST / 호스트 08:20:17~08:21:28 KST. 독립 리뷰는 대기 중이며 PC/호스트 시계 오차를 이번에 검증하지 않았다.

- 5대45파일 해시는 00:00 회차와 모두 동일하여 교체 후보17/보존28이 유지된다. 관측 소유0:0/권한0750, 기본 숫자 ACL, 일반 파일·nlink1, 읽기 중 안정이 확인됐다. 선택한 SELinux/ACL/capability xattr 외 전체 xattr·부모 ACL은 미수집이다.
- 실제 SELinux type: worker01의9파일은 `user_home_t`, 나머지36파일은 `usr_t`. 모든 호스트 `matchpathcon -n` 기본 type은 `usr_t`다. 전체 실제 context user `unconfined_u`와 기본 user `system_u` 차이도 있으므로 36파일을 전체 context 일치라고 표시하지 않는다. 5대 모두 enforcing 관측이다.
- **worker01의 비교대상5개도 type 차이가 있다.** 기존17개 교체 후보에 나머지5개를 자동 추가하거나 라벨을 일괄 변경하지 않는다. Lead는 관측값 보존과 별도 정정 중 적절한 처리, 정확한 기대 context/metadata와 복귀값을 결정한다. 경로 기본값 조회만으로 장애·AVC·원인 또는 승인 기대값을 확정하지 않는다.
- 정책 기본값 조회는 메타데이터 수집보다 약1분 뒤의 별도 회차이며, 동시 정책 변경 배제는 입증하지 않았다. 따라서 실행 직전 새 해시/link/metadata 확인은 여전히 필요하다.
- 백업·교체·relabel·도구 실행·정책 적용0. **검증된 백업, 실제 호출 주체·동시 사용 배제, 대상/실행자/복구 인수 및 GO는 여전히 미확정**이다. 읽기 증적 추가로 배포 준비 완료를 선언하지 않는다.

## 8. 09:16 원본 사본·부모 ACL 보완과 복귀 준비

사용자 단독 창은 [09:10~12:00 KST로 연장](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6071495239)됐다. §3의00~09시는 이전 창이며 새 실행 승인을 대신하지 않는다. 리뷰는 연장 창 이후 회수한다.

[증적182 HEAD f4be8a13af393ef9cb8650a58dc5a33176ac3c6f](https://github.com/321Team/fds-project-evidence/tree/f4be8a13af393ef9cb8650a58dc5a33176ac3c6f/evidence/P1-K8S-01/v4-tool-backup-preparation-20261009T091654)에서 **PC09:16:54~57 / 호스트09:16:56~58 KST**의 새 조회를 보존했다.5대45개 hash는 이전과 동일하며 후보17개·168,782bytes의 원본 내용을 PC로 수신해 해시/구문 대조했다. 부모20개를 포함한65개 객체의 열람 가능한 전체 xattr 목록은 security.selinux뿐이며, root0:0·기본 숫자 ACL을 확인했다. 부모 /opt·/opt/fds는0755, 패키지 root·code 및 파일은0750이다. metadata·xattr 읽기 전후 및 부모 전후는 안정이었으나 다중 파일 원자적 snapshot/동시 호출 배제는 아니다.

원본 stdout·17개 bytes는 **실행자 PC 비공개**로 보존하고 archive39개 member를 수신 파일과 byte 대조했다. 보관 archive SHA256은 `a86024b31d9433e1fdb63127f4a30e7360138d69cd682988b97409acd37a2dfa`다. Git 증적의 projection.json은 파일 내용 필드만 제외한 투영본이며 원 stdout이 아니다. 비공개 원본을 받지 않은 검토자가17개 bytes까지 재검증할 수 있다고 하지 않는다. 전달·보관 인수는 별도다.

| 복귀 준비 항목 | 이번에 확보한 것 | 실제 교체 전에 남은 것 |
|---|---|---|
| 원본 bytes |17개 PC 사본·hash 및 archive 대조 | 실행 직전 현장 원본의 동일성, 호스트별 신규0700 백업 위치·복구 담당의 접근/회수 경로 |
| 파일 metadata | uid/gid/mode/시간·전체 열람 가능 xattr·ACL | 관측값과 승인 기대값 구분, 임시 파일에서 적용·재조회 검증. Windows 사본 속성을 Linux 복구값으로 쓰지 않음 |
| 부모·filesystem |20개 부모 metadata/ACL·가용 bytes/inode·readonly flag | 실제 staging/백업 filesystem 및 공간·정리 범위. flag=false를 쓰기 성공으로 간주하지 않음 |
| SELinux | 관측 문맥 bytes 보존, worker01 type 차이 유지 | Lead의 보존/정정 선택·정확한 적용/복귀 context. 보존5개 relabel 추가는 별도 범위 결정 |
| 복귀 검증 | PC archive에서 원본 bytes 대조 | 서버에서 backup→임시 복원→hash/uid/gid/mode/context/ACL 재검증, 호출 배제·복구 인수 및 정확한 GO |

실행안 확정 시 호스트별 백업 경로·실행 시각·실제 inode/mtime·원본/백업 해시를 같은 기록에 연결한다. 사전 사본과 현장 bytes가 다르면 자동 덮어쓰기/복귀하지 않고 STOP한다. 복귀 대상은 해당 호스트에서 이번에 교체한 파일에 한정하고, 비대상28개 hash를 다시 대조한다. 복원 후 inode/dev/ctime까지 원래 값과 같아야 한다고 요구하지 않는다. 그 값은 수집 식별값이며 복귀 판정은 내용과 승인된 복원 가능 metadata를 기준으로 한다.

**SERVER_BACKUP NOT_CREATED / RESTORE_TEST NOT_RUN / RUNTIME_APPLY NOT_RUN.** 이번 서버 작업은 읽기뿐이고 원격 임시 파일 정리 대상도 없다. PC 사본 검증을 서버 복귀 시험이나 준비 완료로 격상하지 않는다. 운영 도구·정책·manifest17/28 변경0이며 기존 관측 manifest는 당시 기록으로 보존한다.
