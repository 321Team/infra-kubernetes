"""Offline boundary tests. No real firewall, network namespace or systemd changes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('kernel_component', SCRIPTS / 'run-cni-kernel-integration.py')
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


def binding():
    observed = dict(product_uuid='vmware-fixture', machine_id_sha256='b'*64, sources_sha256=tool.hashes())
    decision = dict(observed, schema='fds.cni-kernel-lab-decision.v2', lab_vm='cp1-cni-lab',
        lan_segment='FDS-CNI-LAB', execution_approved=True, operator='MOCK_ONLY', snapshot_reference='MOCK_ONLY',
        review_url='https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-123',
        reviewed_bundle_sha256=tool.bundle_digest(observed['sources_sha256']), scope='KERNEL_COMPONENT_ONLY')
    return observed, decision


class Boundaries(unittest.TestCase):
    def test_start_rejects_wrong_vm_before_creating_files(self):
        observed, decision = binding(); decision['product_uuid']='wrong-vm'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'decision.json';path.write_text(json.dumps(decision))
            with patch.object(tool,'prerequisites',return_value=observed),patch.object(tool.node,'safe_dir') as safe, \
                 patch.object(tool,'system') as system:
                with self.assertRaises(ValueError):tool.start(path,'normal')
                safe.assert_not_called();system.assert_not_called()

    def test_no_network_setup_before_recovery_timer_is_active(self):
        lab=tool.Lab.__new__(tool.Lab);lab.job=tool.job_path('a'*16);lab.create_namespace=Mock()
        with patch.object(tool,'validate_units'),patch.object(tool.Path,'exists',return_value=True), \
             patch.object(tool,'system',return_value='inactive'),patch.object(tool,'fingerprint_host') as host:
            with self.assertRaises(ValueError):lab.build()
            lab.create_namespace.assert_not_called();host.assert_not_called()

    def test_foreign_namespace_is_rejected_before_command(self):
        lab=tool.Lab.__new__(tool.Lab);lab.ns={'r':'fdski-owned-r'}
        lab.check_namespace=Mock(side_effect=ValueError('reused inode'));lab.cmd=Mock()
        with self.assertRaises(ValueError):lab.net('r',['/usr/sbin/nft','flush','ruleset'])
        lab.cmd.assert_not_called()

    def test_real_bounded_subprocess_failure_is_reported(self):
        with self.assertRaisesRegex(ValueError,'Command timeout'):
            tool.call([sys.executable,'-c','import time;time.sleep(10)'],timeout=0.05)
        with self.assertRaisesRegex(ValueError,'Command output truncated'):
            tool.call([sys.executable,'-c','print("x"*100)'],output_limit=10)

    def test_default_plan_has_no_process_or_write(self):
        with patch.object(sys,'argv',['program']), patch.object(tool,'call') as cmd, \
             patch.object(tool,'system') as system, patch.object(tool.node,'save_json') as save, \
             patch.object(tool.Path,'mkdir') as mkdir, patch('builtins.print'):
            tool.main()
        for operation in (cmd,system,save,mkdir): operation.assert_not_called()

    def test_unapproved_or_wrong_bound_decision_cannot_start(self):
        observed, decision = binding()
        for field, wrong in dict(product_uuid='another-vm', machine_id_sha256='c'*64, sources_sha256={},
            lab_vm='server04', lan_segment='NAT', execution_approved=False, snapshot_reference='',
            operator='', review_url='https://example.com/approved', scope='RUNTIME_ISOLATED').items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                tool.authorize(dict(decision, **{field:wrong}),observed)

    def test_valid_lab_contract_does_not_create_qualification(self):
        observed, decision = binding(); tool.authorize(decision, observed)
        self.assertFalse(tool.FLAGS['qualification_created'])
        self.assertFalse(tool.FLAGS['production_node_recovery_tested'])

    def test_shared_transaction_and_original_firewall_helper_remain_pinned(self):
        wanted = {'cni_diag_core.py':'286d7075bf9dbbbebf3378e59b493db9ac1f842e73573bd62dbdbb3e975e2d59',
                  'node-firewall.py':'93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa'}
        for name,digest in wanted.items(): self.assertEqual(tool.hashes()[name],digest)

    def test_path_traversal_and_production_job_rejected(self):
        for value in ('../../cp1-canary','',None,'/var/lib/fds/job','A'*16,'a'*17):
            with self.subTest(value=value), self.assertRaises(ValueError):tool.job_path(value)

    def test_server_refuses_host_namespace_before_listening(self):
        with patch.object(sys,'argv',['program','_server','a'*16,'a']), \
             patch.object(tool.os,'geteuid',return_value=0), \
             patch.object(tool,'require_inside',side_effect=ValueError('host namespace')), \
             patch('http.server.HTTPServer') as server:
            with self.assertRaises(ValueError):tool.main()
            server.assert_not_called()

    def test_probe_refuses_host_namespace_before_connecting(self):
        with patch.object(sys,'argv',['program','_probe','a'*16,'a','10.244.246.81','8080','41000']), \
             patch.object(tool,'require_inside',side_effect=ValueError('host namespace')), \
             patch('http.client.HTTPConnection') as connection:
            with self.assertRaises(ValueError):tool.main()
            connection.assert_not_called()

    def test_probe_refuses_destination_outside_fixture(self):
        with patch.object(sys,'argv',['program','_probe','a'*16,'a','10.1.93.102','8080','41000']), \
             patch.object(tool,'require_inside'), patch.object(tool,'read_json',return_value={'peers':[{'ip':'10.244.246.81'}]}), \
             patch('http.client.HTTPConnection') as connection:
            with self.assertRaises(ValueError):tool.main()
            connection.assert_not_called()

    def test_inode_binding_rejects_host_or_reused_namespace(self):
        job=tool.job_path('a'*16)
        for current,host in ((20,20),(30,20)):
            with self.subTest(current=current), patch.object(tool.node,'safe_dir'), \
                 patch.object(tool,'read_json',return_value={'namespaces':{'a':[4,20]}}), \
                 patch.object(tool.os,'stat',side_effect=lambda p:SimpleNamespace(st_dev=4,st_ino=current if '/self/' in str(p) else host)):
                with self.assertRaises(ValueError):tool.require_inside(job,'a')

    def test_matching_private_namespace_is_accepted(self):
        with patch.object(tool.node,'safe_dir'), patch.object(tool,'read_json',return_value={'namespaces':{'a':[4,30]}}), \
             patch.object(tool.os,'stat',side_effect=lambda p:SimpleNamespace(st_dev=4,st_ino=30 if '/self/' in str(p) else 20)):
            tool.require_inside(tool.job_path('a'*16),'a')

    def test_firewall_mutations_use_private_bus_netns_and_mount_mask(self):
        lab=tool.Lab.__new__(tool.Lab);lab.job=tool.job_path('a'*16);lab.net=Mock(return_value='success')
        lab.command(['--zone=fds-cni-trial','--add-interface=calikia'])
        role,argv=lab.net.call_args.args
        self.assertEqual(role,'r');self.assertEqual(argv[:4],['/usr/bin/unshare','--mount','--propagation','private'])
        self.assertIn('_firewall',argv)
        self.assertEqual(lab.net.call_args.kwargs['env']['DBUS_SYSTEM_BUS_ADDRESS'],'unix:path='+str(lab.job/'dbus/bus.sock'))

    def test_private_daemon_uses_owned_namespace_and_config(self):
        job=tool.job_path('a'*16);docs=tool.documents(job,'normal');fw=docs[tool.unit_name(job,'firewall')+'.service']
        self.assertIn('NetworkNamespacePath=/run/netns/'+tool.ns_names(job)['r'],fw)
        self.assertIn('ProtectSystem=strict',fw);self.assertIn('--nopid',fw)
        self.assertIn('--system-config='+str(job/'firewalld'),fw)
        self.assertNotIn('DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket',fw)

    def test_timer_precedes_work_and_has_boot_retry_definitions(self):
        job=tool.job_path('a'*16);docs=tool.documents(job,'normal')
        timer=docs[tool.unit_name(job,'recover')+'.timer']
        self.assertIn('OnActiveSec=1020s',timer);self.assertIn('OnUnitInactiveSec=30s',timer)
        self.assertIn('WantedBy=multi-user.target',docs[tool.unit_name(job,'recover')+'.service'])
        self.assertIn('KillMode=control-group',docs[tool.unit_name(job,'work')+'.service'])

    def test_accelerated_cases_are_separate_from_production_time(self):
        job=tool.job_path('a'*16)
        for scenario in ('kill','deadline','cleanup-retry'):
            self.assertIn('OnActiveSec=90s',tool.documents(job,scenario)[tool.unit_name(job,'recover')+'.timer'])
        self.assertEqual(tool.core.DURATION,1200)

    def test_command_timeout_is_clamped_to_remaining_window(self):
        lab=tool.Lab.__new__(tool.Lab);lab.recovery=False;lab.guard=Mock(return_value=2)
        with tempfile.TemporaryDirectory() as directory, patch.object(tool,'call',return_value={'stdout':'ok'}) as run:
            lab.job=Path(directory);self.assertEqual(lab.cmd(['/usr/bin/true']),'ok')
            self.assertEqual(run.call_args.kwargs['timeout'],1.5)

    def test_budget_cannot_extend_after_clock_rollback_or_boot(self):
        lab=tool.Lab.__new__(tool.Lab);lab.recovery=False;lab.job=tool.job_path('a'*16)
        lab.request={'deadline':10000,'boot_start':0};lab.boot_changed=False
        with patch.object(tool.time,'time',return_value=0),patch.object(tool.time,'clock_gettime',return_value=1199):
            with self.assertRaises(ValueError):lab.guard()
        lab.boot_changed=True
        with self.assertRaises(ValueError):lab.guard(cleanup=True)

    def test_disarm_refuses_incomplete_resource_cleanup(self):
        lab=SimpleNamespace(result={'cleanup_complete':False})
        with patch.object(tool,'system') as system:
            with self.assertRaises(ValueError):tool.disarm(lab,{})
            system.assert_not_called()

    def test_changed_unit_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            unitdir=Path(directory);job=tool.job_path('a'*16);docs=tool.documents(job,'normal')
            for name,text in docs.items():(unitdir/name).write_text(text)
            name=tool.unit_name(job,'bus')+'.service';(unitdir/name).write_text('FOREIGN')
            with patch.object(tool,'UNITS',unitdir),patch.object(tool.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
                 patch.object(tool,'read_json',side_effect=lambda p:docs if p.name=='UNITS.json' else {'scenario':'normal'}):
                with self.assertRaises(ValueError):tool.validate_units(job)
            self.assertTrue((unitdir/name).exists())

    def test_partial_disarm_allows_missing_but_never_changed_units(self):
        with tempfile.TemporaryDirectory() as directory:
            unitdir=Path(directory);job=tool.job_path('a'*16);docs=tool.documents(job,'normal')
            name=tool.unit_name(job,'bus')+'.service';(unitdir/name).write_text(docs[name])
            with patch.object(tool,'UNITS',unitdir),patch.object(tool.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
                 patch.object(tool,'read_json',side_effect=lambda p:docs if p.name=='UNITS.json' else {'scenario':'normal'}):
                self.assertEqual(tool.validate_units(job,allow_missing=True),docs)
                (unitdir/name).write_text('FOREIGN')
                with self.assertRaises(ValueError):tool.validate_units(job,allow_missing=True)

    def test_interrupted_install_retry_only_tolerates_missing_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            unitdir=Path(directory);job=tool.job_path('a'*16);docs=tool.documents(job,'normal')
            name=tool.unit_name(job,'bus')+'.service';(unitdir/name).write_text(docs[name])
            state={'phase':'RECOVERY_REQUIRED','mutation_attempted':False,'disarm_intent':False}
            with patch.object(tool,'UNITS',unitdir),patch.object(tool.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
                 patch.object(tool,'read_json',side_effect=lambda p:docs if p.name=='UNITS.json' else {'scenario':'normal'}):
                self.assertEqual(tool.recovery_unit_check(job,state),docs)
                with self.assertRaises(ValueError):tool.recovery_unit_check(job,dict(state,mutation_attempted=True))
                with self.assertRaises(ValueError):tool.recovery_unit_check(job,{'phase':'STAGED'})
                (unitdir/name).write_text('FOREIGN')
                with self.assertRaises(ValueError):tool.recovery_unit_check(job,state)

    def test_disarm_retry_after_some_files_already_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);job=tool.job_path('a'*16);docs=tool.documents(job,'normal')
            name=tool.unit_name(job,'bus')+'.service';(root/name).write_text(docs[name])
            lab=SimpleNamespace(job=job,result={'cleanup_complete':True,'disarm_intent':True})
            with patch.object(tool,'UNITS',root),patch.object(tool.node,'safe_read',side_effect=lambda p:p.read_bytes()), \
                 patch.object(tool.node,'save_json'),patch.object(tool,'system') as system:
                tool.disarm(lab,docs)
            self.assertFalse((root/name).exists());self.assertTrue(lab.result['recovery_disarmed'])
            self.assertEqual(system.call_args_list[-1].args[0],['daemon-reload'])

    def test_unknown_namespace_create_outcome_cannot_be_deleted(self):
        job=tool.job_path('a'*16);lab=SimpleNamespace(job=job,result={'phase':'STAGED'},j={},boot_changed=False,ns=tool.ns_names(job))
        lab.cmd=Mock()
        with patch.object(tool,'validate_units',return_value={}), \
             patch.object(tool,'read_json',return_value={'namespaces':{},'pending_namespace':'a'}), \
             patch.object(tool,'system',return_value='not-found'),patch.object(tool.Path,'exists',return_value=True):
            with self.assertRaisesRegex(ValueError,'Unknown namespace create outcome'):tool.cleanup(lab)
            lab.cmd.assert_not_called()

    def test_kernel_component_cannot_satisfy_production_controller(self):
        module=tool.load('original_tests',SCRIPTS.parent/'tests/test_cni_diagnostic.py')
        suite=module.QualificationTests();suite.setUp()
        suite.qualification['stage']=tool.FLAGS['stage']
        with self.assertRaises(ValueError):suite.validate()

    def test_failed_component_still_exports_with_pending_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);job=root/('a'*16);job.mkdir()
            result=dict(tool.FLAGS,recovery_disarmed=False,cleanup_complete=False,phase='RECOVERY_REQUIRED')
            (job/'RESULT.json').write_text(json.dumps(result));(job/'COMMANDS.jsonl').write_text('mock log\n')
            def save(p,d,exclusive=False):p.write_text(json.dumps(d))
            def atomic(p,b,exclusive=False):p.write_bytes(b)
            def safe_dir(p,create=False):
                if create:p.mkdir(parents=True,exist_ok=True)
            with patch.object(tool,'ROOT',root),patch.object(tool.node,'safe_dir',side_effect=safe_dir), \
                 patch.object(tool.node,'safe_read',side_effect=lambda p:p.read_bytes()),patch.object(tool.node,'save_json',side_effect=save), \
                 patch.object(tool.node,'atomic',side_effect=atomic),patch('builtins.print'):
                tool.collect(job)
            archive=next((root/'exports').glob('*/*.tar.gz'))
            with tarfile.open(archive) as tar:
                files={m.name.split('/')[-1]:tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
            self.assertTrue(json.loads(files['EXPORT_STATE.json'])['recovery_pending'])
            matrix=json.loads(files['PRODUCTION_MATRIX.json'])
            self.assertNotEqual(matrix['schema'],'fds.cp1-cni-qualification.v1')
            self.assertTrue(all(v=='NOT_TESTED' for v in matrix['cases'].values()))
            for row in files['SHA256SUMS'].decode().splitlines():
                expected,name=row.split('  ',1);self.assertEqual(hashlib.sha256(files[name]).hexdigest(),expected)
            self.assertEqual(archive.with_name(archive.name+'.sha256').read_text().split()[0],hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == '__main__':unittest.main()
