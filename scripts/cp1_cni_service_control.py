"""Durable enablement and one-shot dispatch for exact owned CP1 units.

No CLI or approval assertion. The authenticated root caller holds the global
installation lock and a separate Journal, and has already persisted the
activation/initial transition. Before ANY firewall change, worker code must
read durable START_REQUESTED/WORK_OBSERVED acceptance and check the live guard.
An early worker may wait only within the immutable launch window; LAUNCH_INTENT
alone never authorizes mutation. Unknown outcomes permit recovery, never restart.
"""
import copy
import os

import cp1_cni_guard as g
from cp1_cni_membership import need, same, fingerprint, remaining, number, RESERVE
from cp1_cni_staging import root_directory, read_regular, verify_sources
from cp1_cni_unit_files import binding, validate_state as validate_publication
from cp1_cni_unit_cleanup import inspect, parent

SCHEMA='fds.cp1-cni-service-launch.v1'


def links(lease):
    unit=g.names(lease['run_id'])
    # Boot recovery is durable before the periodic watchdog link.
    return [(directory+'/'+unit[kind],str(g.UNITS/unit[kind])) for directory,kind in
            (('multi-user.target.wants','recover'),('timers.target.wants','timer'))]


def initial(expected):
    return {'schema':SCHEMA,'binding':copy.deepcopy(expected),'phase':'LINKING',
            'completed':[],'pending':None,'launch_clock':None,'command_result':None,
            'start_requested':False,'work_observed':False,'full_recovery_complete':False,'authorizes_apply':False}


def validate(state,expected,order):
    need(set(state)==set(initial(expected)) and state.get('schema')==SCHEMA and
         same(state.get('binding'),expected),'launch journal binding differs')
    done=state['completed']
    need(isinstance(done,list) and done==order[:len(done)] and len(done)<=len(order),'launch link prefix differs')
    need(state['pending'] is None or len(done)<len(order) and state['pending']==order[len(done)],'launch pending differs')
    phase=state['phase']
    need(phase in ('LINKING','LINKS_READY','RELOAD_INTENT','ENABLED','LAUNCH_INTENT','START_REQUESTED',
                  'WORK_OBSERVED','RECOVERY_REQUIRED'),'unknown launch phase')
    if phase!='LINKING':need(done==order and state['pending'] is None,'launch links incomplete')
    launching=phase in ('LAUNCH_INTENT','START_REQUESTED','WORK_OBSERVED','RECOVERY_REQUIRED')
    need((state['launch_clock'] is not None)==launching,'launch clock phase differs')
    if launching:
        need(set(state['launch_clock'])=={'boot_id','wall','boottime'},'launch clock fields differ')
        for key in ('wall','boottime'):number(state['launch_clock'][key])
    need(state['start_requested'] is (phase in ('START_REQUESTED','WORK_OBSERVED')) and
         state['work_observed'] is (phase=='WORK_OBSERVED') and
         state['full_recovery_complete'] is False and state['authorizes_apply'] is False,'launch result flags differ')
    if phase in ('START_REQUESTED','WORK_OBSERVED'):need(clean_result(state['command_result']),'unverified start acceptance')
    elif phase!='RECOVERY_REQUIRED':need(state['command_result'] is None,'unexpected launch command record')


def clean_result(result):
    return (isinstance(result,dict) and set(result)=={'rc','stdout','stderr','truncated'} and
            type(result['rc']) is int and result['rc']==0 and result['truncated'] is False and
            isinstance(result['stdout'],str) and isinstance(result['stderr'],str) and
            not result['stdout'].strip() and not result['stderr'].strip())


def link_expectations(state,expected,lease):
    """Initial cleanup may accept absence only within this journaled prefix."""
    rows=links(lease);validate(state,expected,[p for p,_ in rows])
    result={}
    for path,target in rows:
        result[path]=[target] if path in state['completed'] else ([None,target] if state['pending']==path else [None])
    return result


def quiet(observed,lease):
    unit=g.validate_owned(observed,lease)
    for kind,name in unit.items():
        p=observed['properties'][name]
        need(p.get('ActiveState')=='inactive' and p.get('SubState')=='dead' and
             p.get('MainPID') in (('','0',None) if kind=='timer' else ('0',)) and p.get('Job') in ('','0'),
             'unit active/failed/pending before launch')
    return unit


def create_link(directory,path,target):
    fd,name=parent(directory,path)
    try:
        os.symlink(target,name,dir_fd=fd)  # O_EXCL semantics; no preexisting adoption
        os.fsync(fd)
    finally:os.close(fd)


class ServiceControl:
    def __init__(self,lease,manifest,source_sha,publication,journal,observe,clock,before_launch):
        self.lease,self.manifest=copy.deepcopy(lease),copy.deepcopy(manifest)
        self.source_sha,self.journal,self.observe,self.clock=source_sha,journal,observe,clock
        self.before_launch=before_launch
        self.expected=binding(lease,g.UNITS,manifest,source_sha)
        validate_publication(publication,self.expected,sorted(g.documents(lease)))
        need(publication['phase']=='DEFINITIONS_LOADED','unit publication not loaded')
        self.rows=links(lease);self.order=[p for p,_ in self.rows]

    def files(self,directory):
        verify_sources(g.ROOT,self.lease['run_id'],self.manifest,self.source_sha)
        for name,text in g.documents(self.lease).items():
            need(read_regular(directory,name,root_owned=True)==text.encode(),'owned unit bytes changed')

    def live(self,directory):
        self.files(directory)
        observed=self.observe();g.validate_owned(observed,self.lease)
        # Read symlinks independently; do not trust an injected armed flag.
        observed=copy.deepcopy(observed);observed['links']={}
        for path,target in self.rows:
            actual=inspect(directory,path,'link')
            if actual is not None:
                need(actual==target,'foreign enablement link')
                observed['links'][str(g.UNITS/path)]=actual
        return observed

    def fresh(self):
        clock=self.clock();remaining(self.lease,clock,RESERVE)
        for key,start in (('wall','wall_start'),('boottime','boot_start')):
            need(0<=clock[key]-self.lease[start]<30,'launch input expired: no lease renewal')
        return clock

    def arm(self,command):
        """Create/reload exact enablement links; does not start any unit."""
        self.fresh();directory=root_directory(g.UNITS,private=False)
        try:
            try:state=self.journal.load()
            except FileNotFoundError:
                self.files(directory);quiet(self.live(directory),self.lease)
                need(all(inspect(directory,p,'link') is None for p,_ in self.rows),'preexisting enablement link')
                state=initial(self.expected);self.journal.save(state)
            validate(state,self.expected,self.order)
            need(state['phase'] in ('LINKING','LINKS_READY','RELOAD_INTENT','ENABLED'),'launch cannot re-arm after dispatch intent')
            quiet(self.live(directory),self.lease)
            for index,(path,target) in enumerate(self.rows):
                actual=inspect(directory,path,'link')
                if index<len(state['completed']):
                    need(actual==target,'committed enablement link missing/different');continue
                if state['pending'] is None:
                    need(actual is None,'enablement link appeared without intent')
                    state['pending']=path;self.journal.save(state)
                else:need(state['pending']==path and actual in (None,target),'pending enablement link differs')
                self.fresh();quiet(self.live(directory),self.lease)
                actual=inspect(directory,path,'link')
                need(actual in (None,target),'enablement changed during check')
                if actual is None:create_link(directory,path,target)
                else:
                    fd,_=parent(directory,path)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                state['completed'].append(path);state['pending']=None;self.journal.save(state)
            state['phase']='RELOAD_INTENT';self.journal.save(state)
            self.fresh();quiet(self.live(directory),self.lease)
            need(clean_result(command(['systemctl','daemon-reload'],timeout=5)),'enablement reload uncertain')
            observed=self.live(directory);unit=quiet(observed,self.lease)
            for kind in ('recover','timer'):
                need(observed['properties'][unit[kind]].get('UnitFileState')=='enabled','enablement not observed')
            state['phase']='ENABLED';self.journal.save(state)
            return copy.deepcopy(state)
        finally:os.close(directory)

    def start(self,command):
        """Exactly one start dispatch. Re-entry after LAUNCH_INTENT is denied.

        before_launch() authenticates the current root activation, persisted
        pristine transition, absence of stop requests, and original full state.
        This callback does not stand in for the work entrypoint's live checks.
        """
        state=self.journal.load();validate(state,self.expected,self.order)
        need(state['phase']=='ENABLED','launch already attempted: recover same run')
        directory=root_directory(g.UNITS,private=False)
        try:
            observed=self.live(directory);unit=quiet(observed,self.lease)
            need(set(observed['links'])=={str(g.UNITS/p) for p,_ in self.rows},'enablement links incomplete')
            for kind in ('timer','recover'):
                need(observed['properties'][unit[kind]].get('UnitFileState')=='enabled','recovery not enabled')
            self.fresh();self.before_launch();clock=self.fresh()
            state.update(phase='LAUNCH_INTENT',launch_clock={k:clock[k] for k in ('boot_id','wall','boottime')})
            self.journal.save(state)
            # Any uncertainty after intent is recovery-only, including a
            # crash before systemctl. Never infer that no job was submitted.
            try:
                self.fresh();quiet(self.live(directory),self.lease)
                result=command(['systemctl','--no-block','start',unit['timer'],unit['work']],timeout=5)
                state['command_result']=copy.deepcopy(result)
                need(clean_result(result),'start dispatch uncertain')
                state.update(phase='START_REQUESTED',start_requested=True);self.journal.save(state)
            except Exception:
                state.update(phase='RECOVERY_REQUIRED',start_requested=False,work_observed=False)
                self.journal.save(state);raise
            return copy.deepcopy(state)
        finally:os.close(directory)

    def confirm(self):
        """Read-only live confirmation; never repeat start or extend clocks."""
        state=self.journal.load();validate(state,self.expected,self.order)
        need(state['phase']=='START_REQUESTED','start result not eligible for confirmation')
        directory=root_directory(g.UNITS,private=False)
        try:
            observed=self.live(directory);unit=g.validate_owned(observed,self.lease)
            clock=self.clock();g.verified_guard(observed,self.lease,clock)
            need(state['launch_clock']['boot_id']==clock['boot_id'],'launch boot changed')
            for key in ('wall','boottime'):
                need(0<=clock[key]-state['launch_clock'][key]<5,'launch confirmation expired')
            work=observed['properties'][unit['work']]
            need(work.get('ActiveState')=='active' and work.get('MainPID','0').isdigit() and
                 int(work['MainPID'])>0 and work.get('Job') in ('','0'),'work not observed active')
            state.update(phase='WORK_OBSERVED',work_observed=True);self.journal.save(state)
            return copy.deepcopy(state)
        finally:os.close(directory)


def supervise_launch(launch,transition,lease,expected,clock,observed,command,persist_request):
    """Cover the queued-start race without an unguarded firewall exception.

    WAIT_START is limited to five seconds from immutable dispatch intent and
    only a pristine transition. Work must read durable dispatch acceptance and
    pass the ordinary live guard before mutation. Progress after accepted
    dispatch uses preparation guard; an unconfirmed dispatch is recovery-only.
    """
    unit=g.validate_owned(observed,lease)
    try:
        validate(launch,expected,[p for p,_ in links(lease)])
        need(launch['phase'] in ('LAUNCH_INTENT','START_REQUESTED','WORK_OBSERVED'),'launch cannot be supervised')
        need(same(transition.get('lease'),lease),'launch transition lease differs')
        work=observed['properties'][unit['work']]
        if (launch['phase']!='LAUNCH_INTENT' and work.get('ActiveState')=='active' and
                work.get('MainPID','0').isdigit() and int(work['MainPID'])>0):
            return g.supervise_preparation(transition,clock,observed,command,persist_request)
        from cp1_cni_transition import initial as transition_initial
        need(launch['phase']!='WORK_OBSERVED' and
             same(transition,transition_initial(lease,transition['baseline'])),'work stopped after progress')
        need(work.get('ActiveState') in ('inactive','activating','active') and work.get('SubState')!='failed',
             'work startup failed')
        g.verified_guard(observed,lease,clock)
        need(launch['launch_clock']['boot_id']==clock['boot_id'],'launch boot changed')
        for key in ('wall','boottime'):
            need(0<=number(clock[key])-launch['launch_clock'][key]<5,'queued start expired')
        return {'action':'WAIT_START','run_id':lease['run_id'],'full_recovery_complete':False}
    except (ValueError,KeyError,TypeError):
        g.dispatch_recovery(lease,unit,clock,command,persist_request)
        return {'action':'RECOVER','run_id':lease['run_id'],'full_recovery_complete':False}
