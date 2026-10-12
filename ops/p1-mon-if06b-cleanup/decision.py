"""Pure IF06B cleanup decisions. No API, subprocess, file I/O, or clock reads.

This is not a guardian or runtime executor. A reviewed adapter must authenticate
observations, persist intent before DELETE, and retain single-writer ownership.
"""
import hashlib
import json
import math

NAME = 'p1-mon-if06b-worker03-to-monitor01-canary'
GROUP = 'crd.projectcalico.org'
API = GROUP + '/v1'
RESOURCE = 'globalnetworkpolicies'
PATH = '/apis/' + API + '/' + RESOURCE + '/' + NAME
BINDING_KEYS = {'session_id', 'cluster_uid', 'host_boot_id', 'guardian_epoch'}
PHASES = {'OWNED', 'DELETE_INTENT', 'DELETE_ACK', 'DELETE_UNKNOWN', 'CONFLICT'}
RECEIPT_KEYS = {'schema', 'binding', 'name', 'uid', 'created_resource_version',
                'spec_sha256', 'created_mono', 'expires_mono', 'cleanup_deadline_mono',
                'observation_max_age', 'phase'}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 1024 and not any(ord(c) < 32 for c in value)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def binding_ok(value):
    return isinstance(value, dict) and set(value) == BINDING_KEYS and all(text(v) for v in value.values())


def spec_hash(value):
    require(isinstance(value, dict) and bool(value), 'SPEC_INVALID')
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def object_fields(body):
    require(isinstance(body, dict), 'BODY_INVALID')
    require(body.get('apiVersion') == API and body.get('kind') == 'GlobalNetworkPolicy', 'KIND_INVALID')
    meta = body.get('metadata')
    require(isinstance(meta, dict) and meta.get('name') == NAME and not meta.get('namespace'), 'NAME_INVALID')
    require(text(meta.get('uid')) and text(meta.get('resourceVersion')), 'IDENTITY_INVALID')
    return meta, spec_hash(body.get('spec'))


def validate_receipt(receipt):
    require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, 'RECEIPT_INVALID')
    require(type(receipt['schema']) is int and receipt['schema'] == 1, 'SCHEMA_INVALID')
    require(binding_ok(receipt['binding']) and receipt['name'] == NAME, 'BINDING_INVALID')
    require(text(receipt['uid']) and text(receipt['created_resource_version']), 'IDENTITY_INVALID')
    pin = receipt['spec_sha256']
    require(isinstance(pin, str) and len(pin) == 64 and all(c in '0123456789abcdef' for c in pin), 'SPEC_PIN_INVALID')
    times = [receipt[k] for k in ('created_mono', 'expires_mono', 'cleanup_deadline_mono', 'observation_max_age')]
    require(all(number(x) for x in times), 'TIME_INVALID')
    require(times[0] < times[1] < times[2] and times[3] > 0, 'TIME_ORDER_INVALID')
    require(receipt['phase'] in PHASES, 'PHASE_INVALID')


def owned_receipt(*, binding, http_status, create_body, reviewed_spec_sha256,
                  created_mono, expires_mono, cleanup_deadline_mono, observation_max_age):
    """Bind only a confirmed create response; GET after create timeout is not ownership."""
    require(type(http_status) is int and http_status == 201, 'CREATE_NOT_CONFIRMED')
    meta, actual_spec = object_fields(create_body)
    require(actual_spec == reviewed_spec_sha256, 'CREATE_SPEC_DRIFT')
    require(not meta.get('deletionTimestamp') and not meta.get('finalizers'), 'CREATE_LIFECYCLE_UNEXPECTED')
    receipt = dict(schema=1, binding=dict(binding), name=NAME, uid=meta['uid'],
                   created_resource_version=meta['resourceVersion'], spec_sha256=actual_spec,
                   created_mono=created_mono, expires_mono=expires_mono,
                   cleanup_deadline_mono=cleanup_deadline_mono,
                   observation_max_age=observation_max_age, phase='OWNED')
    validate_receipt(receipt)
    return receipt


def decide(*, receipt, binding, observation, now_mono, trigger):
    """Return WAIT/ABSENT/HANDOFF or one conditional DELETE intent.

    Receipt/binding/observation are trusted projections supplied by a future
    adapter, not unauthenticated files. Resource versions remain opaque strings.
    All budgets are explicit caller inputs; this module invents no runtime values.
    """
    def result(action, reason, **extra):
        return dict(action=action, reason=reason, **extra)
    try:
        validate_receipt(receipt)
        require(binding_ok(binding) and binding == receipt['binding'], 'GENERATION_CHANGED')
        require(number(now_mono) and now_mono >= receipt['created_mono'], 'CLOCK_INVALID')
        require(trigger in ('expiry', 'abort', 'complete'), 'TRIGGER_INVALID')
        require(isinstance(observation, dict) and set(observation) ==
                {'binding', 'requested_name', 'observed_mono', 'http_status', 'body'}, 'OBSERVATION_INVALID')
        require(observation['binding'] == binding and observation['requested_name'] == NAME, 'OBSERVATION_TARGET_CHANGED')
        stamp = observation['observed_mono']
        require(number(stamp) and receipt['created_mono'] <= stamp <= now_mono, 'OBSERVATION_CLOCK_INVALID')
        require(now_mono - stamp <= receipt['observation_max_age'], 'OBSERVATION_STALE')
        status, body = observation['http_status'], observation['body']
        require(type(status) is int, 'STATUS_INVALID')
        if status == 404:
            require(isinstance(body, dict) and body.get('kind') == 'Status' and
                    body.get('apiVersion') == 'v1' and body.get('reason') == 'NotFound' and
                    type(body.get('code')) is int and body['code'] == 404, 'NOT_FOUND_UNVERIFIED')
            details = body.get('details')
            require(isinstance(details, dict) and details.get('name') == NAME and
                    details.get('group') == GROUP and details.get('kind') == RESOURCE, 'NOT_FOUND_TARGET_CHANGED')
            return result('ABSENT', 'EXPLICIT_NOT_FOUND_BASELINE_STILL_UNVERIFIED')
        require(status == 200, 'API_READ_FAILED')
        meta, current_spec = object_fields(body)
        require(meta['uid'] == receipt['uid'], 'FOREIGN_UID')
        require(current_spec == receipt['spec_sha256'], 'POLICY_DRIFT')
        if receipt['phase'] != 'OWNED':
            return result('HANDOFF', 'DELETE_ALREADY_CLAIMED_OR_OUTCOME_UNRESOLVED')
        if meta.get('deletionTimestamp') or meta.get('finalizers'):
            return result('HANDOFF', 'UNEXPECTED_DELETION_OR_FINALIZERS')
        if now_mono >= receipt['cleanup_deadline_mono']:
            return result('HANDOFF', 'CLEANUP_WINDOW_EXPIRED')
        if trigger == 'expiry' and now_mono < receipt['expires_mono']:
            return result('WAIT', 'LEASE_NOT_EXPIRED')
        # Persist this next state durably under an exclusive writer before any
        # adapter sends the request. A crash/timeout does not authorize retry.
        next_receipt = dict(receipt, binding=dict(receipt['binding']), phase='DELETE_INTENT')
        return result('DELETE_INTENT', 'PERSIST_BEFORE_ONE_REQUEST',
                      next_receipt=next_receipt, path=PATH,
                      body={'apiVersion':'v1', 'kind':'DeleteOptions',
                            'preconditions':{'uid':receipt['uid'], 'resourceVersion':meta['resourceVersion']}})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'INPUT_INVALID'
        return result('HANDOFF', reason)


def record_delete_result(receipt, http_status):
    """No success claim: even a 2xx requires a fresh explicit NotFound readback."""
    validate_receipt(receipt)
    require(receipt['phase'] == 'DELETE_INTENT', 'DELETE_INTENT_REQUIRED')
    phase = ('DELETE_ACK' if type(http_status) is int and http_status in (200, 202) else
             'CONFLICT' if type(http_status) is int and http_status == 409 else 'DELETE_UNKNOWN')
    return dict(receipt, binding=dict(receipt['binding']), phase=phase)
