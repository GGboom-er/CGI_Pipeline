from __future__ import annotations

import unittest

from api.runner import execute_api


class FakeCmds:
    def __init__(self, references, change_namespace=False):
        self.references = references
        self.change_namespace = change_namespace
        self.undo_calls = []

    def ls(self, type):
        assert type == "reference"
        return ["sharedReferenceNode", *self.references]

    def referenceQuery(self, node, **kwargs):
        data = self.references[node]
        if kwargs.get("filename"):
            return data["path"]
        if kwargs.get("namespace"):
            return data["namespace"]
        if kwargs.get("isLoaded"):
            return data["loaded"]
        if kwargs.get("editStrings"):
            return []
        raise AssertionError(f"Unexpected referenceQuery: {kwargs}")

    def file(self, path, **kwargs):
        data = self.references[kwargs["loadReference"]]
        data["path"] = path
        if self.change_namespace:
            data["namespace"] = ":changed"

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)


class MayaReferenceApiTests(unittest.TestCase):
    def test_preview_is_read_only_and_uses_explicit_mapping(self):
        fake = FakeCmds(
            {
                "targetRN": {
                    "path": "C:/assets/target_v001.ma",
                    "namespace": ":target",
                    "loaded": True,
                }
            }
        )
        result = execute_api(
            "maya.rig.reference.update",
            {
                "operation": "preview",
                "target_map": [
                    {
                        "old_path": "C:/assets/target_v001.ma",
                        "asset": "target",
                        "new_path": "C:/assets/target_v002.ma",
                    }
                ],
            },
            {"execution_mode": "background", "cmds_module": fake},
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["output"]["counts"]["UPDATE"], 1)
        self.assertEqual(fake.undo_calls, [])

    def test_apply_reports_verification_failure(self):
        fake = FakeCmds(
            {
                "targetRN": {
                    "path": "C:/assets/target_v001.ma",
                    "namespace": ":target",
                    "loaded": True,
                }
            },
            change_namespace=True,
        )
        result = execute_api(
            "maya.rig.reference.update",
            {
                "operation": "apply",
                "target_map": [
                    {
                        "old_path": "C:/assets/target_v001.ma",
                        "asset": "target",
                        "new_path": "C:/assets/target_v002.ma",
                    }
                ],
            },
            {"execution_mode": "background", "cmds_module": fake},
        )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_code"], "REFERENCE_VERIFY_FAILED")
        self.assertTrue(fake.undo_calls[0]["openChunk"])
        self.assertTrue(fake.undo_calls[-1]["closeChunk"])


if __name__ == "__main__":
    unittest.main()
