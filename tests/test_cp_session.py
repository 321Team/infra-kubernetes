"""Session policy/transaction/orchestration boundaries. STATIC/MOCK only."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from test_cp1_minimal_change import m, e, c, a, BUNDLE, CLOCK, FakeIO, baseline, fixture, pack, MemoryJournal
from test_node_firewall import load, FakeBackend
import test_node_firewall as node_tests
from test_cp1_operator_exception import REGRESSION

w = load('session_work_test', 'tools/RUN_CP_SESSION.py')
authority = load('session_authority_test', 'tools/cp_session_authority.py')
n = load('session_node_test', 'scripts/node-firewall-session.py')
ROOT = Path(__file__).resolve().parents[1]


def binding():
    return dict(source_commit='a'*40, source_bundle_sha256=BUNDLE,review_pr=14,
                review_url='https://github.com/321Team/infra-kubernetes/pull/14#pullrequestreview-123',
                reviewed_at=(CLOCK-dt.timedelta(minutes=2)).isoformat(),
                operator_decision_url='https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-124',
                operator_decided_at=(CLOCK-dt.timedelta(minutes=1)).isoformat())


class SessionFixture(unittest.TestCase):
    def setUp(self):
        self.base,self.original=baseline(); self.io=FakeIO(self.base);self.journal=MemoryJournal()
        self.io.directory=None;self.io.timer_active=lambda retained=False:self.io.armed
        self.auth=w.session_auth(binding(),{'before_sha256':m.fingerprint(self.base),'created_at':CLOCK.isoformat()},
                                 '2026-09-15T08:00:00+00:00',1800)
        fstat=e.os.fstat
        def owner(fd):
            st=fstat(fd);return e.os.stat_result(tuple(st[:4])+(0,)+tuple(st[5:]))
        p=patch.object(e.os,'fstat',side_effect=owner);p.start();self.addCleanup(p.stop)

    def evidence(self):
        record=m.apply(self.io,self.journal,self.original,self.auth,BUNDLE)
        request,wrapper=fixture()
        wrapper['result']['firewall_before']=copy.deepcopy(record['after']['firewall'])
        wrapper['result']['firewall_after']=copy.deepcopy(record['after']['firewall'])
        request['kit_hashes']['expected-firewall.json']=a.sha(a.encoded(record['after']['firewall']))
        wrapper['kit_hashes']=copy.deepcopy(request['kit_hashes'])
        self.io.clock=CLOCK+dt.timedelta(minutes=2)
        handoff=m.receipt(record,self.original,request,wrapper,BUNDLE,self.io.clock)
        raw=pack(request,wrapper)
        decision=dict(schema='fds.cp-session-continuation.v1',operator='GWONUK',decision='CONTINUE_UNDER_DELEGATION',
                      cp1_accepted=False,independent_acceptance='PENDING',targets=m.SESSION_TARGETS.copy(),
                      join_authorized=False,operator_decision_url=self.auth['operator_decision_url'],
                      job_id=record['job_id'],source_bundle_sha256=BUNDLE,handoff_sha256=m.fingerprint(handoff),
                      expires_at=record['withdraw_at'],decided_at=self.io.clock.isoformat(),
                      checks={k:dict(status='PASS',evidence_sha256=v) for k,v in [
                          ('positive',m.digest(raw)),('negative',m.fingerprint(handoff['trial_report'])),
                          ('regression',m.digest(REGRESSION))]})
        return record,handoff,decision,raw

    def prepare(self,directory):
        record,handoff,decision,raw=self.evidence();self.io.directory=Path(directory)
        for name,value in [('CHANGE.json',record),('HANDOFF.json',handoff),('ORIGINAL.json',self.original)]:
            (self.io.directory/name).write_bytes(m.encoded(value))
        path=self.io.directory/'trial.tar.gz';path.write_bytes(raw)
        return record,handoff,decision,path,m.digest(raw)

    def commit(self,values,regression=REGRESSION):
        record,handoff,decision,path,digest=values
        return e.commit_session(self.io,record,BUNDLE,decision,path,digest,regression)


class SessionBoundaries(SessionFixture):
    def test_one_final_timer_before_mutation_no_separate_review_deadline(self):
        record,_,_,_=self.evidence()
        self.assertEqual(self.io.actions[0],'arm')
        self.assertEqual(self.io.actions.count('arm'),1)
        self.assertEqual(record['withdraw_at'],'2026-09-15T07:30:00+00:00')
        self.assertNotIn('retained_withdraw_at',record)
        self.assertFalse(record['cp1_accepted'])
        self.assertEqual(record['after']['objects']['permanent'],self.base['objects']['permanent'])

    def test_source_delegation_is_mandatory_before_arm(self):
        for key,value in [('review_decision','PENDING'),('review_scope','SOURCE_ONLY'),
                          ('independent_reviewer','GWONUK'),('delegated_targets',['k8s-worker01']),
                          ('session_policy','unknown'),('independent_acceptance','ACCEPTED'),
                          ('operator_present_until_end',False),('reviewed_bundle_sha256','f'*64)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.apply(self.io,self.journal,self.original,dict(self.auth,**{key:value}),BUNDLE)
        self.assertEqual(self.io.actions,[])

    def test_bad_window_margin_future_decision_and_overnight_are_rejected(self):
        for key,value in [('recovery_margin_seconds',True),('recovery_margin_seconds',179),
                          ('window_end','2026-09-16T08:00:00+00:00'),('window_end',CLOCK.isoformat()),
                          ('operator_decided_at',(CLOCK+dt.timedelta(minutes=1)).isoformat()),
                          ('window_end','2026-09-15T08:00:00')]:
            with self.subTest(key=key),self.assertRaises((ValueError,TypeError)):
                m.check_authorization(dict(self.auth,**{key:value}),self.base,BUNDLE,CLOCK)

    def test_final_timer_setup_failure_leaves_baseline(self):
        self.io.fail=1
        with self.assertRaises(OSError):m.apply(self.io,self.journal,self.original,self.auth,BUNDLE)
        self.assertFalse(self.io.armed);self.assertEqual(self.io.snapshot(),self.base)
        self.assertFalse(any(x.startswith('stage') for x in self.io.actions))

    def test_old_v2_v3_jobs_cannot_be_adopted_by_continuation(self):
        record,handoff,decision,_=self.evidence()
        for version in ('v1','v2','v3'):
            changed=dict(record,schema='fds.cp1-minimal-change.'+version)
            changed_handoff=dict(handoff,change=changed)
            with self.subTest(version=version),self.assertRaises(ValueError):
                m.validate_session_decision(dict(decision,handoff_sha256=m.fingerprint(changed_handoff)),changed_handoff,self.io.clock)

    def test_decision_cannot_claim_acceptance_expand_targets_or_change_job(self):
        _,handoff,decision,_=self.evidence()
        for key,value in [('cp1_accepted',True),('independent_acceptance','ACCEPTED'),('join_authorized',True),
                          ('targets',['k8s-control02','k8s-control03','k8s-worker01']),('job_id','f'*16),
                          ('expires_at','2027-01-01T00:00:00+00:00'),('independent_reviewer','hayeounglee')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.validate_session_decision(dict(decision,**{key:value}),handoff,self.io.clock)

    def test_root_commit_preserves_handoff_and_true_pending_state(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);before={n:(Path(directory)/n).read_bytes() for n in ('CHANGE.json','HANDOFF.json')}
            result=self.commit(values)
            self.assertEqual(result['status'],m.SESSION_ACTIVE);self.assertFalse(result['cp1_accepted'])
            self.assertEqual(result['independent_acceptance'],'PENDING')
            m.live_session_gate(result,values[0],values[2],self.io.clock)
            for name,raw in before.items():self.assertEqual((Path(directory)/name).read_bytes(),raw)
            self.assertFalse((Path(directory)/'ACCEPTANCE.json').exists())

    def test_proof_is_last_commit_marker_and_incomplete_write_does_not_authorize_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);original=e.atomic
            def interrupt(path,raw):
                if path.name=='SESSION_PROOF.json':raise OSError('MOCK interrupted commit')
                original(path,raw)
            with patch.object(e,'atomic',side_effect=interrupt),self.assertRaises(OSError):self.commit(values)
            self.assertIsNone(e.load_session(self.io,values[0]))
            self.assertEqual(e.session_status(self.io,values[0])['status'],'ACTIVE_REVIEW_REQUIRED')

    def test_regression_bytes_and_trial_hash_must_be_actual(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory)
            with self.assertRaises(ValueError):self.commit(values,REGRESSION.replace(b'PASS',b'FAIL',1))
            bad=list(values);bad[2]=copy.deepcopy(values[2]);bad[2]['checks']['positive']['evidence_sha256']='a'*64
            with self.assertRaises(ValueError):self.commit(bad)
            self.assertFalse((Path(directory)/'SESSION_PROOF.json').exists())

    def test_tampered_root_proof_blocks_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            path=Path(directory)/'SESSION_PROOF.json';proof=json.loads(path.read_text());proof['withdraw_at']='2099-01-01T00:00:00+00:00'
            path.write_bytes(m.encoded(proof))
            with self.assertRaises(ValueError):e.session_status(self.io,values[0])

    def test_retry_does_not_extend_window_or_change_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);first=self.commit(values);actions=len(self.io.actions)
            second=self.commit(values)
            self.assertEqual(first['withdraw_at'],second['withdraw_at']);self.assertEqual(actions,len(self.io.actions))
            changed=list(values);changed[2]=dict(values[2],decided_at=(self.io.clock+dt.timedelta(seconds=1)).isoformat())
            with self.assertRaises(ValueError):self.commit(changed)

    def test_timer_missing_drift_or_late_target_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);status=self.commit(values)
            for changed in (dict(status,recovery_armed=False),dict(status,current=self.base),dict(status,source_bundle_sha256='f'*64)):
                with self.assertRaises(ValueError):m.live_session_gate(changed,values[0],values[2],self.io.clock)
            with self.assertRaises(ValueError):
                m.live_session_gate(status,values[0],values[2],self.io.clock,required_until=values[0]['withdraw_at'])

    def test_late_commit_has_no_valid_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);original=e.atomic
            def delayed(path,raw):
                original(path,raw)
                if path.name=='SESSION_PROOF.json':self.io.clock=m.instant(values[0]['withdraw_at'])
            with patch.object(e,'atomic',side_effect=delayed),self.assertRaises(ValueError):self.commit(values)
            self.assertFalse((Path(directory)/'SESSION_PROOF.json').exists())
            self.assertTrue((Path(directory)/'SESSION_PROOF_LATE.json').exists())

    def test_final_recovery_ignores_continuation_and_restores_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values);self.io.clock=m.instant(values[0]['withdraw_at'])
            restored=m.recover(self.io,self.journal,copy.deepcopy(values[0]))
            self.assertTrue(restored['cleanup_complete']);self.assertEqual(self.io.snapshot(),self.base)

    def test_full_consumer_replays_trial_and_checks_live_root_pending_state(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);status=self.commit(values);record,handoff,decision,trial,digest=values
            rebuilt=c.validate(self.original,handoff,decision,trial,digest,clock=self.io.clock)
            self.assertEqual(rebuilt,record)
            ctl=Mock();state={'stable':{'firewall':record['after']['firewall'],'protection':record['after']['protection']}}
            with patch.object(c,'remote_status',return_value=status),patch.object(c.m,'now',return_value=self.io.clock):
                self.assertEqual(c.before_or_after(ctl,state,'a'*64,record,acceptance=decision)['status'],m.SESSION_ACTIVE)
            bad=copy.deepcopy(decision);bad['checks']['positive']['evidence_sha256']='f'*64
            with self.assertRaises(ValueError):c.validate(self.original,handoff,bad,trial,digest,clock=self.io.clock)


class AdapterSessionFlow(SessionFixture):
    def exercise(self, failure=None):
        import contextlib,io,os
        self.io=FakeIO(self.base);self.journal=MemoryJournal()
        self.io.directory=None;self.io.timer_active=lambda retained=False:self.io.armed
        adapter=load('session_adapter_integration','scripts/run-node-firewall-cni.py')
        helper=adapter.node;target='k8s-control02';calls=[];live_calls=[]
        backend=FakeBackend(node_tests.MemoryJournal(),target);before=backend.snapshot()
        topology=node_tests.Gates().topology()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);values=self.prepare(root);status=self.commit(values)
            record,handoff,decision,trial,trial_sha=values
            stable={'firewall':record['after']['firewall'],'protection':record['after']['protection']}
            state={'stable':stable,'sha256':helper.fingerprint(stable),'status':'CP1_STATE_REVIEW_REQUIRED'}
            inspection=dict(schema='fds.node-firewall-input.v2',phase='runtime',hostname=target,
                created_at=self.io.clock.isoformat(),source_commit='a'*40,source_sha256=adapter.digest(adapter.NODE_SOURCE),
                plan_sha256=helper.plan_hash(target),policy_sha256=helper.policy_hash(),snapshot=before,
                before_sha256=helper.fingerprint(before),residual_metadata_sha256=helper.fingerprint(before['residual_metadata']),
                topology=topology['stable'],topology_sha256=topology['sha256'],cp1_state=stable,cp1_state_sha256=state['sha256'])
            args=['run-node-firewall-cni.py','apply','--node',target]
            def save(name,data):
                raw=helper.encoded(data);path=root/(name+'.json');path.write_bytes(raw)
                digest=m.digest(raw);args.extend(['--'+name,str(path),'--'+name+'-sha256',digest]);return digest
            inspection_sha=save('inspection',inspection);original_sha=save('cp1-receipt',self.original)
            decision_sha=save('cp1-acceptance',decision);save('cp1-handoff',handoff)
            args.extend(['--cp1-trial',str(trial),'--cp1-trial-sha256',trial_sha])
            auth=NodeSessionBoundaries().auth(target)
            auth.update(window_start=(self.io.clock-dt.timedelta(seconds=1)).isoformat(),
                        window_end=(self.io.clock+dt.timedelta(minutes=20)).isoformat(),
                        reviewed_at=binding()['reviewed_at'],operator_decided_at=binding()['operator_decided_at'],
                        inspection_sha256=inspection_sha,cp1_receipt_input_sha256=original_sha,
                        cp1_continuation_sha256=decision_sha,cp1_session_job=record['job_id'],
                        zone_controls={p:{'forward':'yes','masquerade':'no'} for p in ('runtime','permanent')})
            for key in ('source_sha256','plan_sha256','policy_sha256','before_sha256','residual_metadata_sha256','cp1_state_sha256'):
                auth[key]=inspection[key]
            if failure=='false_acceptance':auth['cp1_canary_accepted']=True
            save('authorization',auth)
            def remote(name,request):
                mode=request['mode'];calls.append((name,mode))
                if mode=='topology':data=dict(topology,status='TOPOLOGY_REVIEW_REQUIRED')
                elif mode=='cp1-state':data=state
                elif mode=='apply':
                    receipt=helper.apply_transaction(backend,backend.journal,request)
                    data=dict(status=receipt['status'],receipt=receipt,receipt_sha256=helper.fingerprint(receipt))
                elif mode=='inspect':data=dict(status='INSPECTED_REVIEW_REQUIRED',snapshot=backend.snapshot())
                else:raise AssertionError(mode)
                return {'transport':'PASS','result':data}
            def live(ctl,rec):
                live_calls.append(rec['job_id']);result=copy.deepcopy(status)
                if failure=='before' or failure=='after' and len(live_calls)==2:result['recovery_armed']=False
                return result
            clock=self.io.clock
            class FixedDateTime(dt.datetime):
                @classmethod
                def now(cls,tz=None):return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)
            previous=Path.cwd()
            try:
                os.chdir(root)
                with (patch('sys.argv',args),patch.object(adapter,'verify_source',return_value='a'*40),
                      patch.object(adapter,'preflight',return_value={}),patch.object(adapter,'ControllerLock'),
                      patch.object(adapter,'remote_request',side_effect=remote),patch.object(adapter.cni,'remote_status',side_effect=live),
                      patch.object(adapter.cni.m,'now',return_value=clock),patch.object(dt,'datetime',FixedDateTime),
                      contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as out):
                    adapter.main()
                output=next(p for p in root.glob('FDS_Node_Firewall_*') if p.is_dir())
                result=json.loads((output/'RESULT.json').read_bytes())
                receipt=None
                if (output/'TARGET.json').exists():receipt=json.loads((output/'TARGET.json').read_bytes())['result']['receipt']
                self.assertFalse((output/'CP1_ACCEPTANCE.json').exists())
            finally:os.chdir(previous)
        return out.exception.code,result,calls,live_calls,receipt

    def test_real_adapter_continuation_runs_six_rules_without_fabricating_acceptance(self):
        rc,result,calls,live,receipt=self.exercise()
        self.assertEqual(rc,0,result);self.assertEqual(len(live),2)
        self.assertEqual([v for v in calls if v[1]=='apply'],[('k8s-control02','apply')])
        self.assertEqual(len(receipt['operations']),6);self.assertFalse(receipt['cp1_accepted'])
        self.assertEqual(result['independent_acceptance'],'PENDING');self.assertFalse(result['cp1_accepted'])

    def test_real_adapter_rejects_false_acceptance_before_target(self):
        rc,result,calls,live,receipt=self.exercise('false_acceptance')
        self.assertEqual(rc,2,result);self.assertFalse(any(v[1]=='apply' for v in calls));self.assertIsNone(receipt)

    def test_actual_live_check_failure_blocks_target_or_final_success(self):
        for when in ('before','after'):
            with self.subTest(when=when):
                rc,result,calls,live,receipt=self.exercise(when)
                self.assertEqual(rc,2,result);self.assertEqual(result['status'],'STOP_REVIEW_REQUIRED')
                self.assertEqual(sum(mode=='apply' for _,mode in calls),0 if when=='before' else 1)


class ActualAuthorityBoundaries(unittest.TestCase):
    def records(self):
        b=binding();pr={'number':14,'head':{'sha':b['source_commit']},'base':{'repo':{'full_name':'321Team/infra-kubernetes'}}}
        review={'id':123,'user':{'login':'hayeounglee'},'state':'APPROVED','commit_id':b['source_commit'],
                'submitted_at':b['reviewed_at'],'html_url':b['review_url'],
                'body':'CP_SESSION_DELEGATION_APPROVED / source '+b['source_commit']}
        return b,pr,review

    def test_one_exact_head_real_approval_covers_declared_policy(self):
        b,pr,r=self.records();self.assertEqual(authority.approval(pr,[r],b,CLOCK)['reviewer'],'hayeounglee')

    def test_self_review_empty_approval_old_head_and_later_rejection_fail(self):
        b,pr,r=self.records()
        for changed in (dict(r,user={'login':'GWONUK'}),dict(r,body='승인합니다'),dict(r,commit_id='b'*40),dict(r,state='DISMISSED')):
            with self.assertRaises(ValueError):authority.approval(pr,[changed],b,CLOCK)
        denied=dict(r,id=124,state='CHANGES_REQUESTED',submitted_at=CLOCK.isoformat())
        with self.assertRaises(ValueError):authority.approval(pr,[r,denied],b,CLOCK)

    def test_missing_review_does_not_record_operator_or_touch_server(self):
        b,pr,r=self.records()
        def api(endpoint,*args,**kwargs):
            if endpoint=='user':return {'login':'GWONUK'}
            if '/reviews?' in endpoint:return []
            return pr
        with patch.object(authority,'api',side_effect=api) as call,self.assertRaises(ValueError):authority.check(b,CLOCK)
        self.assertTrue(all(len(v.args)==1 for v in call.call_args_list))

    def test_operator_comment_is_verified_and_not_review_result(self):
        b,pr,r=self.records();review=authority.approval(pr,[r],b,CLOCK)
        def api(endpoint,method,payload):
            self.assertEqual(method,'POST');self.assertIn('independent_acceptance=PENDING',payload['body'])
            return {'body':payload['body'],'user':{'login':'GWONUK'},'html_url':b['operator_decision_url'],
                    'created_at':b['operator_decided_at']}
        with tempfile.TemporaryDirectory() as d,patch.object(authority,'api',side_effect=api):
            result=authority.record_operator(b,review,'2026-09-15T08:00:00+00:00',1800,Path(d))
            self.assertEqual(result['operator_decision_url'],b['operator_decision_url'])


class NodeSessionBoundaries(unittest.TestCase):
    def setUp(self):
        fixed=patch.object(dt,'datetime',node_tests.FixedWeekday)
        fixed.start();self.addCleanup(fixed.stop)

    def auth(self,target='k8s-control02'):
        now=dt.datetime.now(dt.timezone.utc)
        data=dict(schema='fds.node-firewall-authorization.v3',phase='runtime',source_commit='a'*40,target=target,mode='apply',
                  operator='GWONUK',independent_reviewer='hayeounglee',review_decision='APPROVED',
                  review_scope='SOURCE_AND_SESSION_DELEGATION',session_policy=m.SESSION_POLICY,
                  independent_acceptance='PENDING',cp1_canary_accepted=False,cp1_continuation_authorized=True,
                  target_state_validated=True,join_authorized=False,cp1_continuation_sha256='c'*64,cp1_session_job='d'*16,
                  review_url=binding()['review_url'],operator_decision_url=binding()['operator_decision_url'],
                  reviewed_at=(now-dt.timedelta(minutes=2)).isoformat(),operator_decided_at=(now-dt.timedelta(minutes=1)).isoformat(),
                  window_start=(now-dt.timedelta(seconds=1)).isoformat(),window_end=(now+dt.timedelta(minutes=20)).isoformat(),
                  policy_sha256=n.policy_hash(),plan_sha256=n.plan_hash(target),source_sha256='b'*64,before_sha256='b'*64,
                  residual_metadata_sha256='b'*64)
        for key in ('maintenance_freeze','console_recovery_available','backup_verified','protection_reviewed',
                    'rollback_runbook_reviewed','runtime_only_acknowledged'):data[key]=True
        return data

    def test_delegate_can_prepare_cp2_without_claiming_cp1_acceptance(self):
        data=self.auth();n.validate_authorization_record(data,'a'*40,data['target'],'apply')
        self.assertFalse(data['cp1_canary_accepted'])

    def test_cp3_needs_cp2_result_and_worker_expansion_is_rejected(self):
        data=self.auth('k8s-control03')
        with self.assertRaises(ValueError):n.validate_authorization_record(data,'a'*40,data['target'],'apply')
        data['cp2_result_sha256']='a'*64;n.validate_authorization_record(data,'a'*40,data['target'],'apply')
        data=self.auth('k8s-worker01')
        with self.assertRaises(ValueError):n.validate_authorization_record(data,'a'*40,data['target'],'apply')

    def test_pending_review_not_allowed_to_enter_legacy_acceptance_path(self):
        data=self.auth();data['schema']='fds.node-firewall-authorization.v2'
        with self.assertRaises(ValueError):n.validate_authorization_record(data,'a'*40,data['target'],'apply')

    def test_target_policy_and_transactions_unchanged_and_receipt_truthful(self):
        for target in ('k8s-control02','k8s-control03'):
            backend=FakeBackend(MemoryJournal(),target);before=backend.snapshot()
            req=node_tests.Transactions().request_for(backend);req.update(cp1_acceptance_sha256=None,cp1_continuation_sha256='c'*64,
                                                           authorization={'cp1_session_job':'d'*16})
            receipt=n.apply_transaction(backend,backend.journal,req)
            self.assertEqual(receipt['status'],'RUNTIME_APPLIED_REVIEW_REQUIRED')
            self.assertEqual(len(backend.edits),6);self.assertFalse(receipt['cp1_accepted'])
            self.assertIsNone(receipt['cp1_acceptance_sha256']);self.assertEqual(receipt['independent_acceptance'],'PENDING')
            self.assertEqual(n.rollback_transaction(backend,backend.journal,receipt,req)['status'],'ROLLED_BACK_REVIEW_REQUIRED')
            self.assertEqual(before,backend.snapshot())


class WrapperBoundaries(unittest.TestCase):
    def test_read_only_old_state_failure_does_not_consume_execution(self):
        b=dict(binding(),trial_source_commit='a'*40,old_job='b'*16,old_source_bundle_sha256='c'*64)
        with tempfile.TemporaryDirectory() as temp,patch.object(w,'ROOT',Path(temp)):
            root=Path(temp);(root/'trial-source').mkdir()
            work=w.Work(b,m,root,{})
            record=dict(job_id=b['old_job'],source_bundle_sha256=b['old_source_bundle_sha256'],status='ACTIVE_REVIEW_REQUIRED')
            with patch.object(work,'step',side_effect=[b'{}',b'{}',m.encoded(record)]) as step,self.assertRaises(ValueError):
                work.run('2026-09-15T08:00:00+00:00',1800)
            self.assertFalse((root/'EXECUTION_ATTEMPT.json').exists())
            self.assertFalse(any(call.args[0]=='APPLY' for call in step.call_args_list))

    def test_failed_node_output_is_preserved_in_partial_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'home';home.mkdir();directory=root/'evidence';directory.mkdir()
            node=home/'FDS_Node_Firewall_mock';node.mkdir()
            (node/'RESULT.json').write_text('{"status":"STOP_REVIEW_REQUIRED"}')
            (directory/'CP2_APPLY.stdout').write_text('OUTPUT='+str(node)+'\n')
            work=Mock();work.directory=directory;work.state={};work.step.side_effect=RuntimeError('mock failed')
            with patch.object(w,'CONTROLLER_HOME',home),self.assertRaises(RuntimeError):
                w.controller_output(work,'CP2_APPLY',[])
            self.assertEqual((directory/'CP2_APPLY/RESULT.json').read_bytes(),(node/'RESULT.json').read_bytes())

    def test_no_rollover_or_implicit_time_defaults(self):
        self.assertEqual(w.parse_session_window('17:00',1800,CLOCK),'2026-09-15T17:00:00+09:00')
        for end,margin in [('04:00',1800),('17:00',True),('2026-09-16T17:00:00+09:00',1800)]:
            with self.assertRaises(ValueError):w.parse_session_window(end,margin,CLOCK)

    def test_old_job_must_be_actually_restored_not_merely_expired(self):
        data={'job_id':'a'*16,'source_bundle_sha256':'b'*64,'status':'ACTIVE_REVIEW_REQUIRED'}
        with self.assertRaises(ValueError):w.validate_old({},data,{'old_job':'a'*16,'old_source_bundle_sha256':'b'*64},m)

    def test_missing_manifest_entries_rejected(self):
        with tempfile.TemporaryDirectory() as d,patch.object(w,'ROOT',Path(d)):
            (Path(d)/'PACKAGE_SHA256SUMS').write_text('')
            with self.assertRaises(ValueError):w.verify_package()

    def test_cp2_failure_prevents_cp3_and_merged_claims(self):
        work=Mock();work.core=m;work.directory=Path('/tmp');work.state={'nodes':{}}
        work.binding=binding()
        with (patch.object(w,'CONTROLLER_HOME',Path.cwd()),
              patch.object(w,'controller_output',side_effect=ValueError('MOCK CP2 failed')) as call,
              self.assertRaises(ValueError)):
            w.followup_nodes(work,{}, {})
        self.assertEqual(call.call_count,1);self.assertNotIn('k8s-control03',work.state['nodes'])
