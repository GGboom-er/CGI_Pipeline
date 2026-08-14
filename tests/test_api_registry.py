from __future__ import annotations

import unittest

from cgi_pipeline.catalog import capability_help, get_capability, list_capabilities, reload
from cgi_pipeline.execution import execute_api


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


if __name__ == "__main__":
    unittest.main()
