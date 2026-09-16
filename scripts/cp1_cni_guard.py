"""Independent local guard supervision and unit ownership checks.

The caller must deploy source-pinned node entrypoints before arming these
units. No installation/enablement occurs on import. Guard status is verified
from unit fragments, symlinks and systemctl output, not a stored 'armed' flag.
"""
import math
from pathlib import Path
import re
import time
import shlex

from cp1_cni_membership import need, remaining, guard_action, RESERVE, fingerprint, validate_lease

ROOT = Path('/var/lib/fds/icr-d-net-01-01/cni-managed')
UNITS = Path('/etc/systemd/system')


def names(run_id):
    need(isinstance(run_id, str) and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', run_id), 'invalid run ID')
    prefix = 'fds-cni-managed-' + run_id
    return {kind: prefix + '-' + kind + '.service' for kind in ('work', 'guard', 'recover')} | {
        'timer': prefix + '-guard.timer'}


def documents(lease):
    validate_lease(lease)
    unit = names(lease['run_id'])
    command = '/usr/bin/python3 -I -B ' + str(ROOT / lease['run_id'] / 'scripts/run-cp1-cni-managed-node.py')
    # Startup is never a renewal. The node checks both persisted absolute
    # deadlines. RuntimeMaxSec is a second cap, not the source of lease truth.
    work = ('[Unit]\nDescription=FDS managed CP1 membership\nAfter=firewalld.service\n'
            'OnFailure=' + unit['recover'] + '\n[Service]\nType=exec\nUMask=0077\n'
            'KillMode=control-group\nTimeoutStopSec=5\nRestart=no\n'
            'RuntimeMaxSec=' + str(int(lease['boot_end'] - lease['boot_start'] - RESERVE)) + '\n'
            'ExecStart=' + command + ' manage\n')
    guard = ('[Unit]\nDescription=FDS managed CP1 independent guard\nAfter=firewalld.service\n'
             'OnFailure=' + unit['recover'] + '\n[Service]\nType=oneshot\nUMask=0077\n'
             'KillMode=control-group\nTimeoutStartSec=15\nTimeoutStopSec=5\nExecStart=' + command + ' guard\n')
    recover = ('[Unit]\nDescription=FDS managed CP1 recovery\nAfter=firewalld.service NetworkManager.service\n'
               '[Service]\nType=oneshot\nUMask=0077\nKillMode=control-group\nTimeoutStartSec=150\nTimeoutStopSec=5\n'
               'ExecStart=' + command + ' recover\n[Install]\nWantedBy=multi-user.target\n')
    timer = ('[Unit]\nDescription=FDS managed CP1 observation watchdog\n[Timer]\nOnActiveSec=1s\n'
             'OnUnitInactiveSec=5s\nAccuracySec=1s\nRandomizedDelaySec=0\nUnit=' + unit['guard'] +
             '\n[Install]\nWantedBy=timers.target\n')
    return {unit['work']: work, unit['guard']: guard, unit['recover']: recover, unit['timer']: timer}


PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'Job', 'FragmentPath', 'DropInPaths',
              'UnitFileState', 'ExecStart', 'OnFailure', 'KillMode', 'TimeoutStopUSec', 'RuntimeMaxUSec',
              'NextElapseUSecMonotonic', 'Unit', 'TimersMonotonic', 'AccuracyUSec', 'RandomizedDelayUSec',
              'TimeoutStartUSec', 'Type', 'Restart', 'UMask')


def parse_properties(raw):
    values = {}
    for line in raw.splitlines():
        need('=' in line, 'malformed unit property')
        key, value = line.split('=', 1)
        need(key in PROPERTIES and key not in values, 'unknown/duplicate unit property')
        values[key] = value
    return values


def query_units(lease, command, read_private_file, read_link):
    """Read unit state through trusted, bounded adapters. No unit mutation."""
    docs, unit = documents(lease), names(lease['run_id'])
    observed = {'properties': {}, 'files': {}, 'links': {}}
    deadline = time.monotonic() + 10
    def run(argv):
        left = deadline - time.monotonic()
        need(left > 0, 'unit query budget exhausted')
        value = command(argv, timeout=min(3, left))
        need(value['rc'] == 0 and not value.get('stderr', '').strip() and
             value.get('truncated') is False, 'unit query incomplete')
        return value['stdout'].strip()
    for name, expected in docs.items():
        body = read_private_file(UNITS / name)
        need(body == expected.encode(), 'unit file differs from generated source')
        observed['files'][name] = body.decode()
        argv = ['systemctl', 'show', name] + [a for p in PROPERTIES for a in ('-p', p)]
        observed['properties'][name] = parse_properties(run(argv))
    # Read typed D-Bus microseconds; systemctl may format timespans as text.
    for kind, interface, prop in (('timer', 'Timer', 'NextElapseUSecMonotonic'),
                                  ('guard', 'Service', 'TimeoutStartUSec'),
                                  ('guard', 'Service', 'TimeoutStopUSec'),
                                  ('recover', 'Service', 'TimeoutStartUSec'),
                                  ('recover', 'Service', 'TimeoutStopUSec'),
                                  ('work', 'Service', 'TimeoutStopUSec'),
                                  ('work', 'Service', 'RuntimeMaxUSec'),
                                  ('timer', 'Timer', 'TimersMonotonic'),
                                  ('timer', 'Timer', 'AccuracyUSec'),
                                  ('timer', 'Timer', 'RandomizedDelayUSec')):
        name = unit[kind]
        component = ''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x') for c in name)
        raw = run(['busctl', '--system', '--timeout=3', 'get-property', 'org.freedesktop.systemd1',
                   '/org/freedesktop/systemd1/unit/' + component, 'org.freedesktop.systemd1.' + interface, prop])
        if prop == 'TimersMonotonic':
            tokens = shlex.split(raw)
            need(len(tokens) == 8 and tokens[:2] == ['a(stt)', '2'] and
                 all(re.fullmatch(r'[0-9]+', tokens[i]) for i in (3, 4, 6, 7)), 'timer definitions type differs')
            pairs = [(tokens[2], int(tokens[3])), (tokens[5], int(tokens[6]))]
            need(set(pairs) == {('OnActiveUSec', 1000000), ('OnUnitInactiveUSec', 5000000)},
                 'loaded timer period differs')
            observed['properties'][name][prop] = sorted([list(p) for p in pairs])
        else:
            need(re.fullmatch(r't [0-9]+', raw), 'unit D-Bus time property type differs')
            observed['properties'][name][prop] = raw.split()[1]
    for directory, name in (('timers.target.wants', unit['timer']), ('multi-user.target.wants', unit['recover'])):
        key = str(UNITS / directory / name)
        observed['links'][key] = read_link(Path(key))
    return observed


def validate_owned(observed, lease):
    docs, unit = documents(lease), names(lease['run_id'])
    need(observed['files'] == docs and set(observed['properties']) == set(docs), 'owned unit inventory differs')
    command = '/usr/bin/python3 -I -B ' + str(ROOT / lease['run_id'] / 'scripts/run-cp1-cni-managed-node.py')
    for kind, name in unit.items():
        state = observed['properties'][name]
        need(state.get('Id') == name and state.get('LoadState') == 'loaded' and
             state.get('FragmentPath') == str(UNITS / name) and state.get('DropInPaths') == '',
             'foreign/unloaded unit fragment')
        if kind != 'timer':
            exec_start = state.get('ExecStart', '')
            mode = 'manage' if kind == 'work' else kind
            # systemctl's ExecStart also includes pid/timestamps/status. Compare
            # its loaded argv, not just the on-disk unit (daemon may be stale).
            match = re.fullmatch(r'\{ path=/usr/bin/python3 ; argv\[\]=' + re.escape(command + ' ' + mode) +
                                 r' ; ignore_errors=no ; (?:[^{}]*)\}', exec_start)
            need(match, 'loaded unit command differs')
            need(state.get('Type') == ('exec' if kind == 'work' else 'oneshot') and
                 state.get('Restart') == 'no' and state.get('UMask') == '0077' and
                 state.get('KillMode') == 'control-group' and state.get('TimeoutStopUSec') == '5000000',
                 'loaded service lifecycle differs')
        if kind in ('work', 'guard'):
            need(state.get('OnFailure') == unit['recover'], 'loaded recovery dependency differs')
        if kind == 'work':
            need(state.get('KillMode') == 'control-group' and state.get('TimeoutStopUSec') == '5000000' and
                 state.get('RuntimeMaxUSec') == str(int(lease['boot_end'] - lease['boot_start'] - RESERVE) * 1000000),
                 'loaded work lifetime/kill mode differs')
        if kind == 'guard':
            need(state.get('TimeoutStartUSec') == '15000000', 'loaded guard timeout differs')
        if kind == 'recover':
            need(state.get('TimeoutStartUSec') == '150000000', 'loaded recovery timeout differs')
    return unit


def verified_guard(observed, lease, clock):
    unit = validate_owned(observed, lease)
    timer = observed['properties'][unit['timer']]
    recover = observed['properties'][unit['recover']]
    for directory, name in (('timers.target.wants', unit['timer']), ('multi-user.target.wants', unit['recover'])):
        key = str(UNITS / directory / name)
        need(observed['links'].get(key) == str(UNITS / name), 'guard/boot recovery link differs')
    need(timer.get('ActiveState') == 'active' and timer.get('UnitFileState') == 'enabled' and
         timer.get('Unit') == unit['guard'] and recover.get('UnitFileState') == 'enabled', 'recovery not armed')
    need(timer.get('TimersMonotonic') == [['OnActiveUSec', 1000000], ['OnUnitInactiveUSec', 5000000]] and
         timer.get('AccuracyUSec') == '1000000' and timer.get('RandomizedDelayUSec') == '0',
         'loaded timer period/accuracy differs')
    # The real timer is periodic. Its scheduled next check must be near; lease
    # expiry itself is checked using CLOCK_BOOTTIME inside the independent guard.
    raw = timer.get('NextElapseUSecMonotonic', '')
    need(re.fullmatch(r'[0-9]+', raw), 'numeric timer deadline absent')
    monotonic = clock.get('monotonic')
    need(type(monotonic) in (int, float) and math.isfinite(monotonic) and monotonic >= 0,
         'monotonic clock absent')
    running_guard = observed['properties'][unit['guard']]
    executing = (running_guard.get('ActiveState') in ('active', 'activating') and
                 running_guard.get('MainPID', '0').isdigit() and int(running_guard['MainPID']) > 0)
    need(0 < int(raw) / 1_000_000 - monotonic <= 10 or
         int(raw) == 0 and timer.get('SubState') == 'running' and executing,
         'guard neither scheduled within bound nor executing under timeout')
    remaining(lease, clock, RESERVE)
    return {'run_id': lease['run_id'], 'armed': True, 'deadline': lease['boot_end'],
            'unit_observation_sha256': fingerprint(observed)}


def supervise(state, clock, observed, command, persist_request):
    """One independent invocation. No manager journal lock is required to read
    its atomically replaced snapshot. Stop/recover only exact owned units.
    """
    lease = state['lease']
    unit = validate_owned(observed, lease)
    live = observed['properties'][unit['work']]
    alive = live.get('ActiveState') == 'active' and live.get('MainPID', '0').isdigit() and int(live['MainPID']) > 0
    try:
        action = guard_action(state, clock, service_alive=alive)
    except (ValueError, KeyError, TypeError):
        action = 'RECOVER'
    if action == 'WATCH':
        try:
            verified_guard(observed, lease, clock)
        except ValueError:
            action = 'RECOVER'
    if action == 'RECOVER':
        # A distinct fsynced stop request survives manager exit and prevents
        # restart from treating this as a new activation or lease extension.
        storage_error = None
        try:
            persist_request({'run_id': lease['run_id'], 'action': 'RECOVER', 'clock': clock})
        except OSError as error:
            storage_error = error
        result = command(['systemctl', '--no-block', 'start', unit['recover']], timeout=5)
        need(result['rc'] == 0 and result.get('truncated') is False and not result.get('stderr', '').strip(),
             'recovery dispatch uncertain; retry same owned recovery unit')
        need(storage_error is None, 'owned recovery dispatched; stop-request persistence failed')
    return {'action': action, 'run_id': lease['run_id'], 'full_recovery_complete': False}
