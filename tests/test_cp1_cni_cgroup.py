"""Private filesystem error/race tests; kernel cgroup contents are modeled."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_cp1_cni_membership import lease
from test_cp1_cni_guard import unit_fixture
import cp1_cni_cgroup as c
import cp1_cni_guard as g


@unittest.skipUnless(os.geteuid()==0,'requires root temporary-file ownership checks')
class CgroupReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cni-cgroup-test-',dir=Path.home())
        self.root=Path(self.tmp.name);self.l=lease();self.name=g.names(self.l['run_id'])['work']
        (self.root/'system.slice').mkdir(mode=0o755)
        self.directory=self.root/'system.slice'/self.name;self.directory.mkdir(mode=0o755)
        self.events=self.directory/'cgroup.events';self.events.write_text('populated 0\nfrozen 0\n')
        self.patch=patch.object(c,'ROOT',self.root);self.patch.start()

    def tearDown(self):self.patch.stop();self.tmp.cleanup()

    def read(self):
        with patch.object(c,'filesystem_type',return_value=c.CGROUP2_MAGIC):return c.read_events(self.l,'work')

    def test_full_kernel_events_are_read_without_writing(self):
        before=self.events.read_bytes();r=self.read()
        self.assertEqual(r['events'],before.decode());self.assertEqual(self.events.read_bytes(),before)

    def test_only_missing_exact_service_directory_is_absence(self):
        self.events.unlink();self.directory.rmdir();self.assertIsNone(self.read()['events'])
        (self.root/'system.slice').rmdir()
        with self.assertRaises(FileNotFoundError):self.read()

    def test_missing_events_permission_error_and_unknown_mount_are_not_absence(self):
        self.events.unlink()
        with self.assertRaises(FileNotFoundError):self.read()
        with self.assertRaisesRegex(ValueError,'not cgroup2'):c.read_events(self.l,'work')
        original=os.open
        def denied(path,*args,**kwargs):
            if path==self.name:raise PermissionError('denied')
            return original(path,*args,**kwargs)
        with patch.object(c.os,'open',side_effect=denied),self.assertRaises(PermissionError):self.read()

    def test_symlink_events_or_service_directory_cannot_escape_compiled_path(self):
        self.events.unlink();other=self.root/'fake';other.write_text('populated 0\n');self.events.symlink_to(other)
        with self.assertRaises(OSError):self.read()
        self.events.unlink();self.directory.rmdir();self.directory.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):self.read()

    def test_delegated_directory_and_non_regular_or_oversized_events_rejected(self):
        self.directory.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'untrusted'):self.read()
        self.directory.chmod(0o755);self.events.write_bytes(b'x'*4097)
        with self.assertRaisesRegex(ValueError,'size bound'):self.read()
        self.events.unlink();os.mkfifo(self.events)
        with self.assertRaisesRegex(ValueError,'kernel file'):self.read()

    def test_replaced_directory_after_read_is_not_empty_proof(self):
        original=os.read
        def race(fd,size):
            data=original(fd,size)
            self.directory.rename(self.root/'old')
            self.directory.mkdir();(self.directory/'cgroup.events').write_text('populated 1\n')
            return data
        with patch.object(c.os,'read',side_effect=race),self.assertRaisesRegex(ValueError,'replaced'):self.read()

    def test_only_service_names_from_the_current_lease_are_read(self):
        for kind in ('timer','../work','foreign'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):c.read_events(self.l,kind)

    def test_real_fstatfs_distinguishes_regular_directory_from_kernel_cgroup2(self):
        # Read-only on the executor kernel, not an operational CP1 test.
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
        try:self.assertNotEqual(c.filesystem_type(fd),c.CGROUP2_MAGIC)
        finally:os.close(fd)
        fd=os.open('/sys/fs/cgroup',os.O_RDONLY|os.O_DIRECTORY)
        try:self.assertEqual(c.filesystem_type(fd),c.CGROUP2_MAGIC)
        finally:os.close(fd)


class QuiescenceTests(unittest.TestCase):
    def fixture(self):
        l=lease();u=unit_fixture(l);name=g.names(l['run_id'])['work']
        u['properties'][name].update(ActiveState='inactive',SubState='dead',MainPID='0',Job='',ControlGroup='')
        raw={'path':'/sys/fs/cgroup/system.slice/'+name,'filesystem':'cgroup2','events':'populated 0\nfrozen 0\n'}
        return l,u,raw

    def test_fresh_before_after_properties_bracket_recursive_empty_read(self):
        l,u,r=self.fixture();calls=[]
        def observe(deadline):calls.append(deadline);return copy.deepcopy(u)
        with patch.object(c,'read_events',return_value=r):result=c.quiesced(l,'work',observe,deadline=109,monotonic=lambda:100)
        self.assertEqual(result,(u,r));self.assertEqual(calls,[109,109])

    def test_child_population_unknown_fields_and_failed_read_cannot_mean_stopped(self):
        for events in ('populated 1\nfrozen 0\n','frozen 0\n','populated 0\npopulated 1\n','unknown 0\n'):
            l,u,r=self.fixture();r['events']=events
            with self.subTest(events=events),patch.object(c,'read_events',return_value=r),self.assertRaises(ValueError):
                c.quiesced(l,'work',lambda **_:u,deadline=109,monotonic=lambda:100)

    def test_stop_request_and_pid_zero_do_not_override_pending_job_or_active_state(self):
        for field,value in (('Job','7'),('ActiveState','deactivating'),('MainPID','5'),('ControlGroup','/foreign')):
            l,u,r=self.fixture();u['properties'][g.names(l['run_id'])['work']][field]=value
            with self.subTest(field=field),patch.object(c,'read_events',return_value=r),self.assertRaises(ValueError):
                c.quiesced(l,'work',lambda **_:u,deadline=109,monotonic=lambda:100)

    def test_unit_restart_between_reads_and_deadline_overrun_are_rejected(self):
        l,u,r=self.fixture();after=copy.deepcopy(u)
        after['properties'][g.names(l['run_id'])['work']].update(ActiveState='active',MainPID='13')
        rows=iter((u,after))
        with patch.object(c,'read_events',return_value=r),self.assertRaises(ValueError):
            c.quiesced(l,'work',lambda **_:next(rows),deadline=109,monotonic=lambda:100)
        times=iter((100,108,109))
        with patch.object(c,'read_events',return_value=r),self.assertRaisesRegex(ValueError,'deadline exhausted'):
            c.quiesced(l,'work',lambda **_:u,deadline=109,monotonic=lambda:next(times))


if __name__=='__main__':unittest.main()
