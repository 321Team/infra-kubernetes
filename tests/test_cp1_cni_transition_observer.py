"""Actual-derived native/save fixtures with explicitly mocked daemon and CRI."""
import copy
import json
import unittest
from test_cp1_cni_delta_observer import bundle, CLOCK
from test_cp1_cni_observer import native, object_text
from test_cp1_cni_transition import baseline
import cp1_cni_transition as t
import cp1_cni_transition_observer as o


def capture(state=None):
    record, activation, units, members = bundle()
    state = copy.deepcopy(state or baseline())
    for suffix in ('','_after'):
        q=record['queries']
        def put(key,value):
            q[key+suffix]={'rc':0,'stdout':value,'stderr':'','stdout_truncated':False,'stderr_truncated':False}
        for plane in ('runtime','permanent'):
            data=state['inventory'][plane]
            put(plane+'/zones',object_text(data['zones']))
            put(plane+'/policies',object_text(data['policies']))
            put(plane+'/ipsets',' '.join(data['ipsets']))
            put(plane+'/direct',data['direct'])
            for name,value in data['ipsets'].items():put(plane+'/ipsets/'+name,object_text({name:value}))
            for kind in ('policy','ipset'):
                key=plane+'/'+kind+'-description'+suffix
                q.pop(key,None)
                if plane=='permanent' and kind in state['descriptions']:
                    put(plane+'/'+kind+'-description',state['descriptions'][kind])
        raw=json.loads(q['native'+suffix]['stdout'])
        # Remove only the explicit test fixture's modeled owned objects.
        raw['nftables']=[x for x in raw['nftables'] if t.NAME not in json.dumps(x)]
        present=set(state['inventory']['runtime']['zones']['public']['rich rules']) & set(t.six_rules())
        shapes=list(o.six_native().values())
        raw['nftables']=[x for x in raw['nftables'] if 'rule' not in x or
                         not any(t.same(o.rule_shape(x['rule']),s) for s in shapes)]
        raw['nftables'] += [{'rule':copy.deepcopy(o.six_native()[rule])} for rule in sorted(present)]
        runtime=state['inventory']['runtime']
        if t.NAME in runtime['ipsets']:
            mode='ACTIVE' if runtime['policies'].get(t.NAME,{}).get('disable')=='no' else 'DISABLED'
            values=runtime['ipsets'][t.NAME]['entries'].split()
            raw['nftables'] += [x for x in native(mode,values)['nftables'] if t.NAME in json.dumps(x)]
        put('native',json.dumps(raw))
    return record,members


class TransitionObserverTests(unittest.TestCase):
    def test_all_activation_and_recovery_prefixes_share_verified_fixed_state(self):
        from test_cp1_cni_transition import setup
        tx,io,j=setup();states=[io.snapshot()]
        original=io.command
        def run(*args,**kwargs):
            result=original(*args,**kwargs);states.append(io.snapshot());return result
        io.command=run
        tx.activate_empty();tx.recover()
        digests=set()
        for index,state in enumerate(states):
            with self.subTest(index=index):
                r,members=capture(state);out=o.observe(r,members,CLOCK)
                self.assertEqual(out['inventory'],state['inventory'])
                self.assertEqual(out['descriptions'],state['descriptions'])
                digests.add(out['fixed_sha256'])
        self.assertEqual(len(digests),1)
        t.validate_baseline(o.observe(*capture(),CLOCK))

    def test_native_rule_wrong_source_port_prefix_duplicate_or_tail_fails(self):
        for case in ('source','port','prefix','duplicate','tail','absent'):
            r,members=capture()
            for suffix in ('','_after'):
                raw=json.loads(r['queries']['native'+suffix]['stdout'])
                target=next(x for x in raw['nftables'] if x.get('rule',{}).get('chain')=='filter_IN_public_allow' and
                            any(t.same(o.rule_shape(x['rule']),v) for v in o.six_native().values()))
                if case=='source':target['rule']['expr'][0]['match']['right']='10.1.93.200'
                elif case=='port':target['rule']['expr'][1]['match']['right']=6443
                elif case=='prefix':target['rule']['expr'][0]['match']['right']={'prefix':{'addr':'10.1.93.0','len':24}}
                elif case=='duplicate':raw['nftables'].append(copy.deepcopy(target))
                elif case=='tail':raw['nftables'].append({'rule':{'family':'inet','table':'firewalld','chain':'filter_IN_public_allow','expr':[{'drop':None}]}})
                else:raw['nftables'].remove(target)
                r['queries']['native'+suffix]['stdout']=json.dumps(raw)
            with self.subTest(case=case),self.assertRaises(ValueError):o.observe(r,members,CLOCK)

    def test_unrelated_native_changes_are_preserved_in_fixed_hash(self):
        r,members=capture();before=o.observe(r,members,CLOCK)
        for suffix in ('','_after'):
            raw=json.loads(r['queries']['native'+suffix]['stdout'])
            raw['nftables'].append({'table':{'family':'inet','name':'foreign_table'}})
            r['queries']['native'+suffix]['stdout']=json.dumps(raw)
        self.assertNotEqual(before['fixed_sha256'],o.observe(r,members,CLOCK)['fixed_sha256'])

    def test_paired_race_incomplete_stale_api_loss_or_peer_fails(self):
        for case in ('race','incomplete','stale','api','peer','config'):
            r,members=capture()
            if case=='race':r['queries']['runtime/direct_after']['stdout']='new direct rule'
            elif case=='incomplete':r['complete']=False
            elif case=='stale':r['wall']=900
            elif case=='api':r['queries']['api/pods']['rc']=1
            elif case=='peer':members['raw_after']['sandbox_peers'][0]['iflink']+=1
            else:
                for suffix in ('','_after'):r['queries']['configuration'+suffix]['stdout']='FirewallBackend=nftables\nFirewallBackend=iptables'
            with self.subTest(case=case),self.assertRaises(ValueError):o.observe(r,members,CLOCK)

    def test_permanent_owner_or_native_element_race_is_visible(self):
        from test_cp1_cni_transition import setup
        tx,io,j=setup();tx.activate_empty();r,members=capture(io.live)
        r['queries']['permanent/policy-description_after']['stdout']='foreign'
        with self.assertRaises(ValueError):o.observe(r,members,CLOCK)
        r,members=capture(io.live)
        raw=json.loads(r['queries']['native']['stdout'])
        own=next(x['set'] for x in raw['nftables'] if x.get('set',{}).get('name')==t.NAME)
        own['elem']=[{'concat':[{'prefix':{'addr':'10.244.246.80','len':32}},'cali00000000000']}]
        r['queries']['native']['stdout']=json.dumps(raw)
        with self.assertRaisesRegex(ValueError,'native/daemon'):o.observe(r,members,CLOCK)


if __name__=='__main__':unittest.main()
