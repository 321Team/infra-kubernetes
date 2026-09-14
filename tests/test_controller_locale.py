"""Exercise child-process locale and real Ansible inventory startup when installed."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def controller(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControllerLocaleTests(unittest.TestCase):
    def test_child_process_has_utf8_locale_even_when_parent_is_c(self):
        # Python UTF-8 mode alone does not satisfy Ansible's locale.getlocale() check.
        probe = "import json,locale;locale.setlocale(locale.LC_ALL,'');print(json.dumps(locale.getlocale()))"
        for filename in ("run-node-firewall.py", "run-worker-followup.py"):
            with self.subTest(controller=filename):
                module = controller(filename)
                with patch.dict(os.environ, {"LANG": "C", "LC_ALL": "C"}):
                    result = module.run([sys.executable, "-I", "-c", probe])
                self.assertEqual(result.returncode, 0, result.stderr)
                encoding = json.loads(result.stdout)[1]
                self.assertIn((encoding or "").lower(), ("utf-8", "utf8"))

    @unittest.skipUnless(shutil.which("ansible-inventory"), "real Ansible CLI is not installed")
    def test_real_ansible_inventory_parses_without_locale_error(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "hosts.ini"
            inventory.write_text("[utf8_probe]\nlocalhost ansible_connection=local\n", encoding="utf-8")
            for filename in ("run-node-firewall.py", "run-worker-followup.py"):
                with self.subTest(controller=filename):
                    module = controller(filename)
                    with patch.dict(os.environ, {"LANG": "C", "LC_ALL": "C", "ANSIBLE_CONFIG": "/unapproved"}):
                        env = module.controller_env()
                    self.assertNotIn("ANSIBLE_CONFIG", env)
                    # Supply an explicit empty configuration, like preflight supplies its approved config.
                    config = Path(directory) / "ansible.cfg"
                    config.write_text("[defaults]\n", encoding="utf-8")
                    env["ANSIBLE_CONFIG"] = str(config)
                    result = module.run([shutil.which("ansible-inventory"), "-i", str(inventory), "--list"],
                                        cwd=directory, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["utf8_probe"]["hosts"], ["localhost"])


if __name__ == "__main__":
    unittest.main()
