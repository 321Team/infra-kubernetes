"""Execute unmodified production functions, replacing external effects only.

No Kubernetes or etcd access; no sudo, unshare, real restore or production path writes.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash') or 'C:/Program Files/Git/bin/bash.exe'


def function(file, name):
    text = (ROOT/file).read_text(encoding='utf-8')
    match = re.search(r'^'+re.escape(name)+r'\(\) \{.*?^\}', text, re.M | re.S)
    if not match: raise AssertionError(name)
    return match.group(0)


def run(script):
    common = (ROOT/'common.sh').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)/'test.sh'
        path.write_text('set -uo pipefail\n'+common+'\n'+script, encoding='utf-8', newline='\n')
        return subprocess.run([BASH, str(path)], cwd=temp, capture_output=True, text=True,
                              encoding='utf-8', timeout=30)


class Lifecycle(unittest.TestCase):
    def test_no_command_dispatched_after_deadline(self):
        r = run('mono() { echo 100; }; PHASE_DEADLINE=100; timeout 15 touch forbidden; rc=$?; [ "$rc" = 124 ] && [ ! -e forbidden ]')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_real_slow_command_is_bounded(self):
        r = run('phase_begin test 4; timeout 20 bash -c "sleep 10; touch forbidden"; rc=$?; [ "$rc" = 124 ] && [ ! -e forbidden ]')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_repeated_signals_do_not_reenter_finish(self):
        for file, attempt in [('probe.sh', 'POD_ATTEMPT=1; SVC_ATTEMPT=1'), ('backup.sh', 'RUN_CREATED=1')]:
            script = function(file, 'finish') + '\n' + r'''
OUT=out; mkdir out; S=out/summary; REV=test; MODE=run
FINISHED=0; CLEAN_FAIL=0; FAIL=0; SNAP_SHA0=x; SNAP=/none
w() { printf '%s\n' "$2" >> "$1"; }
cleanup() { echo first >> calls; kill -TERM $$; kill -INT $$; echo second >> calls; }
phase_begin() { :; }; snapshot_pods() { :; }; snapshot_eps() { :; }
check_nodes() { return 0; }; elapsed() { echo 0; }; seal_out() { :; }
timeout() { return 1; }; etcdctl_prod() { return 1; }
trap on_signal INT TERM
''' + attempt + '\nfinish 0\n'
            # Postchecks deliberately fail, but both cleanup steps must run once.
            script = script.replace('completion "$st"; st=$?', 'completion() { [ "$(cat calls)" = "$(printf \'first\\nsecond\')" ] || exit 99; return "$1"; }; completion "$st"; st=$?')
            r = run(script)
            self.assertEqual(r.returncode, 3, (file, r.stdout, r.stderr))

    def test_probe_wait_rejects_success_after_expiry(self):
        script = function('probe.sh', 'wait_for') + r'''
echo 0 > clock
mono() { cat clock; }; PHASE_DEADLINE=10; POD_UID=uid; POLL=1; OUT=out; mkdir out
pod_state() { echo 11 > clock; echo 'uid|Running|True|0|cid'; }
ep_state() { echo true; }; w() { :; }; abort() { exit 99; }
wait_for T0 20 'true'; rc=$?; [ "$rc" -eq 1 ] && [ "$PHASE_DEADLINE" -eq 10 ]
'''
        r = run(script)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_probe_wait_includes_api_latency_in_stage_limit(self):
        script = function('probe.sh', 'wait_for') + r'''
echo 0 > clock
mono() { cat clock; }; PHASE_DEADLINE=100; POD_UID=uid; POLL=1; OUT=out; mkdir out
pod_state() { echo 11 > clock; echo 'uid|Running|True|0|cid'; }
ep_state() { echo true; }; w() { :; }; abort() { exit 99; }
wait_for T0 10 'true'; rc=$?; [ "$rc" -eq 1 ] && [ "$PHASE_DEADLINE" -eq 100 ]
'''
        r = run(script)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_expired_cleanup_does_not_query_next_object(self):
        script = function('probe.sh', 'verify_gone') + r'''
mono() { echo 100; }; PHASE_DEADLINE=100; CLEAN_FAIL=0; OUT=out; mkdir out
kc() { touch forbidden; }; w() { :; }
verify_gone pod test; rc=$?; [ "$rc" -eq 1 ] && [ "$CLEAN_FAIL" = 1 ] && [ ! -e forbidden ]
'''
        r = run(script)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_endpoint_unknown_nil_multiple_and_absent(self):
        fn = function('probe.sh', 'ep_state')
        for response, code, expected in [('uid|', 0, 'true'), ('uid|false\nuid|true', 0, 'true'),
                                         ('uid|false', 0, 'false'), ('other|true', 0, 'absent'),
                                         ('uid|bad', 0, 'ERROR'), ('', 1, 'ERROR')]:
            r = run(fn + '\nPOD_UID=uid; NS=ns; SVC=svc; OUT=out; mkdir out\nw() { :; }\n'
                    + f"kc() {{ printf '%s\\n' '{response}'; return {code}; }}\n"
                    + 'ep_state || echo ERROR\n')
            self.assertEqual(r.stdout.strip(), expected, r.stderr)

    def test_uid_unknown_or_foreign_never_deletes_by_name(self):
        for answer in ('UNKNOWN', 'FOREIGN', 'NOTFOUND', 'OWNED|abc'):
            script = function('probe.sh', 'resolve_uid') + '\n' + function('probe.sh', 'cleanup') + f'''
POD_ATTEMPT=1; SVC_ATTEMPT=0; POD_UID=""; SVC_UID=""; POD=test; SVC=test
CLEANED=0; CLEAN_FAIL=0; CLEAN_LIMIT=90; OUT=out; mkdir out
w() {{ :; }}; say() {{ :; }}; adopt_owned() {{ echo '{answer}'; }}
delete_owned() {{ echo "$3" >> deletes; }}; verify_gone() {{ :; }}
cleanup
'''
            script += ('[ "$(cat deletes)" = abc ]' if answer.startswith('OWNED') else '[ ! -e deletes ]')
            r = run(script)
            self.assertEqual(r.returncode, 0, (answer, r.stderr))

    def test_cleanup_failure_has_priority_over_observation_success(self):
        script = function('probe.sh', 'finish') + r'''
OUT=out; mkdir out; S=out/summary; REV=test; MODE=run
FINISHED=0; CLEAN_FAIL=0; FAIL=0; POD_ATTEMPT=1; SVC_ATTEMPT=1
w() { :; }; cleanup() { CLEAN_FAIL=1; }; phase_begin() { :; }
snapshot_pods() { :; }; snapshot_eps() { :; }; check_nodes() { return 0; }
elapsed() { echo 0; }; seal_out() { FAIL=1; }
finish 0
'''
        r = run(script)
        self.assertEqual(r.returncode, 13, r.stderr)


if __name__ == '__main__': unittest.main()
