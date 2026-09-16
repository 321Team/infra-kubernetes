"""Real temporary filesystem and journal; external unit/management IO mocked."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_cp1_cni_unit_files as fixtures
from test_cp1_cni_membership import Crash
from cp1_cni_journal import Journal
import cp1_cni_unit_cleanup as c
import cp1_cni_guard as g


@unittest.skipUnless(os.geteuid()==0,'requires root temporary-file ownership checks')
class UnitCleanupTests(unittest.TestCase):
    # Reuse fixture setup only, not its tests (no duplicated test count).
    writer=fixtures.UnitFilesTests.writer
    loaded=fixtures.UnitFilesTests.loaded
    command=fixtures.UnitFilesTests.command

    def setUp(self):
        fixtures.UnitFilesTests.setUp(self)
        self.publication=self.writer().load(self.command,self.loaded)
        for path,kind,value in c.artifacts(self.lease):
            if kind=='link':
                target=self.units/path;target.parent.mkdir(mode=0o755)
                target.symlink_to(value)
        directory=self.base/'cleanup-journal';directory.mkdir(mode=0o700)
        self.cleanup=Journal(directory).__enter__();self.proofs=[];self.reloads=[];self.fail_proof=False

    def tearDown(self):
        self.cleanup.close();fixtures.UnitFilesTests.tearDown(self)

    def proof(self,state):
        self.proofs.append(copy.deepcopy(state))
        if self.fail_proof:raise ValueError('work cgroup or management not verified')

    def remover(self):
        return c.UnitCleanup(self.lease,self.manifest,self.sha,self.publication,self.cleanup,self.proof)

    def reload(self,args,timeout):
        self.assertEqual(args,['systemctl','daemon-reload']);self.assertEqual(timeout,5)
        self.assertEqual(self.cleanup.load()['phase'],'RELOAD_INTENT');self.reloads.append(args)
        return {'rc':0,'stdout':'','stderr':'','truncated':False}

    def absent(self,state):
        self.assertEqual(state['phase'],'RELOAD_INTENT')
        self.assertTrue(all(not os.path.lexists(self.units/p) for p,_,_ in c.artifacts(self.lease)))

    def test_only_owned_six_artifacts_removed_and_sources_preserved(self):
        unrelated=self.units/'foreign.service';unrelated.write_text('foreign')
        result=self.remover().run(self.reload,self.absent)
        self.assertTrue(result['units_removed']);self.assertFalse(result['full_recovery_complete'])
        self.assertEqual(unrelated.read_text(),'foreign')
        self.assertTrue((self.dst/self.run/'STAGED.json').exists());self.assertEqual(len(result['completed']),6)
        self.assertEqual(len(self.reloads),1)

    def test_deletion_intent_is_durable_before_every_unlink(self):
        original=c.remove;seen=[]
        def remove(fd,path):
            self.assertEqual(self.cleanup.load()['pending'],path);seen.append(path);return original(fd,path)
        with patch.object(c,'remove',side_effect=remove):self.remover().run(self.reload,self.absent)
        self.assertEqual(seen,[p for p,_,_ in c.artifacts(self.lease)])

    def test_crash_after_each_unlink_resumes_exact_missing_prefix(self):
        # Independent complete fixture for each crash point; real unlink and
        # fsynced journal, without performing actual service operations.
        for index in range(6):
            with self.subTest(index=index):
                case=UnitCleanupTests(methodName='runTest');case.setUp()
                try:
                    original=c.remove;count=[0]
                    def interrupted(fd,path):
                        original(fd,path);current=count[0];count[0]+=1
                        if current==index:raise Crash()
                    with patch.object(c,'remove',side_effect=interrupted),self.assertRaises(Crash):
                        case.remover().run(case.reload,case.absent)
                    self.assertIsNotNone(case.cleanup.load()['pending'])
                    result=case.remover().run(case.reload,case.absent)
                    self.assertTrue(result['units_removed'])
                finally:case.tearDown()

    def test_foreign_symlink_and_same_content_hardlink_block_before_delete(self):
        path,kind,target=c.artifacts(self.lease)[0];link=self.units/path;link.unlink();link.symlink_to('/etc/foreign.timer')
        with self.assertRaisesRegex(ValueError,'initial'):self.remover().run(self.reload,self.absent)
        self.assertTrue(link.is_symlink());self.assertEqual(self.reloads,[])
        link.unlink();link.symlink_to(target)
        name=next(p for p,k,v in c.artifacts(self.lease) if k=='file');os.link(self.units/name,self.base/'alias')
        with self.assertRaises(ValueError):self.remover().run(self.reload,self.absent)
        self.assertTrue(link.is_symlink())

    def test_live_proof_failure_does_not_remove_or_reload(self):
        self.fail_proof=True
        with self.assertRaisesRegex(ValueError,'not verified'):self.remover().run(self.reload,self.absent)
        self.assertTrue(all(os.path.lexists(self.units/p) for p,_,_ in c.artifacts(self.lease)))
        self.assertEqual(self.reloads,[])

    def test_file_race_after_live_proof_is_not_unlinked(self):
        path,_,_=c.artifacts(self.lease)[0]
        def proof(state):
            if state['pending']==path:
                link=self.units/path;link.unlink();link.symlink_to('/foreign')
        remover=self.remover();remover.proof=proof
        with self.assertRaisesRegex(ValueError,'changed'):remover.run(self.reload,self.absent)
        self.assertEqual(os.readlink(self.units/path),'/foreign')

    def test_fsync_failure_after_unlink_remains_pending_and_can_resume(self):
        original=c.remove;once=[False]
        def failure(fd,path):
            original(fd,path)
            if not once[0]:once[0]=True;raise OSError('directory fsync error')
        with patch.object(c,'remove',side_effect=failure),self.assertRaises(OSError):
            self.remover().run(self.reload,self.absent)
        self.assertIsNotNone(self.cleanup.load()['pending'])
        self.assertTrue(self.remover().run(self.reload,self.absent)['units_removed'])

    def test_unjournaled_missing_file_and_reappeared_removed_file_are_not_adopted(self):
        path,_,_=c.artifacts(self.lease)[0];(self.units/path).unlink()
        with self.assertRaisesRegex(ValueError,'initial'):self.remover().run(self.reload,self.absent)
        (self.units/path).symlink_to(c.artifacts(self.lease)[0][2])
        self.remover().run(self.reload,self.absent)
        (self.units/path).symlink_to(c.artifacts(self.lease)[0][2])
        with self.assertRaisesRegex(ValueError,'reappeared'):self.remover().run(self.reload,self.absent)

    def test_reload_timeout_or_absence_failure_cannot_claim_completion(self):
        def fail(*args,**kwargs):raise TimeoutError('unknown reload')
        with self.assertRaises(TimeoutError):self.remover().run(fail,self.absent)
        self.assertFalse(self.cleanup.load()['units_removed']);self.assertEqual(self.cleanup.load()['phase'],'RELOAD_INTENT')
        def absent(_):raise ValueError('recovery service still loaded')
        with self.assertRaisesRegex(ValueError,'still loaded'):self.remover().run(self.reload,absent)
        self.assertFalse(self.cleanup.load()['units_removed'])
        self.assertTrue(self.remover().run(self.reload,self.absent)['units_removed'])

    def test_altered_publication_or_staged_sources_blocks_cleanup(self):
        self.publication['binding']['run_id']='foreign'
        with self.assertRaises(ValueError):self.remover()
        self.publication['binding']['run_id']=self.run
        source=self.dst/self.run/'scripts/run-cp1-cni-managed-node.py';source.chmod(0o600);source.write_text('changed')
        with self.assertRaises(ValueError):self.remover().run(self.reload,self.absent)
        self.assertEqual(self.reloads,[])

    def test_writable_wants_directory_or_symlink_parent_is_rejected(self):
        path=self.units/'timers.target.wants';path.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'wants directory'):self.remover().run(self.reload,self.absent)

    def test_publication_without_loaded_definition_is_not_cleanup_authority(self):
        self.publication.update(phase='FILES_READY',definitions_loaded=False)
        with self.assertRaisesRegex(ValueError,'not complete'):self.remover()


if __name__=='__main__':unittest.main()
