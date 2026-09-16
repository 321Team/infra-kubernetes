"""Independent recovery orchestration; caller owns authenticated root IO.

Stop requests are not stop proofs. A recovery unit can restore the firewall,
but only an external finalizer, after that unit exits, may prove all units
and enablement links are gone. Neither stage authorizes CP1/CP2/Join.
"""
import copy
import re

from cp1_cni_membership import need, fingerprint, same, validate_lease
from cp1_cni_guard import names, validate_owned
from cp1_cni_transition import validate_baseline
from cp1_cni_transition_io import management_proof


def cgroup_empty(properties, raw):
    """raw is a fresh trusted read of the compiled unit's cgroup.events.

    populated covers the entire descendant tree, unlike cgroup.procs. None
    means trusted lstat/open found the exact directory absent, NOT a read
    error, permission denial, unknown mount or missing events file.
    """
    name=properties['Id']
    need(re.fullmatch(r'fds-cni-managed-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}-(?:work|guard|recover)\.service',name),
         'cgroup unit name differs')
    expected='/system.slice/'+name
    need(properties.get('ActiveState') in ('inactive','failed') and
         properties.get('SubState') in ('dead','failed') and properties.get('MainPID')=='0' and
         properties.get('Job') in ('','0') and properties.get('ControlGroup') in ('',expected),
         'unit has not stopped')
    need(set(raw)=={'path','filesystem','events'} and raw['path']=='/sys/fs/cgroup'+expected and
         raw['filesystem']=='cgroup2','untrusted cgroup location/type')
    if raw['events'] is None:return
    values={}
    need(isinstance(raw['events'],str) and len(raw['events'])<=4096,'invalid cgroup events')
    for line in raw['events'].splitlines():
        fields=line.split()
        need(len(fields)==2 and fields[0] in ('populated','frozen') and fields[0] not in values and
             fields[1] in ('0','1'),'unknown cgroup events field')
        values[fields[0]]=fields[1]
    need(values.get('populated')=='0','unit cgroup descendants still populated')


def initial(lease):
    validate_lease(lease)
    return {'schema':'fds.cp1-cni-independent-recovery.v1','lease_sha256':fingerprint(lease),
            'phase':'REQUESTED','stop_requested':True,'firewall_restored':False,
            'management_verified':False,'units_removed':False,'full_recovery_complete':False,
            'authorizes_apply':False,'authorizes_join':False}


class Recovery:
    """Injected IO is local trusted code, never an operator JSON assertion.

    io.stop_work() is bounded and returns a command record; quiesced() reads
    current owned loaded unit plus recursive cgroup state. exclusive() takes
    global/run locks only AFTER work stops. firewall() constructs a fresh
    Transaction from its separate durable journal and restores known prefix.
    """
    def __init__(self,lease,baseline,journal,io):
        validate_lease(lease);validate_baseline(baseline)
        self.lease,self.baseline,self.journal,self.io=copy.deepcopy(lease),copy.deepcopy(baseline),journal,io

    def run(self):
        state=initial(self.lease)
        try:
            units=self.io.units();unit=validate_owned(units,self.lease)
            try:
                self.journal.save(state)
            except OSError:
                # Still request stop; never interpret dispatch as completion.
                self.io.stop_work()
                raise
            result=self.io.stop_work()
            state['stop_result']=copy.deepcopy(result);self.journal.save(state)
            # A nonzero/timeout return may have stopped work. Only a fresh
            # independent unit/cgroup read decides whether broad work is safe.
            units,raw=self.io.quiesced()
            validate_owned(units,self.lease)
            cgroup_empty(units['properties'][unit['work']],raw)
            with self.io.exclusive():
                # Close the stop-to-lock race before the first firewall IO.
                units,raw=self.io.quiesced();validate_owned(units,self.lease)
                cgroup_empty(units['properties'][unit['work']],raw)
                state['phase']='FIREWALL_RECOVERY';self.journal.save(state)
                restored=self.io.firewall()
                need(restored.get('phase')=='FIREWALL_RESTORED' and restored.get('firewall_restored') is True and
                     same(restored.get('lease'),self.lease) and same(restored.get('baseline'),self.baseline) and
                     restored.get('pending') is None and same(restored.get('expected'),self.baseline),
                     'firewall recovery receipt differs')
                actual=self.io.snapshot()
                need(same(actual,self.baseline),'final whole firewall differs')
                state.update(phase='FIREWALL_RESTORED_WAIT_EXTERNAL_FINALIZER',firewall_restored=True,
                             transition_sha256=fingerprint(restored))
                self.journal.save(state)
            return copy.deepcopy(state)
        except Exception:
            # Scope-limited fallback has its own ownership observer. A read or
            # journal failure cannot turn into broad reload/deletion authority.
            self.io.emergency_disable()
            raise


def final_result(lease,baseline,recovery,units_after,cgroups,actual,management,challenge,clock):
    """External finalizer validates fresh post-removal evidence.

    Must run outside all owned service cgroups after exact owned-file removal,
    daemon-reload, and authenticated new management session. This function
    validates evidence; it does not authenticate a submitted capture or delete
    anything. Root transport/staging integration remains separate.
    """
    validate_lease(lease);validate_baseline(baseline)
    need(recovery.get('schema')==initial(lease)['schema'] and recovery.get('lease_sha256')==fingerprint(lease) and
         recovery.get('phase')=='FIREWALL_RESTORED_WAIT_EXTERNAL_FINALIZER' and
         recovery.get('firewall_restored') is True and recovery.get('full_recovery_complete') is False,
         'independent firewall recovery incomplete')
    unit=names(lease['run_id'])
    need(set(units_after)=={'properties','files','links'} and units_after['files']=={} and units_after['links']=={} and
         set(units_after['properties'])==set(unit.values()),'unit files/links remain or inventory incomplete')
    need(set(cgroups)=={unit[k] for k in ('work','guard','recover')},'cgroup inventory incomplete')
    for kind,name in unit.items():
        p=units_after['properties'][name]
        need(p.get('Id')==name and p.get('LoadState')=='not-found' and p.get('FragmentPath')=='' and
             p.get('DropInPaths')=='' and p.get('UnitFileState') in ('','not-found') and
             p.get('ActiveState')=='inactive' and p.get('SubState')=='dead' and p.get('Job') in ('','0'),
             'unit still loaded/running or pending')
        if kind!='timer':cgroup_empty(p,cgroups[name])
    need(same(actual,baseline),'post-finalization whole firewall differs')
    proof=management_proof(management,challenge,clock)
    return {'schema':'fds.cp1-cni-full-recovery-evidence.v1','run_id':lease['run_id'],
            'lease_sha256':fingerprint(lease),'phase':'FULL_RECOVERY_OBSERVED_REVIEW_REQUIRED',
            'firewall_restored':True,'management_verified':True,'units_removed':True,
            'full_recovery_complete':True,'independent_runtime_reviewed':False,
            'evidence_sha256':fingerprint({'recovery':recovery,'units':units_after,'cgroups':cgroups,
                                         'snapshot':actual,'management':proof,'clock':clock}),
            'authorizes_apply':False,'authorizes_join':False}
