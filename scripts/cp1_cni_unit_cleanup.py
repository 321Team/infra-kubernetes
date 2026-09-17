"""Crash-aware removal of exact owned unit files, outside owned services.

No stop/enable/start is performed here. Trusted external orchestration first
stops all four units, verifies recursive cgroups and original whole firewall,
and confirms a new authenticated management session. This primitive requires
that fresh proof before each unlink and again after daemon reload. It keeps
the run sources/journals and never deletes an unjournaled foreign file/link.
"""
import copy
import os
import stat
from pathlib import Path

import cp1_cni_guard as guard
from cp1_cni_membership import need, same, fingerprint
from cp1_cni_staging import root_directory, read_regular, verify_sources
from cp1_cni_unit_files import binding, validate_state as validate_publication

SCHEMA='fds.cp1-cni-unit-cleanup.v1'


def artifacts(lease):
    names=guard.names(lease['run_id'])
    return [(str(Path('timers.target.wants')/names['timer']),'link',str(guard.UNITS/names['timer'])),
            (str(Path('multi-user.target.wants')/names['recover']),'link',str(guard.UNITS/names['recover']))] + [
            (name,'file',content) for name,content in sorted(guard.documents(lease).items())]


def initial(expected):
    return {'schema':SCHEMA,'binding':copy.deepcopy(expected),'completed':[], 'pending':None,
            'phase':'REMOVING','units_removed':False,'full_recovery_complete':False,'authorizes_apply':False}


def validate(state,expected,order):
    need(set(state)==set(initial(expected)) and state['schema']==SCHEMA and same(state['binding'],expected),
         'unit cleanup binding differs')
    done=state['completed']
    need(isinstance(done,list) and done==order[:len(done)] and len(done)<=len(order),'cleanup prefix differs')
    need(state['pending'] is None or len(done)<len(order) and state['pending']==order[len(done)],'cleanup pending differs')
    need(state['phase'] in ('REMOVING','RELOAD_INTENT','UNITS_REMOVED'),'unknown cleanup phase')
    if state['phase']!='REMOVING':need(done==order and state['pending'] is None,'cleanup files incomplete')
    need(state['units_removed'] is (state['phase']=='UNITS_REMOVED') and state['full_recovery_complete'] is False and
         state['authorizes_apply'] is False,'file cleanup cannot claim full recovery/authority')


def parent(directory,relative):
    value=Path(relative)
    need(not value.is_absolute() and '..' not in value.parts and len(value.parts)<=2,'invalid unit artifact path')
    if len(value.parts)==1:return os.dup(directory),value.name
    fd=os.open(value.parts[0],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
    info=os.fstat(fd)
    if info.st_uid!=0 or info.st_mode&0o022:
        os.close(fd);raise ValueError('untrusted wants directory')
    return fd,value.name


def inspect(directory,relative,kind):
    fd,name=parent(directory,relative)
    try:
        try:info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:return None
        if kind=='link':
            need(stat.S_ISLNK(info.st_mode) and info.st_uid==0 and info.st_nlink==1,'unsafe owned unit link')
            return os.readlink(name,dir_fd=fd)
        return read_regular(fd,name,root_owned=True).decode()
    finally:os.close(fd)


def remove(directory,relative):
    fd,name=parent(directory,relative)
    try:os.unlink(name,dir_fd=fd);os.fsync(fd)
    finally:os.close(fd)


class UnitCleanup:
    """fresh_quiescent_proof() raises on any incomplete live proof.

    The callback must run outside ALL owned cgroups, verify loaded unit
    ownership, inactive/failed + PID0 + no jobs + recursive cgroups empty,
    unchanged original firewall, and management. After some files disappear,
    only this durable known prefix may substitute for on-disk ownership;
    actual loaded properties and cgroups must still be read each time.
    It must not trust a submitted boolean or an old recovery receipt.
    """
    def __init__(self,lease,manifest,reviewed_sha256,publication,journal,fresh_quiescent_proof,*,launch_state=None):
        self.lease,self.manifest=copy.deepcopy(lease),copy.deepcopy(manifest)
        self.sha,self.journal,self.proof=reviewed_sha256,journal,fresh_quiescent_proof
        self.expected=binding(lease,guard.UNITS,manifest,reviewed_sha256)
        validate_publication(publication,self.expected,sorted(guard.documents(lease)))
        need(publication['phase']=='DEFINITIONS_LOADED','owned publication not complete')
        self.items=artifacts(lease);self.order=[v[0] for v in self.items]
        self.candidates={p:[v] for p,k,v in self.items}
        if launch_state is not None:
            from cp1_cni_service_control import link_expectations
            self.candidates.update(link_expectations(launch_state,self.expected,lease))
            self.expected={**self.expected,'launch_sha256':fingerprint(launch_state)}

    def sources(self):verify_sources(guard.ROOT,self.lease['run_id'],self.manifest,self.sha)

    def run(self,command,verify_absent):
        self.sources();directory=root_directory(guard.UNITS,private=False)
        try:
            try:state=self.journal.load()
            except FileNotFoundError:
                # A full installation requires every artifact. A partial
                # enablement prefix permits absence only where its immutable
                # launch journal proves never-created or uncertain creation.
                need(all(inspect(directory,p,k) in self.candidates[p] for p,k,v in self.items),
                     'unproven initial owned unit artifacts')
                state=initial(self.expected);self.proof(copy.deepcopy(state));self.journal.save(state)
            validate(state,self.expected,self.order)
            for index,(path,kind,expected) in enumerate(self.items):
                actual=inspect(directory,path,kind)
                if index<len(state['completed']):
                    need(actual is None,'removed unit artifact reappeared');continue
                need(state['phase']=='REMOVING','cleanup phase/prefix differs')
                if state['pending'] is None:
                    need(actual in self.candidates[path],'owned unit artifact missing/foreign before intent')
                    state['pending']=path;self.journal.save(state)
                else:need(state['pending']==path and actual in [None,*self.candidates[path]],'pending removal has foreign state')
                self.sources();self.proof(copy.deepcopy(state))
                # Re-read after external proof before unlink. No recursive rm,
                # symlink following or unrelated-file adoption is allowed.
                actual=inspect(directory,path,kind)
                need(actual in [None,*self.candidates[path]],'artifact changed while checking live state')
                if actual is not None:remove(directory,path)
                else:
                    fd,_=parent(directory,path)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                state['completed'].append(path);state['pending']=None;self.journal.save(state)
            state.update(phase='RELOAD_INTENT',units_removed=False);self.journal.save(state)
            self.proof(copy.deepcopy(state))
            result=command(['systemctl','daemon-reload'],timeout=5)
            need(type(result.get('rc')) is int and result['rc']==0 and result.get('truncated') is False and
                 result.get('stdout','').strip()==result.get('stderr','').strip()=='','cleanup reload uncertain')
            need(all(inspect(directory,p,k) is None for p,k,_ in self.items),'unit artifacts remain after reload')
            # Must check not-found, dead/PID0/no jobs, cgroup absence/empty,
            # original firewall and fresh management from outside the units.
            verify_absent(copy.deepcopy(state))
            state.update(phase='UNITS_REMOVED',units_removed=True);self.journal.save(state)
            return copy.deepcopy(state)
        finally:os.close(directory)
