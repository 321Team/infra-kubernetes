# CP 방화벽 변경안 검증 기록

검증일: 2026-09-10. 로컬 Python 표준 라이브러리로 검증했다.

| 검증 | 결과 |
|---|---|
| unittest | 25개 PASS — 계획 11개 + 실행 도구 14개 |
| 정확한 /32·포트·대상 제한 | PASS. /24 및 추가 6443 입력 거부 |
| 기존 규칙 보존 | PASS. 한 plane에 기존 규칙이 있으면 추가 후보에서 제외하고 rollback 인자를 만들지 않음 |
| 관리 SSH / identity / zone / 수집 상태 검사 | PASS. SSH 부재, 잘못된 노드, unreachable, zone 변경, 잘린 출력 거부 |
| 무결성·입력 보호 | PASS. sidecar 변조, 내부 변조, 체크섬 누락, 경로 이탈, symlink, JSON 중복 키 거부 |
| 실제 glrr0dsq 제출본 처리 | 압축파일·내부 8/8 해시 검증 후 3개 CP의 추가 후보 생성 |
| 생성된 추가 후보 | 노드별 6개 × 2개 설정(Runtime/Permanent) × 3대 = 36건 |
| 생성 결과 SHA-256 | `2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7` |
| 원격 실행·서비스 검증 | 미실행. 계획 생성기는 오프라인 전용이며 후속 실행기는 별도 로컬 모의 검증만 수행 |
| 독립 리뷰 / Runtime 승인 | 본 로컬 검증으로 대체하지 않음 |

재현 명령:

```bash
python3 -m unittest discover -s tests -p 'test_cp*.py' -v
```

실제 증적의 계획 재현 명령은 [실행 절차](../../runbooks/ICR-D-NET-01-01-CP-PORTS.md)에 있다. 테스트 fixture는 최소 필드로 만든 합성 데이터이며 원본 운영 로그를 포함하지 않는다. 실제 제출본 실행에서는 고정된 원본 압축파일을 입력으로 사용했다.

## CP1 실행 도구 검증 추가

- 정확한 12건 적용 및 역순 복구, 기존 규칙 보존.
- 영구 의도 기록 실패 시 쓰기 0건, 확인된 부분 성공만 복구.
- timeout/ALREADY_ENABLED 등 불명확한 소유권의 자동 삭제 거부.
- 초기 snapshot 불일치, 추가 etcd member, 복구 전 Node/member 변경, firewall drift 중단.
- Inventory 대상 불일치, 점검 파일 변조, 15분 경과 입력 거부.
- SSH 실패 단계·진단 보존 및 token 마스킹, 부분 결과 압축파일과 체크섬 검증.
- 기존 glrr0dsq의 public Runtime/Permanent 출력 6건을 신규 zone parser로 처리 성공.

모의 Backend는 방화벽·etcd·SSH에 접속하지 않는다. 실제 journaling fsync/lock, sudo, NetworkManager, kubectl exec와 firewalld 연동은 서버 점검 및 canary 결과로 추가 검증해야 한다. 로컬 테스트는 독립 리뷰와 운영 인수를 대체하지 않는다.
