"""Execute the real inner script with fake etcd/etcdctl/ip/ss, no namespaces."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_lifecycle import BASH, ROOT


class BackupInner(unittest.TestCase):
    def exercise(self, scenario):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            binpath = temp/'bin'; binpath.mkdir()
            out = temp/'out'; out.mkdir()
            scripts = {
                'ip': 'exit 0\n',
                'ss': 'echo "LISTEN 0 10 127.0.0.1:23790"\n',
                'etcd': 'trap "exit 0" TERM INT\nwhile :; do sleep 1; done\n',
                'python3': 'exec "'+sys.executable.replace('\\', '/')+'" "$@"\n',
                'etcdctl': r'''
case "$*" in
  *"endpoint health"*) echo healthy; exit 0;;
  *"endpoint status"*) echo '[{"Status":{"header":{"revision":3287996}}}]'; exit 0;;
  *"--count-only"*)
    case "$*" in *"--write-out=fields"*) :;; *) echo bad-format >&2; exit 1;; esac
    case "$SCENARIO" in count_rc) echo 'count-error' >&2; exit 7;; count_text) echo ERROR; exit 0;; esac
    echo '"Count" : 620'; exit 0;;
  *"/registry/secrets/"*)
    [ "$SCENARIO" != secret_bad ] || { echo 'SECRET-SENTINEL'; exit 0; }
    [ "$SCENARIO" != secret_rc ] || { echo '{"header":{},"count":0}'; exit 4; }
    echo '{"header":{},"count":0}'; exit 0;;
esac
exit 90
'''
            }
            for name, content in scripts.items():
                p = binpath/name
                p.write_text('#!/usr/bin/env bash\n'+content, encoding='utf-8', newline='\n')
                p.chmod(0o755)
            env = dict(os.environ, PATH=str(binpath)+os.pathsep+os.environ['PATH'],
                       LIB_DIR=str(ROOT).replace('\\', '/'), BIN=str(binpath).replace('\\', '/'),
                       OUT=str(out).replace('\\', '/'), NAME='fixture', DD=str(temp/'data'),
                       CURL='http://127.0.0.1:23790', PURL='http://127.0.0.1:23800', TOKEN='fixture',
                       SCENARIO=scenario)
            result = subprocess.run([BASH, str(ROOT/'backup-inner.sh')], cwd=temp, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            content = '\n'.join(p.read_text(encoding='utf-8') for p in out.iterdir())
            self.assertNotIn('SECRET-SENTINEL', content + result.stdout + result.stderr)
            return result, content

    def test_complete_observation(self):
        r, content = self.exercise('')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('inner_done', content)

    def test_count_failure_and_error_text_fail(self):
        for scenario in ('count_rc', 'count_text'):
            with self.subTest(scenario=scenario):
                r, content = self.exercise(scenario)
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn('inner_done', content)

    def test_secret_pipeline_parser_and_producer_fail_closed(self):
        for scenario in ('secret_bad', 'secret_rc'):
            with self.subTest(scenario=scenario):
                r, content = self.exercise(scenario)
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn('inner_done', content)


if __name__ == '__main__': unittest.main()
