#!/usr/bin/env python3
"""P1-HA-01(#22) Drain 가용성 판정기 — 결정안 2~6절 구현.

사용: python3 judge.py <probe 로그> <timeline.txt> <drain 대상 worker>
timeline.txt: "이름 epoch초" 줄. baseline_start, drain_start, rescheduled_ready, uncordon, end 필수.
판정 구간 W = [baseline_start, end]. tick의 예정시각으로 구간을 나눈다.
timeline은 순서(baseline_start < drain_start < rescheduled_ready <= uncordon < end)와 결정안 2절의
60초 구간 세 개(기준선, 재배치 Ready 뒤, uncordon 뒤)를 먼저 검사하고, 어긋나면 NOT_VERIFIED다.
종료 코드: 0 PASS, 1 FAIL, 2 NOT_VERIFIED
"""
import sys
from collections import defaultdict

LOG, TIMELINE, DRAINED = sys.argv[1], sys.argv[2], sys.argv[3]
INTERVAL, MAX_LATE, MAX_GAP, MIN_PHASE = 0.5, 0.2, 1.0, 60.0
KEYS = ("baseline_start", "drain_start", "rescheduled_ready", "uncordon", "end")


def not_verified(reason):
    print(f"판정: NOT_VERIFIED ({reason})")
    sys.exit(2)


# 2·5절 timeline 검증 — 판정 전에 fail-close
marks = {}
for line in open(TIMELINE, encoding="utf-8"):
    parts = line.split()
    if len(parts) >= 2:
        try:
            marks[parts[0]] = float(parts[1])
        except ValueError:
            pass
absent = [k for k in KEYS if k not in marks]
if absent:
    not_verified("timeline 항목 없음: " + ", ".join(absent))
b0, d0, r0, u0, e0 = (marks[k] for k in KEYS)
if not (b0 < d0 < r0 <= u0 < e0):
    not_verified("timeline 순서 오류")
phases = {"기준선": d0 - b0, "재배치 Ready 뒤": u0 - r0, "uncordon 뒤": e0 - u0}
print("timeline:", ", ".join(f"{k} {v:.1f}s" for k, v in phases.items()), f"/ drain→Ready {r0 - d0:.1f}s")
short = [k for k, v in phases.items() if v < MIN_PHASE]
if short:
    not_verified(f"관찰 구간 {MIN_PHASE:.0f}초 미만: " + ", ".join(short))
w_start, w_end, drain_start = b0, e0, d0

rows = []
for n, line in enumerate(open(LOG, encoding="utf-8"), 1):
    parts = line.split()
    if len(parts) != 7:
        not_verified(f"probe 로그 {n}행 형식 오류")
    t, sched, sent, worker, code, done, _ms = parts
    rows.append((int(t), float(sched), float(sent), worker, code, float(done)))
if not rows:
    not_verified("probe 로그가 비어 있음")
workers = sorted({r[3] for r in rows})
if DRAINED not in workers or len(workers) != 3:
    not_verified(f"worker 목록 이상: {workers}, drain 대상 {DRAINED}")

in_w = [r for r in rows if w_start <= r[1] <= w_end]
by_tick = defaultdict(list)
for r in in_w:
    by_tick[r[0]].append(r)
ticks = sorted(by_tick)
expected = range(ticks[0], ticks[-1] + 1) if ticks else range(0)

# 4절 측정 무결성
covered = min(r[1] for r in rows) <= w_start and max(r[1] for r in rows) >= w_end
missing = [t for t in expected if sorted(x[3] for x in by_tick.get(t, [])) != workers]
late = [r for r in in_w if r[2] - r[1] > MAX_LATE]
max_late = max((r[2] - r[1] for r in in_w), default=0.0)
# 5절 사전 조건: 기준선에서 3대 모두 200
pre_bad = [r for r in in_w if r[1] < drain_start and r[4] != "200"]
integrity_ok = covered and bool(in_w) and not missing and not late and not pre_bad

# 3절 판정 지표 — B는 판정 구간 안에서 끝난 성공 응답만 센다
healthy = [w for w in workers if w != DRAINED]
fail_a = [r for r in in_w if r[3] in healthy and r[4] != "200"]
ok_done = sorted(r[5] for r in in_w if r[4] == "200" and w_start <= r[5] <= w_end)
points = [w_start] + ok_done + [w_end]
gap_b, gap_at = 0.0, None
for a, b in zip(points, points[1:]):
    if b - a > gap_b:
        gap_b, gap_at = b - a, a
drained_non200 = sum(1 for r in in_w if r[3] == DRAINED and r[4] != "200")

print(f"판정 구간 {w_start:.3f} ~ {w_end:.3f} ({w_end - w_start:.1f}s), tick {len(expected)}개, 요청 {len(in_w)}건")
print(f"[무결성] 구간 전체 측정 {'예' if covered else '아니오'}, 누락 tick {len(missing)}개, "
      f"발사 지연>{MAX_LATE}s {len(late)}건(최대 {max_late:.3f}s), 기준선 비200 {len(pre_bad)}건")
print(f"[A] 정상 Worker {','.join(healthy)} 실패 요청 {len(fail_a)}건 (기준 0)")
print(f"[B] 성공 응답 최대 간격 {gap_b:.3f}s at {gap_at} (기준 ≤{MAX_GAP}s)")
print(f"[참고] drain 대상 {DRAINED} 비200 {drained_non200}건(허용·판정 제외)")
if not integrity_ok:
    not_verified("측정 무결성·사전 조건 미충족 → 기준 변경 없이 새 RUN")
if not fail_a and gap_b <= MAX_GAP:
    print("판정: PASS")
    sys.exit(0)
print("판정: FAIL")
sys.exit(1)
