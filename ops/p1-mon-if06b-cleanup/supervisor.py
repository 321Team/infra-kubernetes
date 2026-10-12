"""IF06B guardian loop, journal-independent outcome record, read-only watcher.

One process owns the journal writer and every API request of one session: it
creates once, waits for the lease to expire or an operator trigger, sends one
conditional DELETE and reads back. There is no retry/resume/adoption path; any
surprise ends in HANDOFF and is preserved outside the journal for takeover.
"""
import copy
import datetime
import json
import os
import re
import stat

import coordinator as c
import decision as d
import journal as j
import transport as t

STEPS = 'supervisor.jsonl'
CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
TERMINAL = {'ABSENT': 'ABSENT_BASELINE_UNVERIFIED', 'HANDOFF': 'HANDOFF'}


def code(exc):
    """Fixed code for a failure; exception text may hold endpoint or response data."""
    if isinstance(exc, (c.CoordinatorError, j.JournalError, t.TransportError, ValueError)) and CODE.match(str(exc)):
        return str(exc)
    if isinstance(exc, FileExistsError):
        return 'SESSION_FILES_EXIST'
    if isinstance(exc, TimeoutError):
        return 'TIMEOUT'
    return 'OS_ERROR' if isinstance(exc, OSError) else 'UNEXPECTED_FAILURE'


def explicit_not_found(status, body):
    """Same test decision.decide applies before it reports ABSENT."""
    details = body.get('details') if isinstance(body, dict) else None
    return (isinstance(details, dict) and status == 404 and body.get('kind') == 'Status' and
            body.get('apiVersion') == 'v1' and body.get('reason') == 'NotFound' and body.get('code') == 404 and
            details.get('name') == d.NAME and details.get('group') == d.GROUP and details.get('kind') == d.RESOURCE)


class Evidence:
    """Append-only step record in a private directory that is not the journal's.

    A failed write never stops a reclaim; it is reported in `complete` and to
    the stream instead. Only fixed codes, identifiers and times are recorded.

    A stored row cannot attest its own write and fsync, so `evidence_complete`
    is never stored. It is added after the attempt, to the returned row and
    the stream copy only: true means this row and every earlier one were
    written in full and fsynced.
    """
    def __init__(self, directory_fd, *, journal_fd, clock, stream_fd=None, name=STEPS):
        j._directory(directory_fd)
        if journal_fd is not None and os.path.samestat(os.fstat(directory_fd), os.fstat(journal_fd)):
            raise j.JournalError('EVIDENCE_DIRECTORY_NOT_INDEPENDENT')
        self._clock, self._stream, self._sequence, self.complete = clock, stream_fd, 0, True
        self._fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | j._flags(),
                           0o600, dir_fd=directory_fd)
        os.fsync(directory_fd)

    def write(self, step, **fields):
        self._sequence += 1
        try:
            mono = self._clock()
        except Exception:
            mono = None
        row = dict(seq=self._sequence, step=step, mono=mono if d.number(mono) else None,
                   utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds'), **fields)
        try:
            raw = self._encode(row)
            if os.write(self._fd, raw) != len(raw):
                raise OSError  # A short write leaves a torn line; it is not retried.
            os.fsync(self._fd)
        except Exception:
            self.complete = False
        reported = dict(row, evidence_complete=self.complete)
        if self._stream is not None:
            try:
                os.write(self._stream, self._encode(reported))
            except Exception:
                self._stream = None  # The launcher/observer may be gone; keep going.
        return reported

    @staticmethod
    def _encode(row):
        return (json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf-8')

    def release_stream(self):
        if self._stream is not None:
            try:
                os.close(self._stream)
            except OSError:
                pass
            self._stream = None

    def close(self):
        self.release_stream()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class _Tap:
    """Pass-through keeping the identity of a create response for diagnosis only."""
    def __init__(self, transport, clock):
        self._transport, self._clock, self.created = transport, clock, None

    def __call__(self, **request):
        response = self._transport(**request)
        if request.get('method') == 'POST':
            try:
                status, body = c.decode_response(response)
                meta = body.get('metadata') if body.get('kind') == 'GlobalNetworkPolicy' else None
                meta = meta if isinstance(meta, dict) and meta.get('name') == d.NAME else {}
                uid, version = meta.get('uid'), meta.get('resourceVersion')
                self.created = dict(http_status=status, uid=uid if d.text(uid) else None,
                                    resource_version=version if d.text(version) else None,
                                    received_mono=self._clock(), use='DIAGNOSTIC_ONLY_NOT_OWNERSHIP')
            except Exception:
                self.created = None
        return response


def check_target(transport, *, expected_cluster_uid, clock, request_timeout):
    """Read-only start gate: reviewed cluster identity and an explicitly absent canary."""
    status, body = c.decode_response(transport(method='GET', path=t.NAMESPACE, body=None,
                                               deadline_mono=clock() + request_timeout))
    meta = body.get('metadata')
    c.require(status == 200 and body.get('kind') == 'Namespace' and isinstance(meta, dict) and
              meta.get('name') == 'kube-system', 'CLUSTER_READ_FAILED')
    c.require(d.text(expected_cluster_uid) and meta.get('uid') == expected_cluster_uid, 'CLUSTER_CHANGED')
    status, body = c.decode_response(transport(method='GET', path=d.PATH, body=None,
                                               deadline_mono=clock() + request_timeout))
    c.require(status != 200, 'CANARY_ALREADY_PRESENT')
    c.require(explicit_not_found(status, body), 'CANARY_ABSENCE_UNVERIFIED')


def _await(expires, clock, trigger_wait):
    while True:
        left = expires - clock()
        if not left > 0:
            return 'expiry'
        try:
            trigger = trigger_wait(left)
        except Exception:
            return 'abort'  # A broken wait must not silently outlive the lease.
        if trigger in ('abort', 'complete'):
            return trigger


def guard(*, directory_fd, evidence, binding, manifest, reviewed_manifest_sha256, reviewed_spec_sha256,
          lease, cleanup_window, observation_max_age, request_timeout, readback_delay,
          clock, binding_probe, transport, trigger_wait, pause, on_armed=None):
    """Run one session to a terminal journal state or a preserved handoff."""
    def final(disposition, reason, **extra):
        last = getattr(transport, 'last', None)
        return evidence.write('FINAL', disposition=disposition, reason=reason,
                              transport_last=copy.deepcopy(last) if isinstance(last, dict) else None,
                              evidence_complete_before_write=evidence.complete, automatic_replay_allowed=False,
                              baseline_verified=False, **extra)

    def step(name, result):
        receipt = result.get('receipt') if isinstance(result.get('receipt'), dict) else {}
        evidence.write(name, action=result.get('action'), reason=result.get('reason'),
                       http_status=result.get('http_status'), handoff_recorded=result.get('handoff_recorded'),
                       not_sent_cause=result.get('not_sent_cause'), uid=receipt.get('uid'),
                       resource_version=receipt.get('created_resource_version'))

    tap, attempted = _Tap(transport, clock), False
    try:
        for value in (lease, cleanup_window, observation_max_age, request_timeout):
            c.require(d.number(value) and value > 0, 'INPUT_INVALID')
        # One GET, one DELETE and one readback must fit; nothing here is a default.
        c.require(d.number(readback_delay) and lease > request_timeout and
                  cleanup_window > 3 * request_timeout + readback_delay, 'INPUT_INVALID')
        expires = clock() + lease
        evidence.write('ARMING', binding=copy.deepcopy(binding), expires_mono=expires,
                       cleanup_deadline_mono=expires + cleanup_window)
        co = c.Coordinator(directory_fd, binding=binding, manifest=manifest,
                           reviewed_manifest_sha256=reviewed_manifest_sha256,
                           reviewed_spec_sha256=reviewed_spec_sha256, expires_mono=expires,
                           cleanup_deadline_mono=expires + cleanup_window,
                           observation_max_age=observation_max_age, request_timeout=request_timeout,
                           clock=clock, binding_probe=binding_probe, transport=tap)
    except Exception as exc:
        # No CREATE was sent. Existing session files are never reused or removed.
        return final('NOT_STARTED', code(exc), create_sent=False)
    try:
        evidence.write('ARMED')  # CREATE_INTENT is durable; the sole POST follows.
        if on_armed is not None:
            on_armed()
        attempted = True
        result = co.create()
        step('CREATE', result)
        if result.get('action') != 'OWNED':
            return final('HANDOFF', result.get('reason'), terminal_recorded=result.get('handoff_recorded'),
                         not_sent_cause=result.get('not_sent_cause'), unowned_create_response=tap.created)
        trigger = _await(expires, clock, trigger_wait)
        evidence.write('TRIGGER', trigger=trigger)
        result = co.cleanup(trigger=trigger)
        step('CLEANUP', result)
        if result.get('action') == 'READBACK_REQUIRED':
            pause(readback_delay)
            result = co.cleanup(trigger=trigger)
            step('READBACK', result)
        action = result.get('action')
        if action not in TERMINAL:
            return final('HANDOFF', 'SUPERVISOR_UNEXPECTED_ACTION', terminal_recorded=False)
        # A terminal decision without this key was appended by plan_delete itself.
        return final(TERMINAL[action], result.get('reason'), terminal_recorded=result.get('handoff_recorded', True),
                     not_sent_cause=result.get('not_sent_cause'))
    except Exception as exc:
        # The journal may hold no terminal record; the watcher reports that case.
        return final('HANDOFF', 'SUPERVISOR_FAILURE', detail=code(exc), terminal_recorded=False,
                     create_attempted=attempted, unowned_create_response=tap.created)
    finally:
        try:
            co.close()
        except Exception:
            pass


def watch(directory_fd, *, poll, until_mono, clock, pause):
    """Read-only. Reports the journal's end, or a writer that left without one."""
    def report(state, handoff_required, **extra):
        return dict(state=state, handoff_required=handoff_required, automatic_replay_allowed=False,
                    baseline_verified=False, **extra)
    c.require(d.number(poll) and poll > 0 and d.number(until_mono), 'INPUT_INVALID')
    while True:
        try:
            history = j.inspect_history(directory_fd)
        except BlockingIOError:  # The writer still holds its exclusive lock.
            if not clock() < until_mono:
                return report('WRITER_STILL_HOLDING_AFTER_WINDOW', True)
            pause(poll)
            continue
        except FileNotFoundError:
            return report('NO_SESSION_FILES', True)
        except (j.JournalError, OSError, ValueError):
            return report('HISTORY_UNREADABLE', True)
        receipt = history['receipt'] or {}
        identity = dict(last_event=history['last_event'], uid=receipt.get('uid'), phase=receipt.get('phase'),
                        created_resource_version=receipt.get('created_resource_version'),
                        terminal_diagnostic=history['terminal_diagnostic'])
        if history['last_event'] == 'ABSENT':
            return report('ABSENT_BASELINE_UNVERIFIED', False, **identity)
        if history['last_event'] == 'HANDOFF':
            return report('HANDOFF', True, **identity)
        # Manual takeover needs a fresh authenticated GET of target/spec/UID/RV.
        return report('WRITER_GONE_WITHOUT_TERMINAL', True, **identity)


def preflight(transport, *, expected_cluster_uid, manifest, reviewed_spec_sha256, clock, request_timeout):
    """Non-persisting checks: identity, absence, access reviews, server dry-run pin."""
    checks = {}

    def call(method, path, body):
        return c.decode_response(transport(method=method, path=path, body=body,
                                           deadline_mono=clock() + request_timeout))

    def run(name, check):
        try:
            checks[name] = 'PASS' if check() else 'FAIL'
        except Exception as exc:
            checks[name] = code(exc)

    def target():
        check_target(transport, expected_cluster_uid=expected_cluster_uid, clock=clock,
                     request_timeout=request_timeout)
        return True

    def allowed(verb, named):
        attributes = dict(group=d.GROUP, resource=d.RESOURCE, verb=verb)
        if named:
            attributes['name'] = d.NAME
        status, body = call('POST', t.ACCESS_REVIEW, dict(
            apiVersion='authorization.k8s.io/v1', kind='SelfSubjectAccessReview',
            spec=dict(resourceAttributes=attributes)))
        return status == 201 and isinstance(body.get('status'), dict) and body['status'].get('allowed') is True

    observed = {}

    def dry_run():
        status, body = call('POST', t.DRY_RUN, manifest)
        meta = body.get('metadata')
        c.require(status == 201 and body.get('kind') == 'GlobalNetworkPolicy' and isinstance(meta, dict) and
                  meta.get('name') == d.NAME and not meta.get('finalizers'), 'DRY_RUN_REJECTED')
        observed['defaulted_spec_sha256'] = d.spec_hash(body.get('spec'))
        return observed['defaulted_spec_sha256'] == reviewed_spec_sha256

    run('cluster_and_absence', target)
    later = [('access_' + verb, lambda verb=verb, named=named: allowed(verb, named))
             for verb, named in (('create', False), ('get', True), ('delete', True))]
    for name, check in later + [('dry_run_spec_pin', dry_run)]:
        if checks['cluster_and_absence'] == 'PASS':  # Send no POST to an unverified cluster.
            run(name, check)
        else:
            checks[name] = 'NOT_RUN'
    return dict(checks=checks, passed=all(v == 'PASS' for v in checks.values()), persisted_writes=0, **observed)


def directory(path):
    """Open a reviewed private directory; it is never created or re-moded here."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not (os.path.isabs(path) and stat.S_ISDIR(os.fstat(fd).st_mode)):
            raise j.JournalError('PRIVATE_DIRECTORY_REQUIRED')
        j._directory(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd
