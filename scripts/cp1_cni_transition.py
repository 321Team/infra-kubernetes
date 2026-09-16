"""Write-ahead lifecycle for the managed policy's firewall definitions.

An authenticated local adapter must verify whole native/API/fixed state and
hold the exclusive run/global locks. No CLI, root staging or approval bypass.
Definition restoration is distinct from management and unit finalization.
"""
import copy
import re
import xml.etree.ElementTree as ET

from cp1_cni_membership import need, same, fingerprint, validate_lease, entries
from cp1_cni_observer import NAME, RICH_RULE, policy_definition
from cp1_cni_guard import ROOT

SCHEMA = 'fds.cp1-cni-firewall-transition.v1'


def six_rules():
    return sorted('rule family=ipv4 source address=10.1.93.%s/32 port port=%s protocol=tcp accept' % (ip, port)
                  for ip in (102, 103, 104) for port in ('10250', '2379-2380'))


def set_definition():
    return {'type': 'hash:net,iface', 'options': 'family=inet', 'entries': '', 'rich rules': []}


def policy(disabled=True):
    return {'target': 'CONTINUE', 'priority': '-1', 'ingress-zones': 'public', 'egress-zones': 'public',
            'disable': 'yes' if disabled else 'no', 'masquerade': 'no', 'rich rules': [RICH_RULE],
            **{key: '' for key in ('services', 'ports', 'protocols', 'icmp-blocks', 'forward-ports', 'source-ports')}}


def documents(run_id):
    need(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', run_id or ''), 'invalid transition run')
    ipset = ET.Element('ipset', type='hash:net,iface')
    ET.SubElement(ipset, 'description').text = run_id
    ET.SubElement(ipset, 'option', name='family', value='inet')
    pol = ET.Element('policy', target='CONTINUE', priority='-1')
    ET.SubElement(pol, 'description').text = run_id
    ET.SubElement(pol, 'disable')
    ET.SubElement(pol, 'ingress-zone', name='public'); ET.SubElement(pol, 'egress-zone', name='public')
    rich = ET.SubElement(pol, 'rule', family='ipv4')
    ET.SubElement(rich, 'source', ipset=NAME); ET.SubElement(rich, 'destination', ipset=NAME)
    ET.SubElement(rich, 'accept')
    return {'ipset.xml': ET.tostring(ipset, encoding='unicode') + '\n',
            'policy.xml': ET.tostring(pol, encoding='unicode') + '\n'}


def validate_snapshot(value):
    need(set(value) == {'inventory', 'descriptions', 'fixed_sha256'}, 'transition snapshot fields differ')
    need(re.fullmatch(r'[a-f0-9]{64}', value.get('fixed_sha256', '')), 'verified fixed state absent')
    need(set(value['inventory']) == {'runtime', 'permanent'}, 'both configuration planes required')
    for plane in ('runtime', 'permanent'):
        data = value['inventory'][plane]
        need(set(data) == {'zones', 'policies', 'ipsets', 'direct'} and 'public' in data['zones'],
             'whole firewall inventory absent')
        for zone in data['zones'].values():
            need(isinstance(zone.get('rich rules'), list) and zone['rich rules'] == sorted(set(zone['rich rules'])),
                 'canonical unique zone rules required')
        if NAME in data['policies']:
            policy_definition(data['policies'][NAME], data['policies'][NAME].get('disable') == 'yes')
            need(NAME in data['ipsets'], 'managed policy without managed set')
        if NAME in data['ipsets']:
            own = copy.deepcopy(data['ipsets'][NAME]); found = entries(own['entries'].split())
            need(' '.join(found) == own['entries'], 'noncanonical owned entries')
            own['entries'] = ''
            need(same(own, set_definition()), 'owned set definition differs')
            if plane == 'permanent':
                need(not found, 'permanent membership must remain empty')
        if plane == 'permanent' and NAME in data['policies']:
            need(data['policies'][NAME]['disable'] == 'yes', 'permanent policy must remain disabled')
    expected_desc = {kind for kind, table in (('ipset', 'ipsets'), ('policy', 'policies'))
                     if NAME in value['inventory']['permanent'][table]}
    need(set(value['descriptions']) == expected_desc, 'description inventory differs')
    return value


def validate_baseline(value):
    validate_snapshot(value)
    need(not value['descriptions'], 'baseline managed description collision')
    planes = copy.deepcopy(value['inventory'])
    for data in planes.values():
        need(NAME not in data['policies'] and NAME not in data['ipsets'], 'baseline owned name collision')
        need(all(zone.get('sources') == '' for zone in data['zones'].values()), 'source-based zone unsupported')
    current, saved = (planes[p]['zones']['public']['rich rules'] for p in ('runtime', 'permanent'))
    need(set(current) - set(saved) == set(six_rules()) and set(saved) <= set(current),
         'reload must discard exactly the original Runtime six')
    runtime = planes['runtime']['zones']['public']; permanent = planes['permanent']['zones']['public']
    need(runtime.get('interfaces') == 'ens160' and permanent.get('interfaces') in ('', 'ens160'),
         'management interface binding differs')
    runtime['rich rules'] = copy.deepcopy(saved)
    runtime['interfaces'] = permanent['interfaces']
    need(same(planes['runtime'], planes['permanent']), 'reload would discard foreign Runtime changes')
    return value


def reloaded(value):
    result = copy.deepcopy(value)
    result['inventory']['runtime'] = copy.deepcopy(result['inventory']['permanent'])
    result['inventory']['runtime']['zones']['public']['interfaces'] = 'ens160'
    return result


def steps(baseline, run_id):
    validate_baseline(baseline)
    documents(run_id); path = ROOT / run_id / 'definitions'
    result = []; state = copy.deepcopy(baseline)
    def put(label, argv, after):
        nonlocal state
        validate_snapshot(after)
        result.append({'label': label, 'argv': argv, 'before': copy.deepcopy(state), 'after': copy.deepcopy(after)})
        state = after
    after = copy.deepcopy(state); after['inventory']['permanent']['ipsets'][NAME] = set_definition()
    after['descriptions']['ipset'] = run_id
    put('create-permanent-empty-set', ['--permanent', '--new-ipset-from-file=' + str(path / 'ipset.xml'), '--name=' + NAME], after)
    after = copy.deepcopy(state); after['inventory']['permanent']['policies'][NAME] = policy()
    after['descriptions']['policy'] = run_id
    put('create-permanent-disabled-policy', ['--permanent', '--new-policy-from-file=' + str(path / 'policy.xml'), '--name=' + NAME], after)
    put('load-disabled-definitions', ['--reload'], reloaded(state))
    for rule in six_rules():
        after = copy.deepcopy(state); after['inventory']['runtime']['zones']['public']['rich rules'].append(rule)
        after['inventory']['runtime']['zones']['public']['rich rules'].sort()
        put('restore-original-' + str(len(result) - 2), ['--zone=public', '--add-rich-rule=' + rule], after)
    after = copy.deepcopy(state); after['inventory']['runtime']['policies'][NAME] = policy(False)
    put('enable-empty-runtime-policy', ['--policy=' + NAME, '--remove-disable'], after)
    return result


def initial(lease, baseline):
    validate_lease(lease); validate_baseline(baseline)
    plan = steps(baseline, lease['run_id'])
    need(lease['policy_sha256'] == fingerprint(documents(lease['run_id'])), 'lease definition hash differs')
    return {'schema': SCHEMA, 'lease': copy.deepcopy(lease), 'baseline': copy.deepcopy(baseline),
            'plan_sha256': fingerprint(plan), 'expected': copy.deepcopy(baseline), 'pending': None,
            'operations': [], 'phase': 'PREPARING', 'membership_handed_off': False, 'firewall_restored': False,
            'full_recovery_complete': False, 'authorizes_apply': False, 'authorizes_join': False}


def clean_result(value):
    return (isinstance(value, dict) and set(value) == {'rc', 'stdout', 'stderr', 'truncated'} and
            type(value['rc']) is int and value['rc'] == 0 and isinstance(value['stdout'], str) and
            isinstance(value['stderr'], str) and value['stdout'].strip() == 'success' and
            value['stderr'].strip() == '' and value['truncated'] is False)


class Transaction:
    """IO is a bounded trusted adapter, not user-supplied JSON attestations.

    guard(cleanup) must check live recovery before grants and a separate hard
    cleanup deadline after expiry/reboot. Caller stops the work cgroup before
    recovery, then acquires the same exclusive journal/global locks.
    """
    def __init__(self, state, io, journal):
        need(state.get('schema') == SCHEMA, 'wrong transition schema')
        validate_lease(state['lease']); validate_baseline(state['baseline'])
        need(state['plan_sha256'] == fingerprint(steps(state['baseline'], state['lease']['run_id'])), 'transition plan changed')
        for key in ('full_recovery_complete', 'authorizes_apply', 'authorizes_join'):
            need(state.get(key) is False, 'firewall transaction cannot grant ' + key)
        need(state['phase'] in ('PREPARING', 'EMPTY_ACTIVE', 'MEMBERSHIP', 'WITHDRAWING', 'FIREWALL_RESTORED'), 'unknown transition phase')
        need(type(state.get('membership_handed_off')) is bool and
             (state['phase'] not in ('PREPARING','EMPTY_ACTIVE') or not state['membership_handed_off']) and
             (state['phase'] != 'MEMBERSHIP' or state['membership_handed_off']), 'membership handoff marker differs')
        validate_snapshot(state['expected'])
        self.state, self.io, self.journal = copy.deepcopy(state), io, journal

    def save(self): self.journal.save(self.state)

    def reconcile(self):
        actual = self.io.snapshot(); validate_snapshot(actual)
        pending = self.state['pending']
        if pending:
            need(any(same(actual, pending[k]) for k in ('before', 'after')), 'pending transition has unknown third state')
            self.state['operations'].append({**copy.deepcopy(pending), 'outcome': 'AFTER_OBSERVED' if same(actual,pending['after']) else 'BEFORE_OBSERVED', 'result': None})
            self.state.update(expected=actual, pending=None); self.save()
        need(same(actual, self.state['expected']), 'foreign firewall/fixed-state drift')
        return actual

    def change(self, label, argv, after, *, cleanup=False):
        self.io.guard(cleanup)
        before = self.reconcile(); validate_snapshot(after)
        need(not same(before, after), 'no-op transition')
        pending = {'label': label, 'argv': list(argv), 'before': before, 'after': copy.deepcopy(after),
                   'clock': self.io.clock(), 'cleanup': cleanup}
        self.state['pending'] = pending; self.save()
        # Re-check the hard time/unit boundary AFTER fsync and immediately
        # before the command. Pending intent remains on every exception.
        self.io.guard(cleanup)
        result = self.io.command(argv, cleanup=cleanup)
        actual = self.io.snapshot(); validate_snapshot(actual)
        need(same(actual, after), 'post-transition state differs: ' + label)
        self.state['operations'].append({**copy.deepcopy(pending), 'result': copy.deepcopy(result),
                                        'outcome': 'COMMAND_AND_STATE_VERIFIED' if clean_result(result) else 'AFTER_OBSERVED_UNCERTAIN_COMMAND'})
        self.state.update(expected=actual, pending=None); self.save()
        # Observed completion is retained, but unknown command results do not
        # permit activation to continue. Recovery can use the observed prefix.
        need(clean_result(result) or cleanup, 'activation command result uncertain')

    def activate_empty(self):
        need(self.state['phase'] == 'PREPARING', 'activation cannot restart after handoff/withdrawal')
        plan = steps(self.state['baseline'], self.state['lease']['run_id'])
        current = self.reconcile()
        candidates = [self.state['baseline']] + [v['after'] for v in plan]
        index = next((i for i, v in enumerate(candidates) if same(v,current)), None)
        need(index is not None, 'activation state is not a known plan prefix')
        # A previous uncertain command must be reviewed/recovered, not turned
        # into a successful activation by invoking this function again.
        need(all(x['outcome'] == 'COMMAND_AND_STATE_VERIFIED' for x in self.state['operations']),
             'activation has unverified command history: recovery only')
        for change in plan[index:]:
            if change['label'] == 'enable-empty-runtime-policy':
                self.io.management()  # fresh external management proof after reload
            self.change(change['label'], change['argv'], change['after'])
        self.state['phase'] = 'EMPTY_ACTIVE'; self.save()
        return copy.deepcopy(self.state)

    def handoff_membership(self):
        need(self.state['phase'] == 'EMPTY_ACTIVE', 'empty activation must precede membership')
        self.reconcile(); self.state.update(phase='MEMBERSHIP', membership_handed_off=True); self.save()

    def recover(self, *, membership_state=None):
        # Irreversible local stop intent; never renew or resume a grant.
        self.state.update(phase='WITHDRAWING',firewall_restored=False)
        try:
            self.save()
            allowed = [copy.deepcopy(self.state['expected'])]
            if self.state['pending']:
                allowed += [copy.deepcopy(self.state['pending'][k]) for k in ('before', 'after')]
            return self._recover(allowed,membership_state)
        except Exception:
            # Narrow withdrawal is attempted even if the membership journal
            # cannot establish a broad deletion/reload prefix.
            self.io.emergency_disable()
            raise

    def _recover(self, allowed, membership_state):
        if self.state['membership_handed_off'] or membership_state is not None:
            from cp1_cni_membership import validate_state
            need(membership_state is not None, 'membership journal required for changed elements')
            validate_state(membership_state)
            need(same(membership_state['lease'], self.state['lease']), 'foreign membership lease')
            found = [membership_state['expected_entries']]
            if membership_state['pending']:
                found += [membership_state['pending'][k] for k in ('before','after')]
            for template in list(allowed):
                # Once an independently observed reload removed the objects,
                # a stale membership journal cannot recreate them in the
                # allowed recovery prefix. The exact prefix remains allowed.
                runtime = template['inventory']['runtime']
                if NAME not in runtime['ipsets'] or NAME not in runtime['policies']:
                    continue
                for values in found:
                    values = entries(values)
                    for disabled in (False, True):
                        candidate = copy.deepcopy(template)
                        candidate['inventory']['runtime']['ipsets'][NAME]['entries'] = ' '.join(values)
                        candidate['inventory']['runtime']['policies'][NAME] = policy(disabled)
                        allowed.append(candidate)
        if self.io.clock()['boot_id'] != self.state['lease']['boot_id']:
            allowed += [reloaded(v) for v in list(allowed)]
        actual = self.io.snapshot(); validate_snapshot(actual)
        need(any(same(actual,v) for v in allowed), 'unknown recovery state or foreign drift')
        self.state.update(expected=actual, pending=None); self.save()
        own = actual['inventory']['runtime']['policies'].get(NAME)
        if own and own['disable'] == 'no':
            after = copy.deepcopy(actual); after['inventory']['runtime']['policies'][NAME] = policy()
            self.change('disable-owned-grant', ['--policy=' + NAME,'--add-disable'], after, cleanup=True)
        for kind, table in (('policy','policies'),('ipset','ipsets')):
            actual = self.state['expected']
            if NAME in actual['inventory']['permanent'][table]:
                need(actual['descriptions'].get(kind) == self.state['lease']['run_id'], 'foreign permanent object')
                after = copy.deepcopy(actual); del after['inventory']['permanent'][table][NAME]
                del after['descriptions'][kind]
                self.change('delete-owned-' + kind,['--permanent','--delete-' + kind + '=' + NAME],after,cleanup=True)
        actual = self.state['expected']
        if any(NAME in actual['inventory']['runtime'][k] for k in ('policies','ipsets')):
            self.change('unload-owned-definitions',['--reload'],reloaded(actual),cleanup=True)
        for rule in six_rules():
            actual = self.state['expected']
            if rule not in actual['inventory']['runtime']['zones']['public']['rich rules']:
                after = copy.deepcopy(actual); after['inventory']['runtime']['zones']['public']['rich rules'].append(rule)
                after['inventory']['runtime']['zones']['public']['rich rules'].sort()
                self.change('restore-original-runtime-rule',['--zone=public','--add-rich-rule='+rule],after,cleanup=True)
        need(same(self.reconcile(),self.state['baseline']), 'original whole firewall not restored')
        self.state.update(firewall_restored=True,phase='FIREWALL_RESTORED');self.save()
        # Unit finalization and independent management proof remain separate.
        # Neither are inferred from a successful firewall-cmd/reload result.
        return copy.deepcopy(self.state)
