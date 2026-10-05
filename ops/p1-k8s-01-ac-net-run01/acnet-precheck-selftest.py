#!/usr/bin/env python3
"""Run real remote gate strings against local CLI stubs; no server/API access.
Only ~/.config, ~/infra-ansible and ~/evidence paths are isolated in a test copy.
"""
import os, pathlib, subprocess, tempfile, unittest
SCRIPT = pathlib.Path(__file__).with_name('acnet-0-precheck.sh')

class Precheck(unittest.TestCase):
    def run_case(self, fault):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); (root/'bin').mkdir(); (root/'out').mkdir()
            (root/'env').write_text(':\n')
            source = SCRIPT.read_text().replace('~/.config/ansible/p1-ans-01.env', '"$FIXTURE_ROOT/env"').replace('~/infra-ansible', '"$FIXTURE_ROOT"').replace('~/ac-net-run01-precheck-', '"$FIXTURE_ROOT/out"/ac-net-run01-precheck-')
            (root/'script.sh').write_text(source)
            (root/'manifest.yaml').write_text('  image: registry.invalid/app@sha256:'+'a'*64+'\n')
            stubs = {
'ansible': '''#!/bin/bash
while [ "$#" -gt 0 ]; do if [ "$1" = -a ]; then shift; command="$1"; break; fi; shift; done
[ "$FAULT" = transport ] && { echo ACNET_NODES_READYZ_OK; exit 42; }
/bin/bash -c "$command"; rc=$?
[ "$FAULT" = missing_marker ] && exit 42
exit "$rc"
''',
'kubectl': '''#!/bin/bash
case "$*" in
 *"get ns"*)
   [ "$FAULT" = ns_error ] && exit 42
   [ "$FAULT" = ns_exists ] && echo namespace/ac-net-run01
   exit 0;;
 *"get nodes"*)
   for n in 1 2 3 4 5 6; do
     status=Ready; name="node$n"
     [ "$FAULT" = notready ] && [ "$n" = 6 ] && status=NotReady
     [ "$FAULT" = unsched ] && [ "$n" = 6 ] && status=Ready,SchedulingDisabled
     [ "$FAULT" = duplicate ] && name=node1
     echo "$name $status worker 1d v1.36"
   done
   [ "$FAULT" = extra_node ] && echo 'node7 Ready worker 1d v1.36'
   [ "$FAULT" = nodes_rc ] && exit 42
   exit 0;;
 *"get --raw=/readyz"*)
   [ "$FAULT" = ready_bad ] && { echo failed; exit 0; }
   echo ok
   [ "$FAULT" = ready_rc ] && exit 42
   exit 0;;
esac
exit 99
''',
'crictl': '''#!/bin/bash
case "$1" in
 images)
   [ "$FAULT" != image_missing ] && echo 'app sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
   [ "$FAULT" = images_rc ] && exit 42
   exit 0;;
 inspecti) [ "$FAULT" = inspect_rc ] && exit 42; echo '{}'; exit 0;;
esac
exit 99
''',
 'tee': '''#!/bin/bash
[ "$FAULT" = log_error ] && { /bin/cat; exit 42; }
exec /usr/bin/tee "$@"
''',
 'sha256sum': '''#!/bin/bash
if [ "$FAULT" = seal_error ] && [[ "$*" == *run.log* ]]; then exit 42; fi
exec /usr/bin/sha256sum "$@"
'''}
            for name, content in stubs.items():
                f=root/'bin'/name; f.write_text(content); f.chmod(0o755)
            env=dict(os.environ, FIXTURE_ROOT=temp, FAULT=fault, PATH=str(root/'bin')+':'+os.environ['PATH'])
            result=subprocess.run(['bash',str(root/'script.sh'),str(root/'manifest.yaml')],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            if fault == 'normal':
                self.assertEqual(result.returncode,0,result.stdout)
                self.assertIn('PRECHECK_PASS',result.stdout)
                out=next((root/'out').iterdir())
                self.assertEqual(subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=out,stdout=subprocess.DEVNULL).returncode,0)
            else:
                self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertNotIn('PRECHECK_PASS',result.stdout)

for scenario in ['normal','transport','ns_error','ns_exists','notready','unsched','duplicate','extra_node','nodes_rc','ready_bad','ready_rc','image_missing','images_rc','inspect_rc','missing_marker','log_error','seal_error']:
    setattr(Precheck, 'test_'+scenario, lambda self, scenario=scenario: self.run_case(scenario))
if __name__ == '__main__': unittest.main(verbosity=2)
