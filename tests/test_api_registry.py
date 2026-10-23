from __future__ import annotations

import unittest
from pathlib import Path

import yaml

import cgi_pipeline.contracts as contracts
from cgi_pipeline.core import capability_registry
from cgi_pipeline.catalog import capability_help, get_capability, list_capabilities, reload
from cgi_pipeline.execution import execute_api

ROOT = Path(__file__).resolve().parents[1]

class ApiRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reload()

    def test_reference_api_is_discoverable_by_facets(self):
        rows = list_capabilities(
            executor="maya", capability="rigging.reference", operation="update"
        )
        self.assertEqual([row["api_id"] for row in rows], ["maya.rig.reference.update"])
        self.assertEqual(rows[0]["modes"], ["preview", "apply"])

    def test_help_is_manifest_backed(self):
        spec = get_capability("maya.rig.reference.update")
        help_data = capability_help("maya.rig.reference.update")
        self.assertNotIn("handler", help_data)
        self.assertEqual(help_data["api_id"], spec["api_id"])
        self.assertIn("target_map", help_data["inputs"])

    def test_capability_state_defaults_and_placeholders(self):
        # Every shipped manifest now records its state, so check the defaults on a copy
        # with the implementation/verification blocks removed.
        manifest = ROOT / get_capability("blender.asset.blender_build_asset_info")["manifest_path"]
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        raw.pop("implementation")
        raw.pop("verification")
        normal = contracts.validate_spec(raw, str(manifest))
        self.assertEqual(normal["implementation"]["status"], "unknown")
        self.assertEqual(normal["verification"]["level"], "untested")

        inherited = capability_help("maya.rig.maya_deformation_inherit_skin")
        self.assertEqual(inherited["implementation"]["status"], "implemented")
        self.assertEqual(inherited["verification"]["level"], "live_tested")

    def test_runner_returns_stable_errors(self):
        missing = execute_api("missing.api")
        self.assertEqual(missing["status"], "ERROR")
        self.assertEqual(missing["error_code"], "API_NOT_FOUND")

        foreground = execute_api(
            "maya.rig.reference.update",
            context={"execution_mode": "foreground"},
        )
        self.assertEqual(foreground["status"], "ERROR")
        self.assertEqual(foreground["error_code"], "API_CONTRACT_ERROR")

    def test_runtime_registry_reload_refreshes_stale_contracts(self):
        contracts.API_STATUSES = {"SUCCESS"}

        capability_registry.reload()

        self.assertIn("AUDIT_FAILED", contracts.API_STATUSES)
        self.assertEqual(
            capability_registry.get_executor("maya.asset.maya_master_cleanup"),
            "maya",
        )


if __name__ == "__main__":
    unittest.main()
