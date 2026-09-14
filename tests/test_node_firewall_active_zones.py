"""Exercise the real snapshot gate with firewalld's default-zone annotation."""
import copy
import unittest
from unittest.mock import patch

from test_node_firewall import n, FakeBackend, MemoryJournal


class ActiveZoneSnapshotTests(unittest.TestCase):
    def snapshot(self, active):
        backend = n.Backend("k8s-control02")
        baseline = FakeBackend(MemoryJournal(), backend.node).snapshot()
        baseline.update(direct_runtime="", direct_permanent="")

        def read(argv):
            if argv == ["firewall-cmd", "--get-active-zones"]:
                return active
            if argv[-1] == "--get-zones":
                return "public"
            if argv[-1].startswith("--info-service="):
                return "unchanged service definition"
            if argv[-1] == "--get-all-passthroughs":
                return ""
            raise AssertionError("unexpected command: " + repr(argv))

        try:
            with patch.object(n.BaseBackend, "snapshot", return_value=copy.deepcopy(baseline)), \
                    patch.object(backend, "read_snapshot", side_effect=read), \
                    patch.object(n, "residual_metadata", return_value=[]):
                result = backend.snapshot()
            self.assertEqual(result["planes"], baseline["planes"])
            self.assertEqual(result["active_zones"], active)  # Keep original evidence for drift checks.
            return result
        finally:
            backend.cache.cleanup()

    def test_plain_public_single_interface_still_passes(self):
        self.snapshot("public\n  interfaces: ens160")

    def test_onsite_default_annotation_passes_without_changing_snapshot_evidence(self):
        self.snapshot("public (default)\n  interfaces: ens160")

    def test_extra_zones_sources_interfaces_and_unknown_annotations_still_stop(self):
        invalid = (
            "public (default)\n  interfaces: ens160\ntrusted\n  interfaces: ens192",
            "public (default)\n  interfaces: ens160 ens192",
            "public (default)\n  interfaces: ens192",
            "public (default)\n  interfaces: ens160 ens160",
            "public (default)\n  interfaces: ens160\n  sources: 192.0.2.0/24",
            "public (default)\n  sources: 192.0.2.1\n  interfaces: ens160",
            "trusted (default)\n  interfaces: ens160",
            "public (default) (default)\n  interfaces: ens160",
            "public (unknown)\n  interfaces: ens160",
            "public\n  interfaces: ens160 (default)",
            "public (default)",
            "",
        )
        for active in invalid:
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, "additional active zone"):
                self.snapshot(active)


if __name__ == "__main__":
    unittest.main()
