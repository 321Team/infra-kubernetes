#!/usr/bin/env python3
"""acnet-3-edge.sh against a stub `ansible`/`sleep`; no server or API access.
Only ~/.config, ~/infra-ansible and the output path are isolated in a test copy. The capture summary command (the real grep
patterns) runs against fixture tcpdump text, so the packet counting is exercised on realistic lines.
Covers: capture polling until finished=1 with a bounded number of polls, UNKNOWN/INVALID on every status failure,
full status text preserved, and the TCP179/UDP4789 counters (direction, port, source, escaped dots).
"""
import os, pathlib, subprocess, tempfile, unittest
SCRIPT = pathlib.Path(__file__).with_name('acnet-3-edge.sh')
WORKER_COUNTERS = pathlib.Path(__file__).with_name('acnet-worker-counters.sh')

CAPTURE = '''tcpdump: listening on ens160, link-type EN10MB (Ethernet), snapshot length 262144 bytes
2026-10-05 15:11:29.114789 IP 10.1.93.105.22 > 10.250.10.10.49256: Flags [.], ack 90960, win 1968, length 0
2026-10-05 15:12:00.147890 IP 10.250.10.10.49256 > 10.1.93.105.22: Flags [P.], seq 1:37, ack 1, win 669, length 36
2026-10-05 15:12:01.060602 IP 10.1.93.50.58964 > 10.1.93.105.179: Flags [S], seq 3417327314, win 64240, length 0
2026-10-05 15:12:02.085824 IP 10.1.93.50.58964 > 10.1.93.105.179: Flags [S], seq 3417327314, win 64240, length 0
2026-10-05 15:12:03.109998 IP 10.1.93.50.58964 > 10.1.93.105.179: Flags [S], seq 3417327314, win 64240, length 0
2026-10-05 15:12:06.062342 IP 10.1.93.50.37093 > 10.1.93.105.4789: VXLAN (invalid)
2026-10-05 15:12:06.062400 IP 10.1.93.50.37094 > 10.1.93.105.4789: UDP, length 3
2026-10-05 15:12:07.000001 IP 10.1.93.105.4789 > 10.1.93.50.37093: UDP, length 3
2026-10-05 15:12:07.000002 IP 10.1.93.50.5 > 10.1.93.105.4790: UDP, length 3
2026-10-05 15:12:07.000003 IP 10.1.93.51.5 > 10.1.93.105.4789: UDP, length 3
2026-10-05 15:12:07.000004 IP 110.1.93.50.5 > 10.1.93.105.4789: UDP, length 3
2026-10-05 15:12:07.000005 IP 10a1b93c50.5 > 10.1.93.105.4789: UDP, length 3
2026-10-05 15:12:07.000006 IP 10.1.93.105.179 > 10.1.93.50.58964: Flags [R.], seq 0, ack 1, win 0, length 0
794 packets captured
794 packets received by filter
0 packets dropped by kernel
'''

ANSIBLE = r'''#!/bin/bash
mod=""; args=""
while [ "$#" -gt 0 ]; do
  case "$1" in -m) mod="$2"; shift;; -a) args="$2"; shift;; esac
  shift
done
case "$mod" in
  async_status)
    [ "$STATUS_FAIL" = 1 ] && { echo "ERROR! could not find job" >&2; exit 4; }
    n=$(( $(cat "$FIXTURE_ROOT/polls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FIXTURE_ROOT/polls"
    if [ "$n" -ge "${JOB_FINISH_AT:-1}" ]; then fin=1; else fin=0; fi
    rcline=""
    if [ "$fin" = 1 ]; then case "${JOB_RC:-0}" in none) ;; *) rcline="\"rc\": ${JOB_RC:-0},";; esac; fi
    cat <<J
k8s-worker01 | CHANGED => {
    "ansible_job_id": "j123.456",
    "changed": true,
    "cmd": "timeout --signal=INT --preserve-status 85 tcpdump -nn -tttt -i ens160",
    "delta": "0:01:25.022394",
    "end": "2026-10-05 15:12:47.536386",
    "finished": $fin,
    "msg": "",
    $rcline
    "results_file": "/root/.ansible_async/j123.456",
    "start": "2026-10-05 15:11:22.513992",
    "started": 1,
    "stderr": "",
    "stderr_lines": [],
    "stdout": "",
    "stdout_lines": [],
    "LAST_STATUS_LINE": "present"
}
J
    ;;
  script)
    case "$args" in
      *" c0") echo "DROP_PKTS=100";; *" c1") echo "DROP_PKTS=103";; *" c2") echo "DROP_PKTS=108";; *) echo "DROP_PKTS=0";;
    esac;;
  ping) echo '{"changed": false, "ping": "pong"}';;
  shell)
    case "$args" in
      *"tcpdump -nn -tttt"*)
        if [ "$NO_JOB_ID" = 1 ]; then echo "k8s-worker01 | FAILED | rc=1 >>"; else echo '{ "ansible_job_id": "j123.456", "started": 1, "finished": 0 }'; fi;;
      *"listening="*)
        cmd="${args//\/var\/log\/fds-evidence\/P1-K8S-01\/ac-net-run01/$FIXTURE_ROOT}"
        echo "k8s-worker01 | CHANGED | rc=0 >>"; /bin/bash -c "$cmd";;
      *"sudo -n true"*) echo "edge01 | CHANGED | rc=0 >>"; echo "SUDO_NO";;
      *) echo "stub ok";;
    esac;;
  *) echo "stub ok";;
esac
'''

class Edge(unittest.TestCase):
    def run_case(self, **env_extra):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp); (root/'bin').mkdir()
            (root/'env').write_text(':\n')
            source = (SCRIPT.read_text()
                      .replace('~/.config/ansible/p1-ans-01.env', '"$FIXTURE_ROOT/env"')
                      .replace('~/infra-ansible', '"$FIXTURE_ROOT"')
                      .replace('~/ac-net-run01-edge.txt', '"$FIXTURE_ROOT/edge.txt"'))
            (root/'script.sh').write_text(source)
            (root/'acnet-worker-counters.sh').write_text(WORKER_COUNTERS.read_text())
            (root/'t15-edge-capture.txt').write_text(CAPTURE)
            for name, content in {'ansible': ANSIBLE, 'sleep': '#!/bin/bash\nexit 0\n'}.items():
                f = root/'bin'/name; f.write_text(content); f.chmod(0o755)
            env = dict(os.environ, FIXTURE_ROOT=temp, PATH=str(root/'bin')+':'+os.environ['PATH'], **env_extra)
            result = subprocess.run(['bash', str(root/'script.sh')], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            polls = int((root/'polls').read_text()) if (root/'polls').exists() else 0
            return result, polls

    def assert_invalid(self, out):
        self.assertIn('CAPTURE=INVALID', out)
        for line in ('T14_UDP4789=NOT_VERIFIED', 'T15_TCP179=NOT_VERIFIED', 'T15_IPIP=NOT_VERIFIED', 'COUNTER_ATTRIBUTION=NOT_VERIFIED'):
            self.assertIn(line, out)
        self.assertNotIn('CAPTURE=VALID', out)
        self.assertNotIn('PASS', out.split('RESULT LINES')[-1].replace('no PASS declared', ''))

    def test_finishes_after_several_polls(self):
        result, polls = self.run_case(JOB_FINISH_AT='4', ACNET_CAP_POLL_MAX='10')
        self.assertEqual(polls, 4, result.stdout)           # polled until finished=1, not once after a fixed sleep
        self.assertIn('CAPTURE=VALID', result.stdout)
        self.assertIn('job_finished=1 job_rc=0', result.stdout)

    def test_never_finishes_is_bounded_and_invalid(self):
        result, polls = self.run_case(JOB_FINISH_AT='99', ACNET_CAP_POLL_MAX='3')
        self.assertEqual(polls, 3, result.stdout)           # bounded, not an endless wait
        self.assert_invalid(result.stdout)
        self.assertIn('finished=0', result.stdout)

    def test_status_command_fails(self):
        result, polls = self.run_case(STATUS_FAIL='1', ACNET_CAP_POLL_MAX='3')
        self.assert_invalid(result.stdout)

    def test_finished_without_rc(self):
        result, polls = self.run_case(JOB_RC='none', ACNET_CAP_POLL_MAX='3')
        self.assert_invalid(result.stdout)

    def test_finished_with_nonzero_rc(self):
        result, polls = self.run_case(JOB_RC='7', ACNET_CAP_POLL_MAX='3')
        self.assert_invalid(result.stdout)

    def test_no_job_id_does_not_poll(self):
        result, polls = self.run_case(NO_JOB_ID='1', ACNET_CAP_POLL_MAX='3')
        self.assertEqual(polls, 0, result.stdout)
        self.assert_invalid(result.stdout)

    def test_full_status_text_is_kept(self):
        result, polls = self.run_case(JOB_FINISH_AT='1')
        self.assertIn('"LAST_STATUS_LINE": "present"', result.stdout)   # beyond line 12 of the status text
        self.assertIn('capture_status polls=1', result.stdout)

    def test_packet_counts_direction_port_source_and_dots(self):
        result, polls = self.run_case(JOB_FINISH_AT='1')
        out = result.stdout
        self.assertIn('CAPTURE=VALID', out)
        # forward edge01 -> worker01:4789 in both tcpdump print forms (VXLAN and UDP) = 2;
        # reverse direction, port 4790, another source, 110.x look-alike and 10a1b93c50 are not counted
        self.assertIn('ARRIVED tcp179_syn=3 udp4789=2 ipip_proto4=0', out)
        self.assertIn('TCP179_REPLIES synack=0 rst=1 icmp_from_target=0', out)   # RST from the target is a reply
        self.assertIn('T15_TCP179=REACHABLE_OR_REJECTED', out)

if __name__ == '__main__':
    unittest.main()
