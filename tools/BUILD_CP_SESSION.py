#!/usr/bin/env python3
"""Build a reproducible candidate kit; does not grant approval or contact a host."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

TRIAL_COMMIT='b3f3374ba71a754f15f69a5cc3a471bd61c79424'
TRIAL_FILES={'run.py':'16c04ce598baf80919a4157d374f3fc5e104768852d9b3a676330656ed1bf351',
             'remote.py':'3962812bf610f322102601736d218fb0b744721ab9ce59e070393228c71d8cfb',
             'pod_probe.py':'64c19fe81c5361785c139d977f13508743bd254ffe32149c1c895b586de1d09d'}
CORE=['cp1_minimal_change.py','run-cp1-minimal-change.py','node-firewall.py',
      'run-node-firewall.py','analyze-cp1-cni-trial.py']
FILES=['scripts/'+v for v in CORE]+[
    'scripts/cp1_minimal_consumer.py','scripts/run-node-firewall-cni.py','scripts/node-firewall-session.py',
    'scripts/cp-canary-node.py','policies/icr-d-net-01-01/node-firewall-v2.json',
    'docs/runbooks/NODE_FIREWALL_FOLLOWUP.md','docs/runbooks/CP1_MINIMAL_CHANGE.md',
    'docs/runbooks/CP_SESSION.md','docs/change/ICR-D-NET-01-01/CP_SESSION_REVIEW_20260918.md']


def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return (json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode()


def archive(path,items):
    with path.open('xb') as output,gzip.GzipFile(filename='',mode='wb',fileobj=output,mtime=0) as stream:
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT) as target:
            for name,raw in sorted(items.items()):
                entry=tarfile.TarInfo(name);entry.size=len(raw);entry.mode=0o600;entry.mtime=0
                target.addfile(entry,io.BytesIO(raw))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--trial-package',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();source=args.source_root.resolve();old=args.trial_package.resolve()
    if not re.fullmatch('[0-9a-f]{40}',args.source_commit):raise ValueError('source commit must be exact')
    destination=args.destination.resolve();destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in FILES:
        path=source/name
        if not path.is_file() or path.is_symlink():raise ValueError('missing/unsafe source '+name)
        target=destination/'source'/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
    lock={'repository':'321Team/infra-kubernetes','commit':args.source_commit,
          'files':{name:sha((source/name).read_bytes()) for name in FILES}}
    (destination/'source/SOURCE_LOCK.json').write_bytes(encode(lock))
    for src,dst in [('tools/RUN_CP_SESSION.py','RUN_CP_SESSION.py'),
                    ('tools/cp_session_authority.py','cp_session_authority.py')]:
        shutil.copyfile(source/src,destination/dst)
    for name in ('trial-kit','trial-source'):
        shutil.copytree(old/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    trial_lock=json.loads((destination/'trial-source/SOURCE_LOCK.json').read_bytes())
    if trial_lock['repository']!='321Team/infra-kubernetes' or trial_lock['commit']!=TRIAL_COMMIT:
        raise ValueError('trial source identity differs')
    for name,digest in trial_lock['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha((destination/'trial-source'/name).read_bytes())!=digest:
            raise ValueError('trial source lock differs')
    for name,digest in TRIAL_FILES.items():
        if sha((destination/'trial-kit'/name).read_bytes())!=digest:raise ValueError('trial runner differs')
    spec=importlib.util.spec_from_file_location('build_session_entry',source/'scripts/run-cp1-minimal-change.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    bundle=entry.m.fingerprint(entry.source_files())
    binding={'schema':'fds.cp-session-package.v1','source_commit':args.source_commit,'source_bundle_sha256':bundle,
             'review_pr':14,'review_status_at_build':'NOT_ASSERTED_VERIFY_ACTUAL_GITHUB',
             'old_job':'12f18e7d72f6c7f4',
             'old_source_bundle_sha256':'9b3ec15118d558b386fddfdc06a2ff9354339de1655034b2a32710348f25068e',
             'original_receipt_sha256':'7fd8ccb787c6abb6a1e81def57c4cf22b6f4e00d0b752f73096b1d6bd70ecbf3',
             'trial_source_commit':TRIAL_COMMIT,'trial_files':TRIAL_FILES,
             'cp1_accepted':False,'independent_acceptance':'PENDING','join_authorized':False}
    (destination/'PACKAGE_BINDING.json').write_bytes(encode(binding))
    archive(destination/'SOURCE.tar.gz',{'source/scripts/'+name:(source/'scripts'/name).read_bytes() for name in CORE})
    readme=(source/'docs/runbooks/CP_SESSION.md').read_text()
    readme=readme.replace('../change/ICR-D-NET-01-01/CP_SESSION_REVIEW_20260918.md',
                        'source/docs/change/ICR-D-NET-01-01/CP_SESSION_REVIEW_20260918.md')
    (destination/'README_KO.md').write_text(readme)
    for name in ('CP_SESSION_VALIDATION_20260918.json','CP_SESSION_TEST_20260918.txt'):
        folder=destination/'review';folder.mkdir(exist_ok=True)
        shutil.copyfile(source/'docs/change/ICR-D-NET-01-01'/name,folder/name)
    manifest=''.join(sha(p.read_bytes())+'  '+p.relative_to(destination).as_posix()+'\n'
                     for p in sorted(destination.rglob('*')) if p.is_file())
    (destination/'PACKAGE_SHA256SUMS').write_text(manifest)
    result=subprocess.run([sys.executable,'-I','-B',str(destination/'RUN_CP_SESSION.py')],capture_output=True,text=True)
    if result.returncode or json.loads(result.stdout).get('mode')!='PLAN_ONLY':
        raise ValueError('package PLAN_ONLY failed: '+result.stderr)
    # Generated package metadata is not an approval and does not authenticate a
    # hand-edited kit. Verify the published outer SHA256 before running it.
    output=destination.with_name(destination.name+'.tar.gz')
    archive(output,{destination.name+'/'+p.relative_to(destination).as_posix():p.read_bytes()
                    for p in destination.rglob('*') if p.is_file()})
    sidecar=Path(str(output)+'.sha256');sidecar.write_text(sha(output.read_bytes())+'  '+output.name+'\n')
    print(json.dumps({'archive':str(output),'sha256':sha(output.read_bytes()),'sidecar':str(sidecar),
                      'source_commit':args.source_commit,'source_bundle_sha256':bundle,'plan':'PASS',
                      'runtime_executed':False},indent=2))


if __name__=='__main__':main()
