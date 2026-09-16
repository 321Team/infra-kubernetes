"""Private, serialized, fsynced JSON journal used by managed CNI transactions.

No network/firewall IO. Privileged callers must additionally pin their fixed
root directory and sources. All traversal rejects symlinks. Atomic replacement
does not claim resilience to broken storage or malicious same-UID writers.
"""
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat

MAX_BYTES = 8 * 1024 * 1024


def need(value, message):
    if not value:
        raise ValueError(message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate journal key')
        result[key] = value
    return result


def directory_fd(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'absolute normalized journal directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        need(info.st_uid == os.geteuid() and not info.st_mode & 0o077, 'journal directory must be private and owned')
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_regular(fd):
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077 and
         info.st_nlink == 1, 'journal file must be private, regular, owned and unlinked elsewhere')


class Journal:
    def __init__(self, path):
        self.directory = directory_fd(path)
        self.lock = None
        self.locked = False
        try:
            self.lock = os.open('LOCK', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.directory)
            private_regular(self.lock)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        need(not self.locked, 'journal already locked')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise
        self.locked = True
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None
        self.locked = False

    def load(self):
        need(self.locked, 'journal lock required')
        fd = os.open('STATE.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        try:
            private_regular(fd)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(MAX_BYTES + 1)
            need(len(raw) <= MAX_BYTES, 'journal size bound exceeded')
            def bad(_):
                raise ValueError('non-finite journal value')
            value = json.loads(raw, object_pairs_hook=unique, parse_constant=bad)
            need(isinstance(value, dict), 'journal JSON object required')
            return value
        finally:
            os.close(fd)

    def save(self, value):
        need(self.locked, 'journal lock required')
        raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
        need(isinstance(value, dict) and len(raw) <= MAX_BYTES, 'journal object/size invalid')
        try:
            old = os.open('STATE.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        except FileNotFoundError:
            pass
        else:
            try:
                private_regular(old)
            finally:
                os.close(old)
        name = 'STATE.tmp-' + secrets.token_hex(12)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
            os.replace(name, 'STATE.json', src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=self.directory)
            except FileNotFoundError:
                pass
