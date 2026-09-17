"""Read-only, nofollow cgroup2 quiescence collector for compiled owned units.

Only absence of the exact service directory is absence evidence. Missing
parents/events, wrong filesystem, access failures and symlinks remain errors.
An authenticated root service caller supplies the bounded live unit reader.
"""
import copy
import ctypes
import os
from pathlib import Path
import stat
import sys
import time

from cp1_cni_membership import need, same, number
from cp1_cni_guard import names, validate_owned
from cp1_cni_staging import root_directory
from cp1_cni_recovery import cgroup_empty

ROOT=Path('/sys/fs/cgroup')
CGROUP2_MAGIC=0x63677270


def filesystem_type(fd):
    need(sys.platform=='linux' and ctypes.sizeof(ctypes.c_long)==8,'supported 64-bit Linux ABI required')
    libc=ctypes.CDLL(None,use_errno=True)
    call=libc.fstatfs;call.argtypes=[ctypes.c_int,ctypes.c_void_p];call.restype=ctypes.c_int
    # Linux statfs is smaller than this buffer on the supported 64-bit ABI;
    # its first member is the signed long filesystem type.
    buffer=ctypes.create_string_buffer(256)
    if call(fd,buffer)!=0:
        code=ctypes.get_errno();raise OSError(code,os.strerror(code))
    return ctypes.c_long.from_buffer(buffer).value


def read_events(lease,kind):
    unit=names(lease['run_id'])
    need(kind in ('work','guard','recover'),'cgroup service scope differs')
    name=unit[kind];path=ROOT/'system.slice'/name
    root=root_directory(ROOT,private=False);parent=directory=events=None
    try:
        need(filesystem_type(root)==CGROUP2_MAGIC,'cgroup root is not cgroup2')
        parent=os.open('system.slice',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        need(filesystem_type(parent)==CGROUP2_MAGIC,'service slice is not cgroup2')
        info=os.fstat(parent)
        need(info.st_uid==0 and not info.st_mode&0o022,'service slice delegated/untrusted')
        try:directory=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        except FileNotFoundError:
            return {'path':str(path),'filesystem':'cgroup2','events':None}
        info=os.fstat(directory)
        need(filesystem_type(directory)==CGROUP2_MAGIC and info.st_uid==0 and not info.st_mode&0o022,
             'service cgroup delegated/untrusted')
        events=os.open('cgroup.events',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        info=os.fstat(events)
        need(filesystem_type(events)==CGROUP2_MAGIC and stat.S_ISREG(info.st_mode) and info.st_uid==0,
             'cgroup events not a trusted kernel file')
        raw=os.read(events,4097)
        need(len(raw)<=4096,'cgroup events exceed size bound')
        # Confirm the opened inode still has the compiled path. An empty
        # unlinked directory must not stand in for a new populated one.
        current=os.stat(name,dir_fd=parent,follow_symlinks=False)
        opened=os.fstat(directory)
        need(stat.S_ISDIR(current.st_mode) and (current.st_dev,current.st_ino)==(opened.st_dev,opened.st_ino),
             'service cgroup replaced during read')
        return {'path':str(path),'filesystem':'cgroup2','events':raw.decode('ascii')}
    finally:
        for fd in (events,directory,parent,root):
            if fd is not None:os.close(fd)


def quiesced(lease,kind,observe,*,deadline,monotonic=time.monotonic):
    """Bracket the recursive kernel read with fresh unit ownership/liveness.

    observe(deadline=...) enforces the same absolute deadline; returning an
    old snapshot is invalid. Caller repeats this after obtaining global lock.
    """
    end=number(deadline);need(0<end-monotonic()<=10,'quiescence deadline differs')
    before=observe(deadline=end);unit=validate_owned(before,lease)
    need(kind in ('work','guard','recover'),'quiescence service differs')
    need(monotonic()<end,'quiescence deadline exhausted')
    raw=read_events(lease,kind)
    cgroup_empty(before['properties'][unit[kind]],raw)
    after=observe(deadline=end);validate_owned(after,lease)
    need(monotonic()<end,'quiescence deadline exhausted')
    cgroup_empty(after['properties'][unit[kind]],raw)
    keys=('Id','LoadState','ActiveState','SubState','MainPID','Job','ControlGroup','FragmentPath','DropInPaths')
    need(same({k:before['properties'][unit[kind]].get(k) for k in keys},
              {k:after['properties'][unit[kind]].get(k) for k in keys}), 'unit changed across cgroup read')
    return copy.deepcopy(after),raw
