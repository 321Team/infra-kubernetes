"""Pinned local unit reads and a closed set of bounded systemctl operations.

No CLI and no authorization creation. The root orchestrator verifies the
staged activation, ownership and durable intent before invoking mutations.
"""
import hashlib
import os
from pathlib import Path
import time

import cp1_cni_guard as g
from cp1_cni_membership import need, number, same
from cp1_cni_staging import root_directory, read_regular
from cp1_cni_unit_cleanup import inspect
from cp1_cni_transition_io import NODE_SHA


class LocalSystemd:
    def __init__(self,node,lease,*,deadline,monotonic=time.monotonic):
        need(os.geteuid()==0,'root systemd adapter required')
        need(hashlib.sha256(Path(node.__file__).read_bytes()).hexdigest()==NODE_SHA,'systemd helper source differs')
        self.node,self.lease,self.deadline,self.monotonic=node,lease,number(deadline),monotonic
        need(0<self.deadline-self.monotonic()<=150,'systemd invocation deadline differs')
        self.unit=g.names(lease['run_id'])

    def command(self,args,*,timeout):
        timeout=min(number(timeout),5,self.deadline-self.monotonic())
        need(timeout>0,'systemd command deadline exhausted')
        allowed=[['systemctl','daemon-reload'],['systemctl','--no-block','start',self.unit['timer'],self.unit['work']],
                 ['systemctl','--no-block','start',self.unit['recover']],['systemctl','stop',self.unit['work']],
                 ['systemctl','stop',*[self.unit[k] for k in ('timer','guard','work','recover')]]]
        read=any(args==['systemctl','show',name]+[a for p in g.PROPERTIES for a in ('-p',p)]
                 for name in self.unit.values())
        bus_paths={'/org/freedesktop/systemd1/unit/'+''.join(c if c.isascii() and c.isalnum()
                   else '_'+format(ord(c),'02x') for c in name) for name in self.unit.values()}
        read=read or (args[:4]==['busctl','--system','--timeout=3','get-property'] and len(args)==8 and
                     args[4]=='org.freedesktop.systemd1' and args[5] in bus_paths and
                     args[6] in ('org.freedesktop.systemd1.Service','org.freedesktop.systemd1.Timer') and
                     args[7] in g.PROPERTIES)
        need(read or any(same(args,row) for row in allowed),'systemd operation outside exact run')
        previous=dict(self.node.COMMAND_STATE)
        try:
            self.node.COMMAND_STATE.update(deadline=self.deadline)
            result=self.node.command(args,timeout=timeout)
            need(self.monotonic()<self.deadline,'systemd result arrived after deadline')
            return {'rc':result['rc'],'stdout':result['stdout'],'stderr':result['stderr'],
                    'truncated':result.get('stdout_truncated',False) is not False or result.get('stderr_truncated',False) is not False}
        finally:
            self.node.COMMAND_STATE.clear();self.node.COMMAND_STATE.update(previous)

    def __call__(self,*,deadline=None,include_links=False):
        end=min(self.deadline,number(deadline)) if deadline is not None else self.deadline
        need(end>self.monotonic(),'systemd observation deadline exhausted')
        directory=root_directory(g.UNITS,private=False)
        try:
            def read_file(path):
                need(path.parent==g.UNITS and path.name in g.documents(self.lease),'unexpected unit file path')
                return read_regular(directory,path.name,root_owned=True)
            def read_link(path):
                relative=str(path.relative_to(g.UNITS))
                allowed={str(Path('multi-user.target.wants')/self.unit['recover']),
                         str(Path('timers.target.wants')/self.unit['timer'])}
                need(relative in allowed,'unexpected unit link path')
                return inspect(directory,relative,'link')
            return g.query_units(self.lease,self.command,read_file,read_link,include_links=include_links,deadline=end)
        finally:os.close(directory)
