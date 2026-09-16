#!/usr/bin/env python3
"""Read-only CRI/Pod/route/peer capture for managed CP1 membership.

Supports CRI JSON metadata and inspectp.info.pid with namespace identity checks.
Unknown runtime layouts fail closed with the raw bounded query errors retained.
No workload exec, host mutation, policy activation or acceptance is performed.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
NODE_SHA = '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa'

_spec = importlib.util.spec_from_file_location('managed_member_core', ROOT / 'scripts/cp1_cni_membership.py')
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def need(value, message):
    if not value:
        raise ValueError(message)


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('non-finite JSON value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def pid_start(raw):
    # comm is parenthesized and can contain spaces/parentheses; field 22 is
    # relative to the final ')' rather than a naive split of the whole line.
    end = raw.rfind(')')
    fields = raw[end + 1:].split()
    need(end > 0 and len(fields) >= 20 and re.fullmatch(r'[0-9]+', fields[19]), 'invalid proc PID stat')
    value = int(fields[19]); need(value > 0, 'empty PID start')
    return value


def collect(node, *, deadline=None):
    started = time.monotonic()
    if deadline is not None:
        need(core.number(deadline) > started, 'peer collection deadline exhausted')
    node.COMMAND_STATE.update(count=0, timeouts=0, budget_exhausted=False,
                              deadline=min(started + 30, deadline) if deadline is not None else started + 30)
    backend = node.BaseBackend('k8s-control01')
    report = {'schema': 'fds.cp1-cni-membership-raw.v1', 'mode': 'READ_ONLY', 'complete': False,
              'queries': {}, 'errors': [], 'raw': {}, 'authorizes_apply': False}
    try:
        backend.identity()
        def read(key, argv=None, kubectl=None):
            result = backend.kubectl(kubectl) if kubectl is not None else node.command(argv, timeout=5)
            report['queries'][key] = result
            need(type(result.get('rc')) is int and result['rc'] == 0 and result.get('stdout_truncated', False) is False and
                 result.get('stderr_truncated', False) is False and isinstance(result.get('stderr'),str) and
                 not result['stderr'].strip() and isinstance(result.get('stdout'),str), 'incomplete query: ' + key)
            return result['stdout'].strip()
        report['wall'] = time.time()
        report['boottime'] = time.clock_gettime(time.CLOCK_BOOTTIME)
        report['boot_id'] = read('boot', ['cat', '/proc/sys/kernel/random/boot_id'])
        raw = report['raw']
        for key, resource in (('pods', 'pods'), ('blocks', 'ipamblocks.crd.projectcalico.org'),
                              ('affinities', 'blockaffinities.crd.projectcalico.org')):
            raw[key] = parse(read(key, kubectl=['get', resource] + (['-A'] if key == 'pods' else []) + ['-o', 'json']))
        raw['links'] = parse(read('links', ['ip', '-j', '-d', 'link', 'show']))
        raw['routes'] = parse(read('routes', ['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
        sandboxes = parse(read('sandboxes', ['crictl', 'pods', '--output', 'json']))['items']
        candidates = [p for p in raw['pods']['items'] if p['spec'].get('nodeName') == 'k8s-control01' and
                      p['spec'].get('hostNetwork') is not True and not p['metadata'].get('deletionTimestamp') and
                      p.get('status', {}).get('phase') not in ('Succeeded', 'Failed') and p.get('status', {}).get('podIP')]
        need(len(candidates) <= 62, 'Pod bound exceeded')
        raw['sandbox_peers'] = []
        bindings = []
        for pod in candidates:
            meta = pod['metadata']
            matches = [s for s in sandboxes if s.get('metadata', {}).get('uid') == meta['uid'] and
                       s.get('state') == 'SANDBOX_READY']
            need(len(matches) == 1, 'ambiguous/missing sandbox for Pod UID')
            sandbox = matches[0]['id']
            need(isinstance(sandbox, str) and re.fullmatch(r'[a-f0-9]{64}', sandbox), 'invalid sandbox ID')
            inspected = parse(read('sandbox/' + sandbox, ['crictl', 'inspectp', '--output', 'json', sandbox]))
            status, info = inspected['status'], inspected['info']
            need(status.get('id') == sandbox and status.get('state') == 'SANDBOX_READY' and
                 status.get('metadata', {}).get('uid') == meta['uid'] and
                 status['metadata'].get('name') == meta['name'] and status['metadata'].get('namespace') == meta['namespace'],
                 'inspectp sandbox metadata differs')
            pid = info.get('pid')
            need(type(pid) is int and pid > 1, 'unsupported runtime: inspectp.info.pid absent')
            proc = '/proc/' + str(pid)
            bindings.append((sandbox,pid,copy.deepcopy(status)))
            first = pid_start(read(sandbox + '/stat_before', ['cat', proc + '/stat']))
            netns_before = int(read(sandbox + '/netns_before', ['stat', '-L', '-c', '%i', proc + '/ns/net']))
            peer = parse(read(sandbox + '/peer', ['nsenter', '--target', str(pid), '--net', '--',
                              node.binary('ip'), '-j', '-d', 'address', 'show', 'dev', 'eth0']))
            need(len(peer) == 1 and peer[0].get('ifname') == 'eth0' and
                 peer[0].get('linkinfo', {}).get('info_kind') == 'veth', 'sandbox eth0 peer absent')
            last = pid_start(read(sandbox + '/stat_after', ['cat', proc + '/stat']))
            netns_after = int(read(sandbox + '/netns_after', ['stat', '-L', '-c', '%i', proc + '/ns/net']))
            raw['sandbox_peers'].append({'pod_uid': meta['uid'], 'pod': meta['name'], 'namespace': meta['namespace'],
                'state': status['state'], 'sandbox_id': sandbox, 'host_ifindex': peer[0]['link_index'],
                'ifindex': peer[0]['ifindex'], 'iflink': peer[0]['link_index'],
                'ipv4': sorted(v['local'] for v in peer[0]['addr_info'] if v['family'] == 'inet'),
                'pid_start_before': first, 'pid_start_after': last, 'netns_before': netns_before, 'netns_after': netns_after})
        # Compare eligibility/ownership/peer facts, not resourceVersion, Ready
        # transitions or unrelated Pod status churn. Preserve both raw samples.
        before_members = core.select_members(raw)
        later_raw = copy.deepcopy(raw)
        for key, resource in (('pods', 'pods'), ('blocks', 'ipamblocks.crd.projectcalico.org'),
                              ('affinities', 'blockaffinities.crd.projectcalico.org')):
            later = parse(read(key + '_after', kubectl=['get', resource] + (['-A'] if key == 'pods' else []) + ['-o', 'json']))
            later_raw[key] = later
        for key, argv in (('links', ['ip', '-j', '-d', 'link', 'show']),
                          ('routes', ['ip', '-j', '-4', 'route', 'show', 'table', 'all'])):
            later_raw[key] = parse(read(key + '_after', argv))
        # Re-probe CRI and the actual network namespace after the API/host
        # reads. Copying the first peer sample would miss sandbox/ifindex reuse.
        for index,(sandbox,pid,status) in enumerate(bindings):
            inspected=parse(read('sandbox/'+sandbox+'_after',['crictl','inspectp','--output','json',sandbox]))
            current=inspected['status']
            need(inspected['info'].get('pid') == pid and type(inspected['info'].get('pid')) is int and
                 all(current.get(k)==status.get(k) for k in ('id','state','metadata')), 'sandbox changed after host/API reads')
            proc='/proc/'+str(pid)
            first=pid_start(read(sandbox+'/final_stat_before',['cat',proc+'/stat']))
            ns_first=int(read(sandbox+'/final_netns_before',['stat','-L','-c','%i',proc+'/ns/net']))
            peer=parse(read(sandbox+'/peer_after',['nsenter','--target',str(pid),'--net','--',
                       node.binary('ip'),'-j','-d','address','show','dev','eth0']))
            need(len(peer)==1 and peer[0].get('ifname')=='eth0' and peer[0].get('linkinfo',{}).get('info_kind')=='veth',
                 'final sandbox peer absent')
            last=pid_start(read(sandbox+'/final_stat_after',['cat',proc+'/stat']))
            ns_last=int(read(sandbox+'/final_netns_after',['stat','-L','-c','%i',proc+'/ns/net']))
            row=later_raw['sandbox_peers'][index]
            row.update(host_ifindex=peer[0]['link_index'],ifindex=peer[0]['ifindex'],iflink=peer[0]['link_index'],
                       ipv4=sorted(v['local'] for v in peer[0]['addr_info'] if v['family']=='inet'),
                       pid_start_before=first,pid_start_after=last,netns_before=ns_first,netns_after=ns_last)
            need(core.same(row,raw['sandbox_peers'][index]), 'sandbox peer identity changed between samples')
        report['raw_after'] = later_raw
        after_members = core.select_members(later_raw)
        need(core.same(before_members, after_members), 'eligible membership changed during peer capture')
        need(read('boot_after', ['cat', '/proc/sys/kernel/random/boot_id']) == report['boot_id'], 'boot changed')
        need(0 <= time.time()-report['wall'] < 30 and
             0 <= time.clock_gettime(time.CLOCK_BOOTTIME)-report['boottime'] < 30, 'membership capture budget exhausted')
        report['complete'] = True
        report['members'] = after_members
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        report['errors'].append(str(error))
    finally:
        backend.cache.cleanup()
        node.COMMAND_STATE['deadline'] = None
    report['ended_wall'] = time.time()
    report['ended_boottime'] = time.clock_gettime(time.CLOCK_BOOTTIME)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    source = ROOT / 'scripts/node-firewall.py'
    need(hashlib.sha256(source.read_bytes()).hexdigest() == NODE_SHA, 'pinned helper differs')
    spec = importlib.util.spec_from_file_location('managed_member_collector_node', source)
    node = importlib.util.module_from_spec(spec); spec.loader.exec_module(node)
    result = collect(node)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'status': 'CAPTURED_REVIEW_REQUIRED' if result['complete'] else 'INCOMPLETE_REVIEW_REQUIRED',
                      'sha256': hashlib.sha256(raw).hexdigest(), 'output': args.output}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
