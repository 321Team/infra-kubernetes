"""Bounded CP1 Runtime CNI change. No worker, join, watcher or boot service.

Permanent definitions are staged without bindings, loaded, then removed before
the sole Runtime cali+ binding is activated. Recovery consumes the same journal.
"""
import copy
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import shlex
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
HOST, BLOCK = 'k8s-control01', '10.244.246.64/26'
ZONE, LOCAL, EGRESS = 'fds-cp1-min', 'fds-cp1-local', 'fds-cp1-egress'
SETS = ('fds-cp1-out4', 'fds-cp1-out6')
NAMES = {'ipsets': SETS, 'zones': (ZONE,), 'policies': (LOCAL, EGRESS)}
PRESERVED_DISABLED_POLICIES = frozenset(('gateway-dmz-to-HOST', 'gateway-lan-to-HOST',
    'gateway-lan-to-work', 'gateway-lan-to-world', 'gateway-world-to-HOST'))
ORDER = [('ipsets', s) for s in SETS] + [('zones', ZONE), ('policies', LOCAL), ('policies', EGRESS)]
PINS = {'node-firewall.py': '93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa',
        'run-node-firewall.py': 'cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return digest(encoded(value))


def instant(value):
    result = dt.datetime.fromisoformat(value)
    need(result.utcoffset() is not None, 'timezone required')
    return result


def now():
    return dt.datetime.now(dt.timezone.utc)


def legacy():
    for name, expected in PINS.items():
        need(digest((ROOT / 'scripts' / name).read_bytes()) == expected, 'legacy source differs')
    spec = importlib.util.spec_from_file_location('minimal_original_controller', ROOT / 'scripts/run-node-firewall.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(rule):
    return shlex.join(shlex.split(rule))


def parse_info(text, name):
    lines = text.strip().splitlines()
    need(lines and lines[0].split()[0] == name, 'object header differs')
    fields, rules, in_rules = {}, [], False
    for line in lines[1:]:
        line = line.strip()
        if line == 'rich rules:':
            need(not in_rules, 'duplicate rules section')
            in_rules = True
        elif in_rules:
            if line:
                need(line.startswith('rule '), 'invalid rich rule')
                rules.append(canonical(line))
        else:
            need(':' in line, 'unknown info field')
            key, value = line.split(':', 1)
            need(key not in fields, 'duplicate info field')
            value = value.strip()
            if key in ('services', 'interfaces', 'sources', 'entries', 'ingress-zones', 'egress-zones'):
                value = ' '.join(sorted(value.split()))
            fields[key] = value
    return {'fields': fields, 'rules': sorted(rules)}


def active_names(raw):
    lines = raw.splitlines()
    need(len(lines) % 3 == 0, 'unknown active-policy output')
    out = {}
    for offset in range(0, len(lines), 3):
        name = lines[offset]
        need(re.fullmatch('[A-Za-z0-9_-]+', name) and name not in out and
             lines[offset + 1].startswith('  ingress-zones: ') and
             lines[offset + 2].startswith('  egress-zones: '), 'ambiguous active policy')
        out[name] = '\n'.join(lines[offset:offset + 3])
    return out


def expected_objects(base):
    zone = copy.deepcopy(base['objects']['runtime']['zones']['public'])
    zone['fields'].update(interfaces='', forward='no')
    fields = dict(disable='no', priority='-2', target='CONTINUE', **{'ingress-zones': ZONE, 'egress-zones': ZONE,
                  'services': '', 'ports': '', 'protocols': '', 'masquerade': 'no', 'forward-ports': '',
                  'source-ports': '', 'icmp-blocks': ''})
    local = {'fields': fields, 'rules': [f'rule family=ipv4 source address={BLOCK} destination address={BLOCK} accept']}
    egress = copy.deepcopy(local)
    egress['fields'].update(priority='-1', **{'egress-zones': 'ANY'})
    egress['rules'] = [f'rule family=ipv4 destination ipset={SETS[0]} accept',
                       f'rule family=ipv6 destination ipset={SETS[1]} accept']
    return {'zones': {ZONE: zone}, 'policies': {LOCAL: local, EGRESS: egress}, 'ipsets': {
        SETS[0]: {'fields': {'type': 'hash:net,iface', 'options': 'family=inet',
                            'entries': '0.0.0.0/1,ens160 128.0.0.0/1,ens160'}, 'rules': []},
        SETS[1]: {'fields': {'type': 'hash:net,iface', 'options': 'family=inet6',
                            'entries': '8000::/1,ens160 ::/1,ens160'}, 'rules': []}}}


def documents(base):
    model, out = expected_objects(base), {}
    for kind, name in ORDER:
        obj = model[kind][name]
        if kind == 'ipsets':
            root = ET.Element('ipset', type='hash:net,iface')
            ET.SubElement(root, 'option', name='family', value='inet6' if name == SETS[1] else 'inet')
            for entry in obj['fields']['entries'].split():
                ET.SubElement(root, 'entry').text = entry
        elif kind == 'zones':
            root = ET.Element('zone')
            # firewalld 2.4.3's --new-zone-from-file path may otherwise drop
            # the serialized forward field and retain the daemon default yes.
            # Make that unbound Permanent-only intermediate state explicit;
            # IO.stage() removes forward and verifies the final no state before
            # any reload can make the zone Runtime-visible.
            ET.SubElement(root, 'forward')
            # The reviewed baseline has only these public services/rich rules.
            # No interface/source tag: the intermediate zone remains inactive.
            for service in obj['fields']['services'].split():
                ET.SubElement(root, 'service', name=service)
            for text in obj['rules']:
                match = re.fullmatch(r'rule family=ipv4 source address=([0-9./]+) port port=([0-9-]+) protocol=tcp accept', text)
                need(match is not None, 'unsupported public rule in mirror')
                rule = ET.SubElement(root, 'rule', family='ipv4')
                ET.SubElement(rule, 'source', address=match[1])
                ET.SubElement(rule, 'port', port=match[2], protocol='tcp')
                ET.SubElement(rule, 'accept')
        else:
            root = ET.Element('policy', target='CONTINUE', priority=obj['fields']['priority'])
            ET.SubElement(root, 'ingress-zone', name=ZONE)
            ET.SubElement(root, 'egress-zone', name=obj['fields']['egress-zones'])
            for family in (('ipv4',) if name == LOCAL else ('ipv4', 'ipv6')):
                rule = ET.SubElement(root, 'rule', family=family)
                if name == LOCAL:
                    ET.SubElement(rule, 'source', address=BLOCK)
                    ET.SubElement(rule, 'destination', address=BLOCK)
                else:
                    ET.SubElement(rule, 'destination', ipset=SETS[0] if family == 'ipv4' else SETS[1])
                ET.SubElement(rule, 'accept')
        ET.indent(root, space='  ')
        out[kind + '/' + name + '.xml'] = ET.tostring(root, encoding='unicode') + '\n'
    return out


def validate_baseline(base, original):
    ctl = legacy(); receipt = ctl.validate_cp1_receipt(original)
    need(base['firewall'] == receipt['after'], 'CP1 differs from original receipt')
    fw = base['firewall']; runtime, permanent = fw['planes']['runtime'], fw['planes']['permanent']
    fields = {'target': 'default', 'ingress-priority': '0', 'egress-priority': '0', 'icmp-block-inversion': 'no',
              'interfaces': 'ens160', 'sources': '', 'services': 'dhcpv6-client ssh', 'ports': '', 'protocols': '',
              'forward': 'yes', 'masquerade': 'no', 'forward-ports': '', 'source-ports': '', 'icmp-blocks': ''}
    lb = 'rule family=ipv4 source address=10.1.93.109/32 port port=6443 protocol=tcp accept'
    need(runtime['fields'] == fields and set(runtime['rules']) == {canonical(rule) for rule in ctl.cp1_rules()} | {lb}, 'public Runtime baseline unsupported')
    fields['interfaces'] = permanent['fields']['interfaces']
    need(fields['interfaces'] in ('', 'ens160') and permanent == {'fields': fields, 'rules': [lb]}, 'Permanent baseline unsupported')
    need(base['version'] == '2.4.3' and base['backend'] == 'nftables', 'unsupported firewalld backend/version')
    for kind in ('zones', 'policies', 'ipsets'):
        rt, perm = (copy.deepcopy(base['objects'][p][kind]) for p in ('runtime', 'permanent'))
        if kind == 'zones':
            rt.pop('public'); perm.pop('public')
        need(rt == perm, 'other Runtime definitions would be lost by reload')
    need(base['services']['runtime'] == base['services']['permanent'], 'public service definitions differ across planes')
    need(base['ipam'] == {'cidr': BLOCK, 'affinity': 'host:' + HOST, 'state': 'confirmed'}, 'owned /26 not confirmed')
    need(re.fullmatch('[a-f0-9-]{36}', base['boot_id']), 'boot identity missing')
    for plane in ('runtime', 'permanent'):
        need(base['objects'][plane]['zones']['public'] == fw['planes'][plane], 'public inventory mismatch')
        for kind, names in NAMES.items():
            need(not set(names) & set(base['objects'][plane][kind]), 'owned name already exists')
        policies = base['objects'][plane]['policies']
        raw = fw['active_policies']['configurations'][plane]
        need(policies == {name: parse_info(value, name) for name, value in raw.items()},
             'policy inventory/receipt mismatch: ' + plane)
        additional = set(policies) - {'allow-host-ipv6'}
        need('allow-host-ipv6' in policies and additional <= PRESERVED_DISABLED_POLICIES,
             'unsupported policy inventory: ' + plane + '/' + ','.join(sorted(additional)))
        need(policies['allow-host-ipv6']['fields'].get('disable') == 'no', 'HOST IPv6 policy disabled')
        # These five definitions already exist in the original CP1 receipt.
        # v2.4.3 excludes disabled policies from activation, even after reload or
        # a new zone binding. Preserve every field and raw output in whole-state
        # comparisons; this exception never enables, deletes or edits a policy.
        for name in sorted(additional):
            header = name + (' (disabled)' if plane == 'runtime' else '')
            need(policies[name]['fields'].get('disable') == 'yes' and raw[name].splitlines()[0] == header,
                 'preserved policy is not disabled: ' + plane + '/' + name)
        for name, zone in base['objects'][plane]['zones'].items():
            need(not zone['fields'].get('sources'), 'source-bound zone needs separate review')
            if name != 'public':
                need(not zone['fields'].get('interfaces'), 'other bound zone needs separate review')
    need(active_names(fw['active_policies']['active_output']) == {
        'allow-host-ipv6': 'allow-host-ipv6\n  ingress-zones: ANY\n  egress-zones: HOST'},
        'additional active policy needs preservation analysis')
    need(not fw['direct_runtime'] and not fw['direct_permanent'], 'direct rules need separate review')
    return receipt


def validate_view(view, base, *, allow_missing_runtime=False,
                  allow_transient_permanent_forward=False):
    """Closed-set before/after comparison; every owned object is checked fully."""
    need(view['version'] == base['version'] and view['backend'] == base['backend'] and
         view['boot_id'] == base['boot_id'] and view['ipam'] == base['ipam'] and
         view['protection'] == base['protection'], 'host/boot/IPAM/protection drift')
    expected = expected_objects(base); projected = copy.deepcopy(view)
    bound = False
    for plane in ('runtime', 'permanent'):
        for kind, names in NAMES.items():
            for name in names:
                actual = projected['objects'][plane][kind].pop(name, None)
                if actual is None:
                    continue
                want = copy.deepcopy(expected[kind][name])
                if kind == 'zones' and plane == 'runtime':
                    binding = actual['fields'].get('interfaces')
                    need(binding in ('', 'cali+'), 'owned Runtime binding drift')
                    bound = binding == 'cali+'
                    want['fields']['interfaces'] = binding
                elif (kind == 'zones' and plane == 'permanent' and name == ZONE and
                      allow_transient_permanent_forward and actual['fields'].get('forward') == 'yes'):
                    # Recovery only: accept the exact unbound intermediate
                    # created before --remove-forward. All other fields and
                    # rules still have to match the reviewed owned object.
                    want['fields']['forward'] = 'yes'
                need(actual == want, 'owned object drift: ' + plane + '/' + kind + '/' + name)
    fw = projected['firewall']
    active = active_names(fw['active_policies']['active_output'])
    for name in (LOCAL, EGRESS):
        for plane in ('runtime', 'permanent'):
            text = fw['active_policies']['configurations'][plane].pop(name, None)
            actual = view['objects'][plane]['policies'].get(name)
            need((text is None) == (actual is None), 'policy inventory/legacy mismatch')
            if text is not None:
                need(parse_info(text, name) == actual, 'policy text/semantic mismatch')
        present = active.pop(name, None)
        if bound:
            target = ZONE if name == LOCAL else 'ANY'
            need(present == f'{name}\n  ingress-zones: {ZONE}\n  egress-zones: {target}', 'active policy missing')
        else:
            need(present is None, 'policy active without owned binding')
    original_active = active_names(base['firewall']['active_policies']['active_output'])
    need(active == original_active, 'foreign active policy drift')
    fw['active_policies']['active_output'] = base['firewall']['active_policies']['active_output']
    if allow_missing_runtime:
        rules = fw['planes']['runtime']['rules']
        wanted = base['firewall']['planes']['runtime']['rules']
        required = base['firewall']['planes']['permanent']['rules']
        need(set(required) <= set(rules) <= set(wanted) and len(rules) == len(set(rules)), 'foreign/missing public rule')
        need(projected['objects']['runtime']['zones']['public'] == fw['planes']['runtime'], 'public views differ')
        fw['planes']['runtime']['rules'] = copy.deepcopy(wanted)
        projected['objects']['runtime']['zones']['public']['rules'] = copy.deepcopy(wanted)
    need(projected == base, 'unplanned state change outside owned objects')
    if bound:
        need(all(name in view['objects']['runtime'][kind] for kind, name in ORDER), 'active prerequisites absent')
        need(all(name not in view['objects']['permanent'][kind] for kind, name in ORDER), 'Permanent staging remains active')
    return bound


def check_authorization(auth, base, source_digest, clock=None):
    clock = clock or now()
    need(auth.get('schema') in ('fds.cp1-minimal-authorization.v1', 'fds.cp1-minimal-authorization.v2') and auth.get('execution_approved') is True and
         auth.get('independent_reviewer') == 'hayeounglee' and auth.get('review_decision') == 'APPROVED', 'independent execution decision absent')
    need(auth.get('operator') == 'GWONUK' and all(auth.get(key) is True for key in
         ('maintenance_freeze', 'console_recovery_available', 'backup_verified', 'protection_reviewed',
          'inert_permanent_staging_acknowledged')),
         'existing operational prerequisites not confirmed')
    url = auth.get('review_url', '').strip()
    if url.startswith('<') and url.endswith('>'):
        url = url[1:-1]
    need(re.fullmatch(r'https://github.com/321Team/infra-kubernetes/pull/11#(?:issuecomment|pullrequestreview)-[0-9]+', url), 'source review URL required')
    need(auth.get('reviewed_bundle_sha256') == source_digest and auth.get('before_sha256') == fingerprint(base) and
         auth.get('scope') == {'hostname': HOST, 'pod_block': BLOCK, 'interfaces': 'cali+', 'l4': 'ALL', 'plane': 'runtime'}, 'reviewed scope/source/state differs')
    start, end, reviewed = (instant(auth[key]) for key in ('window_start', 'window_end', 'reviewed_at'))
    kst = dt.timezone(dt.timedelta(hours=9))
    need(start.astimezone(kst).weekday() < 5 and end.astimezone(kst).weekday() < 5, 'weekday work window required')
    if auth['schema'] == 'fds.cp1-minimal-authorization.v2':
        retained = instant(auth['retention_until'])
        margin = auth.get('recovery_margin_seconds')
        # A staffed educational workday, not a measured performance guarantee.
        # No arbitrary 90-minute default, no silent overnight or weekend hold.
        need(type(margin) is int and margin >= 180, 'recovery margin must cover the existing 180-second recovery service')
        need(auth.get('retention_approved') is True, 'bounded retention was not reviewed')
        need(start.astimezone(kst).date() == end.astimezone(kst).date() == retained.astimezone(kst).date(),
             'one staffed KST workday required; overnight retention is a separate change')
        need(reviewed <= clock and start <= clock < end < retained and
             clock + dt.timedelta(seconds=margin + 420) < end,
             'work window expired/too short or retention order invalid')
        return {'review_url': url, 'withdraw_at': (end - dt.timedelta(seconds=margin)).isoformat(),
                'expires_at': end.isoformat(), 'retained_withdraw_at': (retained - dt.timedelta(seconds=margin)).isoformat(),
                'retention_until': retained.isoformat(), 'recovery_margin_seconds': margin}
    need(reviewed <= clock and start <= clock < end and 0 < (end - start).total_seconds() <= 7200 and
         clock + dt.timedelta(seconds=600) < end, 'window expired/too short/unbounded')
    return {'review_url': url, 'withdraw_at': (end - dt.timedelta(seconds=180)).isoformat(), 'expires_at': end.isoformat()}


def apply(io, journal, original, auth, source_digest):
    base = io.snapshot(); validate_baseline(base, original)
    window = check_authorization(auth, base, source_digest, io.now())
    version = 'v2' if auth['schema'] == 'fds.cp1-minimal-authorization.v2' else 'v1'
    record = {'schema': 'fds.cp1-minimal-change.' + version, 'job_id': io.job_id, 'status': 'PREPARED',
              'source_bundle_sha256': source_digest, 'authorization_sha256': fingerprint(auth),
              'original_receipt_sha256': fingerprint(original), 'before': base, 'operations': [],
              'started_at': io.now().isoformat(), **window, 'cp1_accepted': False}
    journal.save(record)
    if version == 'v2':
        try:
            io.arm(window['withdraw_at'], window['retained_withdraw_at'])
        except Exception as error:
            record.update(status='FAILED_REVIEW_REQUIRED', error=str(error))
            journal.save(record)
            try:
                io.disarm()
                need(io.snapshot() == base, 'state changed during timer setup')
                record.update(status='RESTORED_REVIEW_REQUIRED', restored=base, cleanup_complete=True,
                              recovery_armed=False, recovered_at=io.now().isoformat())
                journal.save(record)
            except Exception as cleanup_error:
                record['recovery_error'] = str(cleanup_error); journal.save(record)
            raise
    else:
        io.arm(window['withdraw_at'])
    record['recovery_armed'] = True; journal.save(record)
    model = documents(base)
    try:
        for kind, name in ORDER:
            validate_view(io.snapshot(), base)
            op = {'action': 'stage', 'kind': kind, 'name': name, 'status': 'INTENT'}
            record['operations'].append(op); journal.save(record)
            io.stage(kind, name, model[kind + '/' + name + '.xml'])
            op['status'] = 'COMPLETE'; journal.save(record)
        validate_view(io.snapshot(), base)
        record['status'] = 'RELOAD_INTENT'; journal.save(record)
        io.reload_restore(base)
        loaded = io.snapshot(); validate_view(loaded, base)
        need(all(name in loaded['objects'][plane][kind] for plane in ('runtime', 'permanent') for kind, name in ORDER), 'staged objects not loaded')
        for kind, name in reversed(ORDER):
            validate_view(io.snapshot(), base)
            io.remove_permanent(kind, name)
        view = io.snapshot(); validate_view(view, base)
        need(all(name not in view['objects']['permanent'][kind] for kind, name in ORDER), 'Permanent staging not removed')
        need(io.now() < instant(window['withdraw_at']) - dt.timedelta(seconds=300), 'activation window exhausted')
        record['status'] = 'ACTIVATE_INTENT'; journal.save(record)
        io.bind(True)
        view = io.snapshot(); need(validate_view(view, base), 'cali binding not active')
        record.update(status='ACTIVE_REVIEW_REQUIRED', after=view, after_sha256=fingerprint(view), ended_at=io.now().isoformat())
        journal.save(record)
        return record
    except Exception as error:
        record['error'] = str(error); record['status'] = 'FAILED_REVIEW_REQUIRED'; journal.save(record)
        try:
            recover(io, journal, record)
        except Exception as recovery_error:
            record['recovery_error'] = str(recovery_error); journal.save(record)
        raise


def recover(io, journal, record):
    if hasattr(io, 'begin_recovery'):
        io.begin_recovery()
    base = record['before']; view = io.snapshot()
    # No blind reload on foreign drift. Report it and retain the journal/timer.
    recovery_view = dict(allow_missing_runtime=True, allow_transient_permanent_forward=True)
    try:
        bound = validate_view(view, base, **recovery_view)
    except ValueError:
        owned = expected_objects(base)
        owned['zones'][ZONE]['fields']['interfaces'] = 'cali+'
        if all(view['objects']['runtime'][kind].get(name) == owned[kind][name] for kind, name in ORDER):
            io.bind(False)  # Withdraw only our exact binding; never reload foreign drift.
            record['binding_withdrawn_on_drift'] = True
            journal.save(record)
        raise
    record['status'] = 'RECOVERING'; journal.save(record)
    if bound:
        io.bind(False)
        need(not validate_view(io.snapshot(), base, **recovery_view), 'withdrawal not observed')
    for kind, name in reversed(ORDER):
        view = io.snapshot(); validate_view(view, base, **recovery_view)
        if name in view['objects']['permanent'][kind]:
            io.remove_permanent(kind, name)
    view = io.snapshot(); validate_view(view, base, **recovery_view)
    if view != base:
        io.reload_restore(base)
    need(io.snapshot() == base, 'full baseline restoration not observed')
    io.disarm()
    record.update(status='RESTORED_REVIEW_REQUIRED', restored=base, cleanup_complete=True,
                  recovery_armed=False, recovered_at=io.now().isoformat())
    journal.save(record)
    return record


def analyzer():
    spec = importlib.util.spec_from_file_location('minimal_trial_analyzer', ROOT / 'scripts/analyze-cp1-cni-trial.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def receipt(record, original, trial_request, trial_wrapper, reviewed_bundle, clock=None):
    """New schema; original receipt is linked intact, never relabelled as new."""
    clock = clock or now()
    need(record.get('status') == 'ACTIVE_REVIEW_REQUIRED' and record.get('recovery_armed') is True and
         record['source_bundle_sha256'] == reviewed_bundle and clock < instant(record['withdraw_at']), 'change inactive/unreviewed/expired')
    validate_baseline(record['before'], original)
    need(record['original_receipt_sha256'] == fingerprint(original) and
         record['after_sha256'] == fingerprint(record['after']) and validate_view(record['after'], record['before']), 'change receipt binding differs')
    trial_report = analyzer().analyze(trial_request, trial_wrapper)
    need(trial_report.get('status') == 'FIVE_FLOWS_REVIEW_REQUIRED' and trial_report.get('passed_flows') == 5 and
         not trial_report.get('issues'), 'actual five-flow evidence incomplete')
    trial = trial_wrapper['result']
    first_exec = min(instant(q['started_at']) for q in trial['queries'] if q['argv'][0] == 'exec')
    observations = [q for q in trial['queries'] if q['argv'] == ['get', 'pods', '-n', trial['namespace'], '-o', 'json']
                    and instant(q['ended_at']) <= first_exec]
    pods = analyzer().parse(max(observations, key=lambda q: instant(q['ended_at']))['stdout'])['items']
    need(all(ipaddress.ip_address(p['status']['podIP']) in ipaddress.ip_network(BLOCK) for p in pods),
         'trial peer outside reviewed /26')
    need(trial['firewall_before'] == trial['firewall_after'] == record['after']['firewall'], 'trial not on retained after-state')
    need(instant(record['ended_at']) <= instant(trial['started_at']) <= instant(trial['ended_at']) < instant(record['withdraw_at']), 'trial outside active change')
    need(instant(trial['ended_at']) <= clock, 'handoff cannot predate trial completion')
    return {'schema': 'fds.cp1-minimal-handoff.v1', 'status': 'AWAITING_INDEPENDENT_ACCEPTANCE',
            'original_receipt_sha256': fingerprint(original), 'change_receipt_sha256': fingerprint(record),
            'change': copy.deepcopy(record), 'after': copy.deepcopy(record['after']['firewall']),
            'trial_report': copy.deepcopy(trial_report), 'trial_wrapper_sha256': fingerprint(trial_wrapper),
            'issued_at': clock.isoformat(), 'cp1_accepted': False, 'authorizes_cp2_change': False}


def validate_handoff(handoff, original, trial_request, trial_wrapper, reviewed_bundle, clock=None, acceptance=None):
    clock = clock or now()
    rebuilt = receipt(handoff['change'], original, trial_request, trial_wrapper, reviewed_bundle,
                      instant(handoff['issued_at']))
    cutoff = handoff['change']['withdraw_at']
    if acceptance is not None and acceptance.get('schema') == 'fds.cp1-minimal-acceptance.v2':
        validate_acceptance(acceptance, handoff, clock)
        cutoff = handoff['change']['retained_withdraw_at']
    need(handoff == rebuilt and instant(handoff['issued_at']) <= clock < instant(cutoff),
         'handoff differs/expired')
    return handoff['change']


def validate_acceptance(data, handoff, clock=None):
    clock = clock or now()
    retained = data.get('schema') == 'fds.cp1-minimal-acceptance.v2'
    need(data.get('schema') in ('fds.cp1-minimal-acceptance.v1', 'fds.cp1-minimal-acceptance.v2') and data.get('decision') == 'ACCEPTED' and
         data.get('independent_reviewer') == 'hayeounglee' and data.get('review_decision') == 'APPROVED',
         'new independent CP1 acceptance absent')
    record = handoff['change']
    need(retained == (record.get('schema') == 'fds.cp1-minimal-change.v2'), 'acceptance/change version mismatch')
    if retained:
        need(data.get('retention_approved') is True and
             instant(data['reviewed_at']) < instant(record['withdraw_at']), 'retention requires acceptance before the first deadline')
    cutoff = record['retained_withdraw_at'] if retained else record['withdraw_at']
    need(data.get('handoff_sha256') == fingerprint(handoff) and
         data.get('source_bundle_sha256') == handoff['change']['source_bundle_sha256'] and
         data.get('expires_at') == cutoff, 'acceptance binding/lifetime differs')
    need(re.fullmatch(r'https://github.com/321Team/infra-kubernetes/pull/11#(?:issuecomment|pullrequestreview)-[0-9]+',
                      data.get('review_url', '')), 'acceptance review URL required')
    need(instant(handoff['issued_at']) <= instant(data['reviewed_at']) <= clock < instant(data['expires_at']),
         'acceptance timestamp invalid/expired')
    checks = data.get('checks', {})
    need(set(checks) == {'positive', 'negative', 'regression'}, 'acceptance test categories incomplete')
    for value in checks.values():
        need(value.get('status') == 'PASS' and re.fullmatch('[0-9a-f]{64}', value.get('evidence_sha256', '')),
             'acceptance check lacks evidence')
    return data


def live_gate(status, record, minimum_seconds=0, clock=None, acceptance=None, required_until=None):
    clock = clock or now()
    retained = acceptance is not None and acceptance.get('schema') == 'fds.cp1-minimal-acceptance.v2'
    cutoff = record['retained_withdraw_at'] if retained else record['withdraw_at']
    if retained:
        need(status.get('acceptance_sha256') == fingerprint(acceptance) and
             status.get('handoff_sha256') == acceptance.get('handoff_sha256'), 'live retained acceptance differs')
    need(status.get('status') == ('ACCEPTED_RETAINED' if retained else 'ACTIVE_REVIEW_REQUIRED') and status.get('recovery_armed') is True and
         status.get('job_id') == record['job_id'] and status.get('source_bundle_sha256') == record['source_bundle_sha256'] and
         status.get('change_receipt_sha256') == fingerprint(record) and status.get('withdraw_at') == cutoff,
         'live job/source/recovery differs')
    need(status.get('current') == record['after'] and validate_view(status['current'], record['before']),
         'live whole after-state differs')
    need(clock + dt.timedelta(seconds=minimum_seconds) < instant(cutoff), 'remaining lifetime insufficient')
    if required_until is not None:
        need(clock < instant(required_until) < instant(cutoff), 'target work window must finish before CP1 withdrawal')


def retention_proof(record, handoff, acceptance, clock=None):
    """Bind an already reviewed result; never infer GitHub approval from a hash."""
    clock = clock or now()
    need(record.get('schema') == 'fds.cp1-minimal-change.v2' and
         record.get('status') == 'ACTIVE_REVIEW_REQUIRED' and record.get('recovery_armed') is True,
         'only a new active v2 job can enter retained state')
    need(handoff.get('schema') == 'fds.cp1-minimal-handoff.v1' and handoff.get('change') == record and
         handoff.get('change_receipt_sha256') == fingerprint(record), 'retention handoff/job differs')
    validate_acceptance(acceptance, handoff, clock)
    need(clock < instant(record['withdraw_at']), 'retention cannot revive an expired trial')
    return {'schema': 'fds.cp1-minimal-retention.v1', 'job_id': record['job_id'],
            'change_receipt_sha256': fingerprint(record), 'handoff_sha256': fingerprint(handoff),
            'acceptance_sha256': fingerprint(acceptance), 'accepted_at': clock.isoformat(),
            'withdraw_at': record['retained_withdraw_at']}


def validate_retention(proof, record, handoff, acceptance):
    # Replay at the committed acceptance time, including at final expiry. The
    # caller still checks the effective deadline; this is not a lifetime bypass.
    need(proof == retention_proof(record, handoff, acceptance, instant(proof['accepted_at'])),
         'root retention proof differs')
    return proof
