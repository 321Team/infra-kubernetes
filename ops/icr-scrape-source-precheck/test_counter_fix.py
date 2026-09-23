import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import collect


class CounterFixTests(unittest.TestCase):
    def test_real_symlink_preserves_invocation_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            alias = pathlib.Path(tmp, "iptables-save")
            alias.symlink_to("/usr/bin/python3")
            with patch.object(collect, "TOOL_DIRS", (tmp,)):
                invoked = collect.tool("iptables-save")
            self.assertEqual(invoked, str(alias))
            r = collect.run([invoked, "-c", "import sys; print(sys.executable)"])
            self.assertEqual(r["rc"], 0)
            self.assertEqual(r["stdout"].strip(), str(alias))

    def test_writable_target_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp, "multi"); target.write_text("fake"); target.chmod(0o777)
            pathlib.Path(tmp, "iptables-save").symlink_to(target)
            with patch.object(collect, "TOOL_DIRS", (tmp,)), self.assertRaises(ValueError):
                collect.tool("iptables-save")

    def test_partial_reads_and_missing_tool_are_explicit(self):
        for r in [{"reads": [{"rc": 1}]}, {"reads": [], "iptables_save_available": False}]:
            collect.set_collection_result(r)
            self.assertEqual(r["result"], "COLLECTED_PARTIAL")

    def test_counter_only_never_requests_capture_tools(self):
        output = io.StringIO()
        def read(argv, **kwargs):
            if "address" in argv:
                stdout = json.dumps([{"ifname": "ens160", "flags": ["UP"], "addr_info": [{"local": "10.1.93.105"}]}])
            elif argv[-1] == "-c":
                stdout = "*filter\n:INPUT DROP [1:60]\nCOMMIT\n*nat\nCOMMIT\n"
            else:
                self.fail("unexpected command: " + repr(argv))
            return {"command": argv, "rc": 0, "stdout": stdout, "stderr": ""}
        def existing_tool(name, **kwargs):
            self.assertIn(name, ["sudo", "ip", "iptables-save"])
            return "/usr/sbin/" + name
        with patch.object(collect.sys, "argv", ["collect.py", "counters"]), \
             patch.object(collect.socket, "gethostname", return_value="k8s-worker01"), \
             patch.object(collect.getpass, "getuser", return_value="svc_ansible"), \
             patch.object(collect, "tool", side_effect=existing_tool), \
             patch.object(collect, "run", side_effect=read), \
             patch.object(collect.time, "sleep") as sleep, contextlib.redirect_stdout(output):
            self.assertEqual(collect.main(), 0)
        sleep.assert_called_once_with(20)
        r = json.loads(output.getvalue())
        self.assertEqual(r["captures"], [])
        self.assertEqual([q["snapshot"] for q in r["reads"] if "snapshot" in q], ["before", "after"])

    def test_read_failure_stops_before_wait(self):
        r = {"reads": []}
        with patch.object(collect, "tool", return_value="/usr/sbin/iptables-save"), \
             patch.object(collect, "run", return_value={"command": ["iptables-save", "-c"], "rc": 1, "stdout": "", "stderr": "denied"}) as run, \
             patch.object(collect.time, "sleep") as sleep, self.assertRaises(ValueError):
            collect.counter_window(r, "/usr/bin/sudo")
        self.assertEqual(r["reads"][0]["stderr"], "denied")
        sleep.assert_not_called(); self.assertEqual(run.call_count, 1)

    def test_empty_successful_output_is_not_complete(self):
        with patch.object(collect, "tool", return_value="/usr/sbin/iptables-save"), \
             patch.object(collect, "run", return_value={"command": ["iptables-save", "-c"], "rc": 0, "stdout": "", "stderr": ""}), \
             self.assertRaises(ValueError):
            collect.counter_window({"reads": []}, "/usr/bin/sudo")


if __name__ == "__main__": unittest.main()
