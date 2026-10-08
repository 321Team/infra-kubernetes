import importlib.util
import pathlib
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', pathlib.Path(__file__).with_name('collect.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CaptureTests(unittest.TestCase):
    def test_missing_tool_retains_error(self):
        with tempfile.TemporaryDirectory() as d, patch.object(c.subprocess, 'Popen', side_effect=FileNotFoundError('missing')):
            row = c.capture(pathlib.Path(d), 'query', ['nft'], time.monotonic() + 10)
            self.assertEqual(row['rc'], 127)
            self.assertIn(b'missing', (pathlib.Path(d) / 'query.stderr').read_bytes())

    def test_budget_prevents_command(self):
        with tempfile.TemporaryDirectory() as d, patch.object(c.subprocess, 'Popen') as p:
            row = c.capture(pathlib.Path(d), 'query', ['nft'], time.monotonic() - 1)
            self.assertEqual(row['rc'], 124)
            p.assert_not_called()

    def test_timeout_preserves_partial_bytes(self):
        with tempfile.TemporaryDirectory() as d, patch.object(c.subprocess, 'Popen') as p, patch.object(c.os, 'killpg', create=True) as kill, patch.object(c.signal, 'SIGKILL', 9, create=True):
            child = p.return_value.__enter__.return_value
            child.pid = 42
            child.communicate.side_effect = [subprocess.TimeoutExpired('nft', 15), (b'partial out', b'partial error')]
            row = c.capture(pathlib.Path(d), 'query', ['nft'], time.monotonic() + 20)
            self.assertEqual(row['rc'], 124)
            self.assertEqual((pathlib.Path(d) / 'query.stdout').read_bytes(), b'partial out')
            kill.assert_called_once()

    def test_failure_and_drift_do_not_pass(self):
        for fail, drift, expected in [(False, False, 0), (True, False, 2), (False, True, 2)]:
            with self.subTest(fail=fail, drift=drift), tempfile.TemporaryDirectory() as d, patch.object(c.os, 'geteuid', return_value=0, create=True):
                out = pathlib.Path(d)
                rows = []
                for key, args in c.plan():
                    (out / f'{key}.stdout').write_bytes(b'new' if drift and key == 'after-runtime-policy' else b'same')
                    rows.append({'id': key, 'rc': 140 if fail and key == 'before-runtime-policy' else 0})
                self.assertEqual(c.finish(out, rows), expected)
                self.assertTrue((out / 'SHA256SUMS').is_file())


if __name__ == '__main__':
    unittest.main()
