import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_lifecycle import BASH, ROOT
from test_checks import c


class ProbeIntegration(unittest.TestCase):
    def exercise(self, scenario, mode='run'):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            bindir = temp/'bin'; bindir.mkdir()
            python = bindir/'python3'
            python.write_text('#!/usr/bin/env bash\nexec "'+sys.executable.replace('\\', '/')+'" "$@"\n', encoding='utf-8', newline='\n')
            python.chmod(0o755)
            env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'],
                       KC=sys.executable.replace('\\', '/')+' '+str(ROOT/'tests/fake_kubectl.py').replace('\\', '/'),
                       CONFIRM='YES', MOCK_STATE=str(temp/'state.json'), SCENARIO=scenario)
            result = subprocess.run([BASH, str(ROOT/'probe.sh'), mode], cwd=temp, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=90)
            folders = list(temp.glob('p1-sec01-ac06-probe-*'))
            folders = [p for p in folders if p.is_dir()]
            self.assertEqual(len(folders), 1, result.stderr)
            c.verify(folders[0])
            self.assertTrue(Path(str(folders[0])+'.completion.txt').exists())
            state = json.loads((temp/'state.json').read_text())
            calls = (temp/'state.calls').read_text()
            summary = (folders[0]/'99-summary-provisional.txt').read_text(encoding='utf-8')
            return result, state, calls, summary

    def test_normal_all_stages_and_both_uid_deletions(self):
        r, state, calls, summary = self.exercise('')
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertEqual(state['stage'], 3)
        self.assertEqual(state['objects'], {})
        self.assertIn('ALL_STAGES_OBSERVED=yes', summary)

    def test_precheck_never_mutates(self):
        r, state, calls, summary = self.exercise('', 'precheck')
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertNotIn('"create"', calls); self.assertNotIn('"delete"', calls)

    def test_stale_and_quota_failure_stop_before_creation(self):
        for scenario in ('stale', 'quota_error'):
            with self.subTest(scenario=scenario):
                r, state, calls, summary = self.exercise(scenario)
                self.assertEqual(r.returncode, 2, r.stdout+r.stderr)
                self.assertNotIn('"create"', calls)

    def test_lost_create_response_is_adopted_and_deleted(self):
        r, state, calls, summary = self.exercise('create_response_lost')
        self.assertEqual(r.returncode, 3, r.stdout+r.stderr)
        self.assertEqual(state['objects'], {})
        self.assertEqual(state['stage'], 0)

    def test_failure_paths_cleanup_without_later_injection(self):
        for scenario, stage in (('inject_fail', 0), ('ep_error', 0), ('token', 0),
                                ('unexpected_restart', 1), ('baseline_change', 1)):
            with self.subTest(scenario=scenario):
                r, state, calls, summary = self.exercise(scenario)
                self.assertEqual(r.returncode, 3, r.stdout+r.stderr)
                self.assertEqual(state['stage'], stage)
                self.assertEqual(state['objects'], {})
                self.assertNotIn('ALL_STAGES_OBSERVED=yes', summary)


if __name__ == '__main__': unittest.main()
