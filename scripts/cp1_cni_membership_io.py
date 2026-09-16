"""Bounded firewall membership adapter; no CLI, installation or approval path.

The caller supplies a trusted full-state observer. A daemon-only ipset query is
insufficient. Root-level activation, source staging and the independent systemd
guard are NOT implemented by this adapter.
"""
import time
import subprocess

from cp1_cni_membership import entries, need, snapshot, remaining, number, RESERVE

IPSET = 'fds-cp1-managed'
POLICY = 'fds-cp1-managed'


class FirewalldMembershipIO:
    def __init__(self, lease, observer, runner, boot_reader, wall=time.time,
                 boottime=lambda: time.clock_gettime(time.CLOCK_BOOTTIME)):
        self.lease, self.observer, self.runner, self.boot_reader = lease, observer, runner, boot_reader
        self.wall, self.boottime = wall, boottime

    def clock(self):
        return {'boot_id': self.boot_reader(), 'wall': self.wall(), 'boottime': self.boottime()}

    def snapshot(self):
        return self.observer()

    def change(self, action, item, *, deadline=None):
        entries([item])
        need(action in ('add', 'remove'), 'unsupported membership action')
        before = self.snapshot()
        actual = snapshot(before, self.lease, require_guard=action == 'add' or before.get('policy_enabled') is True)
        need((action == 'add' and item not in actual) or (action == 'remove' and item in actual),
             'membership action precondition changed')
        timeout = 5
        if before['policy_enabled'] or action == 'add':
            need(before['policy_enabled'] is True and deadline is not None, 'active operation deadline absent')
            clock = self.clock()
            remaining(self.lease, clock, RESERVE)
            timeout = min(timeout, *(number(deadline[k]) - clock[k] for k in ('wall', 'boottime')))
            need(timeout > 0, 'operation observation/lease deadline exhausted')
        # runner must enforce a <= 5s timeout, bounded output, and trusted binary.
        return self.runner(['firewall-cmd', '--ipset=' + IPSET, '--' + action + '-entry=' + item], timeout=timeout)

    def disable(self):
        actual = self.snapshot()
        need(actual.get('owned') is True and actual.get('run_id') == self.lease['run_id'],
             'cannot disable unproven/foreign policy')
        if actual.get('policy_enabled') is False:
            return
        need(actual.get('policy_enabled') is True, 'policy state unknown')
        try:
            result = self.runner(['firewall-cmd', '--policy=' + POLICY, '--add-disable'], timeout=5)
        except (TimeoutError, OSError, subprocess.TimeoutExpired) as error:
            result = {'exception': type(error).__name__, 'message': str(error)}
        # A timeout/nonzero result does not settle the outcome: independently
        # inspect the real rules. Completion still requires the rest of recovery.
        after = self.snapshot()
        need(after.get('owned') is True and after.get('run_id') == self.lease['run_id'] and
             after.get('policy_enabled') is False, 'owned policy disable not observed')
        return result
