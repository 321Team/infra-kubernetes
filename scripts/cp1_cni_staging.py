"""Immutable source staging primitive for the managed CP1 candidate.

No service installation, execution, firewall changes or approval generation.
The caller must obtain reviewed_sha256 independently; hashing unreviewed input
does not constitute approval. Root and every destination ancestor are checked.
"""
import hashlib
import os
from pathlib import Path
import re
import stat

from cp1_cni_membership import need, encoded, fingerprint
from cp1_cni_guard import names

MAX_FILE = 2 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024


def root_directory(path):
    need(os.geteuid() == 0, 'root source staging required')
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'normalized absolute staging path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd);fd=child
            info=os.fstat(fd)
            need(info.st_uid==0 and not info.st_mode & 0o022, 'untrusted staging ancestor')
        need(not os.fstat(fd).st_mode & 0o077, 'staging root must be private')
        return fd
    except BaseException:
        os.close(fd);raise


def validate_manifest(manifest, reviewed_sha256):
    need(isinstance(manifest,dict) and 1 <= len(manifest) <= 64 and
         re.fullmatch(r'[a-f0-9]{64}',reviewed_sha256 or '') and fingerprint(manifest)==reviewed_sha256,
         'reviewed source manifest binding differs')
    for name,digest in manifest.items():
        need(re.fullmatch(r'scripts/[A-Za-z0-9_-]+\.(?:py|json)',name) and
             isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest), 'invalid staged source entry')
    return manifest


def read_regular(directory, name, *, root_owned=False):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    try:
        info=os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=MAX_FILE, 'unsafe source file')
        if root_owned:
            need(info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o400, 'staged source permissions differ')
        with os.fdopen(fd,'rb',closefd=False) as stream:data=stream.read(MAX_FILE+1)
        need(len(data)<=MAX_FILE and len(data)==info.st_size, 'source file changed or exceeds bound')
        return data
    finally:os.close(fd)


def exclusive_write(directory,name,data):
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400,dir_fd=directory)
    try:
        with os.fdopen(fd,'wb',closefd=False) as stream:
            stream.write(data);stream.flush();os.fsync(fd)
    finally:os.close(fd)


def stage_sources(source, destination_root, run_id, manifest, reviewed_sha256):
    """Copy verified bytes once. Failed staging remains visible for diagnosis.

    The final run directory is reserved with mkdir(EXCL behavior); no rename
    can replace an earlier run. STAGED.json is written/fsynced last and is the
    commit marker. Interrupted staging cannot be resumed as a complete bundle.
    """
    names(run_id);validate_manifest(manifest,reviewed_sha256)
    root=root_directory(destination_root)
    source_fd=target=scripts=None
    try:
        # The source may be operator-owned, but no symlink path is followed.
        source_path=Path(source)
        need(source_path.is_absolute() and '..' not in source_path.parts, 'absolute source required')
        source_fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        for part in source_path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=source_fd)
            os.close(source_fd);source_fd=child
        child=os.open('scripts',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=source_fd)
        os.close(source_fd);source_fd=child
        payload={name:read_regular(source_fd,name.split('/')[1]) for name in manifest}
        need(sum(map(len,payload.values()))<=MAX_BUNDLE, 'source bundle size bound')
        need({name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}==manifest, 'source bytes differ')
        os.mkdir(run_id,0o700,dir_fd=root);os.fsync(root)
        target=os.open(run_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        os.mkdir('scripts',0o700,dir_fd=target)
        scripts=os.open('scripts',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=target)
        for name,data in payload.items():exclusive_write(scripts,name.split('/')[1],data)
        os.fsync(scripts)
        receipt={'schema':'fds.cp1-cni-source-stage.v1','run_id':run_id,'manifest':manifest,
                 'reviewed_manifest_sha256':reviewed_sha256,'services_installed':False,'authorizes_apply':False}
        exclusive_write(target,'STAGED.json',encoded(receipt));os.fsync(target);os.fsync(root)
        return receipt
    finally:
        for fd in (scripts,target,source_fd,root):
            if fd is not None:os.close(fd)


def verify_sources(destination_root, run_id, manifest, reviewed_sha256):
    names(run_id);validate_manifest(manifest,reviewed_sha256)
    root=root_directory(Path(destination_root)/run_id)
    scripts=None
    try:
        expected={'schema':'fds.cp1-cni-source-stage.v1','run_id':run_id,'manifest':manifest,
                  'reviewed_manifest_sha256':reviewed_sha256,'services_installed':False,'authorizes_apply':False}
        need(read_regular(root,'STAGED.json',root_owned=True)==encoded(expected),'source stage marker differs')
        scripts=os.open('scripts',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        info=os.fstat(scripts)
        need(info.st_uid==0 and not info.st_mode&0o077,'staged scripts directory differs')
        need(set(os.listdir(scripts))=={n.split('/')[1] for n in manifest},'extra or missing staged source')
        found={n:hashlib.sha256(read_regular(scripts,n.split('/')[1],root_owned=True)).hexdigest() for n in manifest}
        need(found==manifest,'staged source bytes differ')
        return expected
    finally:
        if scripts is not None:os.close(scripts)
        os.close(root)
