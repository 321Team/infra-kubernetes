"""Unit ownership/loaded configuration/watchdog tests; systemd IO is mocked."""
import copy
import unittest

from test_cp1_cni_membership import setup, observed, lease, m
import cp1_cni_guard as g


def unit_fixture(l):
    docs, names = g.documents(l), g.names(l['run_id'])
    properties = {}
    command = '/usr/bin/python3 -I -B ' + str(g.ROOT / l['run_id'] / 'scripts/run-cp1-cni-managed-node.py')
    for kind, name in names.items():
        p = {'Id': name, 'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
             'MainPID': '0', 'Job': '', 'FragmentPath': str(g.UNITS / name), 'DropInPaths': '',
             'UnitFileState': 'static'}
        if kind != 'timer':
            mode = 'manage' if kind == 'work' else kind
            p['ExecStart'] = '{ path=/usr/bin/python3 ; argv[]=' + command + ' ' + mode + ' ; ignore_errors=no ; pid=0 ; code=(null) ; status=0/0 }'
            p.update(Type='exec' if kind == 'work' else 'oneshot', Restart='no', UMask='0077',
                     KillMode='control-group', TimeoutStopUSec='5000000')
        if kind in ('work', 'guard'): p['OnFailure'] = names['recover']
        if kind == 'work':
            p.update(ActiveState='active', MainPID='123', KillMode='control-group', TimeoutStopUSec='5000000',
                     RuntimeMaxUSec=str(int(l['boot_end'] - l['boot_start'] - m.RESERVE)*1000000))
        if kind == 'guard': p['TimeoutStartUSec'] = '15000000'
        if kind == 'timer': p.update(ActiveState='active', SubState='waiting', UnitFileState='enabled',
                                      Unit=names['guard'], NextElapseUSecMonotonic='115000000',
                                      TimersMonotonic=[['OnActiveUSec', 1000000], ['OnUnitInactiveUSec', 5000000]],
                                      AccuracyUSec='1000000', RandomizedDelayUSec='0')
        if kind == 'recover': p.update(UnitFileState='enabled', TimeoutStartUSec='150000000')
        properties[name] = p
    return {'files': docs, 'properties': properties, 'links': {
        str(g.UNITS / 'timers.target.wants' / names['timer']): str(g.UNITS / names['timer']),
        str(g.UNITS / 'multi-user.target.wants' / names['recover']): str(g.UNITS / names['recover'])}}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.manager, self.io, self.j = setup()
        self.state = self.manager.reconcile(observed(self.io))
        self.lease = self.state['lease']
        self.observation = unit_fixture(self.lease)
        self.clock = dict(self.io.clock(), monotonic=110)

    def test_guard_is_derived_from_loaded_units_not_stored_flag(self):
        result = g.verified_guard(self.observation, self.lease, self.clock)
        self.assertTrue(result['armed'])
        self.assertEqual(result['deadline'], self.lease['boot_end'])
        self.assertEqual(result['unit_observation_sha256'], m.fingerprint(self.observation))

    def test_source_file_match_does_not_hide_stale_loaded_exec_or_dropin(self):
        names = g.names(self.lease['run_id'])
        for field, value in (('ExecStart', '/bin/true'), ('DropInPaths', '/etc/foreign.conf'),
                             ('FragmentPath', '/run/transient/other.service'), ('OnFailure', 'foreign.service'),
                             ('TimeoutStopUSec', '999999999')):
            candidate = copy.deepcopy(self.observation)
            candidate['properties'][names['work']][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): g.validate_owned(candidate, self.lease)

    def test_timer_wrong_target_late_schedule_missing_boot_link_rejected(self):
        names = g.names(self.lease['run_id'])
        edits = [lambda v: v['properties'][names['timer']].update(Unit='foreign.service'),
                 lambda v: v['properties'][names['timer']].update(NextElapseUSecMonotonic='500000000'),
                 lambda v: v['properties'][names['timer']].update(ActiveState='inactive'),
                 lambda v: v['links'].clear()]
        for edit in edits:
            value = copy.deepcopy(self.observation); edit(value)
            with self.subTest(edit=edit), self.assertRaises(ValueError): g.verified_guard(value, self.lease, self.clock)

    def test_current_guard_invocation_can_have_no_next_elapse_until_it_exits(self):
        names = g.names(self.lease['run_id'])
        self.observation['properties'][names['timer']].update(NextElapseUSecMonotonic='0', SubState='running')
        self.observation['properties'][names['guard']].update(ActiveState='activating', MainPID='456')
        self.assertTrue(g.verified_guard(self.observation, self.lease, self.clock)['armed'])
        self.observation['properties'][names['guard']]['MainPID'] = '0'
        with self.assertRaises(ValueError): g.verified_guard(self.observation, self.lease, self.clock)

    def test_live_healthy_work_does_not_dispatch_recovery(self):
        calls = []
        result = g.supervise(self.state, self.clock, self.observation,
                             lambda *a, **kw: calls.append(a), lambda v: calls.append(v))
        self.assertEqual(result['action'], 'WATCH'); self.assertEqual(calls, [])

    def test_stale_boot_change_or_stopped_work_dispatches_same_owned_recovery_after_persist(self):
        for scenario in ('stale', 'boot', 'stopped'):
            clock, units = copy.deepcopy(self.clock), copy.deepcopy(self.observation)
            if scenario == 'stale': clock['boottime'] += 30
            if scenario == 'boot': clock['boot_id'] = 'a'*8 + clock['boot_id'][8:]
            if scenario == 'stopped': units['properties'][g.names(self.lease['run_id'])['work']]['MainPID'] = '0'
            calls = []
            def command(argv, **kw):
                self.assertEqual(calls, ['persist'])
                calls.append(argv); return {'rc': 0, 'stdout': '', 'stderr': '', 'truncated': False}
            result = g.supervise(self.state, clock, units, command, lambda v: calls.append('persist'))
            with self.subTest(scenario=scenario):
                self.assertEqual(result['action'], 'RECOVER')
                self.assertEqual(calls[-1], ['systemctl', '--no-block', 'start', g.names(self.lease['run_id'])['recover']])
                self.assertFalse(result['full_recovery_complete'])

    def test_unknown_or_foreign_unit_never_started_or_stopped(self):
        name = g.names(self.lease['run_id'])['recover']
        self.observation['properties'][name]['FragmentPath'] = '/etc/foreign.service'
        calls = []
        with self.assertRaises(ValueError):
            g.supervise(self.state, self.clock, self.observation, lambda *a, **k: calls.append(a), lambda x: None)
        self.assertEqual(calls, [])

    def test_query_uses_typed_dbus_microseconds_and_read_only_commands(self):
        calls = []
        properties = self.observation['properties']
        def command(argv, timeout):
            self.assertLessEqual(timeout, 3); calls.append(argv)
            if argv[0] == 'systemctl':
                body = '\n'.join(k+'='+str(v) for k, v in properties[argv[2]].items())
            else:
                prop = argv[-1]
                kind = next(k for k, name in g.names(self.lease['run_id']).items()
                            if argv[-3].endswith(''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x') for c in name)))
                body = ('a(stt) 2 "OnActiveUSec" 1000000 111000000 "OnUnitInactiveUSec" 5000000 115000000'
                        if prop == 'TimersMonotonic' else 't ' + properties[g.names(self.lease['run_id'])[kind]][prop])
                self.assertIn('_2d', argv[-3])
            return {'rc': 0, 'stdout': body, 'stderr': '', 'truncated': False}
        result = g.query_units(self.lease, command,
                               lambda p: self.observation['files'][p.name].encode(),
                               lambda p: self.observation['links'][str(p)])
        self.assertEqual(result, self.observation)
        self.assertEqual(len(calls), 14)
        self.assertTrue(all(a[0] in ('systemctl', 'busctl') for a in calls))

    def test_unit_names_cannot_contain_path_or_shell_syntax(self):
        for value in ('../foreign', '20260917T000000Z-abcdef012345;reboot', 'a'*64):
            with self.subTest(value=value), self.assertRaises(ValueError): g.names(value)

    def test_generated_guard_and_work_share_no_process_or_restart_policy(self):
        names, docs = g.names(self.lease['run_id']), g.documents(self.lease)
        self.assertIn('Restart=no', docs[names['work']])
        self.assertIn('OnUnitInactiveSec=5s', docs[names['timer']])
        self.assertIn('WantedBy=multi-user.target', docs[names['recover']])
        self.assertNotIn('PartOf=', docs[names['guard']])

    def test_stale_loaded_timer_period_blocked_even_when_next_elapse_is_near(self):
        name = g.names(self.lease['run_id'])['timer']
        self.observation['properties'][name]['TimersMonotonic'][1][1] = 3600000000
        with self.assertRaisesRegex(ValueError, 'period/accuracy'):
            g.verified_guard(self.observation, self.lease, self.clock)

    def test_disk_failure_still_dispatches_owned_recovery_without_claiming_success(self):
        self.clock['boottime'] += 30
        calls = []
        def persist(_): raise OSError('disk full')
        def command(argv, **kwargs):
            calls.append(argv); return {'rc': 0, 'stdout': '', 'stderr': '', 'truncated': False}
        with self.assertRaisesRegex(ValueError, 'persistence failed'):
            g.supervise(self.state, self.clock, self.observation, command, persist)
        self.assertEqual(len(calls), 1)

    def test_stale_loaded_lifecycle_cannot_extend_recovery_or_restart_work(self):
        names = g.names(self.lease['run_id'])
        for kind, field, value in (('work', 'Restart', 'always'), ('guard', 'Type', 'forking'),
                                   ('guard', 'KillMode', 'process'), ('recover', 'TimeoutStartUSec', '900000000'),
                                   ('recover', 'TimeoutStopUSec', '90000000'), ('recover', 'UMask', '0022')):
            observed = copy.deepcopy(self.observation)
            observed['properties'][names[kind]][field] = value
            with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                g.validate_owned(observed, self.lease)


if __name__ == '__main__': unittest.main()
