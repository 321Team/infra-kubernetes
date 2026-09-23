#!/usr/bin/env python3
"""Read an existing p3dmjivl-format archive; never execute its recorded commands.

This is an evidence consistency report, not an acceptance/authorization consumer.
SHA256 proves integrity against the supplied digest, not provenance or approval.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

MAX_BYTES = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SOURCE = 'b3f3374ba71a754f15f69a5cc3a471bd61c79424'
REFERENCE_PROBE = '64c19fe81c5361785c139d977f13508743bd254ffe32149c1c895b586de1d09d'
REFERENCE_KIT = {
    'pod_probe.py': REFERENCE_PROBE,
    'remote.py': '3962812bf610f322102601736d218fb0b744721ab9ce59e070393228c71d8cfb',
    'run.py': '16c04ce598baf80919a4157d374f3fc5e104768852d9b3a676330656ed1bf351',
}
LEGACY = {
    'node-firewall.py': '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa',
    'run-node-firewall.py': 'cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3',
}
FLOWS = ('a_to_b_pod', 'b_to_a_pod', 'a_to_service', 'a_dns', 'b_dns')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def parse(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            need(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    def invalid(_):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def checked_file(path, digest):
    need(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest), 'external SHA256 required')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    need(len(raw) <= MAX_BYTES and sha(raw) == digest, 'input size/hash differs')
    return raw


def archive_inputs(path, digest):
    files, names, root = {}, set(), None
    with tarfile.open(fileobj=io.BytesIO(checked_file(path, digest)), mode='r:gz') as archive:
        # Stream iteration stops before an unbounded member list can be allocated.
        for index, member in enumerate(archive):
            need(index < 4, 'unexpected archive members')
            parts = PurePosixPath(member.name).parts
            need(not member.name.startswith('/') and '..' not in parts and
                 len(parts) in (1, 2) and parts not in names, 'unsafe/duplicate archive path')
            names.add(parts)
            root = root or parts[0]
            need(parts[0] == root and re.fullmatch('FDS_CP1_CNI_Trial_[A-Za-z0-9]+', root), 'wrong archive root')
            if member.isdir():
                need(len(parts) == 1, 'unexpected archive directory')
                continue
            need(member.isfile() and len(parts) == 2 and parts[1] in
                 ('REQUEST.json', 'RESULT.json', 'SHA256SUMS'), 'unexpected archive object')
            need(0 <= member.size <= MAX_BYTES, 'archive member too large')
            files[parts[1]] = archive.extractfile(member).read(MAX_BYTES + 1)
    need(set(files) == {'REQUEST.json', 'RESULT.json', 'SHA256SUMS'}, 'archive incomplete')
    sums = {}
    for line in files['SHA256SUMS'].decode('ascii').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (REQUEST.json|RESULT.json)', line)
        need(match is not None and match[2] not in sums, 'invalid/duplicate manifest entry')
        sums[match[2]] = match[1]
    need(set(sums) == {'REQUEST.json', 'RESULT.json'} and
         all(sha(files[name]) == digest for name, digest in sums.items()), 'internal checksum differs')
    return parse(files['REQUEST.json']), parse(files['RESULT.json']), sums


def instant(value):
    value = dt.datetime.fromisoformat(value)
    need(value.utcoffset() is not None, 'timezone missing')
    return value


def differences(before, after, path=()):
    """No field exclusions; lists stay atomic. Return paths, never UUIDs or values."""
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                out.append(list(path + (key,)))
            else:
                out.extend(differences(before[key], after[key], path + (key,)))
        return out
    return [list(path)]


def legacy_receipt_after(wrapper):
    for name, digest in LEGACY.items():
        need(sha((ROOT / 'scripts' / name).read_bytes()) == digest, 'legacy dependency bytes differ')
    spec = importlib.util.spec_from_file_location('cp1_legacy_report', ROOT / 'scripts/run-node-firewall.py')
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    # Pure validator only: main/preflight/remote_request are never invoked.
    return controller.validate_cp1_receipt(wrapper)['after']


def analyze(request, wrapper, receipt=None):
    need(isinstance(request, dict) and isinstance(wrapper, dict), 'controller envelope must be an object')
    result = wrapper.get('result')
    need(isinstance(result, dict), 'trial result absent')
    report = {'schema': 'fds.cp1-cni-trial-analysis.v1', 'mode': 'OFFLINE_EVIDENCE_REVIEW',
              'flows': {name: 'NOT_VERIFIED' for name in FLOWS}, 'issues': [], 'issue_details': {},
              'cp1_accepted': False, 'authorizes_cp2_change': False, 'authorizes_join': False,
              'production_consumer_connected': False, 'legacy_receipt_after_matches': None}

    def check(label, action):
        try:
            action()
            return True
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
            report['issues'].append(label)
            report['issue_details'][label] = str(error)
            return False

    def envelope():
        need(request['schema'] == wrapper['schema'] == 'fds.cp1-cni-trial-controller.v2' and
             result['schema'] == 'fds.cp1-cni-trial.v2', 'schema differs')
        need(all(wrapper.get(k) == v for k, v in request.items()) and
             set(wrapper) == set(request) | {'rc', 'stderr', 'result'}, 'request binding differs')
        need(request['source_commit'] == REFERENCE_SOURCE and
             all(request['kit_hashes'].get(k) == v for k, v in REFERENCE_KIT.items()), 'source review required')
        need(request['kit_hashes']['expected-firewall.json'] == sha(encoded(result['firewall_before'])),
             'recorded before differs from kit baseline')
        for key in ('namespace', 'until', 'max_seconds', 'cleanup_seconds'):
            need(result[key] == request['request'][key], 'request/result mismatch')
        need(type(result['max_seconds']) is int and result['max_seconds'] == 1200 and
             type(result['cleanup_seconds']) is int and result['cleanup_seconds'] == 180, 'trial budget differs')
        need(type(wrapper['rc']) is int and wrapper['rc'] == 0 and wrapper['stderr'] == '', 'transport uncertain')
        need(request['acceptance'] == result['acceptance'] == 'REVIEW_REQUIRED' and
             result['cross_node'] == 'NOT_TESTED', 'unsupported scope/acceptance claim')
    check('ENVELOPE_SOURCE_OR_TRANSPORT', envelope)

    queries = result.get('queries', [])
    def command_integrity():
        need(isinstance(queries, list) and 0 < len(queries) <= 512, 'queries missing')
        start, end, until = map(instant, (result['started_at'], result['ended_at'], result['until']))
        need(start <= end <= until and end <= dt.datetime.now(dt.timezone.utc) and
             (until - start).total_seconds() <= 1200, 'trial outside budget')
        for q in queries:
            need(type(q['rc']) is int and q['rc'] == 0 and q['truncated'] is False and
                 q['stderr'] == '' and isinstance(q['stdout'], str), 'uncertain command')
            need(start <= instant(q['started_at']) <= instant(q['ended_at']) <= end, 'command outside trial')
    check('COMMAND_INTEGRITY_OR_TIME', command_integrity)

    def flow_checks():
        ns = result['namespace']
        need(re.fullmatch('fds-cni-[a-z0-9-]+', ns), 'namespace invalid')
        execs = [q for q in queries if q['argv'][0] == 'exec']
        need(len(execs) == 2 and {q['argv'][3] for q in execs} == {'a', 'b'}, 'exactly two probe commands required')
        first_exec = min(instant(q['started_at']) for q in execs)
        def observation(argv):
            candidates = [q for q in queries if q['argv'] == argv and instant(q['ended_at']) <= first_exec]
            need(bool(candidates), 'observed object absent before probes')
            return parse(max(candidates, key=lambda q: instant(q['ended_at']))['stdout'])
        items = observation(['get', 'pods', '-n', ns, '-o', 'json'])['items']
        need(len(items) == 2 and {p['metadata']['name'] for p in items} == {'a', 'b'}, 'peer identities incomplete')
        peers = {p['metadata']['name']: p for p in items}
        for pod in peers.values():
            need(pod['metadata']['namespace'] == ns and pod['metadata']['uid'] and
                 pod['spec']['nodeName'] == 'k8s-control01' and pod['spec'].get('hostNetwork', False) is False and
                 pod['status']['phase'] == 'Running' and pod['status']['containerStatuses'] and
                 all(c['ready'] is True for c in pod['status']['containerStatuses']), 'peer not Ready/local')
            ipaddress.IPv4Address(pod['status']['podIP'])
        need(peers['a']['status']['podIP'] != peers['b']['status']['podIP'] and
             peers['a']['metadata']['uid'] != peers['b']['metadata']['uid'], 'peers identical')
        service = observation(['get', 'service', 'peer-b', '-n', ns, '-o', 'json'])
        need(service['metadata']['name'] == 'peer-b' and service['metadata']['namespace'] == ns and
             service['spec']['selector'] == {'fds-cni-trial': ns, 'trial-peer': 'b'} and
             any(p.get('port') == p.get('targetPort') == 8080 and p.get('protocol', 'TCP') == 'TCP'
                 for p in service['spec']['ports']), 'service target differs')
        svc_ip = str(ipaddress.IPv4Address(service['spec']['clusterIP']))
        need(svc_ip not in {p['status']['podIP'] for p in peers.values()}, 'service is a Pod IP')
        need(set(result['checks']) == {'a', 'b'}, 'unexpected peer checks')
        pending = {}
        for q in execs:
            peer, other = q['argv'][3], ('b' if q['argv'][3] == 'a' else 'a')
            need(q['argv'][:-2] == ['exec', '-n', ns, peer, '-c', 'probe', '--', 'python3', '-I', '-B', '-c'] and
                 sha(q['argv'][-2].encode()) == REFERENCE_PROBE, 'probe command differs')
            expected_http = [{'ip': peers[other]['status']['podIP'], 'expected': ns + '/' + other,
                              'test': peer + '_to_' + other + '_pod'}]
            if peer == 'a':
                expected_http.append({'ip': svc_ip, 'expected': ns + '/b', 'test': 'a_to_service'})
            expected = {'http': expected_http, 'dns_name': 'peer-b.' + ns + '.svc', 'dns_ip': svc_ip}
            need(parse(q['argv'][-1]) == expected, 'probe endpoints differ')
            raw = parse(q['stdout'])
            need(raw == result['checks'][peer] and set(raw) == {'http', 'dns'} and
                 len(raw['http']) == len(expected_http), 'raw probe/summary differs')
            for actual, endpoint in zip(raw['http'], expected_http):
                need(all(actual.get(k) == v for k, v in endpoint.items()) and type(actual.get('passed')) is bool,
                     'HTTP evidence identity differs')
                ok = type(actual.get('status')) is int and actual['status'] == 200 and actual.get('body') == endpoint['expected'] and not actual.get('error')
                need(actual['passed'] == ok, 'HTTP passed flag contradicts response')
                pending[endpoint['test']] = 'PASS' if ok else 'FAIL'
            dns = raw['dns']
            need(dns['name'] == expected['dns_name'] and dns['expected'] == svc_ip and type(dns.get('passed')) is bool,
                 'DNS identity differs')
            ok = dns.get('addresses') == [svc_ip] and not dns.get('error')
            need(dns['passed'] == ok, 'DNS passed flag contradicts answers')
            pending[peer + '_dns'] = 'PASS' if ok else 'FAIL'
        report['flows'] = {key: pending[key] for key in FLOWS}
    check('FIVE_FLOW_EVIDENCE_INCONSISTENT', flow_checks)

    def end_state():
        for key in ('firewall', 'health'):
            before, after = result[key + '_before'], result[key + '_after']
            need(isinstance(before, dict) and before and isinstance(after, dict) and after, 'state absent')
            report[key + '_changed_paths'] = differences(before, after)
            report[key + '_after_sha256'] = sha(encoded(after))
            need(result[key + '_unchanged'] is True and before == after, 'state drift')
        need(result['firewall_after']['hostname'] == 'k8s-control01' and
             result['firewall_after']['ip'] == '10.1.93.102', 'wrong host')
        ns = result['namespace']
        absent = [q for q in queries if q['argv'] == ['get', 'namespace', ns, '--ignore-not-found', '-o', 'json']]
        deleted = [q for q in queries if q['argv'] == ['delete', '--raw=/api/v1/namespaces/' + ns, '-f', '-']]
        exec_ends = [instant(q['ended_at']) for q in queries if q['argv'][0] == 'exec']
        need(result['cleanup'] == 'CONFIRMED_ABSENT' and absent and
             max(absent, key=lambda q: instant(q['ended_at']))['stdout'].strip() == '', 'cleanup unverified')
        need(deleted and exec_ends and max(exec_ends) <= min(instant(q['started_at']) for q in deleted) <=
             max(instant(q['ended_at']) for q in deleted) <= max(instant(q['started_at']) for q in absent),
             'namespace absence is not after probe/deletion')
    check('END_STATE_OR_CLEANUP', end_state)
    if receipt is not None:
        def compare_receipt():
            before = legacy_receipt_after(receipt)
            paths = differences(before, result['firewall_after'])
            report['legacy_receipt_changed_paths'] = paths
            report['legacy_receipt_after_matches'] = not paths
            need(not paths, 'legacy consumer requires linked CNI change support')
        check('LEGACY_RECEIPT_OR_AFTER_MISMATCH', compare_receipt)
    count = sum(v == 'PASS' for v in report['flows'].values())
    check('SUMMARY_CONTRADICTS_FLOWS', lambda: need(result.get('test_status') == ('PASS' if count == 5 else 'FAIL'),
                                                  'test_status differs from verified five flows'))
    report['passed_flows'] = count
    report['expected_flows'] = 5
    report['status'] = 'FIVE_FLOWS_REVIEW_REQUIRED' if count == 5 and not report['issues'] else 'INCOMPLETE_REVIEW_REQUIRED'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--cp1-receipt')
    parser.add_argument('--cp1-receipt-sha256')
    args = parser.parse_args()
    try:
        need(bool(args.cp1_receipt) == bool(args.cp1_receipt_sha256), 'receipt and hash must be supplied together')
        request, result, hashes = archive_inputs(args.archive, args.sha256)
        receipt = parse(checked_file(args.cp1_receipt, args.cp1_receipt_sha256)) if args.cp1_receipt else None
        report = analyze(request, result, receipt)
        report.update(archive_sha256=args.sha256, input_hashes=hashes,
                      cp1_receipt_input_sha256=args.cp1_receipt_sha256)
        sys.stdout.buffer.write(encoded(report))
        return 0 if report['status'] == 'FIVE_FLOWS_REVIEW_REQUIRED' else 1
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError, UnicodeError):
        sys.stdout.buffer.write(encoded({'status': 'INVALID_INPUT', 'authorizes_cp2_change': False,
                                        'cp1_accepted': False, 'authorizes_join': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
