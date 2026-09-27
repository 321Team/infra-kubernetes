"""Local dbus-daemon process tests, not target VM/systemd/SELinux integration.

Run separately from STATIC/MOCK: python3 -B -m unittest discover -s tests
    -p 'test_dbus_config_runtime.py' -v
Uses only a temporary private Unix socket. No host bus or firewall commands.
"""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import selectors
import shutil
import socket
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('dbus_config_kernel',
                                            SCRIPTS / 'run-cni-kernel-integration.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@unittest.skipUnless(shutil.which('dbus-daemon') and shutil.which('busctl'),
                     'dbus-daemon and busctl are required')
class PrivateBusRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fds-dbus-config-')
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        (self.job / 'dbus').mkdir(mode=0o700)
        self.address = tool.private_env(self.job)['DBUS_SYSTEM_BUS_ADDRESS']
        self.config = self.job / 'bus.conf'

    @contextmanager
    def daemon(self, config):
        self.config.write_text(config)
        with (self.job / 'stderr.log').open('w+') as errors:
            proc = subprocess.Popen([shutil.which('dbus-daemon'), '--nofork',
                '--nosyslog', '--print-address=1', '--config-file=' + str(self.config)],
                stdout=subprocess.PIPE, stderr=errors, text=True,
                env={'PATH': os.defpath, 'LANG': 'C'})
            try:
                yield proc, errors
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=3)
                proc.stdout.close()

    def ready(self, proc, errors):
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), 'private bus startup timed out')
        address = proc.stdout.readline().strip()
        errors.flush(); errors.seek(0)
        diagnostic = errors.read()
        if 'Failed to open socket: Operation not permitted' in diagnostic:
            # Record an environment block, never substitute a successful daemon.
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
                    pass
            except PermissionError:
                self.skipTest('configuration reached socket setup; local AF_UNIX creation is denied')
        self.assertTrue(address.startswith(self.address + ','), diagnostic)
        self.assertIsNone(proc.poll())
        return address

    def call(self, method, *args):
        return subprocess.run([shutil.which('busctl'), '--address=' + self.address,
            '--no-pager', '--timeout=2', 'call', 'org.freedesktop.DBus',
            '/org/freedesktop/DBus', 'org.freedesktop.DBus', method, *args],
            text=True, capture_output=True, timeout=5,
            env={'PATH': os.defpath, 'LANG': 'C'})

    def test_previous_per_user_connection_rule_is_rejected(self):
        broken = tool.private_bus_config(self.job).replace(
            '<allow user="root"/></policy><policy user="root">',
            '</policy><policy user="root"><allow user="root"/>')
        self.assertNotEqual(broken, tool.private_bus_config(self.job))
        with self.daemon(broken) as (proc, errors):
            self.assertEqual(proc.wait(timeout=5), 1)
            errors.flush(); errors.seek(0)
            self.assertIn('rule cannot be per-user because it has bus-global semantics',
                          errors.read())
        self.assertFalse((self.job / 'dbus/bus.sock').exists())

    @unittest.skipUnless(os.geteuid() == 0, 'production policy permits root only')
    def test_generated_config_accepts_readiness_and_name_ownership(self):
        with self.daemon(tool.private_bus_config(self.job)) as (proc, errors):
            self.ready(proc, errors)
            result = self.call('GetConnectionUnixProcessID', 's', 'org.freedesktop.DBus')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'u ' + str(proc.pid))
            name = self.call('RequestName', 'su', 'org.fedoraproject.FirewallD1', '0')
            self.assertEqual(name.returncode, 0, name.stderr)
            self.assertEqual(name.stdout.strip(), 'u 1')

    @unittest.skipUnless(os.geteuid() == 0, 'negative admission test uses root client')
    def test_default_deny_blocks_connection_without_root_exception(self):
        # Exercise the admission rule, not a filesystem denial. This environment
        # cannot setuid; a real non-root client test remains separately untested.
        denied = tool.private_bus_config(self.job).replace('<allow user="root"/>', '')
        with self.daemon(denied) as (proc, errors):
            self.ready(proc, errors)
            result = self.call('GetConnectionUnixProcessID', 's', 'org.freedesktop.DBus')
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(proc.poll())


if __name__ == '__main__':
    unittest.main()
