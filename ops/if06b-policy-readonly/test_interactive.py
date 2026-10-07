"""Exercise the whole transport wrapper with fake SSH/SCP; no network/root work."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
BASH = os.environ.get('TEST_BASH') or shutil.which('bash')

SSH = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$MOCK_LOG"
if [[ " $* " == *' -O exit '* ]]; then echo cleanup >> "$MOCK_EVENTS"; exit 0; fi
if [[ " $* " == *' -Nf '* ]]; then echo auth >> "$MOCK_EVENTS"; exit 0; fi
[[ " $* " == *' BatchMode=yes '* ]] || exit 90
last="${!#}"
if [[ "$last" = true ]]; then
  [[ "$SCENARIO" != reuse_failure ]] || exit 21
elif [[ "$last" == *'mktemp -d'* ]]; then
  printf '/tmp/if06b-code.ABC12345\n'
elif [[ "$last" == bash* ]]; then
  echo collect >> "$MOCK_EVENTS"
  [[ "$SCENARIO" != remote_failure ]] || exit 23
else exit 91
fi
'''
SCP = r'''#!/usr/bin/env bash
set -eu
printf 'scp %s\n' "$*" >> "$MOCK_LOG"
[[ " $* " == *' BatchMode=yes '* ]] || exit 90
last="${!#}"
[[ "$last" != server01_mgmt-gw@* ]] || exit 0
src="${@: -2:1}"; name="${src##*/}"
case "$name" in
 capture.tar.gz)
   [[ "$SCENARIO" != transfer_failure ]] || exit 22
   printf fixture > "$last/$name";;
 capture.tar.gz.sha256)
   if [[ "$SCENARIO" = hash_failure ]]; then
     printf '%064d  capture.tar.gz\n' 0 > "$last/$name"
   else (cd "$last" && sha256sum capture.tar.gz) > "$last/$name"; fi;;
 collector.rc)
   if [[ "$SCENARIO" = collector_stop ]]; then printf '2\n' > "$last/$name"
   elif [[ "$SCENARIO" = invalid_rc ]]; then printf 'garbage\n' > "$last/$name"
   else printf '0\n' > "$last/$name"; fi;;
 *) printf fixture > "$last/$name";;
esac
'''


@unittest.skipUnless(BASH, 'Bash required for shell integration mock')
class TransportTests(unittest.TestCase):
    def run_case(self, scenario):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in [('ssh', SSH), ('scp', SCP), ('hostname', '#!/usr/bin/env bash\necho ansible01\n')]:
                p=root/name
                p.write_text(body, encoding='utf-8', newline='\n')
                p.chmod(0o700)
            shutil.copyfile(HERE/'run-interactive.sh', root/'run-interactive.sh')
            (root/'collect.py').write_text('fixture\n', newline='\n')
            # Bash converts paths for its own tools, including on Git for Windows.
            script='''set -eu
cd "$TEST_ROOT"
export HOME="$PWD" PATH="$PWD:$PATH" MOCK_LOG="$PWD/calls" MOCK_EVENTS="$PWD/events"
sha256sum collect.py > SOURCE_SHA256SUMS
sha256sum collect.py run-interactive.sh SOURCE_SHA256SUMS > SHA256SUMS
bash run-interactive.sh
'''
            env=dict(os.environ, TEST_ROOT=str(root), SCENARIO=scenario)
            p=subprocess.run([BASH,'-c',script],env=env,capture_output=True,timeout=20)
            events=(root/'events').read_text().splitlines()
            calls=(root/'calls').read_text()
            self.assertEqual(events.count('auth'),1)
            self.assertEqual(events.count('cleanup'),1)
            self.assertNotIn('-MNf', calls)
            return p, events, list(root.glob('if06b-receipt.*/collector.rc')) != []

    def test_success(self):
        p, events, preserved = self.run_case('success')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(events.count('collect'),1)
        self.assertTrue(preserved)

    def test_reuse_failure_stops_before_collection(self):
        p, events, _ = self.run_case('reuse_failure')
        self.assertEqual(p.returncode,21,p.stderr)
        self.assertNotIn('collect',events)

    def test_collector_stop_preserved_and_propagated(self):
        p, events, preserved = self.run_case('collector_stop')
        self.assertEqual(p.returncode,2,p.stderr)
        self.assertTrue(preserved)
        self.assertIn(b'TRANSFER_COMPLETE collector_rc=2',p.stdout)

    def test_transfer_and_hash_failure(self):
        for scenario in ['transfer_failure','hash_failure']:
            with self.subTest(scenario=scenario):
                p, _, _ = self.run_case(scenario)
                self.assertNotEqual(p.returncode,0)
                self.assertNotIn(b'TRANSFER_COMPLETE',p.stdout)

    def test_invalid_exit_record(self):
        p, _, preserved = self.run_case('invalid_rc')
        self.assertEqual(p.returncode,12,p.stderr)
        self.assertTrue(preserved)

    def test_remote_error_not_hidden_by_successful_transfer(self):
        p, _, preserved = self.run_case('remote_failure')
        self.assertEqual(p.returncode,23,p.stderr)
        self.assertTrue(preserved)


if __name__ == '__main__': unittest.main()
