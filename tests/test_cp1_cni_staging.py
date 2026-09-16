"""Real filesystem adversarial tests; no unit/service/firewall operations."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cp1_cni_staging as s

RUN='20260917T000000Z-abcdef012345'


@unittest.skipUnless(os.geteuid()==0,'root ownership tests require a root test process')
class StagingTests(unittest.TestCase):
    def setUp(self):
        # A private root-owned parent is required; /tmp and the shared
        # workspace are intentionally rejected by the production validator.
        self.tmp=tempfile.TemporaryDirectory(prefix='cni-stage-test-',dir=Path.home())
        self.base=Path(self.tmp.name);self.src=self.base/'source';self.dst=self.base/'staged'
        self.src.mkdir(mode=0o700);(self.src/'scripts').mkdir(mode=0o700);self.dst.mkdir(mode=0o700)
        self.data=b'# test-only harmless source\n'
        (self.src/'scripts/probe.py').write_bytes(self.data)
        self.manifest={'scripts/probe.py':hashlib.sha256(self.data).hexdigest()}
        self.sha=s.fingerprint(self.manifest)

    def tearDown(self):self.tmp.cleanup()

    def stage(self):return s.stage_sources(self.src,self.dst,RUN,self.manifest,self.sha)
    def verify(self):return s.verify_sources(self.dst,RUN,self.manifest,self.sha)

    def test_exact_copy_private_immutable_files_and_no_execution_authority(self):
        result=self.stage();self.assertEqual(self.verify(),result)
        self.assertEqual((self.dst/RUN/'scripts/probe.py').read_bytes(),self.data)
        self.assertEqual((self.dst/RUN/'scripts/probe.py').stat().st_mode&0o777,0o400)
        self.assertFalse(result['authorizes_apply']);self.assertFalse(result['services_installed'])

    def test_source_tamper_wrong_external_pin_and_extra_manifest_path_block_before_publish(self):
        (self.src/'scripts/probe.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.stage()
        self.assertFalse((self.dst/RUN).exists())
        with self.assertRaises(ValueError):s.stage_sources(self.src,self.dst,RUN,self.manifest,'f'*64)
        bad={'scripts/../bad.py':'a'*64}
        with self.assertRaises(ValueError):s.stage_sources(self.src,self.dst,RUN,bad,s.fingerprint(bad))

    def test_existing_run_is_never_overwritten_or_resumed(self):
        self.stage()
        with self.assertRaises(FileExistsError):self.stage()
        self.verify()

    def test_source_leaf_symlink_and_hardlink_block(self):
        p=self.src/'scripts/probe.py';p.unlink();p.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):self.stage()
        p.unlink();origin=self.src/'original';origin.write_bytes(self.data);os.link(origin,p)
        with self.assertRaisesRegex(ValueError,'unsafe source'):self.stage()

    def test_source_parent_symlink_block(self):
        alias=self.base/'alias';alias.symlink_to(self.src,target_is_directory=True)
        with self.assertRaises(OSError):s.stage_sources(alias,self.dst,RUN,self.manifest,self.sha)

    def test_destination_symlink_and_writable_ancestor_block(self):
        alias=self.base/'alias';alias.symlink_to(self.dst,target_is_directory=True)
        with self.assertRaises(OSError):s.stage_sources(self.src,alias,RUN,self.manifest,self.sha)
        self.base.chmod(0o777)
        try:
            with self.assertRaisesRegex(ValueError,'ancestor'):self.stage()
        finally:self.base.chmod(0o700)

    def test_nonroot_rejected_before_copy(self):
        with patch.object(s.os,'geteuid',return_value=1000),self.assertRaisesRegex(ValueError,'root source staging'):
            self.stage()
        self.assertFalse((self.dst/RUN).exists())

    def test_interrupted_copy_never_publishes_commit_marker(self):
        with patch.object(s,'exclusive_write',side_effect=OSError('disk failure')),self.assertRaises(OSError):self.stage()
        self.assertFalse((self.dst/RUN/'STAGED.json').exists())
        with self.assertRaises(FileNotFoundError):self.verify()
        with self.assertRaises(FileExistsError):self.stage()

    def test_staged_file_tamper_mode_and_extra_import_payload_block(self):
        self.stage();p=self.dst/RUN/'scripts/probe.py'
        p.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'permissions'):self.verify()
        p.write_bytes(b'tampered');p.chmod(0o400)
        with self.assertRaisesRegex(ValueError,'bytes differ'):self.verify()
        p.chmod(0o600);p.write_bytes(self.data);p.chmod(0o400)
        (p.parent/'extra.py').write_bytes(b'pass')
        with self.assertRaisesRegex(ValueError,'extra or missing'):self.verify()

    def test_marker_with_duplicated_or_extra_json_fields_is_rejected(self):
        self.stage();p=self.dst/RUN/'STAGED.json';p.chmod(0o600)
        p.write_text('{"authorizes_apply": true,"authorizes_apply": false}');p.chmod(0o400)
        with self.assertRaisesRegex(ValueError,'marker differs'):self.verify()


if __name__=='__main__':unittest.main()
