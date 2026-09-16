"""Recovery/finalizer composition: command model, no host service mutation."""
import copy
from contextlib import contextmanager
import unittest
from test_cp1_cni_transition import setup, baseline
from test_cp1_cni_membership import MemoryJournal
from test_cp1_cni_guard import unit_fixture
import cp1_cni_guard as g
import cp1_cni_recovery as r


def group(name,events='populated 0\nfrozen 0\n'):
    return {'path':'/sys/fs/cgroup/system.slice/'+name,'filesystem':'cgroup2','events':events}


def management(challenge,clock):
    return {'challenge':challenge,'hostname':'k8s-control01','user':'svc_ansible',
            'ssh_connection':'10.250.10.10 42000 10.1.93.102 22',
            'boot_id':clock['boot_id'],'wall':clock['wall'],'boottime':clock['boottime'],
            'route':[{'dst':'10.250.10.10','gateway':'10.1.93.58','dev':'ens160','prefsrc':'10.1.93.102'}]}


class RecoveryIO:
    def __init__(self,tx,firewall):
        self.tx,self.f=tx,firewall;self.live=unit_fixture(firewall.lease)
        self.n=g.names(firewall.lease['run_id']);self.trace=[];self.locked=False
        self.stop=True;self.child=False;self.restart=False;self.qcount=0;self.result={'rc':0,'stdout':'','stderr':'','truncated':False}
    def units(self):return copy.deepcopy(self.live)
    def stop_work(self):
        self.trace.append('stop')
        if self.stop:self.live['properties'][self.n['work']].update(ActiveState='inactive',SubState='dead',MainPID='0',Job='',ControlGroup='')
        return copy.deepcopy(self.result)
    def quiesced(self):
        self.trace.append('quiesced');self.qcount+=1
        if self.restart and self.qcount==2:self.live['properties'][self.n['work']]['MainPID']='456'
        return self.units(),group(self.n['work'],'populated 1\nfrozen 0\n' if self.child else 'populated 0\nfrozen 0\n')
    @contextmanager
    def exclusive(self):
        self.trace.append('lock');self.locked=True
        try:yield
        finally:self.locked=False;self.trace.append('unlock')
    def firewall(self):
        assert self.locked;self.trace.append('firewall');return self.tx.recover()
    def snapshot(self):assert self.locked;return self.f.snapshot()
    def emergency_disable(self):self.trace.append('emergency');return self.f.emergency_disable()


def recovery():
    tx,f,j=setup();tx.activate_empty();io=RecoveryIO(tx,f);journal=MemoryJournal()
    return r.Recovery(f.lease,baseline(),journal,io),io,journal


def finished_units(io):
    units={'files':{},'links':{},'properties':{}}
    groups={}
    for kind,name in io.n.items():
        units['properties'][name]={'Id':name,'LoadState':'not-found','FragmentPath':'','DropInPaths':'',
           'UnitFileState':'','ActiveState':'inactive','SubState':'dead','MainPID':'0','Job':'','ControlGroup':''}
        if kind!='timer':groups[name]=group(name,None)
    return units,groups


class RecoveryTests(unittest.TestCase):
    def test_stop_cgroup_lock_firewall_order_and_external_finalization(self):
        job,io,j=recovery();state=job.run()
        self.assertEqual(io.trace,['stop','quiesced','lock','quiesced','firewall','unlock'])
        self.assertTrue(state['firewall_restored']);self.assertFalse(state['full_recovery_complete'])
        units,groups=finished_units(io);clock=io.f.clock();challenge='a'*64
        result=r.final_result(io.f.lease,baseline(),state,units,groups,io.f.snapshot(),management(challenge,clock),challenge,clock)
        self.assertTrue(result['full_recovery_complete']);self.assertFalse(result['authorizes_apply'])
        self.assertFalse(result['independent_runtime_reviewed'])

    def test_stop_acknowledged_but_work_or_descendant_alive_blocks_firewall(self):
        for case in ('work','child','restart'):
            job,io,j=recovery()
            if case=='work':io.stop=False
            elif case=='child':io.child=True
            else:io.restart=True
            with self.subTest(case=case),self.assertRaises(ValueError):job.run()
            self.assertNotIn('firewall',io.trace);self.assertIn('emergency',io.trace)
            self.assertFalse(j.saved['full_recovery_complete'])

    def test_uncertain_stop_result_requires_independently_empty_cgroup(self):
        job,io,j=recovery();io.result={'exception':'TimeoutError'}
        self.assertTrue(job.run()['firewall_restored'])

    def test_foreign_unit_blocks_even_stop(self):
        job,io,j=recovery();io.live['properties'][io.n['work']]['FragmentPath']='/etc/foreign.service'
        with self.assertRaises(ValueError):job.run()
        self.assertNotIn('stop',io.trace);self.assertNotIn('firewall',io.trace)

    def test_journal_failure_still_stops_and_disables_never_reloads(self):
        job,io,j=recovery();j.fail=True
        with self.assertRaises(OSError):job.run()
        self.assertEqual(io.trace,['stop','emergency']);self.assertIsNone(j.saved)

    def test_finalizer_rejects_own_recovery_still_running_any_unit_link_or_group(self):
        for case in ('recover','guard','work','timer','link','file','group','pending','dropin'):
            job,io,j=recovery();state=job.run();units,groups=finished_units(io);clock=io.f.clock()
            if case in ('recover','guard','work','timer'):units['properties'][io.n[case]]['ActiveState']='active'
            elif case=='link':units['links']['any']='any'
            elif case=='file':units['files']['any']='any'
            elif case=='group':groups[io.n['work']]['events']='populated 1\nfrozen 0\n'
            elif case=='pending':units['properties'][io.n['recover']]['Job']='5/start'
            else:units['properties'][io.n['guard']]['DropInPaths']='/run/foreign.conf'
            with self.subTest(case=case),self.assertRaises(ValueError):
                r.final_result(io.f.lease,baseline(),state,units,groups,io.f.snapshot(),management('a'*64,clock),'a'*64,clock)

    def test_firewall_drift_or_missing_management_cannot_be_full_recovery(self):
        for case in ('drift','challenge','source','route','age','boot','user'):
            job,io,j=recovery();state=job.run();units,groups=finished_units(io);clock=io.f.clock();proof=management('a'*64,clock)
            actual=io.f.snapshot()
            if case=='drift':actual['fixed_sha256']='f'*64
            elif case=='challenge':proof['challenge']='old'
            elif case=='source':proof['ssh_connection']='10.1.93.58 42000 10.1.93.102 22'
            elif case=='route':proof['route'][0]['gateway']='10.1.93.1'
            elif case=='age':proof['wall']-=30
            elif case=='boot':proof['boot_id']='a'*8+clock['boot_id'][8:]
            else:proof['user']='root'
            with self.subTest(case=case),self.assertRaises(ValueError):
                r.final_result(io.f.lease,baseline(),state,units,groups,actual,proof,'a'*64,clock)

    def test_cgroup_read_error_cannot_be_interpreted_as_absent(self):
        name=g.names(recovery()[1].f.lease['run_id'])['work']
        p={'Id':name,'ActiveState':'inactive','SubState':'dead','MainPID':'0','Job':'','ControlGroup':''}
        for sample in (group(name,''),group(name,'populated 0\npopulated 0'),
                       {**group(name), 'filesystem':'tmpfs'}, {**group(name),'path':'/sys/fs/cgroup/foreign'}):
            with self.subTest(sample=sample),self.assertRaises(ValueError):r.cgroup_empty(p,sample)


if __name__=='__main__':unittest.main()
