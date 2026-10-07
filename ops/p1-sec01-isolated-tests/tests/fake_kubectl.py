"""Stateful fake API, confined to MOCK_STATE. Never opens a network connection."""
import json
import os
from pathlib import Path
import re
import sys

args = sys.argv[1:]
state_path = Path(os.environ['MOCK_STATE'])
state = json.loads(state_path.read_text()) if state_path.exists() else {'stage': 0, 'objects': {}}
scenario = os.environ.get('SCENARIO', '')
with state_path.with_suffix('.calls').open('a', encoding='utf-8') as f:
    f.write(json.dumps(args) + '\n')


def done(value='', rc=0):
    state_path.write_text(json.dumps(state), encoding='utf-8')
    print(value if isinstance(value, str) else json.dumps(value), end='')
    sys.exit(rc)


def following(word): return args[args.index(word)+1]


digest = 'sha256:a7093136d12640da00570c18ed278eacbb8e9ba3637c7d509571346eac45cc33'
image = '10.1.93.54/fds/transaction-api@'+digest
if 'version' in args: done({'mock': True})
if 'create' in args:
    text = Path(following('-f')).read_text()
    name = re.search(r'^  name: (.+)$', text, re.M)[1]
    kind = 'pod' if 'kind: Pod\n' in text else 'svc'
    runid = name.removeprefix('sec01-probe-test-')
    state['objects'][kind] = {'name': name, 'uid': kind+'-uid', 'runid': runid}
    if scenario == 'create_response_lost' and kind == 'pod': done('timeout', 1)
    done(kind+'-uid')
if 'delete' in args:
    assert '--raw' in args, 'name-only delete forbidden'
    body = json.load(sys.stdin)
    kind = 'pod' if '/pods/' in following('--raw') else 'svc'
    assert body['preconditions']['uid'] == state['objects'][kind]['uid']
    del state['objects'][kind]
    done('{}')
if 'exec' in args:
    if scenario == 'inject_fail': done('injection failed', 1)
    state['stage'] += 1
    done()
if 'get' not in args: done('{}')
kind = following('get')
jsonpath = next((a for a in args if a.startswith('jsonpath=')), '')
if kind == 'nodes':
    done({'items': [{'metadata': {'name': f'k8s-{role}{i:02}'}, 'status': {'conditions': [
        {'type': 'Ready', 'status': 'True'}]}} for role in ('control', 'worker') for i in (1, 2, 3)]})
if kind in ('ns', 'resourcequota', 'limitrange', 'events'):
    done('{}', 1 if scenario == 'quota_error' and kind == 'resourcequota' else 0)
if kind == 'node': done('k8s-worker01|True\n')
if kind == 'pod,svc': done('pod/sec01-probe-test-stale' if scenario == 'stale' else '')
if kind == 'pods':
    if jsonpath: done('k8s-worker01|'+digest+';\n')
    done({'items': [{'metadata': {'name': 'app', 'uid': 'app-uid'}, 'status': {
        'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
        'containerStatuses': [{'name': 'app', 'restartCount': 0, 'ready': True}]}}]})
if kind == 'endpointslices':
    if '-l' in args:
        if scenario == 'ep_error': done('API failure', 1)
        done('pod-uid|'+('false' if state['stage'] == 1 else 'true')+'\n')
    address = 'changed' if scenario == 'baseline_change' and state['stage'] else '10.0.0.1'
    done({'items': [{'metadata': {'name': 'app-slice', 'uid': 'slice'}, 'endpoints': [
        {'addresses': [address], 'conditions': {'ready': True}}]}]})
if kind in ('pod', 'svc'):
    obj = state['objects'].get(kind)
    if obj is None:
        done('', 0 if '--ignore-not-found' in args else 1)
    if '--ignore-not-found' in args and not jsonpath: done(kind+'/'+obj['name'])
    if 'managed-by' in jsonpath:
        mgr = 'foreign' if scenario == 'foreign' else 'sec01-ac06-test'
        done('|'.join([obj['uid'], obj['runid'], mgr, image if kind == 'pod' else '', obj['runid'] if kind == 'svc' else '']))
    if jsonpath:
        restart = 1 if state['stage'] >= 3 or (scenario == 'unexpected_restart' and state['stage'] == 1) else 0
        done(f"{obj['uid']}|Running|{'False' if state['stage'] == 1 else 'True'}|{restart}|cid-{restart}")
    done({'metadata': {'uid': obj['uid']}, 'spec': {'automountServiceAccountToken': scenario == 'token', 'volumes': []}})
done('unhandled fake command', 90)
