"""One-session request/journal coordinator; no network client or supervisor.

The injected transport must authenticate the reviewed endpoint, enforce the
absolute deadline (including DNS/connect/write/read), and never retry/redirect.
It returns (HTTP status, JSON bytes). These are trusted integration requirements,
not guarantees this module can impose on an arbitrary Python callable.
"""
import copy
import hashlib
import json
import os
import threading

import decision as d
import journal as j

MAX_RESPONSE = 1048576
COLLECTION = '/apis/' + d.API + '/' + d.RESOURCE
NOT_SENT_CAUSES = frozenset({'GENERATION_CHANGED', 'CLOCK_INVALID', 'REQUEST_WINDOW_EXPIRED'})


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class CoordinatorError(RuntimeError):
    pass


class RequestNotSent(CoordinatorError):
    """Local failure before entering transport; never inferred from its error."""
    def __init__(self, reason, cause='ADAPTER_FAILURE'):
        super().__init__(reason)
        self.cause = cause  # Returned to the supervisor only; never journaled.


def require(ok, reason):
    if not ok:
        raise CoordinatorError(reason)


def _not_sent(exc):
    # PR83 follow-up: the journal reason stays *_NOT_SENT; the supervisor also
    # needs to know whether the generation changed or only time ran out.
    return dict(not_sent_cause=exc.cause) if isinstance(exc, RequestNotSent) else {}


def decode_response(response):
    require(type(response) is tuple and len(response) == 2, 'RESPONSE_INVALID')
    status, raw = response
    require(type(status) is int and 100 <= status <= 599 and type(raw) is bytes and
            0 < len(raw) <= MAX_RESPONSE, 'RESPONSE_INVALID')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'RESPONSE_INVALID')
            result[key] = value
        return result
    def constant(_):
        raise CoordinatorError('RESPONSE_INVALID')
    try:
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        require(isinstance(body, dict), 'RESPONSE_INVALID')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CoordinatorError('RESPONSE_INVALID') from None
    return status, body


class Coordinator:
    """Caller retains this object; no resume/adoption/retry entry point.

    No default times, budgets, credentials, or endpoint. The local directory,
    manifest/defaulted spec pin, clock and live binding probe must be reviewed.
    One supervisor thread calls create(), then cleanup(trigger=...) as needed.
    """
    def __init__(self, directory_fd, *, binding, manifest, reviewed_manifest_sha256, reviewed_spec_sha256,
                 expires_mono, cleanup_deadline_mono, observation_max_age,
                 request_timeout, clock, binding_probe, transport):
        require(d.binding_ok(binding), 'INPUT_INVALID')
        require(isinstance(manifest, dict) and set(manifest) == {'apiVersion', 'kind', 'metadata', 'spec'} and
                manifest['apiVersion'] == d.API and manifest['kind'] == 'GlobalNetworkPolicy', 'INPUT_INVALID')
        meta = manifest['metadata']
        require(isinstance(meta, dict) and meta.get('name') == d.NAME and
                set(meta) <= {'name', 'annotations', 'labels'}, 'INPUT_INVALID')
        for key in ('annotations', 'labels'):
            require(key not in meta or (isinstance(meta[key], dict) and
                    all(d.text(k) and d.text(v) for k, v in meta[key].items())), 'INPUT_INVALID')
        # The effective response may contain reviewed CRD defaults; its expected
        # hash is separate from the submitted manifest and is never auto-learned.
        d.spec_hash(manifest['spec'])
        require(manifest_hash(manifest) == reviewed_manifest_sha256, 'INPUT_INVALID')
        require(callable(clock) and callable(binding_probe) and callable(transport), 'INPUT_INVALID')
        times = [expires_mono, cleanup_deadline_mono, observation_max_age, request_timeout]
        require(all(d.number(v) and v > 0 for v in times) and expires_mono < cleanup_deadline_mono,
                'INPUT_INVALID')
        self._clock, self._probe, self._transport = clock, binding_probe, transport
        self._binding, self._manifest = copy.deepcopy(binding), copy.deepcopy(manifest)
        self._pin = reviewed_spec_sha256
        self._expires, self._deadline = expires_mono, cleanup_deadline_mono
        self._age, self._timeout = observation_max_age, request_timeout
        self._guard, self._pid = threading.Lock(), os.getpid()
        self._phase, self._session = 'NEW', None
        self._last_clock = None
        self._check(self._expires)
        # This constructor returns only after CREATE_INTENT is durable.
        self._session = j.Session(directory_fd, binding=self._binding, reviewed_spec_sha256=self._pin)

    def _check(self, deadline):
        require(self._probe() == self._binding, 'GENERATION_CHANGED')
        now = self._clock()
        require(d.number(now) and (self._last_clock is None or now >= self._last_clock), 'CLOCK_INVALID')
        self._last_clock = now
        require(now < deadline, 'REQUEST_WINDOW_EXPIRED')
        return now

    def _request(self, method, path, body, deadline):
        try:
            payload = copy.deepcopy(body)
            start = self._check(deadline)
            end = min(deadline, start + self._timeout)
        except Exception as exc:
            if method in {'POST', 'DELETE'}:
                reason = 'CREATE_NOT_SENT' if method == 'POST' else 'DELETE_NOT_SENT'
                cause = str(exc) if type(exc) is CoordinatorError and str(exc) in NOT_SENT_CAUSES else 'ADAPTER_FAILURE'
                raise RequestNotSent(reason, cause) from None
            raise
        try:
            response = self._transport(method=method, path=path, body=payload, deadline_mono=end)
        except Exception:
            # Even RequestNotSent raised by the transport is uncertain: once
            # entered, its exception cannot prove no request reached the API.
            raise CoordinatorError('TRANSPORT_UNCERTAIN') from None
        self._check(end)
        return start, decode_response(response)

    def _handoff(self, reason, **detail):
        self._phase = 'HANDOFF'
        reason = reason if isinstance(reason, str) and reason in j.REASONS else 'ADAPTER_FAILURE'
        recorded = False
        try:
            self._session.handoff(reason)
            recorded = True
        except (j.JournalError, OSError, ValueError, TypeError):
            pass  # Failed/partial journal is preserved; never repair or retry.
        return dict(action='HANDOFF', reason=reason, handoff_recorded=recorded,
                    baseline_verified=False, **detail)

    def _run(self, action):
        require(os.getpid() == self._pid, 'FORKED_COORDINATOR')
        require(self._guard.acquire(blocking=False), 'COORDINATOR_BUSY')
        try:
            require(self._phase not in {'HANDOFF', 'ABSENT', 'CLOSED'}, 'COORDINATOR_TERMINAL')
            try:
                return action()
            except Exception as exc:
                # Both JournalError and OSError stop execution, as PR82 review
                # requires. Unknown exceptions never expose their messages.
                reason = str(exc) if isinstance(exc, (CoordinatorError, j.JournalError, ValueError)) else 'ADAPTER_FAILURE'
                return self._handoff(reason, **_not_sent(exc))
        finally:
            self._guard.release()

    def create(self):
        def action():
            require(self._phase == 'NEW', 'CREATE_NOT_CONFIRMED')
            self._phase = 'CREATE_CLAIMED'  # Transport uncertainty never permits another POST.
            started, (status, body) = self._request('POST', COLLECTION, self._manifest, self._expires)
            inputs = dict(binding=self._binding, http_status=status,
                create_body=body, reviewed_spec_sha256=self._pin, created_mono=started,
                expires_mono=self._expires, cleanup_deadline_mono=self._deadline,
                observation_max_age=self._age)
            # Reject an unconfirmed/drifting response before entering the
            # writer's poison-on-validation-error operation, so the usable
            # journal can still retain the concrete handoff reason.
            d.owned_receipt(**inputs)
            receipt = self._session.record_owned(**inputs)
            self._phase = 'OWNED'
            return dict(action='OWNED', receipt=receipt, baseline_verified=False)
        return self._run(action)

    def cleanup(self, *, trigger):
        def action():
            require(self._phase in {'OWNED', 'DELETE_SENT'}, 'CREATE_NOT_CONFIRMED')
            require(trigger in {'expiry', 'abort', 'complete'}, 'TRIGGER_INVALID')
            started, (status, body) = self._request('GET', d.PATH, None, self._deadline)
            observed = dict(binding=copy.deepcopy(self._binding), requested_name=d.NAME,
                            observed_mono=started, http_status=status, body=body)
            now = self._check(self._deadline)
            result = self._session.plan_delete(binding=self._binding, observation=observed,
                                               now_mono=now, trigger=trigger)
            if result['action'] in {'ABSENT', 'HANDOFF'}:
                self._phase = result['action']
            if result['action'] != 'DELETE_INTENT':
                return dict(result, baseline_verified=False)
            # plan_delete has fsynced intent. Recheck live generation/time again
            # inside _request immediately before the sole permitted DELETE.
            require(self._phase == 'OWNED', 'ADAPTER_FAILURE')
            self._phase = 'DELETE_CLAIMED'
            try:
                _, (status, _body) = self._request('DELETE', result['path'], result['body'], self._deadline)
            except RequestNotSent as exc:
                # Intent remains durable, but there is no response/loss event.
                return self._handoff(str(exc), **_not_sent(exc))
            except CoordinatorError as exc:
                self._session.record_delete_result(None)
                return self._handoff(str(exc))
            self._phase = 'DELETE_SENT'
            self._session.record_delete_result(status)
            return dict(action='READBACK_REQUIRED', http_status=status, baseline_verified=False)
        return self._run(action)

    def close(self):
        require(os.getpid() == self._pid, 'FORKED_COORDINATOR')
        with self._guard:
            self._phase = 'CLOSED'
            self._session.close()
