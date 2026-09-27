"""R4: recorded firewalld 2.4.3 output plus fail-closed transaction tests.

No daemon, kernel or production host is changed by this module.
"""
import copy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_cni_diagnostic import core, peers, baseline, Simulator, Crash


# Exact zone section from COMMANDS.jsonl, command index 79 (zero-based), job
# d6c7e1e672c418ca. Archive SHA256:
# b1b63bbda9172fb20c1a4389ae581da1955224abfb7e11bb7ac4143ec59bacaa.
OBSERVED_ZONE = '''fds-cni-trial
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces: 
  sources: 
  services: 
  ports: 
  protocols: 
  forward: yes
  masquerade: no
  forward-ports: 
  source-ports: 
  icmp-blocks: 
  rich rules: 
'''


class ZoneImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.io = Simulator(self.directory)
        self.tx = core.Transaction(self.io, self.io.j)

    def tearDown(self):
        self.temp.cleanup()

    def test_recorded_created_zone_differs_only_in_forward(self):
        observed = core.parse_objects(OBSERVED_ZONE)[core.ZONE]
        expected = core.empty_zone()
        self.assertEqual({k for k in observed if observed[k] != expected[k]}, {'forward'})
        self.assertEqual(observed, dict(expected, forward='yes'))

    def test_legacy_file_import_ignores_omitted_forward(self):
        path = self.directory / (core.ZONE + '.xml')
        root = ET.parse(path).getroot()
        root.remove(root.find('forward'))
        path.write_text(ET.tostring(root, encoding='unicode'))
        self.io.command(['--permanent', '--new-zone-from-file=' + str(path)])
        self.assertEqual(self.io.live['permanent']['zones'][core.ZONE]['forward'], 'yes')
        self.assertNotIn(core.ZONE, self.io.live['runtime']['zones'])

    def test_forward_is_disabled_before_any_reload_or_binding(self):
        command = self.io.command
        states = []

        def observe(args, cleanup=False):
            if '--reload' in args or any(a.startswith('--add-interface=') for a in args):
                self.assertEqual(self.io.live['permanent']['zones'][core.ZONE]['forward'], 'no')
            command(args, cleanup)
            states.append(copy.deepcopy(self.io.live))
            runtime_zone = self.io.live['runtime']['zones'].get(core.ZONE)
            if runtime_zone:
                self.assertEqual(runtime_zone['forward'], 'no')

        self.io.command = observe
        self.tx.prepare(peers(), self.directory)
        self.assertNotIn(core.ZONE, states[0]['runtime']['zones'])
        self.assertEqual(states[0]['permanent']['zones'][core.ZONE],
                         core.parse_objects(OBSERVED_ZONE)[core.ZONE])
        self.assertEqual(self.io.trace[1], ['--permanent', '--zone=' + core.ZONE, '--remove-forward'])
        self.assertIn('disable-permanent-zone-forward', self.io.saved['completed'])

    def test_disable_command_error_recovers_unbound_created_zone(self):
        command = self.io.command

        def fail(args, cleanup=False):
            if '--remove-forward' in args:
                raise ValueError('injected command error')
            return command(args, cleanup)

        self.io.command = fail
        with self.assertRaisesRegex(ValueError, 'injected command error'):
            self.tx.prepare(peers(), self.directory)
        self.assertNotIn(core.ZONE, self.io.live['runtime']['zones'])
        self.io.command = command
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_noop_disable_is_not_accepted_or_loaded(self):
        command = self.io.command

        def noop(args, cleanup=False):
            if '--remove-forward' not in args:
                return command(args, cleanup)

        self.io.command = noop
        with self.assertRaisesRegex(ValueError, 'Post-mutation state differs: disable-permanent-zone-forward'):
            self.tx.prepare(peers(), self.directory)
        self.assertFalse(any('--reload' in a for a in self.io.trace))
        self.assertNotIn(core.ZONE, self.io.live['runtime']['zones'])
        self.io.command = command
        core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(self.io.live, baseline())

    def test_foreign_field_mismatch_is_never_normalized_or_overwritten(self):
        command = self.io.command

        def drift(args, cleanup=False):
            command(args, cleanup)
            if any(a.startswith('--new-zone-from-file=') for a in args):
                self.io.live['permanent']['zones'][core.ZONE]['ports'] = '9000/tcp'

        self.io.command = drift
        with self.assertRaisesRegex(ValueError, 'Post-mutation state differs: create-permanent-zone'):
            self.tx.prepare(peers(), self.directory)
        count = len(self.io.trace)
        with self.assertRaisesRegex(ValueError, 'Firewall drift'):
            core.Transaction(self.io, copy.deepcopy(self.io.saved)).withdraw()
        self.assertEqual(len(self.io.trace), count)
        self.assertEqual(self.io.live['permanent']['zones'][core.ZONE]['ports'], '9000/tcp')

    def test_reboot_at_creation_or_disable_recovers_unbound_zone(self):
        for position in (1, 2):
            for timing in ('before', 'after'):
                with self.subTest(position=position, timing=timing):
                    io = Simulator(self.directory)
                    io.crash_at, io.timing = position, timing
                    with self.assertRaises(Crash):
                        core.Transaction(io, io.j).prepare(peers(), self.directory)
                    io.crash_at = None
                    io.live['runtime'] = copy.deepcopy(io.live['permanent'])
                    io.live['runtime']['zones']['public']['interfaces'] = 'ens160'
                    if core.ZONE in io.live['runtime']['zones']:
                        self.assertEqual(io.live['runtime']['zones'][core.ZONE]['interfaces'], '')
                        self.assertEqual(io.live['runtime']['zones'][core.ZONE]['sources'], '')
                    io.boot_changed = True
                    core.Transaction(io, copy.deepcopy(io.saved)).withdraw()
                    self.assertEqual(io.live, baseline())


if __name__ == '__main__':
    unittest.main()
