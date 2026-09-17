"""Real private staging/links/journals; systemd and firewall are mocked."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_cp1_cni_unit_files as f
import test_cp1_cni_transition as tf
from test_cp1_cni_guard import unit_fixture
from test_cp1_cni_membership import Crash
from cp1_cni_journal import Journal
import cp1_cni_service_control as s
import cp1_cni_unit_cleanup as cleanup
import cp1_cni_guard as g
import cp1_cni_transition as t

OK={'rc':0,'stdout':'','stderr':'','truncated':False}


@unittest.skipUnless(os.geteuid()==0,'requires root private-filesystem ownership checks')
class ServiceControlTests(unittest.TestCase):
    writer=f.UnitFilesTests.writer
    loaded=f.UnitFilesTests.loaded
    command=f.UnitFilesTests.command

    def setUp(self):
        f.UnitFilesTests.setUp(self)
        self.lease.update(wall_start=1000,wall_end=8200,boot_start=110,boot_end=7310)
        self.lease['policy_sha256']=s.fingerprint(t.documents(self.run))
        self.publication=self.writer().load(self.command,self.loaded)
        self.observed=self.loaded()
        for directory in ('multi-user.target.wants','timers.target.wants'):(self.units/directory).mkdir(mode=0o755)
        self.service_dir=self.base/'launch';self.service_dir.mkdir(mode=0o700)
        self.launch=Journal(self.service_dir).__enter__()
        self.now={'boot_id':self.lease['boot_id'],'wall':1000,'boottime':110,'monotonic':110}
        self.commands=[];self.prechecks=0;self.auto_start=False

    def tearDown(self):
        self.launch.close();f.UnitFilesTests.tearDown(self)

    def before_launch(self):self.prechecks+=1

    def control(self):
        return s.ServiceControl(self.lease,self.manifest,self.sha,self.publication,self.launch,
             lambda:copy.deepcopy(self.observed),lambda:copy.deepcopy(self.now),self.before_launch)

    def run_command(self,argv,timeout):
        self.assertEqual(timeout,5);self.commands.append(argv)
        unit=g.names(self.run)
        if argv==['systemctl','daemon-reload']:
            self.assertEqual(self.launch.load()['phase'],'RELOAD_INTENT')
            for kind in ('timer','recover'):self.observed['properties'][unit[kind]]['UnitFileState']='enabled'
        else:
            self.assertEqual(argv,['systemctl','--no-block','start',unit['timer'],unit['work']])
            self.assertEqual(self.launch.load()['phase'],'LAUNCH_INTENT')
            if self.auto_start:self.observed=unit_fixture(self.lease)
        return copy.deepcopy(OK)

    def test_arm_creates_exact_links_in_order_without_starting(self):
        result=self.control().arm(self.run_command)
        self.assertEqual(result['phase'],'ENABLED');self.assertFalse(result['start_requested'])
        self.assertEqual(result['completed'],[p for p,_ in s.links(self.lease)])
        for path,target in s.links(self.lease):self.assertEqual(os.readlink(self.units/path),target)
        self.assertEqual(self.commands,[['systemctl','daemon-reload']])

    def test_each_link_creation_requires_durable_intent(self):
        original=s.create_link;seen=[]
        def create(fd,path,target):
            self.assertEqual(self.launch.load()['pending'],path);seen.append(path);original(fd,path,target)
        with patch.object(s,'create_link',side_effect=create):self.control().arm(self.run_command)
        self.assertEqual(seen,[p for p,_ in s.links(self.lease)])

    def test_crash_before_and_after_each_link_resumes_only_its_known_prefix(self):
        for index in range(2):
            for after in (False,True):
                case=ServiceControlTests(methodName='runTest');case.setUp()
                try:
                    original=s.create_link;count=[0]
                    def create(*args):
                        current=count[0];count[0]+=1
                        if current==index and not after:raise Crash()
                        original(*args)
                        if current==index and after:raise Crash()
                    with patch.object(s,'create_link',side_effect=create),self.assertRaises(Crash):case.control().arm(case.run_command)
                    self.assertIsNotNone(case.launch.load()['pending'])
                    self.assertEqual(case.control().arm(case.run_command)['phase'],'ENABLED')
                finally:case.tearDown()

    def test_existing_identical_or_foreign_link_is_not_adopted(self):
        for value in ('same','foreign'):
            path,target=s.links(self.lease)[0];link=self.units/path
            link.symlink_to(target if value=='same' else '/foreign')
            with self.subTest(value=value),self.assertRaises(ValueError):self.control().arm(self.run_command)
            self.assertFalse((self.service_dir/'STATE.json').exists());link.unlink()

    def test_writable_or_symlink_wants_directory_is_rejected(self):
        directory=self.units/'multi-user.target.wants';directory.chmod(0o777)
        with self.assertRaises(ValueError):self.control().arm(self.run_command)
        directory.chmod(0o755);directory.rmdir();directory.symlink_to(self.base,target_is_directory=True)
        with self.assertRaises(OSError):self.control().arm(self.run_command)
        self.assertEqual(self.commands,[])

    def test_changed_sources_loaded_command_or_pending_job_block_arm(self):
        props=self.observed['properties'][g.names(self.run)['work']]
        original=copy.deepcopy(props)
        for field,value in (('ExecStart','/bin/true'),('Job','27'),('ActiveState','active')):
            props.update({field:value})
            with self.subTest(field=field),self.assertRaises(ValueError):self.control().arm(self.run_command)
            props.clear();props.update(original)
        source=self.dst/self.run/'scripts/run-cp1-cni-managed-node.py';source.chmod(0o600)
        with self.assertRaises(ValueError):self.control().arm(self.run_command)
        self.assertEqual(self.commands,[])

    def test_link_or_reload_journal_failure_never_dispatches_work(self):
        original=self.launch.save
        def fail(state):
            if state['pending']:raise OSError('fsync failed')
            original(state)
        with patch.object(self.launch,'save',side_effect=fail),self.assertRaises(OSError):self.control().arm(self.run_command)
        self.assertTrue(all(not os.path.lexists(self.units/p) for p,_ in s.links(self.lease)))
        self.assertEqual(self.commands,[])

    def test_reload_failure_keeps_intent_and_can_reconcile_without_start(self):
        def uncertain(*args,**kwargs):return dict(OK,rc=1)
        with self.assertRaisesRegex(ValueError,'reload uncertain'):self.control().arm(uncertain)
        self.assertEqual(self.launch.load()['phase'],'RELOAD_INTENT')
        self.assertEqual(self.control().arm(self.run_command)['phase'],'ENABLED')

    def test_start_acceptance_is_distinct_from_live_work_confirmation(self):
        c=self.control();c.arm(self.run_command)
        result=c.start(self.run_command)
        self.assertEqual(result['phase'],'START_REQUESTED');self.assertFalse(result['work_observed'])
        with self.assertRaises(ValueError):c.confirm()
        self.observed=unit_fixture(self.lease)
        result=c.confirm();self.assertTrue(result['work_observed']);self.assertFalse(result['full_recovery_complete'])
        self.assertEqual(self.prechecks,1)

    def test_any_second_start_or_arm_after_intent_is_denied(self):
        c=self.control();c.arm(self.run_command);c.start(self.run_command);before=len(self.commands)
        for method in (c.arm,c.start):
            with self.assertRaises(ValueError):method(self.run_command)
        self.assertEqual(len(self.commands),before)

    def test_crash_before_or_after_dispatch_never_repeats_start(self):
        for after in (False,True):
            case=ServiceControlTests(methodName='runTest');case.setUp()
            try:
                c=case.control();c.arm(case.run_command)
                def crash(argv,timeout):
                    if after:case.run_command(argv,timeout)
                    raise Crash()
                with self.assertRaises(Crash):c.start(crash)
                self.assertEqual(case.launch.load()['phase'],'LAUNCH_INTENT')
                before=len(case.commands)
                with self.assertRaises(ValueError):c.start(case.run_command)
                self.assertEqual(len(case.commands),before)
            finally:case.tearDown()

    def test_timeout_warning_boolean_rc_and_failed_dispatch_are_recovery_only(self):
        for result in (dict(OK,rc=1),dict(OK,rc=False),dict(OK,stdout='queued?'),dict(OK,stderr='warning'),dict(OK,truncated=True)):
            case=ServiceControlTests(methodName='runTest');case.setUp()
            try:
                c=case.control();c.arm(case.run_command)
                with self.assertRaises(ValueError):c.start(lambda *args,**kw:result)
                self.assertEqual(case.launch.load()['phase'],'RECOVERY_REQUIRED')
                with self.assertRaises(ValueError):c.start(case.run_command)
            finally:case.tearDown()

    def test_before_launch_failure_or_expiry_after_fsync_cannot_start(self):
        c=self.control();c.arm(self.run_command)
        c.before_launch=lambda:(_ for _ in ()).throw(ValueError('baseline changed'))
        with self.assertRaisesRegex(ValueError,'baseline changed'):c.start(self.run_command)
        self.assertEqual(self.launch.load()['phase'],'ENABLED')
        c.before_launch=self.before_launch;original=self.launch.save
        def save(state):
            original(state)
            if state['phase']=='LAUNCH_INTENT':self.now['wall']+=30;self.now['boottime']+=30
        with patch.object(self.launch,'save',side_effect=save),self.assertRaises(ValueError):c.start(self.run_command)
        self.assertEqual(self.commands,[['systemctl','daemon-reload']])

    def test_confirmation_deadline_does_not_renew_or_hide_changed_guard(self):
        c=self.control();c.arm(self.run_command);self.auto_start=True;c.start(self.run_command)
        self.now['wall']+=5;self.now['boottime']+=5
        with self.assertRaisesRegex(ValueError,'expired'):c.confirm()
        self.assertFalse(self.launch.load()['work_observed'])

    def test_confirmation_rejects_launch_record_from_another_boot(self):
        c=self.control();c.arm(self.run_command);self.auto_start=True;c.start(self.run_command)
        state=self.launch.load();state['launch_clock']['boot_id']='foreign';self.launch.save(state)
        with self.assertRaisesRegex(ValueError,'boot changed'):c.confirm()
        self.assertFalse(self.launch.load()['work_observed'])

    def test_partial_link_prefix_cleanup_never_deletes_uncreated_foreign_link(self):
        original=s.create_link;count=[0]
        def crash(fd,path,target):
            original(fd,path,target);count[0]+=1
            if count[0]==1:raise Crash()
        with patch.object(s,'create_link',side_effect=crash),self.assertRaises(Crash):self.control().arm(self.run_command)
        launch=self.launch.load();directory=self.base/'partial-cleanup';directory.mkdir(mode=0o700)
        with Journal(directory) as journal:
            remover=cleanup.UnitCleanup(self.lease,self.manifest,self.sha,self.publication,journal,lambda _:None,launch_state=launch)
            path,target=s.links(self.lease)[1];(self.units/path).symlink_to(target)
            with self.assertRaisesRegex(ValueError,'initial'):remover.run(lambda *a,**kw:OK,lambda _:None)
            (self.units/path).unlink()
            result=remover.run(lambda *a,**kw:OK,lambda _:None)
            self.assertTrue(result['units_removed']);self.assertFalse(result['full_recovery_complete'])
            self.assertTrue(all(not os.path.lexists(self.units/p) for p,_,_ in cleanup.artifacts(self.lease)))

    def test_partial_cleanup_rejects_new_link_after_initial_absence_proof(self):
        state=s.initial(self.control().expected)
        directory=self.base/'partial-cleanup';directory.mkdir(mode=0o700)
        path,target=s.links(self.lease)[1]
        def proof(record):
            if record['pending']==path and not os.path.lexists(self.units/path):(self.units/path).symlink_to(target)
        with Journal(directory) as journal:
            remover=cleanup.UnitCleanup(self.lease,self.manifest,self.sha,self.publication,journal,proof,launch_state=state)
            with self.assertRaisesRegex(ValueError,'changed'):remover.run(lambda *a,**kw:OK,lambda _:None)
            self.assertEqual(os.readlink(self.units/path),target)


class LaunchGuardTests(unittest.TestCase):
    def fixture(self):
        tx,io,j=tf.setup();l=io.lease;l.update(wall_start=1000,wall_end=8200,boot_start=110,boot_end=7310)
        state=t.initial(l,io.live);units=unit_fixture(l)
        expected={'fixture':'binding'};launch=s.initial(expected)
        launch.update(phase='START_REQUESTED',completed=[p for p,_ in s.links(l)],
            launch_clock={'boot_id':l['boot_id'],'wall':1000,'boottime':110},start_requested=True,command_result=OK)
        clock={**io.clock(),'monotonic':110};return l,state,units,expected,launch,clock

    def run_check(self,edit=None):
        l,state,units,expected,launch,clock=self.fixture()
        if edit:edit(l,state,units,launch,clock)
        calls=[]
        result=s.supervise_launch(launch,state,l,expected,clock,units,
               lambda args,timeout:calls.append(args) or OK,lambda _:calls.append('persist'))
        return result,calls

    def test_live_work_delegates_to_existing_preparation_guard(self):
        result,calls=self.run_check();self.assertEqual(result['action'],'WATCH');self.assertEqual(calls,[])

    def test_queued_start_has_five_seconds_only_with_pristine_transition(self):
        def waiting(l,state,units,launch,clock):
            units['properties'][g.names(l['run_id'])['work']].update(ActiveState='activating',MainPID='0',Job='27')
        result,calls=self.run_check(waiting);self.assertEqual(result['action'],'WAIT_START');self.assertEqual(calls,[])
        def expired(*args):waiting(*args);args[-1]['wall']+=5;args[-1]['boottime']+=5
        result,calls=self.run_check(expired);self.assertEqual(result['action'],'RECOVER');self.assertEqual(calls[0],'persist')

    def test_unconfirmed_dispatch_never_allows_progress_or_refreshes_start_clock(self):
        def intent(l,state,units,launch,clock):
            launch.update(phase='LAUNCH_INTENT',start_requested=False,command_result=None)
        self.assertEqual(self.run_check(intent)[0]['action'],'WAIT_START')
        def progressed(*args):intent(*args);args[1]['pending']={'unverified':'progress'}
        self.assertEqual(self.run_check(progressed)[0]['action'],'RECOVER')

    def test_missing_guard_boot_change_failed_work_or_sticky_recovery_dispatch(self):
        for case in ('guard','boot','failed','sticky','withdrawn'):
            def edit(l,state,units,launch,clock):
                if case=='guard':units['links']={}
                elif case=='boot':clock['boot_id']='a'*8+clock['boot_id'][8:]
                elif case=='sticky':launch.update(phase='RECOVERY_REQUIRED',start_requested=False)
                elif case=='withdrawn':state['phase']='WITHDRAWING'
                else:units['properties'][g.names(l['run_id'])['work']].update(ActiveState='failed',SubState='failed',MainPID='0')
            with self.subTest(case=case):self.assertEqual(self.run_check(edit)[0]['action'],'RECOVER')


if __name__=='__main__':unittest.main()
