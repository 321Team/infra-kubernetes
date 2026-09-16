"""Crash-aware publication/loading of reviewed unit DEFINITIONS only.

This is not an operational installer. It never creates wants links, arms a
timer, starts services or changes the firewall. The missing activation and
full-recovery entrypoint must be staged and independently reviewed before a
caller can use this primitive. No CLI accepts an 'approved' boolean.

The root caller supplies a separate locked Journal and trusted bounded live
unit reader. A same-UID malicious root writer is outside this trust boundary.
"""
import copy
import os
from pathlib import Path
import secrets

import cp1_cni_guard as guard
from cp1_cni_membership import need, fingerprint, validate_lease, same
from cp1_cni_staging import root_directory, read_regular, verify_sources, exclusive_write
from cp1_cni_guard import documents, validate_owned

ENTRYPOINT = 'scripts/run-cp1-cni-managed-node.py'
SCHEMA = 'fds.cp1-cni-unit-definitions.v1'


def inactive(properties, *, absent=False):
    """Do not mistake stop-request acceptance for a stopped service."""
    timer = properties.get('Id', '').endswith('.timer')
    need(properties.get('ActiveState') == 'inactive' and properties.get('SubState') == 'dead' and
         properties.get('MainPID') in (('', '0', None) if timer else ('0',)) and properties.get('Job') in ('', '0'),
         'unit is running, failed or has a pending job')
    if absent or properties.get('LoadState') == 'not-found':
        need(properties.get('LoadState') == 'not-found' and properties.get('FragmentPath') == '' and
             properties.get('DropInPaths') == '' and properties.get('UnitFileState') in ('', 'not-found'),
             'preexisting loaded unit or drop-in')
    else:
        need(properties.get('LoadState') == 'loaded' and properties.get('UnitFileState') in ('static', 'disabled'),
             'unit unexpectedly enabled/masked')


def binding(lease, destination, manifest, reviewed_sha256):
    validate_lease(lease)
    need(ENTRYPOINT in manifest, 'reviewed operational entrypoint absent')
    need(lease['source_sha256'] == reviewed_sha256, 'lease source manifest differs')
    return {'lease_sha256': fingerprint(lease), 'source_sha256': reviewed_sha256,
            'run_id': lease['run_id'], 'unit_directory': str(Path(destination)),
            'documents_sha256': fingerprint(documents(lease))}


def initial(binding_value):
    return {'schema': SCHEMA, 'binding': copy.deepcopy(binding_value), 'completed': [],
            'pending': None, 'phase': 'PUBLISHING', 'definitions_loaded': False,
            'recovery_armed': False, 'authorizes_apply': False}


def validate_state(state, expected, order):
    need(set(state) == set(initial(expected)) and state.get('schema') == SCHEMA and
         same(state.get('binding'), expected), 'unit journal binding/schema differs')
    done = state['completed']
    need(isinstance(done, list) and done == order[:len(done)] and len(done) <= len(order),
         'unit journal is not a known publication prefix')
    need(state['pending'] is None or len(done) < len(order) and state['pending'] == order[len(done)],
         'unit pending intent differs')
    need(state['phase'] in ('PUBLISHING', 'FILES_READY', 'LOAD_INTENT', 'DEFINITIONS_LOADED'),
         'unknown unit journal phase')
    if state['phase'] != 'PUBLISHING':
        need(done == order and state['pending'] is None, 'unit completion prefix incomplete')
    need(state['definitions_loaded'] is (state['phase'] == 'DEFINITIONS_LOADED') and
         state['recovery_armed'] is False and state['authorizes_apply'] is False,
         'unit definitions cannot grant execution authority')


def existing(directory, name):
    try:
        return read_regular(directory, name, root_owned=True)
    except FileNotFoundError:
        return None


def publish(directory, name, content):
    # Use a non-unit temporary name; publish a fully fsynced inode using
    # link(O_EXCL behavior). No rename can replace an existing unit.
    temporary = '.' + name + '.' + secrets.token_hex(12) + '.tmp'
    try:
        exclusive_write(directory, temporary, content)
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


class UnitFiles:
    """Separate pre-activation journal, never the live membership STATE.json.

    trusted_units() returns current systemctl properties for the exact four
    names. It must be read-only, bounded and collect again on every call.
    It cannot return cached approval/armed flags supplied by a user.
    """
    def __init__(self, lease, source_root, unit_directory, manifest, reviewed_sha256,
                 journal, trusted_units):
        self.lease, self.manifest = copy.deepcopy(lease), copy.deepcopy(manifest)
        self.source_root, self.unit_directory = Path(source_root), Path(unit_directory)
        need(self.source_root == guard.ROOT and self.unit_directory == guard.UNITS,
             'unit source/destination must match compiled paths')
        self.reviewed_sha256, self.journal, self.trusted_units = reviewed_sha256, journal, trusted_units
        self.expected = binding(lease, unit_directory, manifest, reviewed_sha256)
        self.docs = documents(lease)
        self.order = sorted(self.docs)

    def sources(self):
        verify_sources(self.source_root, self.lease['run_id'], self.manifest, self.reviewed_sha256)

    def live(self, *, absent=False):
        observed = self.trusted_units()
        need(isinstance(observed, dict) and set(observed) == set(self.docs), 'live unit inventory differs')
        for name, props in observed.items():
            need(props.get('Id') == name, 'live unit name differs')
            inactive(props, absent=absent)
            if props.get('LoadState') == 'loaded':
                need(props.get('FragmentPath') == str(self.unit_directory / name) and
                     props.get('DropInPaths') == '', 'foreign loaded unit fragment/drop-in')
        return observed

    def stage(self):
        self.sources()
        directory = root_directory(self.unit_directory, private=False)
        try:
            try:
                state = self.journal.load()
            except FileNotFoundError:
                # All collisions are checked before the first durable intent.
                # Even byte-identical preexisting files are not adopted.
                need(all(existing(directory, name) is None for name in self.order), 'preexisting unit file')
                self.live(absent=True)
                state = initial(self.expected)
                self.journal.save(state)
            validate_state(state, self.expected, self.order)
            self.live(absent=not state['completed'] and state['pending'] is None)
            for index, name in enumerate(self.order):
                content = existing(directory, name)
                if index < len(state['completed']):
                    need(content == self.docs[name].encode(), 'committed unit bytes missing/differ')
                    continue
                need(state['phase'] == 'PUBLISHING', 'published journal unexpectedly incomplete')
                if state['pending'] is None:
                    need(content is None, 'unit appeared without durable intent')
                    state['pending'] = name
                    self.journal.save(state)
                else:
                    need(state['pending'] == name, 'wrong pending unit')
                self.sources()
                self.live()
                if content is None:
                    publish(directory, name, self.docs[name].encode())
                else:
                    need(content == self.docs[name].encode(), 'pending unit has unknown bytes')
                    # link() succeeded but process died before directory sync
                    # or journal completion: durability is re-established.
                    os.fsync(directory)
                state['completed'].append(name)
                state['pending'] = None
                self.journal.save(state)
            if state['phase'] == 'PUBLISHING':
                state['phase'] = 'FILES_READY'
                self.journal.save(state)
            return copy.deepcopy(state)
        finally:
            os.close(directory)

    def load(self, command, trusted_loaded_observation):
        """Load definitions only; independently verify daemon's actual argv.

        A timeout/unknown result keeps LOAD_INTENT; replay may reload the same
        definitions after verifying files and inactive units. It cannot start
        any unit. No claim of arming or full recovery follows from this call.
        """
        state = self.stage()
        need(state['phase'] in ('FILES_READY', 'LOAD_INTENT', 'DEFINITIONS_LOADED'), 'unit files not ready')
        self.sources()
        self.live()
        state.update(phase='LOAD_INTENT', definitions_loaded=False)
        self.journal.save(state)
        result = command(['systemctl', 'daemon-reload'], timeout=5)
        need(type(result.get('rc')) is int and result['rc'] == 0 and result.get('truncated') is False and
             result.get('stdout', '').strip() == result.get('stderr', '').strip() == '',
             'daemon reload result uncertain')
        self.sources()
        observed = trusted_loaded_observation()
        validate_owned(observed, self.lease)
        need(observed.get('links') == {}, 'definition loader must not create enablement links')
        for properties in observed['properties'].values():
            inactive(properties)
        state.update(phase='DEFINITIONS_LOADED', definitions_loaded=True)
        self.journal.save(state)
        return copy.deepcopy(state)
