#!/usr/bin/python3
"""IF06B guardian CLI: validate (offline), preflight (non-persisting), run, watch.

Only `run` creates or deletes the canary. Every value comes from one reviewed
JSON input file; there are no default budgets, endpoints, paths or credentials.
Output is one JSON object per line with fixed codes and no exception text.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

import coordinator as c
import decision as d
import supervisor as s
import transport as t

EXIT = dict(OK=0, ERROR=2, HANDOFF=3, NOT_STARTED=4, CHECK_FAILED=5)
KEYS = {'schema', 'session_id', 'expected_cluster_uid', 'api', 'credential', 'manifest_file',
        'reviewed_manifest_sha256', 'reviewed_spec_sha256', 'journal_dir', 'evidence_dir', 'budgets'}
API_KEYS = {'connect_ip', 'port', 'tls_server_name', 'ca_file', 'ca_sha256'}
CREDENTIALS = {'bearer_token_file': {'kind', 'token_file'},
               'client_cert_files': {'kind', 'cert_file', 'key_file'}}
BUDGETS = ('lease_seconds', 'cleanup_window_seconds', 'observation_max_age_seconds',
           'request_timeout_seconds', 'readback_delay_seconds')


def say(fd, **row):
    os.write(fd, (json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf-8'))


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            c.require(key not in out, 'INPUT_INVALID')
            out[key] = value
        return out

    def constant(_):
        raise c.CoordinatorError('INPUT_INVALID')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise c.CoordinatorError('INPUT_INVALID') from None


def sha256_text(value):
    return isinstance(value, str) and len(value) == 64 and all(x in '0123456789abcdef' for x in value)


def load(path):
    """Shape and file checks only. Budgets are accepted as given, never filled in."""
    cfg = strict_json(t.read_private(os.path.abspath(path), secret=False))
    c.require(isinstance(cfg, dict) and set(cfg) == KEYS and type(cfg['schema']) is int and cfg['schema'] == 1,
              'INPUT_INVALID')
    c.require(d.text(cfg['session_id']) and d.text(cfg['expected_cluster_uid']), 'INPUT_INVALID')
    api, credential, budgets = cfg['api'], cfg['credential'], cfg['budgets']
    c.require(isinstance(api, dict) and set(api) == API_KEYS and sha256_text(api['ca_sha256']), 'INPUT_INVALID')
    c.require(isinstance(credential, dict) and credential.get('kind') in CREDENTIALS and
              set(credential) == CREDENTIALS[credential['kind']], 'INPUT_INVALID')
    c.require(sha256_text(cfg['reviewed_manifest_sha256']) and sha256_text(cfg['reviewed_spec_sha256']),
              'INPUT_INVALID')
    paths = [api['ca_file'], cfg['manifest_file'], cfg['journal_dir'], cfg['evidence_dir']]
    paths += [v for k, v in credential.items() if k != 'kind']
    c.require(all(isinstance(p, str) and os.path.isabs(p) for p in paths), 'INPUT_INVALID')
    c.require(isinstance(budgets, dict) and set(budgets) == set(BUDGETS) and
              all(d.number(budgets[k]) for k in BUDGETS), 'INPUT_INVALID')
    manifest = strict_json(t.read_private(cfg['manifest_file'], secret=False))
    c.require(isinstance(manifest, dict), 'INPUT_INVALID')
    return cfg, manifest


def clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)  # Keeps counting across a guest suspend.


def live_binding(cfg):
    """Re-read on every coordinator check: a reboot or fork changes the result."""
    def probe():
        with open('/proc/sys/kernel/random/boot_id', 'rb') as handle:
            boot = handle.read(128).decode('ascii').strip()
        with open('/proc/self/stat', 'rb') as handle:
            started = handle.read(4096).rsplit(b')', 1)[1].split()[19].decode('ascii')
        return dict(session_id=cfg['session_id'], cluster_uid=cfg['expected_cluster_uid'],
                    host_boot_id=boot, guardian_epoch='%d-%s' % (os.getpid(), started))
    return probe


class Triggers:
    """SIGUSR1/TERM/INT request an early reclaim (abort), SIGUSR2 reports completion.

    A hangup is ignored: the session that started the guardian may end first.
    """
    def __init__(self):
        self._event, self._value = threading.Event(), None
        for number, name in ((signal.SIGUSR1, 'abort'), (signal.SIGUSR2, 'complete'),
                             (signal.SIGTERM, 'abort'), (signal.SIGINT, 'abort')):
            signal.signal(number, lambda _n, _f, name=name: self._set(name))
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def _set(self, name):
        if self._value is None:
            self._value = name
        self._event.set()

    def wait(self, timeout):
        self._event.wait(timeout)
        return self._value


def detach():
    """Leave the launching session. The launcher relays the arming result and exits."""
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child:
        os.close(write_fd)
        os.waitpid(child, 0)
        armed = False
        with os.fdopen(read_fd, 'rb') as pipe:
            for line in iter(lambda: pipe.readline(65536), b''):
                os.write(1, line)
                if b'"step":"ARMED"' in line:
                    armed = True
                if armed or b'"step":"FINAL"' in line or b'"step":"NOT_STARTED"' in line:
                    break
        raise SystemExit(EXIT['OK'] if armed else EXIT['NOT_STARTED'])
    os.close(read_fd)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir('/')
    os.umask(0o077)
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    return write_fd


def command_validate(args):
    cfg, manifest = load(args.config)
    t.build(cfg['api'], cfg['credential'], clock=clock, allowed=t.SESSION_REQUESTS)
    journal = s.directory(cfg['journal_dir'])
    evidence = s.directory(cfg['evidence_dir'])
    same = os.path.samestat(os.fstat(journal), os.fstat(evidence))
    unused = not os.listdir(journal) and s.STEPS not in os.listdir(evidence)
    budgets = cfg['budgets']
    fits = (budgets['lease_seconds'] > budgets['request_timeout_seconds'] > 0 and
            budgets['observation_max_age_seconds'] > 0 and budgets['cleanup_window_seconds'] >
            3 * budgets['request_timeout_seconds'] + budgets['readback_delay_seconds'])
    row = dict(step='VALIDATE', network_requests=0,
               manifest_sha256_matches=c.manifest_hash(manifest) == cfg['reviewed_manifest_sha256'],
               directories_independent=not same, session_directories_unused=unused, budgets_consistent=fits,
               computed_manifest_sha256=c.manifest_hash(manifest),
               submitted_spec_sha256=d.spec_hash(manifest.get('spec')))
    ok = all(row[k] for k in ('manifest_sha256_matches', 'directories_independent',
                              'session_directories_unused', 'budgets_consistent'))
    say(1, passed=ok, **row)
    return EXIT['OK'] if ok else EXIT['CHECK_FAILED']


def command_preflight(args):
    cfg, manifest = load(args.config)
    c.require(c.manifest_hash(manifest) == cfg['reviewed_manifest_sha256'], 'INPUT_INVALID')
    transport = t.build(cfg['api'], cfg['credential'], clock=clock, allowed=t.PREFLIGHT_REQUESTS)
    result = s.preflight(transport, expected_cluster_uid=cfg['expected_cluster_uid'], manifest=manifest,
                         reviewed_spec_sha256=cfg['reviewed_spec_sha256'], clock=clock,
                         request_timeout=cfg['budgets']['request_timeout_seconds'])
    say(1, step='PREFLIGHT', **result)
    return EXIT['OK'] if result['passed'] else EXIT['CHECK_FAILED']


def command_run(args):
    cfg, manifest = load(args.config)
    stream = os.dup(1) if args.foreground else detach()
    try:
        budgets = cfg['budgets']
        journal = s.directory(cfg['journal_dir'])
        evidence_dir = s.directory(cfg['evidence_dir'])
        transport = t.build(cfg['api'], cfg['credential'], clock=clock, allowed=t.SESSION_REQUESTS)
        probe = live_binding(cfg)
        binding = probe()
        s.check_target(transport, expected_cluster_uid=cfg['expected_cluster_uid'], clock=clock,
                       request_timeout=budgets['request_timeout_seconds'])
        triggers = Triggers()
        evidence = s.Evidence(evidence_dir, journal_fd=journal, clock=clock, stream_fd=stream)
    except Exception as exc:
        # Nothing was created and no journal exists; report to the launcher only.
        say(stream, step='NOT_STARTED', reason=s.code(exc), create_sent=False)
        return EXIT['NOT_STARTED']
    final = s.guard(directory_fd=journal, evidence=evidence, binding=binding, manifest=manifest,
                    reviewed_manifest_sha256=cfg['reviewed_manifest_sha256'],
                    reviewed_spec_sha256=cfg['reviewed_spec_sha256'], lease=budgets['lease_seconds'],
                    cleanup_window=budgets['cleanup_window_seconds'],
                    observation_max_age=budgets['observation_max_age_seconds'],
                    request_timeout=budgets['request_timeout_seconds'],
                    readback_delay=budgets['readback_delay_seconds'], clock=clock, binding_probe=probe,
                    transport=transport, trigger_wait=triggers.wait, pause=time.sleep,
                    on_armed=None if args.foreground else evidence.release_stream)
    evidence.close()
    return {'ABSENT_BASELINE_UNVERIFIED': EXIT['OK'], 'NOT_STARTED': EXIT['NOT_STARTED']}.get(
        final['disposition'], EXIT['HANDOFF'])


def command_watch(args):
    fd = s.directory(args.journal_dir)
    result = s.watch(fd, poll=args.poll_seconds, until_mono=clock() + args.window_seconds,
                     clock=clock, pause=time.sleep)
    say(1, step='WATCH', **result)
    return EXIT['HANDOFF'] if result['handoff_required'] else EXIT['OK']


def command_survival(args):
    """No API, credential or policy: only proves this launch path outlives its session."""
    c.require(args.seconds > 0 and args.interval_seconds > 0, 'INPUT_INVALID')
    target = s.directory(args.evidence_dir)
    stream = os.dup(1) if args.foreground else detach()
    probe = live_binding(dict(session_id='SURVIVAL_CHECK', expected_cluster_uid='NOT_USED'))
    try:
        record = s.Evidence(target, journal_fd=None, clock=clock, stream_fd=stream, name='survival.jsonl')
    except Exception as exc:
        say(stream, step='NOT_STARTED', reason=s.code(exc))
        return EXIT['NOT_STARTED']
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    record.write('ARMED', **probe())
    if not args.foreground:
        record.release_stream()
    end = clock() + args.seconds
    while clock() < end:
        time.sleep(min(args.interval_seconds, max(end - clock(), 0)))
        record.write('BEAT')
    record.write('FINAL', disposition='SURVIVED_REQUESTED_DURATION', complete=record.complete)
    record.close()
    return EXIT['OK']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name, handler in (('validate', command_validate), ('preflight', command_preflight), ('run', command_run)):
        sub = commands.add_parser(name)
        sub.add_argument('--config', required=True)
        sub.set_defaults(handler=handler)
        if name == 'run':
            sub.add_argument('--foreground', action='store_true')
    sub = commands.add_parser('watch')
    sub.add_argument('--journal-dir', required=True)
    sub.add_argument('--poll-seconds', required=True, type=float)
    sub.add_argument('--window-seconds', required=True, type=float)
    sub.set_defaults(handler=command_watch)
    sub = commands.add_parser('survival-check')
    sub.add_argument('--evidence-dir', required=True)
    sub.add_argument('--seconds', required=True, type=float)
    sub.add_argument('--interval-seconds', required=True, type=float)
    sub.add_argument('--foreground', action='store_true')
    sub.set_defaults(handler=command_survival)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SystemExit:
        raise
    except BaseException as exc:
        say(1, step='ERROR', reason=s.code(exc))
        return EXIT['ERROR']


if __name__ == '__main__':
    sys.exit(main())
