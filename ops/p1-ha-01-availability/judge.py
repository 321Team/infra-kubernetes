#!/usr/bin/env python3
"""P1-HA-01(#22) Drain 가용성 판정기 — 결정안 2~6절 구현.

사용: python3 judge.py <probe 로그> <timeline.txt> <drain 대상 worker>
timeline.txt: "이름 epoch초" 줄. baseline_start, drain_start, rescheduled_ready, uncordon, end 필수.
판정 구간 W = [baseline_start, end]. tick의 예정시각으로 구간을 나눈다.
"""
import sys
from collections import defaultdict

LOG, TIMELINE, DRAINED = sys.argv[1], sys.argv[2], sys.argv[3]
INTERVAL, MAX_LATE, MAX_GAP = 0.5, 0.2, 1.0

marks = {}
for line in open(TIMELINE, encoding="utf-8"):
    parts = line.split()
    if len(parts) >= 2:
        try:
            marks[parts[0]] = float(parts[1])
        except ValueError:
            pass
for key in ("baseline_start", "drain_start", "rescheduled_ready", "uncordon", "end"):
    if key not in marks:
        sys.exit(f"STOP timeline에 {key} 없음")
w_start, w_end, drain_start = marks["baseline_start"], marks["end"], marks["drain_start"]

rows = []
for line in open(LOG, encoding="utf-8"):
    t, sched, sent, worker, code, done, _ms = line.split()
    rows.append((int(t), float(sched), float(sent), worker, code, float(done)))
workers = sorted({r[3] for r in rows})
if DRAINED not in workers or len(workers) != 3:
    sys.exit(f"STOP worker 목록 이상: {workers}, drain 대상 {DRAINED}")

in_w = [r for r in rows if w_start <= r[1] <= w_end]
by_tick = defaultdict(list)
for r in in_w:
    by_tick[r[0]].append(r)
first_tick = min(r[0] for r in rows if r[1] >= w_start)
last_tick = max(r[0] for r in rows if r[1] <= w_end)
expected = range(first_tick, last_tick + 1)

# 4절 측정 무결성
covered = min(r[1] for r in rows) <= w_start and max(r[1] for r in rows) >= w_end
missing = [t for t in expected if sorted(x[3] for x in by_tick.get(t, [])) != workers]
late = [r for r in in_w if r[2] - r[1] > MAX_LATE]
max_late = max((r[2] - r[1] for r in in_w), default=0.0)
# 5절 사전 조건: 기준선에서 3대 모두 200
pre_bad = [r for r in in_w if r[1] < drain_start and r[4] != "200"]
integrity_ok = covered and not missing and not late and not pre_bad

# 3절 판정 지표
healthy = [w for w in workers if w != DRAINED]
fail_a = [r for r in in_w if r[3] in healthy and r[4] != "200"]
ok_done = sorted(r[5] for r in in_w if r[4] == "200")
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
    print("판정: NOT_VERIFIED (측정 무결성·사전 조건 미충족 → 기준 변경 없이 새 RUN)")
elif not fail_a and gap_b <= MAX_GAP:
    print("판정: PASS")
else:
    print("판정: FAIL")
