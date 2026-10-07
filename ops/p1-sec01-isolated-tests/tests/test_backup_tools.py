"""Real provenance functions against synthetic binaries; no etcd/server access."""
import hashlib
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_lifecycle import BASH, ROOT


class BackupTools(unittest.TestCase):
    def exercise(self, scenario='normal'):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tools = tmp/'tools'; tools.mkdir()
            out = tmp/'out'; out.mkdir()
            pins = []
            for b in ('etcd', 'etcdutl', 'etcdctl'):
                prefix = 'etcd Version:' if b == 'etcd' else b+' version:'
                version = '3.6.9' if scenario == 'other_patch' and b == 'etcd' else '3.6.8'
                if scenario == 'mixed' and b == 'etcdutl': version = '3.6.0'
                script = f'#!/usr/bin/env bash\necho "{prefix} {version}"\n'
                if scenario == 'loader' and b == 'etcd':
                    script = '#!/usr/bin/env bash\necho loader-failure >&2\nexit 127\n'
                if scenario == 'duplicate_version' and b == 'etcd':
                    script += f'echo "{prefix} {version}"\n'
                if scenario == 'drift' and b == 'etcd':
                    script += 'echo "# changed" >> "$0"\n'
                p = tools/b; p.write_text(script, encoding='utf-8', newline='\n'); p.chmod(0o755)
                pins.append(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {b}\n')
            (tmp/'backup-binaries.sha256').write_text(''.join(pins), encoding='utf-8', newline='\n')
            script = '''set -uo pipefail
source "$TOOLS_LIB"
OUT=out; LIB_DIR=.; FAIL=0; EXPECT_BINARY_VERSION=3.6.8; EXPECT_STORAGE_VERSION=3.6.0
w() { printf '%s\\n' "$2" >> "$1" || { FAIL=1; return 12; }; }
timeout() {
  shift
  if [ "$SCENARIO" = timeout ] && [ "$1" = tools/etcd ]; then echo partial; echo expired >&2; return 124; fi
  if [ "$SCENARIO" = hash_failure ] && [ "$1" = sha256sum ] && [ "$2" = tools/etcdutl ]; then echo cannot-read >&2; return 1; fi
  "$@"
}
exe=tools/etcd
[ "$SCENARIO" != exe_mismatch ] || exe=tools/etcdctl
capture_backup_tools tools "$exe"
'''
            f = tmp/'run.sh'; f.write_text(script, encoding='utf-8', newline='\n')
            env = dict(os.environ, TOOLS_LIB=(ROOT/'backup-tools.sh').as_posix(), SCENARIO=scenario)
            p = subprocess.run([BASH, str(f)], cwd=tmp, env=env, capture_output=True,
                               text=True, encoding='utf-8', timeout=25)
            files = {x.name: x.read_text(encoding='utf-8') for x in out.iterdir()}
            return p, files

    def test_exact_program_pins_accept_different_storage_version(self):
        p, files = self.exercise()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('snapshot_storage_version=3.6.0 rejected=0', files['12-validation.txt'])
        self.assertEqual(files['12-binaries-version.txt'], 'etcd=3.6.8\netcdutl=3.6.8\netcdctl=3.6.8\n')

    def test_first_mismatch_keeps_all_three_raw_versions_and_hashes(self):
        p, files = self.exercise('other_patch')
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn('3.6.9', files['12-etcd-version.stdout'])
        self.assertEqual(len(files['12-binaries-sha256.txt'].splitlines()), 3)
        self.assertEqual(files['12-etcdctl-version.rc'], '0\n')

    def test_nonzero_and_timeout_keep_stderr_rc_and_later_tools(self):
        for scenario, rc, text in [('loader', 127, 'loader-failure'), ('timeout', 124, 'expired')]:
            with self.subTest(scenario=scenario):
                p, files = self.exercise(scenario)
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertEqual(files['12-etcd-version.rc'], str(rc)+'\n')
                self.assertIn(text, files['12-etcd-version.stderr'])
                self.assertEqual(files['12-etcdctl-version.rc'], '0\n')

    def test_mixed_ambiguous_hash_failure_drift_and_exe_mismatch_rejected(self):
        for scenario in ('mixed', 'duplicate_version', 'hash_failure', 'drift', 'exe_mismatch'):
            with self.subTest(scenario=scenario):
                p, files = self.exercise(scenario)
                self.assertEqual(p.returncode, 2, (scenario, p.stdout, p.stderr))
                self.assertIn('12-etcdctl-version.rc', files)


if __name__ == '__main__': unittest.main()
