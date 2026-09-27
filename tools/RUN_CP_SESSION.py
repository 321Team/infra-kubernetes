#!/usr/bin/env python3
"""Reviewed CP session orchestration. Default: offline plan. Join is never executed."""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
SOURCE = ROOT / 'source'
CONTROLLER_HOME = Path('/home/server01_ansible')
OLD_ORIGINAL = '/home/svc_ansible/FDS_CP1_Minimal_1ff137a_v1.2_stage/inputs/original-receipt.json'
CP1_BASE = '/var/lib/fds/cp1-minimal'
TRIAL_MAX_SECONDS = 1200
# Existing trial's 20m total budget + SSH grace90 + regression180 + issue snapshot360.
TRIAL_AND_HANDOFF_BUDGET = TRIAL_MAX_SECONDS + 90 + 180 + 360
SSH = ['ssh', '-F', '/dev/null', '-i', '/home/server01_ansible/.ssh/p1_ansible_ed25519',
       '-b', '10.250.10.10', '-T']
for key, value in {
    'BatchMode':'yes', 'PreferredAuthentications':'publickey', 'PasswordAuthentication':'no',
    'KbdInteractiveAuthentication':'no', 'StrictHostKeyChecking':'yes',
    'UserKnownHostsFile':'/home/server01_ansible/.ssh/known_hosts', 'IdentitiesOnly':'yes',
    'IdentityAgent':'none', 'ControlMaster':'no', 'ControlPersist':'no', 'ControlPath':'none',
    'ConnectTimeout':'10', 'ConnectionAttempts':'1', 'ServerAliveInterval':'10',
    'ServerAliveCountMax':'2', 'UpdateHostKeys':'no', 'PermitLocalCommand':'no',
}.items():
    SSH += ['-o', key + '=' + value]
SSH += ['svc_ansible@10.1.93.102']


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_package():
    entries = {}
    for line in (ROOT / 'PACKAGE_SHA256SUMS').read_text().splitlines():
        digest, path = line.split('  ', 1)
        need(re.fullmatch('[0-9a-f]{64}', digest) and not Path(path).is_absolute()
             and '..' not in Path(path).parts and path not in entries, 'Invalid package manifest')
        file = ROOT / path
        need(file.is_file() and not file.is_symlink() and sha(file.read_bytes()) == digest,
             'Package checksum differs: ' + path)
        entries[path] = digest
    required = {'RUN_CP_SESSION.py','cp_session_authority.py','PACKAGE_BINDING.json','SOURCE.tar.gz',
                'source/SOURCE_LOCK.json','source/scripts/node-firewall-session.py',
                'source/scripts/run-node-firewall-cni.py','source/scripts/cp1_minimal_consumer.py'}
    required.update('source/scripts/'+name for name in ('cp1_minimal_change.py','run-cp1-minimal-change.py',
                    'node-firewall.py','run-node-firewall.py','analyze-cp1-cni-trial.py'))
    need(required <= set(entries),'package manifest is incomplete')
    return entries


def validate_old(status, record, binding, core):
    need(record.get('job_id') == binding['old_job']
         and record.get('source_bundle_sha256') == binding['old_source_bundle_sha256'], 'Old job/source differs')
    need(record.get('status') == 'RESTORED_REVIEW_REQUIRED' and record.get('cleanup_complete') is True
         and record.get('recovery_armed') is False, 'Old job has not completed recovery')
    need(record.get('restored') == record.get('before') and 'recovered_at' in record, 'Old restoration proof incomplete')
    need(status.get('job_id') == record['job_id'] and status.get('status') == 'RESTORED_REVIEW_REQUIRED'
         and status.get('source_bundle_sha256') == record['source_bundle_sha256']
         and status.get('change_receipt_sha256') == core.fingerprint(record)
         and status.get('recovery_armed') is False and status.get('current') == record['before'],
         'Current state/old receipt/timer differs; no new apply')


STAGE_SOURCE = r"""
import hashlib, io, json, os, pathlib, sys, tarfile
stage=pathlib.Path(sys.argv[1]); expected=sys.argv[2]; original=pathlib.Path(sys.argv[3]); original_sha=sys.argv[4]
assert os.geteuid()!=0 and not original.is_symlink() and original.is_file()
raw=sys.stdin.buffer.read(2*1024*1024+1)
assert len(raw)<=2*1024*1024 and hashlib.sha256(raw).hexdigest()==expected
receipt=original.read_bytes()
assert hashlib.sha256(receipt).hexdigest()==original_sha
names={'source/scripts/'+name for name in ('cp1_minimal_change.py','run-cp1-minimal-change.py',
       'node-firewall.py','run-node-firewall.py','analyze-cp1-cni-trial.py')}
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
    members=archive.getmembers()
    assert len(members)==len(names) and {m.name for m in members}==names
    assert all(m.isfile() and 0<=m.size<1024*1024 for m in members)
    stage.mkdir(mode=0o700, exist_ok=False)
    for member in members:
        path=stage/member.name; path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as f: f.write(archive.extractfile(member).read())
        path.chmod(0o600)
(stage/'inputs').mkdir(mode=0o700)
with (stage/'inputs/original-receipt.json').open('xb') as f:f.write(receipt)
print(json.dumps({'stage':str(stage),'original_sha256':original_sha}))
"""
PUT_FILE = r"""
import hashlib, os, pathlib, sys
path=pathlib.Path(sys.argv[1]); expected=sys.argv[2]
raw=sys.stdin.buffer.read(32*1024*1024+1)
assert len(raw)<=32*1024*1024 and hashlib.sha256(raw).hexdigest()==expected
with os.fdopen(os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600),'wb') as f:
    f.write(raw); f.flush(); os.fsync(f.fileno())
print(expected)
"""
REGRESSION = r"""
set -Eeuo pipefail
test "$(hostname -s)" = k8s-control01
test "$(id -un)" = svc_ansible
read -r ssh_source _ ssh_target _ <<< "$SSH_CONNECTION"
test "$ssh_source" = 10.250.10.10
test "$ssh_target" = 10.1.93.102
printf 'MANAGEMENT_SSH=PASS\n'
test "$(sudo -n /usr/bin/firewall-cmd --get-zone-of-interface=ens160)" = public
printf 'ENS160_ZONE=public\n'
test "$(sudo -n /usr/bin/kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s get --raw=/readyz)" = ok
printf 'KUBE_APISERVER_READYZ=PASS\n'
test "$(sudo -n /usr/bin/kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s get node k8s-control01 -o 'jsonpath={range .status.conditions[?(@.type=="Ready")]}{.status}{end}')" = True
printf 'CP1_NODE_READY=PASS\n'
/usr/bin/python3 -I -B -c 'import socket; a=socket.getaddrinfo("registry-1.docker.io",443,socket.AF_INET,socket.SOCK_STREAM); assert a; print("EXTERNAL_DNS_IPV4=PASS")'
curl_bin="$(command -v curl)"
test -n "$curl_bin"
test "$("$curl_bin" --silent --show-error --output /dev/null --write-out '%{http_code}' --connect-timeout 10 --max-time 20 --proto '=https' --tlsv1.2 https://registry-1.docker.io/v2/)" = 401
printf 'EXTERNAL_HTTPS_REGISTRY_401=PASS\n'
printf 'HOST_EXTERNAL_REGRESSION=PASS\n'
"""


class Work:
    def __init__(self, binding, core, directory, manifest):
        self.binding, self.core, self.directory, self.manifest = binding, core, directory, manifest
        self.state = {'schema':'fds.cp-session-run.v1', 'status':'PREPARING', 'source_commit':binding['source_commit'],
                      'runtime_mutation':'NOT_ATTEMPTED', 'cp1_accepted':False, 'cp2_cp3_join':'NOT_EXECUTED', 'nodes':{}, 'independent_acceptance':'PENDING'}
        self.job = None

    def save(self):
        (self.directory/'RUN_STATE.json').write_bytes(encoded(self.state))

    def step(self, name, argv, *, remote=False, data=None, timeout=400):
        self.state['phase'] = name; self.save()
        print('[PHASE] ' + name, flush=True)
        command = SSH + [shlex.join(argv)] if remote else argv
        try:
            result = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            (self.directory/(name+'.stdout')).write_bytes(error.stdout or b'')
            (self.directory/(name+'.stderr')).write_bytes(error.stderr or b'')
            raise RuntimeError(name+': timeout; remote outcome may be unknown') from error
        (self.directory/(name+'.stdout')).write_bytes(result.stdout)
        (self.directory/(name+'.stderr')).write_bytes(result.stderr)
        need(result.returncode == 0, name+': rc='+str(result.returncode)+'; see evidence stderr')
        return result.stdout

    def runner(self, name, source, mode, *args):
        return self.step(name, ['sudo','-n','/usr/bin/python3','-I','-B',source,mode,*args],
                         remote=True,timeout=600 if mode=='apply' else 400)

    def run(self, end, margin):
        b, m = self.binding, self.core
        # Existing trial controller preflight is read-only and validates the exact
        # inventory, controller account, SSH identities and source lock before CP1 mutation.
        check_code = (
            "import importlib.util,json; from pathlib import Path; "
            "p=Path('scripts/run-node-firewall.py'); "
            "s=importlib.util.spec_from_file_location('ctl',p); c=importlib.util.module_from_spec(s); "
            "s.loader.exec_module(c); assert c.verify_source()=="+repr(b['trial_source_commit'])+"; "
            "print(json.dumps(c.preflight(),sort_keys=True))"
        )
        previous = Path.cwd()
        try:
            os.chdir(ROOT/'trial-source')
            self.step('CONTROLLER_PREFLIGHT', [sys.executable,'-I','-B','-c',check_code], timeout=180)
        finally:
            os.chdir(previous)

        old_runner = CP1_BASE+'/'+b['old_job']+'/scripts/run-cp1-minimal-change.py'
        old = json.loads(self.runner('OLD_STATUS', old_runner, 'status', '--job', b['old_job']))
        record = json.loads(self.step('OLD_CHANGE', ['sudo','-n','cat',CP1_BASE+'/'+b['old_job']+'/CHANGE.json'],remote=True,timeout=30))
        validate_old(old, record, b, m)

        stage='/home/svc_ansible/FDS_CP_Session_'+secrets.token_hex(8)
        self.state['remote_stage']=stage; self.save()
        payload=(ROOT/'SOURCE.tar.gz').read_bytes()
        self.step('STAGE', ['/usr/bin/python3','-I','-B','-c',STAGE_SOURCE,stage,sha(payload),
                            OLD_ORIGINAL,b['original_receipt_sha256']], remote=True,data=payload,timeout=60)
        runner=stage+'/source/scripts/run-cp1-minimal-change.py'
        plan=json.loads(self.step('REMOTE_PLAN',['/usr/bin/python3','-I','-B',runner],remote=True,timeout=30))
        need(plan['mode']=='PLAN_ONLY' and plan['source_bundle_sha256']==b['source_bundle_sha256'], 'Remote source differs')
        original=stage+'/inputs/original-receipt.json'
        inspection_raw=self.runner('INSPECTION',runner,'inspect','--original',original,
                                   '--original-sha256',b['original_receipt_sha256'])
        inspection=m.analyzer().parse(inspection_raw)
        need(inspection.get('schema')=='fds.cp1-minimal-inspection.v1'
             and inspection['source_bundle_sha256']==b['source_bundle_sha256'], 'Fresh inspection failed')
        auth = session_auth(b, inspection, end, margin)
        window=m.check_authorization(auth,inspection['before'],b['source_bundle_sha256'])
        need(m.now()+dt.timedelta(seconds=360+TRIAL_AND_HANDOFF_BUDGET)<m.instant(window['withdraw_at']),
             'Work window cannot cover the existing apply/trial/handoff command budgets; no apply')
        auth_raw=encoded(auth); (self.directory/'AUTHORIZATION.json').write_bytes(auth_raw)
        inspect_remote=stage+'/inputs/INSPECTION.json';auth_remote=stage+'/inputs/AUTHORIZATION.json'
        for name,path,raw in [('COPY_INSPECTION',inspect_remote,inspection_raw),('COPY_AUTHORIZATION',auth_remote,auth_raw)]:
            self.step(name,['/usr/bin/python3','-I','-B','-c',PUT_FILE,path,sha(raw)],remote=True,data=raw,timeout=30)

        # Read-only failures leave the package reusable. Consume it only when
        # starting a mutation, with main holding the package execution lock.
        with (ROOT/'EXECUTION_ATTEMPT.json').open('x') as f:
            json.dump({'evidence':str(self.directory),'operator_decision_url':b['operator_decision_url']},f)
            f.flush();os.fsync(f.fileno())
        self.state['runtime_mutation']='APPLY_ATTEMPTED_OUTCOME_PENDING'; self.save()
        output=self.runner('APPLY',runner,'apply','--original',original,'--original-sha256',b['original_receipt_sha256'],
                           '--inspection',inspect_remote,'--inspection-sha256',sha(inspection_raw),
                           '--authorization',auth_remote,'--authorization-sha256',sha(auth_raw))
        lines=output.decode().splitlines()
        need(lines and re.fullmatch('JOB_ID=[0-9a-f]{16}',lines[0]), 'Apply job identifier missing')
        self.job=lines[0].split('=',1)[1]
        changed=m.analyzer().parse(('\n'.join(lines[1:])+'\n').encode())
        need(changed['job_id']==self.job and changed['schema']=='fds.cp1-minimal-change.v4'
             and changed['status']=='ACTIVE_REVIEW_REQUIRED'
             and changed['source_bundle_sha256']==b['source_bundle_sha256']
             and changed['authorization_sha256']==m.fingerprint(auth)
             and changed['before']==inspection['before']
             and changed['original_receipt_sha256']==inspection['original_receipt_sha256']
             and changed['after_sha256']==m.fingerprint(changed['after'])
             and all(changed.get(key)==value for key,value in window.items()), 'Apply result/input binding differs')
        self.state.update(job_id=self.job,runtime_mutation='EXECUTED_NOT_ACCEPTED',
                          withdraw_at=changed['withdraw_at'])
        self.save()
        frozen=CP1_BASE+'/'+self.job+'/scripts/run-cp1-minimal-change.py'
        status=json.loads(self.runner('STATUS_BEFORE_TRIAL',frozen,'status','--job',self.job))
        m.live_gate(status,changed,minimum_seconds=TRIAL_AND_HANDOFF_BUDGET)

        trial=self.directory/'trial-kit'; shutil.copytree(ROOT/'trial-kit',trial)
        (trial/'expected-firewall.json').write_bytes(encoded(changed['after']['firewall']))
        sums=''.join(sha(p.read_bytes())+'  '+p.name+'\n' for p in sorted(trial.iterdir()) if p.is_file())
        (trial/'SHA256SUMS').write_text(sums)
        trial_output=self.step('TRIAL_EXECUTION',[sys.executable,'-I','-B',str(trial/'run.py'),'--execute',
                                                '--source',str(ROOT/'trial-source')],timeout=1500).decode()
        matches=re.findall(r'^ARCHIVE=(.+)$',trial_output,re.MULTILINE)
        need(len(matches)==1, 'Trial archive unavailable; no retry')
        archive=Path(matches[0])
        need(archive.parent==CONTROLLER_HOME and re.fullmatch('FDS_CP1_CNI_Trial_[A-Za-z0-9_]+\\.tar\\.gz',archive.name)
             and archive.is_file() and not archive.is_symlink(), 'Unexpected trial archive')
        sidecar=Path(str(archive)+'.sha256'); need(sidecar.is_file() and not sidecar.is_symlink(),'Trial sidecar missing')
        raw=archive.read_bytes(); trial_sha=sha(raw)
        fields=sidecar.read_text().split()
        need(len(fields)==2 and fields[0]==trial_sha and fields[1].lstrip('*')==archive.name,'Trial checksum differs')
        local_archive=self.directory/archive.name; local_archive.write_bytes(raw)
        shutil.copy2(sidecar,self.directory/sidecar.name)
        self.state.update(trial_archive=archive.name,trial_sha256=trial_sha);self.save()
        request,wrapper,_=m.analyzer().archive_inputs(local_archive,trial_sha)
        report=m.analyzer().analyze(request,wrapper)
        (self.directory/'FIVE_FLOW_ANALYSIS.json').write_bytes(encoded(report))
        need(report.get('status')=='FIVE_FLOWS_REVIEW_REQUIRED' and report['passed_flows']==5
             and not report['issues'], 'Five-flow/cleanup evidence incomplete; no retry')

        regression=self.step('HOST_EXTERNAL_REGRESSION',['bash','-s'],remote=True,data=REGRESSION.encode(),timeout=180)
        need('HOST_EXTERNAL_REGRESSION=PASS' in regression.decode().splitlines(),'Regression incomplete')
        remote_trial=stage+'/'+archive.name
        self.step('COPY_TRIAL',['/usr/bin/python3','-I','-B','-c',PUT_FILE,remote_trial,trial_sha],
                  remote=True,data=raw,timeout=60)
        self.state['remote_trial']=remote_trial;self.save()
        handoff_raw=self.runner('HANDOFF',frozen,'issue-receipt','--job',self.job,
                                '--trial',remote_trial,'--trial-sha256',trial_sha)
        handoff=m.analyzer().parse(handoff_raw)
        original_raw=self.step('ORIGINAL_RECEIPT',['cat',original],remote=True,timeout=30)
        need(sha(original_raw)==b['original_receipt_sha256'],'Original receipt changed')
        m.validate_handoff(handoff,m.analyzer().parse(original_raw),request,wrapper,b['source_bundle_sha256'])
        need(handoff['change']==changed and handoff['cp1_accepted'] is False,'Handoff binding differs')
        (self.directory/'HANDOFF.json').write_bytes(handoff_raw)
        (self.directory/'CHANGE.json').write_bytes(encoded(changed))
        self.state.update(status='AWAITING_INDEPENDENT_ACCEPTANCE',passed_flows=5,
                          handoff_sha256=m.fingerprint(handoff),
                          negative_scope='cleanup/drift/consistency only; deny and cross-node NOT_TESTED',
                          acceptance_expires_at=changed['withdraw_at'])
        self.save()

    def finish(self, success):
        if not success:
            # Read the original job journal only. Never trigger recover/reload/retry.
            apply_file=self.directory/'APPLY.stdout'
            if self.job is None and apply_file.exists():
                found=re.findall(rb'^JOB_ID=([0-9a-f]{16})$',apply_file.read_bytes(),re.MULTILINE)
                if len(found)==1:self.job=found[0].decode()
            if self.job:
                self.state['job_id']=self.job
                try:
                    self.step('FAILURE_CHANGE',['sudo','-n','cat',CP1_BASE+'/'+self.job+'/CHANGE.json'],remote=True,timeout=30)
                except Exception as error:self.state['failure_journal_error']=str(error)
        self.save()
        archive=Path(str(self.directory)+('' if success else '.partial')+'.tar.gz')
        with tarfile.open(archive,'w:gz') as out:out.add(self.directory,arcname=self.directory.name,recursive=True)
        digest=sha(archive.read_bytes())
        Path(str(archive)+'.sha256').write_text(digest+'  '+archive.name+'\n')
        print('STATUS='+self.state['status'])
        print('RUNTIME_MUTATION='+self.state['runtime_mutation'])
        print('JOB_ID='+str(self.job or '<none>'))
        print('EVIDENCE_ARCHIVE='+str(archive))
        print('SHA256='+digest)
        print('SIDECAR='+str(archive)+'.sha256')
        if 'withdraw_at' in self.state:print('WITHDRAW_AT='+self.state['withdraw_at'])
        print('CP1_ACCEPTED=false\nINDEPENDENT_ACCEPTANCE=PENDING\nJOIN=NOT_EXECUTED')
        print('CP2_CP3_RESULTS='+json.dumps(self.state.get('nodes',{}),sort_keys=True))


def session_auth(binding, inspection, end, margin):
    return dict(schema='fds.cp1-minimal-authorization.v4', execution_approved=True,
                operator='GWONUK', independent_reviewer='hayeounglee', review_decision='APPROVED',
                review_scope='SOURCE_AND_SESSION_DELEGATION', session_policy='fds.cp-session-policy.v1',
                delegated_targets=['k8s-control02','k8s-control03'], independent_acceptance='PENDING',
                review_url=binding['review_url'], reviewed_at=binding['reviewed_at'],
                operator_decision_url=binding['operator_decision_url'], operator_decided_at=binding['operator_decided_at'],
                reviewed_bundle_sha256=binding['source_bundle_sha256'], before_sha256=inspection['before_sha256'],
                window_start=inspection['created_at'], window_end=end, recovery_margin_seconds=margin,
                backup_verified=True,console_recovery_available=True,maintenance_freeze=True,protection_reviewed=True,
                inert_permanent_staging_acknowledged=True,operator_present_until_end=True,
                scope={'hostname':'k8s-control01','pod_block':'10.244.246.64/26',
                       'interfaces':'cali+','l4':'ALL','plane':'runtime'})


def parse_session_window(value, margin, clock):
    if re.fullmatch(r'[0-9]{2}:[0-9]{2}', value):
        parts=dt.datetime.strptime(value,'%H:%M')
        end=clock.astimezone(KST).replace(hour=parts.hour,minute=parts.minute,second=0,microsecond=0)
    else:
        end=dt.datetime.fromisoformat(value)
    need(end.utcoffset() is not None and clock.astimezone(KST).weekday()<5 and
         end.astimezone(KST).date()==clock.astimezone(KST).date(), 'one explicit staffed KST weekday required')
    need(type(margin) is int and margin>=180 and clock+dt.timedelta(seconds=margin+420)<end,
         'session window expired/too short or recovery margin invalid')
    return end.isoformat()


def continuation(work):
    m=work.core; d=work.directory
    handoff=m.analyzer().parse((d/'HANDOFF.json').read_bytes())
    record=handoff['change']
    decision=dict(schema='fds.cp-session-continuation.v1',operator='GWONUK',
                  decision='CONTINUE_UNDER_DELEGATION',cp1_accepted=False,independent_acceptance='PENDING',
                  targets=['k8s-control02','k8s-control03'],join_authorized=False,
                  operator_decision_url=work.binding['operator_decision_url'],job_id=work.job,
                  source_bundle_sha256=work.binding['source_bundle_sha256'],
                  handoff_sha256=m.fingerprint(handoff),expires_at=record['withdraw_at'],
                  decided_at=m.now().isoformat(),
                  checks={k:dict(status='PASS',evidence_sha256=v) for k,v in [
                      ('positive',work.state['trial_sha256']),
                      ('negative',m.fingerprint(handoff['trial_report'])),
                      ('regression',sha((d/'HOST_EXTERNAL_REGRESSION.stdout').read_bytes()))]})
    m.validate_session_decision(decision,handoff)
    raw=encoded(decision); (d/'SESSION_DECISION.json').write_bytes(raw)
    stage=work.state['remote_stage']
    regression=(d/'HOST_EXTERNAL_REGRESSION.stdout').read_bytes()
    for name,content in [('SESSION_DECISION.json',raw),('SESSION_REGRESSION.txt',regression)]:
        work.step('COPY_'+name,['/usr/bin/python3','-I','-B','-c',PUT_FILE,stage+'/'+name,sha(content)],
                  remote=True,data=content,timeout=30)
    frozen=CP1_BASE+'/'+work.job+'/scripts/run-cp1-minimal-change.py'
    status=m.analyzer().parse(work.runner('CONTINUE_SESSION',frozen,'continue-session','--job',work.job,
             '--decision',stage+'/SESSION_DECISION.json','--decision-sha256',sha(raw),
             '--regression',stage+'/SESSION_REGRESSION.txt','--regression-sha256',sha(regression),
             '--trial',work.state['remote_trial'],'--trial-sha256',work.state['trial_sha256']))
    m.live_session_gate(status,record,decision)
    (d/'SESSION_LIVE.json').write_bytes(encoded(status))
    work.state.update(status='CP_SESSION_ACTIVE_REVIEW_PENDING',cp1_session_committed=True)
    work.save()
    return handoff,decision


def controller_output(work, phase, argv):
    failure=None
    try:
        raw=work.step(phase,argv,timeout=1500)
    except Exception as error:
        failure=error
        raw=(work.directory/(phase+'.stdout')).read_bytes()
    paths=re.findall(r'^OUTPUT=(.+)$',raw.decode(),re.M)
    need(len(paths)==1,'node evidence output missing; do not retry mutation')
    path=Path(paths[0])
    need(path.parent==CONTROLLER_HOME and re.fullmatch('FDS_Node_Firewall_[A-Za-z0-9_]+',path.name)
         and path.is_dir() and not path.is_symlink(),'unexpected node evidence path')
    work.state.setdefault('node_evidence',[]).append(str(path));work.save()
    # Preserve one consolidated submission while retaining original per-node outputs.
    files=list(path.rglob('*'))
    need(not any(p.is_symlink() for p in files) and len(files)<500 and
         sum(p.stat().st_size for p in files if p.is_file())<32*1024*1024,'node evidence unsafe/too large')
    shutil.copytree(path,work.directory/phase)
    if failure is not None:
        raise failure
    result=work.core.analyzer().parse((path/'RESULT.json').read_bytes())
    return path,result


def node_auth(work, target, inspection, inspection_sha, decision_sha, cp2_result):
    m=work.core
    helper=load_module('session_node_authority',SOURCE/'scripts/node-firewall-session.py')
    current=dt.datetime.now(UTC); cutoff=m.instant(work.state['withdraw_at'])
    end=min(current+dt.timedelta(seconds=7200),cutoff-dt.timedelta(seconds=420))
    need(current+dt.timedelta(seconds=420)<end,'insufficient session time for a target transaction')
    snapshot=inspection['snapshot']
    controls={p:{k:snapshot['planes'][p]['fields'][k] for k in ('forward','masquerade')}
              for p in ('runtime','permanent')}
    data=dict(schema='fds.node-firewall-authorization.v3',phase='runtime',source_commit=work.binding['source_commit'],
              target=target,mode='apply',operator='GWONUK',independent_reviewer='hayeounglee',
              review_decision='APPROVED',review_scope='SOURCE_AND_SESSION_DELEGATION',
              session_policy='fds.cp-session-policy.v1',review_url=work.binding['review_url'],
              reviewed_at=work.binding['reviewed_at'],operator_decision_url=work.binding['operator_decision_url'],
              operator_decided_at=work.binding['operator_decided_at'],independent_acceptance='PENDING',
              cp1_canary_accepted=False,cp1_continuation_authorized=True,target_state_validated=True,
              cp1_continuation_sha256=decision_sha,cp1_session_job=work.job,join_authorized=False,
              cp1_receipt_input_sha256=sha((work.directory/'ORIGINAL_RECEIPT.stdout').read_bytes()),
              window_start=current.isoformat(),window_end=end.isoformat(),inspection_sha256=inspection_sha,
              maintenance_freeze=True,console_recovery_available=True,backup_verified=True,protection_reviewed=True,
              rollback_runbook_reviewed=True,runtime_only_acknowledged=True,zone_controls=controls)
    for key in ('source_sha256','plan_sha256','policy_sha256','before_sha256','residual_metadata_sha256','cp1_state_sha256'):
        data[key]=inspection[key]
    if target=='k8s-control03':
        need(cp2_result is not None,'CP2 verified result required before CP3')
        data['cp2_result_sha256']=cp2_result
    helper.validate_authorization_record(data,work.binding['source_commit'],target,'apply')
    helper.validate_zone_controls(snapshot,controls)
    return data


def followup_nodes(work, handoff, decision):
    m=work.core; d=work.directory; cp2_result=None
    runner=SOURCE/'scripts/run-node-firewall-cni.py'
    previous=Path.cwd()
    try:
        os.chdir(CONTROLLER_HOME)
        for target in ('k8s-control02','k8s-control03'):
            prefix='CP2' if target.endswith('02') else 'CP3'
            work.state['nodes'][target]='INSPECTION';work.save()
            folder,result=controller_output(work,prefix+'_INSPECT',
                         [sys.executable,'-I','-B',str(runner),'inspect','--node',target])
            need(result.get('status')=='INSPECTED_REVIEW_REQUIRED' and result.get('hostname')==target and
                 result.get('source_commit')==work.binding['source_commit'],'target inspection incomplete')
            inspect_path=folder/'INSPECTION.json'; inspect_raw=inspect_path.read_bytes()
            inspection=m.analyzer().parse(inspect_raw)
            decision_path=d/'SESSION_DECISION.json';decision_sha=sha(decision_path.read_bytes())
            auth=node_auth(work,target,inspection,sha(inspect_raw),decision_sha,cp2_result)
            auth_path=d/(prefix+'_AUTHORIZATION.json');auth_path.write_bytes(encoded(auth))
            args=[sys.executable,'-I','-B',str(runner),'apply','--node',target,
                  '--inspection',str(inspect_path),'--inspection-sha256',sha(inspect_raw),
                  '--authorization',str(auth_path),'--authorization-sha256',sha(auth_path.read_bytes()),
                  '--cp1-receipt',str(d/'ORIGINAL_RECEIPT.stdout'),'--cp1-receipt-sha256',work.binding['original_receipt_sha256'],
                  '--cp1-handoff',str(d/'HANDOFF.json'),'--cp1-handoff-sha256',sha((d/'HANDOFF.json').read_bytes()),
                  '--cp1-acceptance',str(decision_path),'--cp1-acceptance-sha256',decision_sha,
                  '--cp1-trial',str(d/work.state['trial_archive']),'--cp1-trial-sha256',work.state['trial_sha256']]
            work.state['nodes'][target]='APPLY_ATTEMPTED_OUTCOME_PENDING';work.save()
            folder,result=controller_output(work,prefix+'_APPLY',args)
            need(result.get('status')=='RUNTIME_APPLIED_REVIEW_REQUIRED' and result.get('hostname')==target and
                 result.get('source_commit')==work.binding['source_commit'] and result.get('cp1_accepted') is False and
                 result.get('independent_acceptance')=='PENDING','target result requires investigation')
            wrapper=m.analyzer().parse((folder/'TARGET.json').read_bytes())
            request=m.analyzer().parse((folder/'REQUEST.json').read_bytes())
            helper=load_module('session_node_result',SOURCE/'scripts/node-firewall-session.py')
            payload=wrapper['result']; receipt=payload['receipt']
            need(helper.fingerprint(receipt)==payload['receipt_sha256'],'target receipt hash differs')
            helper.validate_receipt(receipt,request)
            need(receipt.get('cp1_continuation_sha256')==decision_sha and receipt.get('cp1_session_job')==work.job and
                 receipt.get('cp1_accepted') is False and receipt.get('independent_acceptance')=='PENDING',
                 'target continuation provenance differs')
            post=m.analyzer().parse((folder/'TARGET_AFTER.json').read_bytes())['result']
            need(post['snapshot']==receipt['after'],'target post-state differs from receipt')
            work.state['nodes'][target]='RUNTIME_APPLIED_REVIEW_REQUIRED';work.save()
            if target=='k8s-control02':
                cp2_result=payload['receipt_sha256']
    finally:
        os.chdir(previous)
    work.state.update(status='CP_FIREWALL_HANDOFF_REVIEW_PENDING',cp2_cp3_join='FIREWALL_APPLIED_JOIN_NOT_EXECUTED',
                      k8s_join_readiness='NOT_VERIFIED',independent_acceptance='PENDING')
    work.save()
    (d/'K8S_HANDOFF.json').write_bytes(encoded({
        'schema':'fds.cp-firewall-handoff.v1','source_commit':work.binding['source_commit'],
        'cp1_job':work.job,'cp1_handoff_sha256':m.fingerprint(handoff),
        'session_decision_sha256':sha((d/'SESSION_DECISION.json').read_bytes()),
        'cp1_accepted':False,'independent_acceptance':'PENDING','nodes':work.state['nodes'],
        'valid_before':work.state['withdraw_at'],'join_executed':False,
        'join_readiness':'NOT_VERIFIED','cross_node_packets':'NOT_TESTED',
        'reboot_persistence':'NOT_TESTED','permanent_promotion':'NOT_EXECUTED'}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--window-end')
    parser.add_argument('--recovery-margin',type=int)
    args=parser.parse_args()
    os.umask(0o077)
    manifest=verify_package()
    binding=json.loads((ROOT/'PACKAGE_BINDING.json').read_bytes())
    entry=load_module('session_entry',SOURCE/'scripts/run-cp1-minimal-change.py');core=entry.m
    need(core.fingerprint(entry.source_files())==binding['source_bundle_sha256'],'CP1 bundle differs')
    for name,expected in binding['trial_files'].items():
        need(sha((ROOT/'trial-kit'/name).read_bytes())==expected,'existing trial code differs')
    if not args.execute and not args.check:
        print(json.dumps({'mode':'PLAN_ONLY','source_commit':binding['source_commit'],
              'source_bundle_sha256':binding['source_bundle_sha256'],
              'review_required':'one real exact-head source/session-delegation APPROVED review',
              'workflow':['old recovery state','fresh CP1 inspect/apply','five-flow/regression',
                          'root continuation','CP2 inspect/apply/check','CP3 inspect/apply/check','one handoff archive'],
              'automatic_recovery':'one explicit staffed window end, no separate review-wait deadline',
              'runtime_executed':False,'cp1_accepted':False,'join_executed':False},indent=2))
        return 0
    need(os.geteuid()!=0 and pwd.getpwuid(os.getuid()).pw_name=='server01_ansible' and
         socket.gethostname().split('.')[0]=='ansible01','run on ansible01 as server01_ansible')
    authority=load_module('session_authority',ROOT/'cp_session_authority.py')
    review=authority.check(binding)
    if not args.execute:
        print(json.dumps({'status':'POLICY_DELEGATION_VERIFIED','review':review,'runtime_executed':False},indent=2))
        return 0
    print('현재 KST='+dt.datetime.now(KST).isoformat())
    if args.window_end is None:
        args.window_end=input('CP 작업·인계·복구를 마칠 당일 KST 종료시각 (HH:MM): ').strip()
    if args.recovery_margin is None:
        args.recovery_margin=int(input('복구 여유(초, 최소 180; 현장 대응 가능한 값): ').strip())
    end=parse_session_window(args.window_end,args.recovery_margin,dt.datetime.now(UTC))
    print('CP1/CP2/CP3의 기존 백업·콘솔·보호·복구 경로를 확인하고 지정 종료시각까지 운영자가 대응해야 합니다.')
    print('동시에 firewall/NM/Calico/reload/reboot/join/reset 작업을 실행하지 않습니다.')
    print('실행 시 GWONUK 계정으로 소스·대상·작업창 결정 댓글을 한 번 기록합니다. 결과의 독립 인수는 대기입니다.')
    need(input('확인했다면 ICR-CP-SESSION-GO 입력: ').strip()=='ICR-CP-SESSION-GO','operator conditions unconfirmed')
    # Held through finish(); OS releases it on all failures and process exit.
    execution_lock=(ROOT/'EXECUTION.lock').open('a')
    fcntl.flock(execution_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    need(not (ROOT/'EXECUTION_ATTEMPT.json').exists(),'this package already has an execution attempt; inspect evidence before retry')
    directory=CONTROLLER_HOME/('ICR_CP_Session_'+dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(4))
    directory.mkdir(mode=0o700)
    decision=authority.record_operator(binding,review,end,args.recovery_margin,directory)
    binding.update(review);binding.update(decision)
    shutil.copy2(ROOT/'PACKAGE_SHA256SUMS',directory/'PACKAGE_SHA256SUMS')
    (directory/'EXECUTION_BINDING.json').write_bytes(encoded(binding))
    work=Work(binding,core,directory,manifest);success=False
    try:
        work.run(end,args.recovery_margin)
        handoff,continuation_decision=continuation(work)
        followup_nodes(work,handoff,continuation_decision)
        success=True
    except (Exception,KeyboardInterrupt) as error:
        work.state.update(status='STOP_REVIEW_REQUIRED',error=str(error))
        print('[STOP] '+str(error),file=sys.stderr)
    finally:
        try:work.finish(success)
        finally:execution_lock.close()
    return 0 if success else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Exception,KeyboardInterrupt) as error:
        print('[STOP] '+str(error),file=sys.stderr)
        raise SystemExit(2)
