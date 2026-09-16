"""Bounded local IO for reviewed firewall transitions; no activation CLI.

Root source/journal staging, external authenticated management transport and
work cgroup termination are caller prerequisites. A hard per-invocation
deadline is never renewed by repeated reads or recovery commands.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path
import time
import re

from cp1_cni_membership import need, number, boot, remaining, RESERVE, same
from cp1_cni_observer import collect, protection
from cp1_cni_transition_observer import observe
from cp1_cni_transition import steps, six_rules, NAME

ROOT=Path(__file__).resolve().parents[1]
NODE_SHA='93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa'


def management_proof(value, challenge, clock):
    """Validate fresh new-session results FROM authenticated controller IO.

    This validates meaning, not authenticity. Uploaded JSON is not a valid
    substitute for the independently authenticated controller adapter.
    """
    need(set(value)=={'challenge','hostname','user','ssh_connection','boot_id','wall','boottime','route'},
         'management proof fields differ')
    need(isinstance(challenge,str) and re.fullmatch(r'[a-f0-9]{64}',challenge),'fresh management challenge required')
    boot(clock['boot_id'])
    need(value['challenge']==challenge and value['hostname']=='k8s-control01' and
         value['user']=='svc_ansible' and value['boot_id']==clock['boot_id'], 'management identity differs')
    tokens=value['ssh_connection'].split()
    need(len(tokens)==4 and tokens[0]=='10.250.10.10' and tokens[2:] == ['10.1.93.102','22'] and
         tokens[1].isdigit() and 1 <= int(tokens[1]) <= 65535, 'new management session source/destination differs')
    for key in ('wall','boottime'):
        need(0 <= number(clock[key])-number(value[key]) < 30,'management proof stale/future')
    route=value['route']
    need(isinstance(route,list) and len(route)==1 and
         route[0].get('dst')=='10.250.10.10' and route[0].get('gateway')=='10.1.93.58' and
         route[0].get('dev')=='ens160' and route[0].get('prefsrc')=='10.1.93.102' and
         route[0].get('type','unicast')=='unicast', 'management return route differs')
    return copy.deepcopy(value)


def peer_capture(node,deadline):
    spec=importlib.util.spec_from_file_location('transition_peer_collector',ROOT/'scripts/collect-cp1-cni-membership.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.collect(node,deadline=deadline)


class LocalTransitionIO:
    def __init__(self,node,lease,baseline,clock,runner,live_guard,quiesced,external_management,
                 emergency_disable,*,deadline,monotonic=time.monotonic):
        need(hashlib.sha256(Path(node.__file__).read_bytes()).hexdigest()==NODE_SHA,'transition helper source differs')
        self.node,self.lease,self.baseline=node,copy.deepcopy(lease),copy.deepcopy(baseline)
        self.clock,self.runner,self.live_guard,self.quiesced=clock,runner,live_guard,quiesced
        self.external_management,self.emergency_disable=external_management,emergency_disable
        self.monotonic,self.deadline=monotonic,number(deadline)
        need(0 < self.deadline-self.monotonic() <= 150,'transition invocation budget differs')
        self.evidence=None

    def left(self):
        value=self.deadline-self.monotonic();need(value>0,'transition hard deadline exhausted');return value

    def guard(self,cleanup):
        self.left()
        if cleanup:
            # Must independently read stopped unit AND recursive cgroup state.
            self.quiesced()
        else:
            remaining(self.lease,self.clock(),RESERVE)
            self.live_guard()
        self.left()

    def snapshot(self):
        self.left();end=min(self.deadline,self.monotonic()+29)
        capture=collect(self.node,protection,compatibility=True,delta_api=True,deadline=end)
        self.evidence={'capture':capture}
        peer=peer_capture(self.node,end);self.evidence['membership_capture']=peer
        current=self.clock();self.evidence['clock']=current
        self.left()
        result=observe(capture,peer,current)
        self.evidence['verified_snapshot']=copy.deepcopy(result)
        return result

    def command(self,argv,*,cleanup=False):
        plan=steps(self.baseline,self.lease['run_id'])
        allowed=[p['argv'] for p in plan] if not cleanup else [
            ['--policy='+NAME,'--add-disable'],['--permanent','--delete-policy='+NAME],
            ['--permanent','--delete-ipset='+NAME],['--reload'],
            *[['--zone=public','--add-rich-rule='+rule] for rule in six_rules()]]
        need(any(same(argv,v) for v in allowed),'unreviewed transition command')
        self.guard(cleanup)
        # The trusted runner must use a fixed executable and enforce the given
        # bound with bounded output. No shell command or root-provided argv.
        return self.runner(['firewall-cmd',*argv],timeout=min(5,self.left()))

    def management(self):
        import secrets
        self.left();challenge=secrets.token_hex(32)
        result=self.external_management(challenge,timeout=min(10,self.left()))
        self.left()
        return management_proof(result,challenge,self.clock())
