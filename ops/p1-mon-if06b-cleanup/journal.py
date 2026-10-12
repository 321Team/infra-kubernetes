"""Linux local journal for IF06B; no API, credentials, CLI, or recovery replay.

The caller supplies a trusted, already-open private directory on durable local
storage. No directory, permission, service, or cluster policy is provisioned.
"""
import copy
from contextlib import contextmanager
import json
import os
import stat
import sys
import threading

import decision as d

if sys.platform == 'linux':
    import fcntl

JOURNAL = 'journal.jsonl'
LOCK = 'writer.lock'
MAX_LINE = 65536
MAX_FILE = 1048576


class JournalError(RuntimeError):
    """Stop further requests, preserve files, and hand over; never auto-retry."""


def _require(ok, reason):
    if not ok:
        raise JournalError(reason)


def _directory(fd):
    _require(sys.platform == 'linux', 'LINUX_REQUIRED')
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
             stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')


def _file(fd, directory, name):
    info = os.fstat(fd)
    path = os.stat(name, dir_fd=directory, follow_symlinks=False)
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
             stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
             (info.st_dev, info.st_ino) == (path.st_dev, path.st_ino),
             'PRIVATE_FILE_CHANGED')
    return info


def _flags():
    return os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _transition(state, event, payload):
    """Validate a bounded event history; it is never a replay authorization."""
    state = copy.deepcopy(state)
    phase = state.get('event')
    if event == 'CREATE_INTENT':
        _require(not state and isinstance(payload, dict) and
                 set(payload) == {'binding', 'spec_sha256'} and
                 d.binding_ok(payload['binding']), 'CREATE_INTENT_INVALID')
        pin = payload['spec_sha256']
        _require(isinstance(pin, str) and len(pin) == 64 and
                 all(c in '0123456789abcdef' for c in pin), 'SPEC_PIN_INVALID')
        state.update(copy.deepcopy(payload))
    elif event == 'OWNED':
        _require(phase == 'CREATE_INTENT', 'CREATE_ALREADY_RECORDED')
        d.validate_receipt(payload)
        _require(payload['phase'] == 'OWNED' and
                 payload['binding'] == state['binding'] and
                 payload['spec_sha256'] == state['spec_sha256'], 'OWNERSHIP_CHANGED')
        state['receipt'] = copy.deepcopy(payload)
    elif event == 'DELETE_INTENT':
        _require(phase == 'OWNED' and isinstance(payload, dict) and
                 set(payload) == {'receipt', 'path', 'body'}, 'DELETE_INTENT_INVALID')
        expected = dict(state['receipt'], phase='DELETE_INTENT')
        _require(payload['receipt'] == expected and payload['path'] == d.PATH,
                 'DELETE_IDENTITY_CHANGED')
        body = payload['body']
        _require(isinstance(body, dict) and set(body) == {'apiVersion', 'kind', 'preconditions'} and
                 body['apiVersion'] == 'v1' and body['kind'] == 'DeleteOptions', 'DELETE_BODY_INVALID')
        pre = body['preconditions']
        _require(isinstance(pre, dict) and set(pre) == {'uid', 'resourceVersion'} and
                 pre['uid'] == expected['uid'] and d.text(pre['resourceVersion']), 'DELETE_PRECONDITION_INVALID')
        state['receipt'] = copy.deepcopy(expected)
    elif event == 'DELETE_RESULT':
        _require(phase == 'DELETE_INTENT' and isinstance(payload, dict) and
                 set(payload) == {'http_status', 'receipt'}, 'DELETE_RESULT_INVALID')
        status = payload['http_status']
        _require(status is None or (type(status) is int and 100 <= status <= 599), 'STATUS_INVALID')
        expected = d.record_delete_result(state['receipt'], status)
        _require(payload['receipt'] == expected, 'DELETE_RESULT_CHANGED')
        state['receipt'] = copy.deepcopy(expected)
    elif event == 'ABSENT':
        _require(phase in {'OWNED', 'DELETE_INTENT', 'DELETE_RESULT'} and payload == {}, 'ABSENCE_ORDER_INVALID')
    elif event == 'HANDOFF':
        _require(phase in {'CREATE_INTENT', 'OWNED', 'DELETE_INTENT', 'DELETE_RESULT'} and
                 payload == {}, 'HANDOFF_ORDER_INVALID')
    else:
        raise JournalError('EVENT_INVALID')
    state['event'] = event
    return state


class Session:
    """Fresh process-local writer. Constructor success precedes one CREATE.

    Existing journals (even empty ones) are never reopened for writing. Keep the
    session alive across the network calls, performed by a separate adapter.
    """
    def __init__(self, directory_fd, *, binding, reviewed_spec_sha256):
        self._guard = threading.Lock()
        self._pid = os.getpid()
        self._active = False
        self._dir = self._lock = self._fd = None
        self._state, self._sequence, self._size = {}, 0, 0
        _directory(directory_fd)
        payload = dict(binding=copy.deepcopy(binding), spec_sha256=reviewed_spec_sha256)
        _transition({}, 'CREATE_INTENT', payload)  # Validate before creating files.
        try:
            self._dir = os.dup(directory_fd)
            self._lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | _flags(), 0o600, dir_fd=self._dir)
            _file(self._lock, self._dir, LOCK)
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = os.open(JOURNAL, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | _flags(),
                               0o600, dir_fd=self._dir)
            self._active = True
            self._append('CREATE_INTENT', payload)
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _operation(self):
        _require(os.getpid() == self._pid, 'FORKED_WRITER_REJECTED')
        _require(self._guard.acquire(blocking=False), 'WRITER_BUSY')
        try:
            _require(self._active, 'WRITER_CLOSED_OR_FAILED')
            self._check_files()
            yield
        except BaseException:
            self._active = False
            raise
        finally:
            self._guard.release()

    def _check_files(self):
        _directory(self._dir)
        _file(self._lock, self._dir, LOCK)
        _require(_file(self._fd, self._dir, JOURNAL).st_size == self._size,
                 'JOURNAL_SIZE_CHANGED')

    def _append(self, event, payload):
        # Any uncertainty poisons this instance; close only releases descriptors.
        try:
            self._check_files()
            next_state = _transition(self._state, event, payload)
            entry = dict(seq=self._sequence + 1, event=event, payload=payload)
            raw = (json.dumps(entry, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            _require(len(raw) <= MAX_LINE and self._size + len(raw) <= MAX_FILE, 'JOURNAL_LIMIT')
            pending = memoryview(raw)
            while pending:
                count = os.write(self._fd, pending)
                _require(count > 0, 'SHORT_WRITE')
                pending = pending[count:]
            os.fsync(self._fd)
            os.fsync(self._dir)
            self._size += len(raw)
            self._sequence += 1
            self._state = next_state
            self._check_files()
        except BaseException:
            self._active = False
            raise

    def record_owned(self, **create_inputs):
        """Only the unchanged decision module's confirmed HTTP201 is accepted."""
        with self._operation():
            receipt = d.owned_receipt(**create_inputs)
            self._append('OWNED', receipt)
            return copy.deepcopy(receipt)

    def plan_delete(self, *, binding, observation, now_mono, trigger):
        """Return one intent only AFTER file and directory fsync succeed."""
        with self._operation():
            _require(self._state['event'] in {'OWNED', 'DELETE_INTENT', 'DELETE_RESULT'},
                     'OBSERVATION_PHASE_INVALID')
            result = d.decide(receipt=self._state['receipt'], binding=binding,
                              observation=observation, now_mono=now_mono, trigger=trigger)
            action = result['action']
            if action == 'DELETE_INTENT':
                self._append('DELETE_INTENT', dict(receipt=result['next_receipt'],
                                                   path=result['path'], body=result['body']))
            elif action in {'ABSENT', 'HANDOFF'}:
                self._append(action, {})
            return result

    def record_delete_result(self, http_status):
        with self._operation():
            _require(self._state['event'] == 'DELETE_INTENT', 'DELETE_INTENT_REQUIRED')
            receipt = d.record_delete_result(self._state['receipt'], http_status)
            self._append('DELETE_RESULT', dict(http_status=http_status, receipt=receipt))
            return copy.deepcopy(receipt)

    def handoff(self):
        """Create/transport uncertainty: retain history and end automatic work."""
        with self._operation():
            self._append('HANDOFF', {})

    def close(self):
        # Never LOCK_UN: closing a forked child's duplicate must not unlock its parent.
        with self._guard:
            self._active = False
            for key in ('_fd', '_lock', '_dir'):
                fd = getattr(self, key)
                if fd is not None:
                    os.close(fd)
                    setattr(self, key, None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        _require(key not in out, 'DUPLICATE_JSON_KEY')
        out[key] = value
    return out


def inspect_history(directory_fd):
    """Read-only handover. Even a valid prefix never permits CREATE/DELETE replay."""
    _directory(directory_fd)
    lock = fd = None
    try:
        lock = os.open(LOCK, os.O_RDONLY | _flags(), dir_fd=directory_fd)
        _file(lock, directory_fd, LOCK)
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fd = os.open(JOURNAL, os.O_RDONLY | _flags(), dir_fd=directory_fd)
        before = _file(fd, directory_fd, JOURNAL)
        _require(0 < before.st_size <= MAX_FILE, 'JOURNAL_SIZE_INVALID')
        raw = bytearray()
        while len(raw) <= MAX_FILE:
            chunk = os.read(fd, min(65536, MAX_FILE + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        _require(len(raw) == before.st_size and raw.endswith(b'\n'), 'JOURNAL_INCOMPLETE')
        state = {}
        for sequence, line in enumerate(raw.splitlines(), 1):
            _require(len(line) < MAX_LINE, 'JOURNAL_LIMIT')
            entry = json.loads(line, object_pairs_hook=_unique_pairs)
            _require(isinstance(entry, dict) and set(entry) == {'seq', 'event', 'payload'} and
                     type(entry['seq']) is int and entry['seq'] == sequence, 'SEQUENCE_INVALID')
            state = _transition(state, entry['event'], entry['payload'])
        after = _file(fd, directory_fd, JOURNAL)
        _file(lock, directory_fd, LOCK)
        _directory(directory_fd)
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'HISTORY_CHANGED')
        return dict(last_event=state['event'], binding=state['binding'],
                    receipt=state.get('receipt'), automatic_replay_allowed=False,
                    disposition='HANDOFF_BASELINE_UNVERIFIED')
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise JournalError('HISTORY_INVALID') from exc
    finally:
        for handle in (fd, lock):
            if handle is not None:
                os.close(handle)
