"""Explicit new-handoff consumer. The original receipt and controller stay intact."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


m = load('consumer_minimal_core', 'cp1_minimal_change.py')
entry = load('consumer_minimal_entry', 'run-cp1-minimal-change.py')


def validate(original, handoff, acceptance, trial_path, trial_sha256, clock=None):
    request, wrapper, _ = m.analyzer().archive_inputs(trial_path, trial_sha256)
    record = m.validate_handoff(handoff, original, request, wrapper, m.fingerprint(entry.source_files()), clock, acceptance)
    if acceptance.get('schema') == 'fds.cp-session-continuation.v1':
        m.validate_session_decision(acceptance, handoff, clock)
        m.need(acceptance['checks']['positive']['evidence_sha256'] == trial_sha256 and
               acceptance['checks']['negative']['evidence_sha256'] == m.fingerprint(handoff['trial_report']),
               'session trial evidence differs')
    else:
        m.validate_acceptance(acceptance, handoff, clock)
    return record


def remote_status(controller, record):
    # First use the unchanged approved transport's CP1 identity validation.
    proof = controller.remote_request(m.HOST, {'mode': 'cp1-state', 'node': m.HOST})
    controller.require_result(proof, 'CP1_STATE_REVIEW_REQUIRED')
    job = record['job_id']
    m.need(controller.re.fullmatch('[0-9a-f]{16}', job), 'invalid CP1 job id')
    files = entry.source_files()
    # Read-only status invocation from a root-owned frozen checkout. No source
    # upload, arbitrary paths, shell interpolation, policy mutation or self-approval.
    code = '''import os,stat,json,hashlib,subprocess,sys
from pathlib import Path
job=Path('/var/lib/fds/cp1-minimal')/JOB
for p in reversed([job]+list(job.parents)):
 s=p.lstat()
 if not(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022): raise SystemExit('untrusted root path')
for name,digest in FILES.items():
 p=job/'scripts'/name
 for q in (p.parent,p):
  s=q.lstat()
  if s.st_uid!=0 or s.st_mode&0o022 or stat.S_ISLNK(s.st_mode): raise SystemExit('untrusted frozen source')
 if hashlib.sha256(p.read_bytes()).hexdigest()!=digest: raise SystemExit('frozen source differs')
r=subprocess.run(['/usr/bin/python3','-I','-B',str(job/'scripts/run-cp1-minimal-change.py'),'status','--job',JOB],capture_output=True,text=True,timeout=180)
sys.stdout.write(r.stdout);sys.stderr.write(r.stderr);raise SystemExit(r.returncode)
'''
    payload = 'JOB=' + repr(job) + '\nFILES=' + repr(files) + '\n' + code
    result = controller.run(controller.ssh_argv(m.HOST, ['sudo', '-n', '/usr/bin/python3', '-I', '-']),
                            input=payload, timeout=200)
    m.need(result.returncode == 0 and not result.stderr.strip(), 'CP1 live status unavailable')
    status = m.analyzer().parse(result.stdout.encode())
    return status


def before_or_after(controller, state, expected, record, minimum_seconds=0, acceptance=None, required_until=None):
    # Keep the original whole stable-state fingerprint comparison, then replace
    # only its old-receipt expectation with the independently validated new state.
    controller.cp1_state_gate(state, expected)
    m.need(state['stable']['firewall'] == record['after']['firewall'] and
           state['stable']['protection'] == record['after']['protection'], 'CP1 stable state differs from new receipt')
    status = remote_status(controller, record)
    if acceptance is not None and acceptance.get('schema') == 'fds.cp-session-continuation.v1':
        m.live_session_gate(status, record, acceptance, required_until=required_until)
    else:
        m.live_gate(status, record, minimum_seconds, acceptance=acceptance, required_until=required_until)
    return status
