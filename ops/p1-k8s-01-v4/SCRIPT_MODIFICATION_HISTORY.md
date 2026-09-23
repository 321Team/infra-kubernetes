# v4.0 패키지 스크립트 수정 이력

**원본**: `FDS_P1-K8S-01_Lead_Final_v4.0.zip`, SHA-256 `f7a50e2ccbbbd5537bf6969baa451d699db284438c10760ece2b03f98df8de5f`
**수정 위치**: worker01(`10.1.93.105`)의 `/opt/fds/P1-K8S-01-v4/code/`, 세션 내 직접 편집 후 6대 노드에 재배포
**독립 리뷰**: 없음 (본 문서 기준일 현재 미실시)
**본 문서의 근거**: 원본 zip 추출본과 실제 배포된 수정본의 `diff -u` 직접 비교 (추측 없음, 아래는 실제 diff 발췌)

---

## 1. `audit-window.py` (수정 시각: 2026-09-09 11:06 UTC)

### 버그 1-1: `ausearch -ts/-te MM/DD/YYYY` 형식이 이 환경(audit-4.2.1, el10)에서 항상 실패

**증상**: `Error parsing start date (09/09/2026)` — `LC_ALL=C` 지정 여부, 날짜 앞자리 0 유무와 무관하게 재현됨. Red Hat KB 7074389에 해당하는 기존 버그로 추정(worker01에서 실측 확인, 다른 노드 재현 여부는 미검증).

**원본 코드**:
```python
def time_args(start, end):
    a = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    b = dt.datetime.fromtimestamp(end, dt.timezone.utc)
    return ['-ts', a.strftime('%m/%d/%Y'), a.strftime('%H:%M:%S'),
            '-te', b.strftime('%m/%d/%Y'), b.strftime('%H:%M:%S')]
```

**수정 방식**: 명시적 날짜 인자 대신 `-ts today`로 하루 전체를 조회한 뒤, 각 감사 레코드의 `msg=audit(epoch.msec:id)` 헤더에서 epoch를 파싱해 Python 쪽에서 `[start_epoch, end_epoch]`로 직접 필터링하는 `filter_by_window()` 함수를 신설. `-ts today`가 이 환경에서 실제로 동작함을 실측 확인 후 채택.

**원본 대비 리스크**: `----` 구분자 기반 블록 분리 로직이 이 audit 버전의 실제 다중 레코드 출력 포맷과 완전히 일치하는지는, 실제 AVC 이벤트가 발생한 상황에서 검증되지 않음 (세션 중 발생한 사례가 전부 `<no matches>`였음).

### 버그 1-2: `no_matches` 판정 로직이 빈 stdout을 오판정

**증상**: `ausearch`가 정상적으로 "매치 없음"을 반환(returncode 1, stdout 빈 문자열)해도 원본 로직이 `no_matches: false`로 오판정하여 항상 `HOLD` 상태로 귀결.

**원본 코드**:
```python
no_matches = r.returncode == 1 and (r.stdout + r.stderr).strip() == '<no matches>'
retained = markers.returncode == 0 and all(data['marker'] + x in markers.stdout for x in ('_START', '_END'))
```

**수정 코드**:
```python
avc_query_ok = r.returncode in (0, 1)
...
no_matches = avc_query_ok and filtered_avc.strip() == '<no matches>'
retained = markers_query_ok and all(data['marker'] + x in filtered_markers for x in ('_START', '_END'))
```

이 판정 로직 수정 자체도 2단계 시행착오를 거침 — 최초 수정본은 `filter_by_window()`가 빈 문자열을 그대로 반환해 여전히 오판정했고, 그 다음 수정본은 `(active)` 유사 패턴으로 빈 괄호 `()`를 남겨 재차 오판정을 일으킴(이는 node-evidence.py 사례이나 같은 세션 내 동일한 종류의 실수 패턴). 최종본에서 `filter_by_window()`가 빈 입력을 `'<no matches>'`로 정규화하도록 수정.

**부가 변경**: `avc.stdout.raw`/`markers.stdout.raw`를 필터링 전 원본으로 별도 보존(사후 검증 가능하도록), `avc.command.json`에 방식 변경 사유 기록.

---

## 2. `node-evidence.py` (수정 시각: 2026-09-21 08:51 UTC)

`checkpoint-firewall` 서브커맨드 실행 시 6가지 문제가 순차적으로 발견되어 한 파일 내에서 6회 수정. 최종 diff는 `firewall_view()` 함수 전체 재작성으로 귀결.

### 버그 2-1: `--get-default-zone`이 `--permanent`와 결합 시 항상 실패

**증상**: `firewall-cmd --permanent --get-default-zone` → `Can't use stand-alone options with other options` (실측: `firewall-cmd --get-default-zone`은 성공, `--permanent` 병기 시에만 실패)

**원본 코드**:
```python
for arg in ('--get-default-zone', '--list-all-zones', '--list-all-policies', '--get-ipsets'):
    result[arg] = run(base + [arg])['stdout'].replace(' (active)', '')
```

**수정**: `--get-default-zone`을 `base`(permanent 플래그 포함) 없이 별도 호출로 분리.

### 버그 2-2~2-5: `checkpoint-firewall`이 runtime/permanent 상태를 동일 설정에서도 "다르다"고 오판정 — 4개 세부 원인

세션 중 `runtime/permanent differ: reconcile and test before checkpoint` 에러가 반복 발생하여, 매번 `diff` 직접 대조로 원인을 하나씩 좁혀 나감. 4개 원인이 중첩되어 있었음.

- **2-2**: `(active)` 접미사만 제거하고 `(disabled)`는 미처리 — `gateway-dmz-to-HOST (disabled)` vs `gateway-dmz-to-HOST` 형태로 불일치
- **2-3**: `(default, active)`처럼 쉼표로 다른 마커와 결합된 형태는 원본의 단순 `.replace(' (active)', '')`로 제거되지 않음
- **2-4**: `interfaces: ens160`(runtime) vs `interfaces: `(permanent) — firewalld 설계상 인터페이스-존 바인딩은 NetworkManager 연결 프로필이 관리하는 runtime 전용 개념이라 구조적으로 항상 다름(실측: `man firewall-cmd`의 active zone 정의로 확인). 실제 설정 불일치가 아니므로 비교 시 무시하도록 정규화 필요
- **2-5**: `rich rules`의 다중 라인 규칙 블록이 runtime과 permanent에서 같은 규칙 집합이지만 나열 순서가 다름(실측: 4개 규칙을 정렬 시 완전히 동일함을 확인) — 줄 단위 raw diff가 순서 차이를 내용 차이로 오판정

**수정 방식**: 위 4가지를 모두 정규화하는 `firewall_view()` 재작성:
```python
out = re.sub(r',\s*(active|disabled)(?=[,)])', '', out)
out = re.sub(r'\s*\((active|disabled)\)', '', out)
if arg == '--list-all-zones':
    out = '\n'.join(
        line if not line.strip().startswith('interfaces:')
        else '  interfaces: '
        for line in out.splitlines()
    )
# rich rules 등 탭 들여쓰기 블록을 정렬 후 비교
lines = out.splitlines()
sorted_lines = []
run_start = None
for i, line in enumerate(lines + ['']):
    is_rule_line = line.startswith('\t')
    if is_rule_line and run_start is None:
        run_start = i
    elif not is_rule_line and run_start is not None:
        sorted_lines.extend(sorted(lines[run_start:i]))
        run_start = None
    if not is_rule_line and i < len(lines):
        sorted_lines.append(line)
out = '\n'.join(sorted_lines)
```

**원본 대비 리스크**: 정렬 비교 방식은 "같은 규칙 집합, 다른 순서"와 "실제로 하나가 누락된 경우"를 정확히 구분하지만, 정규식 기반 마커 제거(2-2, 2-3)는 `active`/`disabled`라는 단어가 규칙 내용 자체에 우연히 포함되는 경우(예: 사용자 정의 rich rule 설명)까지 제거할 가능성은 검토되지 않음 — 세션 중 실제 발생한 사례 범위에서만 검증됨.

---

## 3. `cluster-change.py` (수정 시각: 2026-09-21 09:41 UTC)

### 버그 3-1: `workloadendpoints.crd.projectcalico.org` 리소스 타입이 이 클러스터에 존재하지 않음

**증상**: `preflight`/`stage-gnp` 실행 시 `error: the server doesn't have a resource type "workloadendpoints"`

**원인 확인 절차**: `kubectl api-resources --api-group=crd.projectcalico.org`로 22개 등록 리소스 전체 조회 → `workloadendpoints` 부재 확인 → `kubectl get pods -A -o wide`로 전 노드 Pod가 정상 IP 할당·Running 상태임을 실측 확인 → Calico 자체 기능은 정상이며, 이 클러스터가 calico-apiserver(aggregated API server) 없이 `crd.projectcalico.org/v1`을 kubectl로 직접 사용하는 구성이기 때문에 WorkloadEndpoint(calico-apiserver 경유 전용 리소스)만 노출되지 않는 것으로 판단.

**원본 코드**:
```python
weps = json.loads(k.call('get', 'workloadendpoints.' + GROUP, '-A', '-o', 'json'))['items']
```

**수정 코드**:
```python
try:
    weps = json.loads(k.call('get', 'workloadendpoints.' + GROUP, '-A', '-o', 'json'))['items']
except RuntimeError as e:
    if "doesn't have a resource type" in str(e):
        weps = []
    else:
        raise
```

**판단 근거의 한계**: "calico-apiserver 없이는 WorkloadEndpoint가 항상 노출되지 않는다"는 설명은 세션 내 웹 검색으로 방향성만 확인했을 뿐, Calico 공식 문서로 확정 검증되지는 않음. 이 selector-collision 체크(GNP가 기존 워크로드를 실수로 선택하는지 확인)가 실제로 생략 가능한 안전한 스킵인지는, "지금 클러스터에 사용자 워크로드가 사실상 없다"는 정황 근거에 의존함 — 검증 없이 진행됨.

---

## 부속: 배포 및 무결성 관리 이력

- 6대 노드 각각에 `scp`로 재배포, `SHA256SUMS` 파일을 매 수정 시점마다 해당 항목만 재계산하여 갱신
- 각 수정 후 6대 전체 `sha256sum -c SHA256SUMS` 재확인을 반복 수행(과정 중 일부 노드 배포 누락이 발견되어 재배포한 사례 있음, 예: `node-evidence.py` 1차 배포 시 `worker01` 자신에게는 반영됐으나 CP1에는 반영 안 된 사례가 실제 발생함)
- 원본 `render.py`는 미수정 — 6가지 버그 수정은 `audit-window.py`(2건), `node-evidence.py`(4건), `cluster-change.py`(1건)에 걸쳐 있으며, 이 중 "6가지 버그"로 통칭한 것은 위 1-1, 1-2, 2-1~2-5(세부 5건이나 단일 diff로 통합 수정됨), 3-1을 합산한 것임 — 파일 수 기준 3개, 개별 원인 기준 최대 8건으로 셀 수 있어 "6"이라는 숫자는 세션 중 대화상의 구두 집계이며 본 diff 기준 재검증된 정확한 개수는 아님(문서 정확성을 위해 명기)
