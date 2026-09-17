"""Pinned command boundary tests; no actual systemctl commands are executed."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_cp1_cni_membership import lease
import cp1_cni_systemd as s
import cp1_cni_guard as g


class SystemdAdapterTests(unittest.TestCase):
    def fixture(self):
        calls=[];l=lease();clock=[100.]
        def command(args,timeout):
            calls.append((args,timeout));return {'rc':0,'stdout':'','stderr':'','stdout_truncated':False,'stderr_truncated':False}
        node=SimpleNamespace(__file__=str(Path(s.__file__).with_name('node-firewall.py')),
                             COMMAND_STATE={'count':12,'deadline':101},command=command)
        return s.LocalSystemd(node,l,deadline=120,monotonic=lambda:clock[0]),node,calls,clock

    def test_exact_owned_commands_use_bound_and_restore_helper_context(self):
        v,node,calls,clock=self.fixture();before=copy.deepcopy(node.COMMAND_STATE)
        r=v.command(['systemctl','--no-block','start',v.unit['recover']],timeout=99)
        self.assertEqual(r,{'rc':0,'stdout':'','stderr':'','truncated':False})
        self.assertEqual(calls[0][1],5);self.assertEqual(node.COMMAND_STATE,before)

    def test_foreign_service_generic_enable_restart_or_shell_are_not_allowed(self):
        v,node,calls,clock=self.fixture()
        for argv in (['systemctl','restart',v.unit['work']],['systemctl','stop','firewalld'],
                     ['systemctl','enable','--now',v.unit['work']],['sh','-c','true'],
                     ['systemctl','--no-block','start',v.unit['work']],
                     ['busctl','--system','--timeout=3','get-property','org.freedesktop.systemd1',
                      '/org/freedesktop/systemd1/unit/foreign','org.freedesktop.systemd1.Service','MainPID']):
            with self.subTest(argv=argv),self.assertRaises(ValueError):v.command(argv,timeout=5)
        self.assertEqual(calls,[])

    def test_command_timeout_budget_does_not_renew_on_repeated_calls(self):
        v,node,calls,clock=self.fixture();clock[0]=119
        v.command(['systemctl','daemon-reload'],timeout=5);self.assertEqual(calls[0][1],1)
        clock[0]=120
        with self.assertRaisesRegex(ValueError,'exhausted'):v.command(['systemctl','daemon-reload'],timeout=5)
        self.assertEqual(len(calls),1)

    def test_late_result_or_exception_restores_prior_global_command_state(self):
        v,node,calls,clock=self.fixture();before=copy.deepcopy(node.COMMAND_STATE)
        def late(*args,**kwargs):clock[0]=120;return {'rc':0,'stdout':'','stderr':''}
        node.command=late
        with self.assertRaisesRegex(ValueError,'after deadline'):v.command(['systemctl','daemon-reload'],timeout=5)
        self.assertEqual(node.COMMAND_STATE,before)
        clock[0]=100;node.command=lambda *a,**kw:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):v.command(['systemctl','daemon-reload'],timeout=5)
        self.assertEqual(node.COMMAND_STATE,before)

    def test_unit_query_respects_external_absolute_deadline(self):
        l=lease();calls=[]
        with patch.object(g.time,'monotonic',return_value=100):
            with self.assertRaisesRegex(ValueError,'deadline exhausted'):
                g.query_units(l,lambda *a,**kw:calls.append(a),lambda _:b'',lambda _:None,deadline=100)
        self.assertEqual(calls,[])

    def test_root_and_pinned_helper_are_required_before_any_command(self):
        with patch.object(s.os,'geteuid',return_value=1000),self.assertRaisesRegex(ValueError,'root'):self.fixture()
        v,node,calls,clock=self.fixture();node.__file__=s.__file__
        with self.assertRaisesRegex(ValueError,'source differs'):s.LocalSystemd(node,lease(),deadline=120,monotonic=lambda:100)

    def test_truncation_is_carried_to_the_callers_strict_result_gate(self):
        v,node,calls,clock=self.fixture()
        node.command=lambda *a,**kw:{'rc':0,'stdout':'','stderr':'','stderr_truncated':True}
        self.assertTrue(v.command(['systemctl','daemon-reload'],timeout=5)['truncated'])

    def test_real_files_and_pinned_adapter_feed_service_arm_and_start(self):
        from test_cp1_cni_service_control import ServiceControlTests
        import cp1_cni_service_control as control
        case=ServiceControlTests(methodName='runTest');case.setUp()
        try:
            node=SimpleNamespace(__file__=str(Path(s.__file__).with_name('node-firewall.py')),COMMAND_STATE={})
            def command(argv,timeout):
                props=case.observed['properties']
                if argv[:2]==['systemctl','show']:
                    body='\n'.join(k+'='+str(v) for k,v in props[argv[2]].items())
                elif argv[0]=='busctl':
                    name=next(n for n in props if argv[5].endswith(''.join(c if c.isascii() and c.isalnum()
                              else '_'+format(ord(c),'02x') for c in n)))
                    body=('a(stt) 2 "OnActiveUSec" 1000000 111000000 "OnUnitInactiveUSec" 5000000 115000000'
                          if argv[-1]=='TimersMonotonic' else 't '+props[name][argv[-1]])
                else:
                    case.run_command(argv,timeout);body=''
                return {'rc':0,'stdout':body,'stderr':'','stdout_truncated':False,'stderr_truncated':False}
            node.command=command
            adapter=s.LocalSystemd(node,case.lease,deadline=120,monotonic=lambda:100)
            c=control.ServiceControl(case.lease,case.manifest,case.sha,case.publication,case.launch,
                    adapter,lambda:copy.deepcopy(case.now),case.before_launch)
            with patch.object(g.time,'monotonic',return_value=100):
                self.assertEqual(c.arm(adapter.command)['phase'],'ENABLED')
                self.assertEqual(c.start(adapter.command)['phase'],'START_REQUESTED')
                self.assertEqual(len(case.commands),2)
                observed=adapter(include_links=True)
                self.assertEqual(len(observed['links']),2)
                # A changed real unit file blocks observation before any new mutation.
                path=case.units/g.names(case.run)['work'];path.chmod(0o600)
                path.write_text('foreign\n');path.chmod(0o400)
                with self.assertRaises(ValueError):adapter()
                self.assertEqual(len(case.commands),2)
        finally:case.tearDown()


if __name__=='__main__':unittest.main()
