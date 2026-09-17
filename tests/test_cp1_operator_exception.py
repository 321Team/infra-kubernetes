"""Operator exception candidate: STATIC/MOCK only; no real approvals or Runtime."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_cp1_minimal_change import m, e, c, a, BUNDLE, CLOCK, fixture, pack
from test_cp1_work_window import WorkFixture

REGRESSION = (b'MANAGEMENT_SSH=PASS\nENS160_ZONE=public\nKUBE_APISERVER_READYZ=PASS\n'
              b'CP1_NODE_READY=PASS\nEXTERNAL_DNS_IPV4=PASS\nEXTERNAL_HTTPS_REGISTRY_401=PASS\n'
              b'HOST_EXTERNAL_REGRESSION=PASS\n')


class OperatorFixture(WorkFixture):
    def setUp(self):
        super().setUp()
        self.auth.update(schema='fds.cp1-minimal-authorization.v3',
                         operator_exception_policy=m.EXCEPTION_POLICY,
                         operator_exception_permitted=True, exception_operator='GWONUK')
        fstat=e.os.fstat
        def root_owner(fd):
            st=fstat(fd)
            return e.os.stat_result(tuple(st[:4])+(0,)+tuple(st[5:]))
        owner=patch.object(e.os,'fstat',side_effect=root_owner)
        self.addCleanup(owner.stop);owner.start()

    def evidence(self):
        record=self.apply()
        request,wrapper=fixture()
        wrapper['result']['firewall_before']=copy.deepcopy(record['after']['firewall'])
        wrapper['result']['firewall_after']=copy.deepcopy(record['after']['firewall'])
        request['kit_hashes']['expected-firewall.json']=a.sha(a.encoded(record['after']['firewall']))
        wrapper['kit_hashes']=copy.deepcopy(request['kit_hashes'])
        self.io.clock=CLOCK+dt.timedelta(minutes=2)
        handoff=m.receipt(record,self.original,request,wrapper,BUNDLE,self.io.clock)
        raw=pack(request,wrapper)
        exception=dict(schema='fds.cp1-operator-exception.v1',decision='OPERATOR_AUTHORIZED',
                       operator='GWONUK',independent_review_status='PENDING',cp1_accepted=False,
                       post_review_required=True,reason_code='REVIEWER_UNAVAILABLE',reason='MOCK reviewer unavailable',
                       authority_url='https://github.com/321Team/infra-kubernetes/pull/42#issuecomment-123',
                       authorized_at=self.io.clock.isoformat(),job_id=record['job_id'],
                       source_bundle_sha256=BUNDLE,handoff_sha256=m.fingerprint(handoff),
                       expires_at=record['retained_withdraw_at'],
                       scope={'hostname':m.HOST,'action':'retain-existing-cp1','plane':'runtime',
                              'pod_block':m.BLOCK,'interfaces':'cali+','l4':'ALL'},
                       checks={key:dict(status='PASS',evidence_sha256=digest) for key,digest in
                               [('positive',m.digest(raw)),('negative',m.fingerprint(handoff['trial_report'])),
                                ('regression',m.digest(REGRESSION))]})
        proof=m.exception_retention_proof(record,handoff,exception,self.io.clock)
        return record,request,wrapper,handoff,exception,proof,raw

    def prepare(self,directory):
        record,request,wrapper,handoff,exception,proof,raw=self.evidence()
        self.io.directory=Path(directory)
        for name,data in [('CHANGE.json',record),('HANDOFF.json',handoff),('ORIGINAL.json',self.original)]:
            (self.io.directory/name).write_bytes(m.encoded(data))
        trial=self.io.directory/'trial.tar.gz';trial.write_bytes(raw)
        return record,handoff,exception,proof,trial,m.digest(raw)

    def commit(self,values,regression=REGRESSION):
        record,handoff,exception,proof,trial,digest=values
        return e.commit_operator_exception(self.io,record,BUNDLE,exception,trial,digest,regression)

    def accepted(self,record,handoff):
        return dict(schema='fds.cp1-minimal-acceptance.v3',decision='ACCEPTED',
                    independent_reviewer='hayeounglee',review_decision='APPROVED',retention_approved=True,
                    review_url='https://github.com/321Team/infra-kubernetes/pull/42#pullrequestreview-123',
                    reviewed_at=self.io.clock.isoformat(),handoff_sha256=m.fingerprint(handoff),
                    source_bundle_sha256=BUNDLE,expires_at=record['retained_withdraw_at'],
                    checks={key:dict(status='PASS',evidence_sha256='a'*64) for key in ('positive','negative','regression')})


class GrantAndIdentity(OperatorFixture):
    def test_new_grant_is_sealed_at_apply_and_both_timers_precede_firewall(self):
        record=self.apply()
        self.assertEqual(record['schema'],'fds.cp1-minimal-change.v3')
        self.assertTrue(record['operator_exception_permitted'])
        self.assertEqual(record['exception_operator'],'GWONUK')
        self.assertEqual(self.io.actions[:2],['arm:retention','arm:initial'])
        self.assertEqual(record['after']['objects']['permanent'],self.base['objects']['permanent'])

    def test_no_source_review_or_explicit_grant_never_mutates(self):
        for key,value in [('review_decision','PENDING'),('independent_reviewer','GWONUK'),
                          ('reviewed_bundle_sha256','f'*64),('operator_exception_permitted',False),
                          ('exception_operator','someone'),('operator_exception_policy','unknown')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.check_authorization(dict(self.auth,**{key:value}),self.base,BUNDLE,CLOCK)
        self.assertEqual(self.io.actions,[])

    def test_separate_policy_pr_is_allowed_only_for_v3(self):
        auth=dict(self.auth,review_url='https://github.com/321Team/infra-kubernetes/pull/42#issuecomment-123')
        m.check_authorization(auth,self.base,BUNDLE,CLOCK)
        with self.assertRaises(ValueError):
            m.check_authorization(dict(auth,schema='fds.cp1-minimal-authorization.v2'),self.base,BUNDLE,CLOCK)

    def test_v1_v2_and_v3_without_grant_cannot_use_exception(self):
        record,_,_,handoff,exception,_,_=self.evidence()
        for change in [dict(record,schema='fds.cp1-minimal-change.v1'),
                       dict(record,schema='fds.cp1-minimal-change.v2'),
                       dict(record,operator_exception_permitted=False)]:
            bundle=dict(handoff,change=change)
            data=dict(exception,handoff_sha256=m.fingerprint(bundle))
            with self.subTest(schema=change['schema']),self.assertRaises(ValueError):
                m.validate_operator_exception(data,bundle,self.io.clock)

    def test_operator_cannot_claim_independent_approval(self):
        _,_,_,handoff,exception,_,_=self.evidence()
        for change in [dict(operator='hayeounglee'),dict(independent_review_status='APPROVED'),
                       dict(cp1_accepted=True),dict(independent_reviewer='hayeounglee'),
                       dict(review_decision='APPROVED'),dict(post_review_required=False),
                       dict(decision='ACCEPTED'),dict(reason=''),dict(reason_code='convenience')]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.validate_operator_exception(dict(exception,**change),handoff,self.io.clock)

    def test_scope_state_and_deadline_are_exact(self):
        record,_,_,handoff,exception,_,_=self.evidence()
        for key,value in [('job_id','a'*16),('source_bundle_sha256','f'*64),('handoff_sha256','f'*64),
                          ('expires_at',self.auth['retention_until']),('authority_url','https://example.org'),
                          ('scope',dict(exception['scope'],action='apply-cp2')),
                          ('authorized_at',record['withdraw_at']),('checks',{})]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.validate_operator_exception(dict(exception,**{key:value}),handoff,self.io.clock)

    def test_no_late_exception_or_resurrection(self):
        record,_,_,handoff,exception,proof,_=self.evidence()
        with self.assertRaises(ValueError):
            m.exception_retention_proof(record,handoff,exception,m.instant(record['withdraw_at']))
        with self.assertRaises(ValueError):
            m.validate_exception_retention(dict(proof,committed_at=record['withdraw_at']),record,handoff,exception)


class RootCommitAndDeadlines(OperatorFixture):
    def test_real_file_commit_preserves_receipts_and_does_not_touch_rules_or_timers(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);record,handoff,exception,proof,trial,sha=values
            before={name:(self.io.directory/name).read_bytes() for name in ('CHANGE.json','HANDOFF.json')}
            actions=list(self.io.actions)
            result=self.commit(values)
            self.assertEqual(result['status'],m.PROVISIONAL)
            self.assertFalse(result['cp1_accepted'])
            self.assertFalse((self.io.directory/'ACCEPTANCE.json').exists())
            self.assertFalse((self.io.directory/'RETENTION.json').exists())
            self.assertEqual(e.load_retention(self.io,record),proof)
            self.assertEqual(actions,self.io.actions)
            for name,raw in before.items():self.assertEqual((self.io.directory/name).read_bytes(),raw)

    def test_original_trial_and_regression_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory)
            for key in ('positive','negative','regression'):
                changed=copy.deepcopy(values[2]);changed['checks'][key]['evidence_sha256']='f'*64
                modified=(*values[:2],changed,*values[3:])
                with self.subTest(key=key),self.assertRaises(ValueError):self.commit(modified)
            bad=b'HOST_EXTERNAL_REGRESSION=PASS\n'
            changed=copy.deepcopy(values[2]);changed['checks']['regression']['evidence_sha256']=m.digest(bad)
            with self.assertRaises(ValueError):self.commit((*values[:2],changed,*values[3:]),bad)
            self.assertFalse((self.io.directory/'EXCEPTION_RETENTION.json').exists())

    def test_tampered_trial_stops_without_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);values[4].write_bytes(b'not the reviewed trial')
            with self.assertRaises(ValueError):self.commit(values)
            self.assertFalse((self.io.directory/'EXCEPTION_RETENTION.json').exists())

    def test_same_input_retry_keeps_original_deadline_and_rejects_different_input(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            before=(self.io.directory/'EXCEPTION_RETENTION.json').read_bytes();actions=list(self.io.actions)
            self.io.clock=m.instant(values[0]['withdraw_at'])+dt.timedelta(seconds=10)
            self.io.early=False
            result=self.commit(values)
            self.assertEqual(result['status'],m.PROVISIONAL)
            self.assertFalse(result['cp1_accepted'])
            self.assertEqual((self.io.directory/'EXCEPTION_RETENTION.json').read_bytes(),before)
            self.assertEqual(actions,self.io.actions)
            different=dict(values[2],reason='changed reason')
            with self.assertRaises(ValueError):self.commit((*values[:2],different,*values[3:]))

    def test_failed_marker_write_does_not_skip_early_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);real=e.atomic
            def fail(path,raw):
                if path.name=='EXCEPTION_RETENTION.json':raise OSError('MOCK interrupted marker')
                return real(path,raw)
            with patch.object(e,'atomic',side_effect=fail),self.assertRaises(OSError):self.commit(values)
            self.assertIsNone(e.load_retention(self.io,values[0]))
            self.io.clock=m.instant(values[0]['withdraw_at'])
            restored=e.deadline_action(self.io,self.journal,values[0],None)
            self.assertTrue(restored['cleanup_complete'])

    def test_crossed_first_deadline_is_not_a_successful_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);real=e.atomic
            def late(path,raw):
                real(path,raw)
                if path.name=='EXCEPTION_RETENTION.json':self.io.clock=m.instant(values[0]['withdraw_at'])
            with patch.object(e,'atomic',side_effect=late),self.assertRaises(ValueError):self.commit(values)
            self.assertIsNone(e.load_retention(self.io,values[0]))
            self.assertTrue((self.io.directory/'EXCEPTION_RETENTION_LATE.json').exists())

    def test_first_deadline_can_hold_but_final_always_recovers(self):
        record,_,_,_,_,proof,_=self.evidence()
        self.io.clock=m.instant(record['withdraw_at']);count=len(self.io.actions)
        result=e.deadline_action(self.io,self.journal,record,proof)
        self.assertEqual(result['status'],m.PROVISIONAL)
        self.assertFalse(result['cp1_accepted'])
        self.assertEqual(len(self.io.actions),count)
        self.io.clock=m.instant(record['retained_withdraw_at'])
        result=e.deadline_action(self.io,self.journal,record,proof)
        self.assertEqual(result['status'],'RESTORED_REVIEW_REQUIRED')
        self.assertFalse(self.io.hard or self.io.early)
        self.assertEqual(self.io.snapshot(),self.base)

    def test_no_exception_proof_or_missing_hard_timer_recovers(self):
        record,_,_,_,_,proof,_=self.evidence()
        self.io.clock=m.instant(record['withdraw_at']);self.io.hard=False
        self.assertTrue(e.deadline_action(self.io,self.journal,record,proof)['cleanup_complete'])

    def test_whole_state_drift_and_hard_timer_absence_block_live_use(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            self.io.hard=False
            with self.assertRaises(ValueError):e.live_status(self.io,values[0])
            self.io.hard=True;self.io.view['protection']['calico']='MOCK drift'
            with self.assertRaises(ValueError):e.live_status(self.io,values[0])

    def test_root_proof_tamper_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            proof=dict(values[3],withdraw_at=self.auth['retention_until'])
            (self.io.directory/'EXCEPTION_RETENTION.json').write_bytes(m.encoded(proof))
            with self.assertRaises(ValueError):e.load_retention(self.io,values[0])

    def test_normal_independent_acceptance_can_supersede_before_first_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            record,handoff,exception,proof,trial,sha=values
            accepted=self.accepted(record,handoff)
            result=e.commit_retention(self.io,record,BUNDLE,accepted,trial,sha)
            self.assertEqual(result['status'],'RETENTION_COMMITTED')
            status=e.live_status(self.io,record)
            self.assertEqual(status['status'],'ACCEPTED_RETAINED')
            m.live_gate(status,record,clock=self.io.clock,acceptance=accepted)
            self.assertTrue((self.io.directory/'OPERATOR_EXCEPTION.json').exists())
            with self.assertRaises(ValueError):self.commit(values)

    def test_late_independent_review_is_not_backdated_into_runtime_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            record,handoff,_,_,trial,sha=values
            self.io.clock=m.instant(record['withdraw_at'])+dt.timedelta(seconds=1)
            accepted=self.accepted(record,handoff)
            with self.assertRaises(ValueError):e.commit_retention(self.io,record,BUNDLE,accepted,trial,sha)
            self.assertFalse((self.io.directory/'ACCEPTANCE.json').exists())

    def test_downstream_does_not_treat_operator_hold_as_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            values=self.prepare(directory);self.commit(values)
            record,handoff,exception,proof,trial,sha=values
            status=e.live_status(self.io,record)
            m.live_exception_gate(status,record,proof,self.io.clock)
            with self.assertRaises(ValueError):m.live_gate(status,record,clock=self.io.clock)
            with self.assertRaises(ValueError):c.validate(self.original,handoff,exception,trial,sha,self.io.clock)


if __name__=='__main__':unittest.main(verbosity=2)
