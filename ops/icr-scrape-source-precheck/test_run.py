"""Controller-only integration. Fake ansible-playbook never contacts a server."""
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

MOCK = '''#!/usr/bin/env python3
import sys,json,pathlib,os
if '--syntax-check' in sys.argv:
 print('MOCK syntax-check')
 sys.exit(2 if os.environ.get('ICR_MOCK_CASE')=='syntax-fail' else 0)
inp=next(x[1:] for x in sys.argv if x.startswith('@'))
d=pathlib.Path(json.load(open(inp))['icr_output_dir'])
mode=json.load(open(d/'run-metadata.json'))['collection_mode']
names=['k8s-control01-guard-before','k8s-control01-guard-after']
for worker in ['k8s-worker01','k8s-worker03']:
 for phase in (['counters'] if mode=='counters-only' else ['prepare','observe']):
  names.append(worker+'-'+phase)
for n in names:
 partial=os.environ.get('ICR_MOCK_CASE')=='partial' and n.endswith('observe')
 record={'result':'COLLECTED_PARTIAL' if partial else 'COLLECTED_PENDING_ANALYSIS','read_failures':4 if partial else 0}
 (d/(n+'.json')).write_text(json.dumps({'rc':0,'stdout':json.dumps(record)})+'\\n')
print('MOCK completed; no remote calls')
'''


class ControllerTests(unittest.TestCase):
    def exercise(self, mode, case, expected_rc):
        source = pathlib.Path(__file__).resolve().with_name("run.sh")
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp,"infra-ansible");(root/"inventory").mkdir(parents=True)
            (root/"inventory/hosts.yml").write_text("all: {}\n")
            for argv in [["git","init","-q",str(root)], ["git","-C",str(root),"add","inventory/hosts.yml"],
                         ["git","-C",str(root),"-c","user.name=ICR Mock","-c","user.email=mock@example.invalid","commit","-qm","synthetic inventory"]]:
                subprocess.run(argv,check=True,capture_output=True)
            bindir=pathlib.Path(tmp,"bin");bindir.mkdir();mock=bindir/"ansible-playbook"
            mock.write_text(MOCK);mock.chmod(0o755)
            env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],ICR_MOCK_CASE=case)
            argv=["bash",str(source),str(root/"inventory/hosts.yml"),"a"*40]
            if mode=="counters-only":argv.append("--counters-only")
            r=subprocess.run(argv,env=env,text=True,capture_output=True)
            self.assertEqual(r.returncode,expected_rc,(r.stdout,r.stderr))
            archive=pathlib.Path(next(x.split('=',1)[1] for x in r.stdout.splitlines() if x.startswith('ARCHIVE=')))
            self.assertTrue(archive.with_name(archive.name+'.sha256').is_file())
            with tarfile.open(archive) as t:
                files={pathlib.Path(m.name).name:t.extractfile(m).read() for m in t if m.isfile()}
            self.assertEqual(int(files['controller-exit-code.txt']),expected_rc)
            self.assertEqual(json.loads(files['run-metadata.json'])['collection_mode'],mode)
            if case=='syntax-fail':self.assertFalse(any(n.startswith('k8s-') for n in files))
            else:
                self.assertIn('k8s-control01-guard-after.json',files)
                self.assertEqual(sum(n.endswith('-observe.json') for n in files),0 if mode=='counters-only' else 2)
                self.assertEqual(sum(n.endswith('-counters.json') for n in files),2 if mode=='counters-only' else 0)

    def test_source_mode_complete(self):self.exercise('source','complete',0)
    def test_counters_mode_complete(self):self.exercise('counters-only','complete',0)
    def test_syntax_failure_preserves_bundle(self):self.exercise('counters-only','syntax-fail',2)
    def test_partial_reads_return_3_after_post_guard(self):self.exercise('source','partial',3)


if __name__=='__main__':unittest.main()
