#!/usr/bin/env python3
"""Edge Cutover(#19) 요청줄 캡처 판정 — 읽기 전용. 서버에 접속하지 않고 주어진 텍스트만 읽는다.

사용: python3 -I cutover-capture-judge.py <check|apply|rollback> <RUN> <TAG> <WIN> [--edge-only] <파일...>
  파일: edge01 실행 산출물(SENT·POD cluster=ek3s 줄), cutover-capture.sh report 출력(HIT·CAPTURE 줄,
        ansible 출력에 섞여 있어도 됨), control01 pre/post 로그(POD cluster=k8s 줄).
  --edge-only: edge01 캡처만 판정한다(edge01-cutover.sh가 자기 단계 안에서 쓴다). 최종 판정이 아니다.

판정(각 표식 = `cutover-<RUN>-<TAG>-<번호>`, 보낸 요청은 SENT 줄 기준):
  공통   : 필요한 호스트마다 같은 WIN의 CAPTURE 1개, err=0, ready < 첫 SENT, stop > 마지막 SENT.
           같은 WIN에 다른 TAG의 표식이 있으면 섞임 → 판정 불가.
  check  : edge01에 `1`이 1번(nginx 경유: X-Real-IP 있음, E-K3S Pod),
           k8s-worker01/02/03에 w105/w107/w108이 각 1번(직접 요청: src 10.1.93.50, X-Real-IP 없음, 그 노드의 Pod)
           → METHOD_READY
  apply  : Worker 3대 합계로 1~9·smoke가 각 정확히 1번, 모두 src 10.1.93.50·X-Real-IP 있음·그 노드의 K8s Pod,
           edge01(E-K3S)에는 0건 → PATH_OK. edge01에 1건이라도 있으면 OLD_PATH(실패).
  rollback: edge01(E-K3S Pod)에 1~3·smoke가 각 정확히 1번(X-Real-IP 있음) → PATH_OK.
           Worker 캡처가 있으면 0건이어야 한다(있으면 NEW_PATH, 실패). Worker 캡처가 없으면 그 사실을 적는다.
종료 코드: 0=판정 통과, 3=반대 경로 관측(실패), 4=판정 불가(누락·중복·캡처 오류·입력 부족).
"""
import re
import sys

WORKERS = {'k8s-worker01': 'w105', 'k8s-worker02': 'w107', 'k8s-worker03': 'w108'}
EDGE = 'edge01'
EDGE_IP = '10.1.93.50'
EXPECT = {
    'check': ['1', 'w105', 'w107', 'w108'],
    'apply': [str(i) for i in range(1, 10)] + ['smoke'],
    'rollback': ['1', '2', '3', 'smoke'],
}
FIELD = r'([^\s\\",]*)'


def fields(line, kind):
    """`KIND k=v k=v ...` 조각을 줄 안 어디에 있든 꺼낸다(ansible JSON 출력의 따옴표·\\r\\n 안에 있어도)."""
    out = []
    for m in re.finditer(r'(?:^|[\s"\'\[])' + kind + r'((?: [a-z_0-9]+=[^\s\\",]*)+)', line):
        out.append(dict(re.findall(r' ([a-z_0-9]+)=' + FIELD, m.group(1))))
    return out


def load(paths):
    sent, caps, hits, pods = {}, {}, {}, {}
    problems = []
    for p in paths:
        for line in open(p, encoding='utf-8', errors='replace'):
            for f in fields(line, 'SENT'):
                sent.setdefault((f.get('tag'), f.get('id')), set()).add((f.get('http'), f.get('t'), f.get('run')))
            for f in fields(line, 'CAPTURE'):
                key = (f.get('host'), f.get('win'), f.get('run'))
                caps.setdefault(key, set()).add(tuple(sorted(f.items())))
            for f in fields(line, 'HIT'):
                key = tuple(f.get(k) for k in ('host', 'run', 'win', 'tag', 'id', 'src', 'dst', 'seq'))
                hits[key] = f
            for f in fields(line, 'POD'):
                key = (f.get('cluster'), f.get('ip'))
                pods.setdefault(key, set()).add((f.get('name'), f.get('uid'), f.get('node')))
    for key, vals in pods.items():
        if len(vals) != 1:
            problems.append('POD %s/%s 정보가 파일마다 다름' % key)
    return sent, caps, list(hits.values()), {k: sorted(v)[0] for k, v in pods.items()}, problems


def judge(mode, run, tag, win, paths, edge_only=False):
    sent, caps, hits, pods, problems = load(paths)
    notes = []
    fail = []       # 반대 경로가 관측됨
    unsure = list(problems)

    # 보낸 요청(SENT)
    want = EXPECT[mode]
    times = []
    for i in want:
        v = sent.get((tag, i))
        if not v:
            unsure.append('SENT %s 없음' % i)
            continue
        if len(v) != 1:
            unsure.append('SENT %s가 %d번' % (i, len(v)))
            continue
        http, t, r = next(iter(v))
        if r != run:
            unsure.append('SENT %s의 RUN이 다름' % i)
        if http != ('201' if i == 'smoke' else '200'):
            unsure.append('SENT %s http=%s' % (i, http))
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            unsure.append('SENT %s 시각 없음' % i)
    extra_sent = sorted(i for (t, i) in sent if t == tag and i not in want)
    if extra_sent:
        unsure.append('예상 밖 SENT %s' % ','.join(extra_sent))

    # 캡처 상태
    hosts = [EDGE] if edge_only else [EDGE] + sorted(WORKERS)
    required = set(hosts) if mode != 'rollback' else {EDGE}
    captured = set()
    for h in hosts:
        v = caps.get((h, win, run))
        if not v:
            (unsure if h in required else notes).append('%s 캡처 없음' % h)
            continue
        if len(v) != 1:
            unsure.append('%s CAPTURE 줄이 서로 다름' % h)
            continue
        c = dict(next(iter(v)))
        if c.get('err') != '0':
            unsure.append('%s 캡처 오류(%s)' % (h, c.get('reason')))
            continue
        try:
            ready, stop = float(c.get('ready')), float(c.get('stop'))
        except (TypeError, ValueError):
            unsure.append('%s 캡처 시각 없음' % h)
            continue
        if times and not ready < min(times):
            unsure.append('%s 캡처 준비(%s)가 첫 요청(%.3f)보다 늦음' % (h, c.get('ready'), min(times)))
            continue
        if times and not stop > max(times):
            unsure.append('%s 캡처 종료(%s)가 마지막 요청(%.3f)보다 이름' % (h, c.get('stop'), max(times)))
            continue
        captured.add(h)

    # 이 WIN의 표식
    mine = [x for x in hits if x.get('run') == run and x.get('win') == win and x.get('host') in captured]
    others = sorted({'%s:%s' % (x.get('tag'), x.get('id')) for x in mine if x.get('tag') != tag})
    if others:
        unsure.append('같은 WIN에 다른 표식 %s' % ','.join(others))
    mine = [x for x in mine if x.get('tag') == tag]
    for x in mine:
        print('   HIT %s id=%s src=%s dst=%s %s %s host=%s xri=%s' % (
            x.get('host'), x.get('id'), x.get('src'), x.get('dst'), x.get('method'), x.get('proto'),
            x.get('hdr_host'), x.get('xri')))

    def pod_of(x, cluster, node):
        ip = (x.get('dst') or '').rsplit(':', 1)[0]
        p = pods.get((cluster, ip))
        if not p:
            unsure.append('%s id=%s dst %s에 대응하는 %s Pod 없음' % (x.get('host'), x.get('id'), ip, cluster))
            return False
        name, uid, pnode = p
        if node and pnode != node:
            unsure.append('%s id=%s dst %s는 %s 노드 Pod(%s)' % (x.get('host'), x.get('id'), ip, pnode, name))
            return False
        print('   BIND id=%s pod=%s uid=%s ip=%s node=%s' % (x.get('id'), name, uid, ip, pnode))
        return True

    def count(xs):
        c = {}
        for x in xs:
            c[x.get('id')] = c.get(x.get('id'), 0) + 1
        return c

    def via_nginx(x):
        return x.get('xri') not in (None, '', '-')

    edge_hits = [x for x in mine if x.get('host') == EDGE]
    worker_hits = [x for x in mine if x.get('host') in WORKERS]
    edge_part = 'NOT_CAPTURED' if EDGE not in captured else 'OK'

    if mode == 'check':
        if EDGE in captured:
            if count(edge_hits) != {'1': 1}:
                unsure.append('edge01 표식 %s(기대 1:1)' % count(edge_hits))
                edge_part = 'MISMATCH'
            elif not via_nginx(edge_hits[0]):
                unsure.append('edge01 표식 1에 nginx 경유 헤더(X-Real-IP) 없음')
                edge_part = 'MISMATCH'
            elif not pod_of(edge_hits[0], 'ek3s', None):
                edge_part = 'MISMATCH'
        if not edge_only:
            for h, w in sorted(WORKERS.items()):
                if h not in captured:
                    continue
                xs = [x for x in worker_hits if x.get('host') == h]
                if count(xs) != {w: 1}:
                    unsure.append('%s 표식 %s(기대 %s:1)' % (h, count(xs), w))
                    continue
                x = xs[0]
                if x.get('src', '').rsplit(':', 1)[0] != EDGE_IP or via_nginx(x):
                    unsure.append('%s %s가 edge01 직접 요청 모양이 아님(src=%s xri=%s)' % (h, w, x.get('src'), x.get('xri')))
                    continue
                pod_of(x, 'k8s', h)
        ok_word = 'METHOD_READY'

    elif mode == 'apply':
        if edge_hits:
            fail.append('edge01(E-K3S Pod)에 apply 표식 %s — 옛 경로' % count(edge_hits))
            edge_part = 'OLD_PATH'
        if not edge_only:
            c = count(worker_hits)
            if c != {i: 1 for i in want}:
                unsure.append('Worker 합계 표식 %s(기대 1~9·smoke 각 1)' % c)
            for x in worker_hits:
                if x.get('src', '').rsplit(':', 1)[0] != EDGE_IP:
                    unsure.append('%s id=%s src=%s(기대 %s)' % (x.get('host'), x.get('id'), x.get('src'), EDGE_IP))
                elif not via_nginx(x):
                    unsure.append('%s id=%s nginx 경유 헤더(X-Real-IP) 없음' % (x.get('host'), x.get('id')))
                else:
                    pod_of(x, 'k8s', x.get('host'))
        ok_word = 'PATH_OK'

    else:  # rollback
        if EDGE in captured:
            c = count(edge_hits)
            if c != {i: 1 for i in want}:
                unsure.append('edge01 표식 %s(기대 1~3·smoke 각 1)' % c)
                edge_part = 'MISMATCH'
            for x in edge_hits:
                if not via_nginx(x):
                    unsure.append('edge01 id=%s nginx 경유 헤더(X-Real-IP) 없음' % x.get('id'))
                    edge_part = 'MISMATCH'
                elif not pod_of(x, 'ek3s', None):
                    edge_part = 'MISMATCH'
        if not edge_only:
            if worker_hits:
                fail.append('Worker에 rollback 표식 %s — 새 경로' % count(worker_hits))
            if not captured & set(WORKERS):
                notes.append('Worker 캡처 없음 — 새 경로 0건은 관측하지 않음(edge01 각 1번으로 옛 경로 경유만 판정)')
        ok_word = 'PATH_OK'

    if fail:
        verdict, rc = ('OLD_PATH' if mode == 'apply' else 'NEW_PATH'), 3
    elif unsure:
        verdict, rc = ('METHOD_NOT_READY' if mode == 'check' else 'UNCONFIRMED'), 4
    else:
        verdict, rc = ok_word, 0
    for s in fail:
        print('   FAIL: %s' % s)
    for s in unsure:
        print('   UNSURE: %s' % s)
    for s in notes:
        print('   NOTE: %s' % s)
    print('JUDGE mode=%s run=%s tag=%s win=%s scope=%s captured=%s edge=%s verdict=%s' % (
        mode, run, tag, win, 'edge01' if edge_only else 'all', ','.join(sorted(captured)) or '-',
        edge_part, verdict))
    return rc


def main(argv):
    args = [a for a in argv if a != '--edge-only']
    if len(args) < 5 or args[0] not in EXPECT or not re.fullmatch(r'\d{8}T\d{4}', args[1]) \
            or not re.fullmatch(r'[A-Za-z0-9]+', args[2]) or not re.fullmatch(r'(check|apply|rollback)[1-9]?', args[3]):
        sys.stderr.write(__doc__)
        return 2
    return judge(args[0], args[1], args[2], args[3], args[4:], edge_only='--edge-only' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
