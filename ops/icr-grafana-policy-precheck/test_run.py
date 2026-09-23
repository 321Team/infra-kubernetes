"""Exercise controller packaging with a fake ansible-playbook, no remote access."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).parent
FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
payload=json.loads(pathlib.Path(args[args.index('-e')+1][1:]).read_text())
assert payload['ansible_become'] is False
out=pathlib.Path(payload['icr_output_dir'])
mode=os.environ.get('MOCK_MODE','success')
if '--syntax-check' in args:
    print('SYNTHETIC syntax only')
    sys.exit(7 if mode=='syntaxfail' else 0)
if mode!='nohosts':
    (out/'icr-grafana-policy.json').write_text(json.dumps({'rc':0,'synthetic':True}))
print('SYNTHETIC collection only')
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'checkout'; self.repo.mkdir()
        (self.repo / 'inventory').mkdir()
        (self.repo / 'inventory/hosts.yml').write_text('all: {}\n')
        (self.repo / 'ansible.cfg').write_text('[defaults]\n')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Synthetic',
                        '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'synthetic'], check=True)
        self.kit = self.root / 'kit'; self.kit.mkdir()
        for name in ('collect.py', 'collect.yml', 'run.sh'):
            shutil.copyfile(HERE / name, self.kit / name)
        (self.kit / 'SHA256SUMS').write_text(''.join(
            hashlib.sha256((self.kit/n).read_bytes()).hexdigest() + '  ' + n + '\n'
            for n in ('collect.py', 'collect.yml', 'run.sh')))
        bindir = self.root / 'bin'; bindir.mkdir()
        fake = bindir / 'ansible-playbook'; fake.write_text(FAKE); fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'])

    def run_kit(self, mode='success'):
        return subprocess.run(['bash', str(self.kit / 'run.sh'),
                               str(self.repo / 'inventory/hosts.yml'), 'a' * 40],
                              env=dict(self.env, MOCK_MODE=mode), text=True,
                              capture_output=True, timeout=20)

    def check_archive(self, run, expected_rc):
        self.assertEqual(run.returncode, expected_rc, run.stdout + run.stderr)
        archive = Path(next(x.split('=', 1)[1] for x in run.stdout.splitlines() if x.startswith('ARCHIVE=')))
        self.assertEqual(archive.with_suffix(archive.suffix + '.sha256').read_text().split()[0],
                         hashlib.sha256(archive.read_bytes()).hexdigest())
        with tarfile.open(archive) as tar:
            files = {Path(m.name).name: tar.extractfile(m).read() for m in tar if m.isfile()}
        self.assertEqual(files['controller-exit-code.txt'].decode().strip(), str(expected_rc))
        for line in files['SHA256SUMS'].decode().splitlines():
            digest, name = line.split(None, 1)
            self.assertEqual(hashlib.sha256(files[Path(name).name]).hexdigest(), digest)
        return files

    def test_success_package_integrity_and_existing_checkout_unchanged(self):
        files = self.check_archive(self.run_kit(), 0)
        self.assertIn('icr-grafana-policy.json', files)
        status = subprocess.run(['git', '-C', str(self.repo), 'diff', '--exit-code'], capture_output=True)
        self.assertEqual(status.returncode, 0)

    def test_syntax_failure_preserves_rc_and_archive(self):
        files = self.check_archive(self.run_kit('syntaxfail'), 7)
        self.assertNotIn('icr-grafana-policy.json', files)

    def test_zero_host_collection_does_not_report_success(self):
        files = self.check_archive(self.run_kit('nohosts'), 2)
        self.assertNotIn('icr-grafana-policy.json', files)

    def test_modified_download_stops_before_collection(self):
        (self.kit / 'collect.py').write_text('changed\n')
        run = self.run_kit()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('phase=source manifest', run.stderr)
        self.assertEqual(list(self.repo.glob('ICR_Grafana_Policy_*')), [])


if __name__ == '__main__':
    unittest.main()
