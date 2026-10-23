from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from cgi_pipeline.capabilities.maya import scene_components_measurements as operations
from cgi_pipeline.contracts import ApiContext, validate_spec


ROOT = Path(__file__).resolve().parents[1]


class FakeCmds:
    def __init__(self):
        self.selection = ["|geo|body.f[0]"]
        self.nodes = {
            "|geo|body": [1.0, 2.0, 3.0],
            "|geo|target": [4.0, 6.0, 3.0],
        }
        self.components = {
            "|geo|body": [f"|geo|body.vtx[{index}]" for index in range(5)],
            "|geo|body.e[0]": ["|geo|body.vtx[0]", "|geo|body.vtx[1]"],
        }

    def ls(self, values=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if values == "body":
            return ["|geo|body", "|other|body"]
        values = values if isinstance(values, list) else [values]
        found = []
        for value in values:
            if value in self.nodes or value in self.components:
                found.append(value)
            elif isinstance(value, str) and "." in value:
                found.append(value)
        return found

    def polyListComponentConversion(self, source, **kwargs):
        if kwargs.get("toVertex"):
            result = []
            for value in source:
                result.extend(self.components.get(value, []))
            return result
        suffix = {
            "toEdge": "e[0]", "toFace": "f[0]", "toUV": "map[0]",
            "toVertexFace": "vtxFace[0][0]",
        }
        target = next((value for flag, value in suffix.items() if kwargs.get(flag)), "")
        return ["|geo|body." + target] if target else []

    def select(self, items=None, **kwargs):
        values = list(items or [])
        if kwargs.get("clear"):
            self.selection = []
        elif kwargs.get("add"):
            self.selection.extend(item for item in values if item not in self.selection)
        elif kwargs.get("deselect"):
            self.selection = [item for item in self.selection if item not in values]
        else:
            self.selection = values

    def xform(self, node, **kwargs):
        assert kwargs == {"query": True, "worldSpace": True, "rotatePivot": True}
        return list(self.nodes[node])

    def exactWorldBoundingBox(self, node):
        if node == "|geo|body":
            return [-1.0, -2.0, -3.0, 3.0, 4.0, 5.0]
        return []


def _context(cmds, api_id):
    return ApiContext.from_mapping(
        {"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"},
        "maya",
    )


def test_component_conversion_is_bounded_and_recoverable():
    cmds = FakeCmds()
    result = operations.select_components(
        {
            "action": "convert_select", "source": ["|geo|body"],
            "component_type": "vertex", "selection_mode": "replace",
            "max_items": 3,
        },
        _context(cmds, "maya.scene.components.select"),
    )

    assert result["status"] == "SUCCESS"
    assert result["output"]["items"] == [
        "|geo|body.vtx[0]", "|geo|body.vtx[1]", "|geo|body.vtx[2]",
    ]
    assert result["output"]["truncated"] is True
    assert result["output"]["recovery"]["selection"] == ["|geo|body.f[0]"]
    assert cmds.selection == result["output"]["after"]


def test_component_clear_and_invalid_requests():
    cmds = FakeCmds(); context = _context(cmds, "maya.scene.components.select")
    cleared = operations.select_components(
        {"action": "clear", "source": [], "max_items": 10}, context)
    assert cleared["status"] == "SUCCESS" and cmds.selection == []

    missing = operations.select_components(
        {"action": "convert_select", "source": [], "max_items": 10}, context)
    assert missing["status"] == "ERROR" and "non-empty" in missing["error"]
    unsupported = operations.select_components(
        {"action": "convert_select", "source": ["|geo|body"],
         "component_type": "shell", "max_items": 10}, context)
    assert unsupported["status"] == "ERROR" and "component_type" in unsupported["error"]
    over_limit = operations.select_components(
        {"action": "clear", "max_items": 2001}, context)
    assert over_limit["status"] == "ERROR" and "between 1 and 2000" in over_limit["error"]
    for invalid_limit in (True, "10", 1.5):
        invalid = operations.select_components(
            {"action": "clear", "max_items": invalid_limit}, context)
        assert invalid["status"] == "ERROR"
        assert "must be an integer" in invalid["error"]
    ambiguous = operations.select_components(
        {"action": "convert_select", "source": ["body"],
         "component_type": "vertex", "max_items": 10}, context)
    assert ambiguous["status"] == "ERROR" and "ambiguous" in ambiguous["error"]


def test_post_selection_failure_restores_previous_selection():
    class FailingReadCmds(FakeCmds):
        def __init__(self):
            super().__init__()
            self.selection_reads = 0

        def ls(self, values=None, **kwargs):
            if kwargs.get("selection"):
                self.selection_reads += 1
                if self.selection_reads == 2:
                    raise RuntimeError("post-selection read failed")
            return super().ls(values, **kwargs)

    cmds = FailingReadCmds()
    before = list(cmds.selection)
    result = operations.select_components(
        {"action": "convert_select", "source": ["|geo|body"],
         "component_type": "vertex", "max_items": 3},
        _context(cmds, "maya.scene.components.select"),
    )
    assert result["status"] == "ERROR"
    assert "previous selection restored" in result["error"]
    assert "post-selection read failed" in result["error"]
    assert cmds.selection == before


def test_selection_restore_failure_preserves_both_errors():
    class RestoreFailureCmds(FakeCmds):
        def __init__(self):
            super().__init__()
            self.selection_reads = 0
            self.select_calls = 0

        def ls(self, values=None, **kwargs):
            if kwargs.get("selection"):
                self.selection_reads += 1
                if self.selection_reads == 2:
                    raise RuntimeError("post-read failed")
            return super().ls(values, **kwargs)

        def select(self, items=None, **kwargs):
            self.select_calls += 1
            if self.select_calls == 2:
                raise RuntimeError("restore failed")
            return super().select(items, **kwargs)

    cmds = RestoreFailureCmds()
    result = operations.select_components(
        {"action": "convert_select", "source": ["|geo|body"],
         "component_type": "vertex", "max_items": 3},
        _context(cmds, "maya.scene.components.select"),
    )
    assert result["status"] == "ERROR"
    assert "original=post-read failed" in result["error"]
    assert "restore=restore failed" in result["error"]


def test_distance_and_bounding_box_measurements():
    cmds = FakeCmds()
    result = operations.inspect_measurements(
        {
            "items": [
                {"action": "distance", "a": "|geo|body", "b": "|geo|target"},
                {"action": "distance", "a": [0, 0, 0], "b": [0, 3, 4]},
                {"action": "bounding_box", "node": "|geo|body"},
            ],
            "max_items": 10,
        },
        _context(cmds, "maya.scene.measurements.inspect"),
    )

    assert result["status"] == "SUCCESS"
    assert result["output"]["items"][0]["distance"] == 5.0
    assert result["output"]["items"][1]["distance"] == 5.0
    bbox = result["output"]["items"][2]
    assert bbox["center"] == [1.0, 1.0, 1.0]
    assert bbox["size"] == [4.0, 6.0, 8.0]
    assert result["output"]["space"] == "world"


def test_measurements_are_bounded_and_fail_loudly():
    cmds = FakeCmds(); context = _context(cmds, "maya.scene.measurements.inspect")
    bounded = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": [0, 0, 0], "b": [1, 0, 0]}] * 3,
         "max_items": 2}, context)
    assert bounded["status"] == "SUCCESS"
    assert bounded["output"]["count"] == 2 and bounded["output"]["truncated"] is True

    bad_action = operations.inspect_measurements(
        {"items": [{"action": "angle"}], "max_items": 10}, context)
    assert bad_action["status"] == "ERROR" and "Unsupported" in bad_action["error"]
    bad_point = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": [0, 1], "b": [0, 0, 0]}],
         "max_items": 10}, context)
    assert bad_point["status"] == "ERROR" and "three-number" in bad_point["error"]
    missing_node = operations.inspect_measurements(
        {"items": [{"action": "bounding_box", "node": "missing"}],
         "max_items": 10}, context)
    assert missing_node["status"] == "ERROR" and "does not exist" in missing_node["error"]
    over_limit = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": [0, 0, 0], "b": [1, 0, 0]}],
         "max_items": 201}, context)
    assert over_limit["status"] == "ERROR" and "between 1 and 200" in over_limit["error"]
    for invalid_limit in (False, "2", 2.0):
        invalid = operations.inspect_measurements(
            {"items": [{"action": "distance", "a": [0, 0, 0],
                        "b": [1, 0, 0]}], "max_items": invalid_limit}, context)
        assert invalid["status"] == "ERROR"
        assert "must be an integer" in invalid["error"]


@pytest.mark.parametrize(
    "point",
    ([True, 0, 0], [float("nan"), 0, 0], [float("inf"), 0, 0]),
)
def test_measurement_rejects_non_finite_literal_points(point):
    cmds = FakeCmds()
    result = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": point, "b": [0, 0, 0]}]},
        _context(cmds, "maya.scene.measurements.inspect"),
    )
    assert result["status"] == "ERROR"
    assert "finite numbers" in result["error"]


def test_measurement_rejects_non_finite_host_values():
    cmds = FakeCmds()
    cmds.nodes["|geo|body"] = [float("nan"), 0.0, 0.0]
    pivot = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": "|geo|body",
                    "b": [0, 0, 0]}]},
        _context(cmds, "maya.scene.measurements.inspect"),
    )
    assert pivot["status"] == "ERROR" and "finite numbers" in pivot["error"]

    cmds = FakeCmds()
    cmds.exactWorldBoundingBox = lambda _node: [0, 0, 0, 1, 1, float("inf")]
    bbox = operations.inspect_measurements(
        {"items": [{"action": "bounding_box", "node": "|geo|body"}]},
        _context(cmds, "maya.scene.measurements.inspect"),
    )
    assert bbox["status"] == "ERROR"
    assert "finite world bounding box" in bbox["error"]


def test_measurement_rejects_non_finite_derived_values():
    cmds = FakeCmds()
    distance = operations.inspect_measurements(
        {"items": [{"action": "distance", "a": [-1e308, 0, 0],
                    "b": [1e308, 0, 0]}]},
        _context(cmds, "maya.scene.measurements.inspect"),
    )
    assert distance["status"] == "ERROR"
    assert "distance delta" in distance["error"]

    cmds.exactWorldBoundingBox = lambda _node: [-1e308, 0, 0, 1e308, 1, 1]
    bbox = operations.inspect_measurements(
        {"items": [{"action": "bounding_box", "node": "|geo|body"}]},
        _context(cmds, "maya.scene.measurements.inspect"),
    )
    assert bbox["status"] == "ERROR"
    assert "bounding-box size" in bbox["error"]


def test_new_manifests_validate_without_generated_catalog():
    relatives = [
        "src/cgi_pipeline/capabilities/maya/scene/components_select/capability.yaml",
        "src/cgi_pipeline/capabilities/maya/scene/measurements_inspect/capability.yaml",
    ]
    expected_levels = {
        relatives[0]: "contract_tested",
        relatives[1]: "live_tested",
    }
    for relative, expected_level in expected_levels.items():
        spec = validate_spec(
            yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")), relative)
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == expected_level
