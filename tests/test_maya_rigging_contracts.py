"""Contract-level coverage for non-MFace Maya rigging capabilities.

These tests deliberately use tiny Maya command stubs.  They prove that the
deployed handlers are real implementations with stable receipt boundaries;
they do not claim a live Maya result (that requires a mayapy/foreground smoke).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
RIG_ROOT = ROOT / "src" / "cgi_pipeline" / "capabilities" / "maya" / "rig"


def _maya_modules(cmds):
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    api = types.ModuleType("maya.api")
    om = types.ModuleType("maya.api.OpenMaya")
    oma = types.ModuleType("maya.api.OpenMayaAnim")
    api.OpenMaya = om
    api.OpenMayaAnim = oma
    maya.api = api
    return {"maya": maya, "maya.cmds": cmds, "maya.api": api,
            "maya.api.OpenMaya": om, "maya.api.OpenMayaAnim": oma}


def test_non_mface_rig_manifests_deploy_real_handlers():
    expected = {
        "maya.rig.maya_check_asset_hierarchy",
        "maya.rig.maya_clean_skinweights",
        "maya.rig.maya_fix_asset_hierarchy",
        "maya.rig.maya_fix_shape_names",
        "maya.rig.maya_get_hierarchy",
        "maya.rig.maya_sync_rig_incremental",
        "maya.rig.reference.update",
        "maya.rig.skin.query_weights",
    }
    for path in RIG_ROOT.rglob("capability.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec["api_id"] in expected:
            assert spec["implementation"]["status"] == "implemented"
            assert spec["verification"]["level"] in {"contract_tested", "live_tested"}
            assert "tests/test_maya_rigging_contracts.py" in spec["verification"]["evidence"]


def test_check_hierarchy_helpers_are_executable_without_scene_mutation():
    class FakeCmds:
        def objExists(self, node):
            return node in {"|root", "|root|mesh"}

        def listRelatives(self, node, **kwargs):
            if kwargs.get("type") == "mesh":
                return ["|root|mesh"]
            return []

        def getAttr(self, plug):
            assert plug.endswith(".intermediateObject")
            return False

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_check_asset_hierarchy.maya_check_asset_hierarchy"
        )
        assert mod._valid_meshes_under("|root") == ["|root|mesh"]
        safe, review = mod._classify_top_nodes([])
        assert safe == [] and review == []


def test_hierarchy_world_depth_zero_includes_top_level_transforms():
    class FakeCmds:
        def ls(self, node=None, **kwargs):
            if kwargs.get("assemblies"):
                return ["|root", "|other"]
            return []

        def listRelatives(self, node, **kwargs):
            if kwargs.get("allDescendents") and node == "|root":
                return ["|root|child"]
            return []

        def nodeType(self, node):
            return "transform"

        def objectType(self, node, **kwargs):
            assert kwargs == {"isAType": "transform"}
            return True

        def objExists(self, node):
            return True

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_get_hierarchy.adapter"
        )
        context = types.SimpleNamespace(extras={"_api_id": "maya.rig.maya_get_hierarchy", "_api_version": "1.0.0"}, project="", asset_name="", source_path="", run_dir="")
        receipt = mod.execute({"root": "|", "depth": 0}, context)
    assert receipt["status"] == "SUCCESS"
    assert [row["path"] for row in receipt["output"]["nodes"]] == ["|root", "|other"]


def test_hierarchy_without_shapes_keeps_joint_and_transform_subclasses():
    class FakeCmds:
        types = {"|root": "transform", "|root|joint": "joint",
                 "|root|joint|custom": "customTransform",
                 "|root|joint|custom|shape": "mesh"}

        def objExists(self, node):
            return node in self.types

        def ls(self, node=None, **kwargs):
            return ["|root"] if kwargs.get("assemblies") else [node]

        def listRelatives(self, node, **kwargs):
            return [path for path in self.types if path.startswith(node + "|")]

        def nodeType(self, node):
            return self.types[node]

        def objectType(self, node, **kwargs):
            assert kwargs == {"isAType": "transform"}
            return self.types[node] != "mesh"

    with patch.dict(sys.modules, _maya_modules(FakeCmds())):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_get_hierarchy.adapter"
        )
        result = mod._implementation({"parameters": {"root": "|root", "depth": 2}})
        assert [row["type"] for row in result["output"]["nodes"]] == [
            "transform", "joint", "customTransform"
        ]
        result = mod._implementation({"parameters": {"root": "|root", "include_shapes": True}})
        assert len(result["output"]["nodes"]) == 4


def test_hierarchy_unknown_root_is_an_error_receipt():
    modules = _maya_modules(types.SimpleNamespace(objExists=lambda _node: False))
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_get_hierarchy.adapter"
        )
        from cgi_pipeline.contracts import ApiContext
        context = ApiContext.from_mapping({"_api_id": "maya.rig.maya_get_hierarchy"}, "maya")
        receipt = mod.execute({"root": "|missing"}, context)
    assert receipt["status"] == "ERROR"
    assert "|missing" in receipt["error"]


def test_check_hierarchy_block_on_fail_preserves_audit_status():
    class FakeCmds:
        def file(self, **kwargs):
            return ""

        def objExists(self, _node):
            return False

        def ls(self, **kwargs):
            return []

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_check_asset_hierarchy.maya_check_asset_hierarchy"
        )
        mod._required_root_from_config = lambda _project, _stage: ("|Group|Geometry|cache", [])
        adapter = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_check_asset_hierarchy.adapter"
        )
        from cgi_pipeline.contracts import ApiContext

        context = ApiContext.from_mapping(
            {"_api_id": "maya.rig.maya_check_asset_hierarchy", "_api_version": "1.0.0"},
            "maya",
        )
        receipt = adapter.execute({"block_on_fail": True}, context)
    assert receipt["status"] == "AUDIT_FAILED"
    assert receipt["error"]


def test_check_hierarchy_missing_project_config_is_blocked():
    class FakeCmds:
        def file(self, **kwargs):
            return ""

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_check_asset_hierarchy.maya_check_asset_hierarchy"
        )
        mod._required_root_from_config = lambda *_args: (_ for _ in ()).throw(
            FileNotFoundError("项目配置不存在")
        )
        receipt = mod.execute({"parameters": {"project": "missing"}})

    assert receipt["status"] == "BLOCKED"
    assert receipt["output"]["code"] == "PROJECT_CONFIG_REQUIRED"
    assert "config/missing_config.json" in receipt["recovery_hint"]


def test_clean_skinweights_empty_scene_returns_canonical_receipt():
    class FakeCmds:
        def file(self, **kwargs):
            return ""

        def ls(self, **kwargs):
            assert kwargs == {"type": "skinCluster"}
            return []

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.maya_clean_skinweights"
        )
        receipt = mod.execute({"parameters": {}, "source_path": ""})
    assert receipt["api_id"] == "maya_clean_skinweights"
    assert receipt["status"] == "SUCCESS"


def test_fix_asset_hierarchy_missing_check_result_fails_loudly():
    class FakeCmds:
        def file(self, **kwargs):
            return ""

        def undoInfo(self, **kwargs):
            return None

        def undo(self):
            return None

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_fix_asset_hierarchy.maya_fix_asset_hierarchy"
        )
        receipt = mod.execute({"parameters": {}, "source_path": ""})
    assert receipt["api_id"] == "maya_fix_asset_hierarchy"
    assert receipt["status"] == "ERROR"


def test_fix_shape_names_empty_scene_is_idempotent():
    class FakeCmds:
        def file(self, **kwargs):
            return ""

        def ls(self, **kwargs):
            assert kwargs == {"type": "mesh", "long": True}
            return []

        def undoInfo(self, **kwargs):
            return None

    modules = _maya_modules(FakeCmds())
    with patch.dict(sys.modules, modules):
        mod = importlib.import_module(
            "cgi_pipeline.capabilities.maya.rig.maya_fix_shape_names.maya_fix_shape_names"
        )
        receipt = mod.execute({"parameters": {}, "source_path": ""})
    assert receipt["api_id"] == "maya_fix_shape_names"
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["renamed_count"] == 0


def test_deformation_inherit_skin_missing_maya_context_fails_loudly():
    from cgi_pipeline.capabilities.maya.rig.maya_deformation_inherit_skin import adapter
    from cgi_pipeline.contracts import ApiContext

    context = ApiContext.from_mapping(
        {"_api_id": "maya.rig.maya_deformation_inherit_skin", "_api_version": "1.0.0"},
        "maya",
    )
    receipt = adapter.execute({}, context)
    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "MAYA_DEFORMATION_INHERIT_SKIN_FAILED"
    assert "Maya execution context" in receipt["error"]


def test_deformation_inherit_skin_manifest_exposes_resolvable_contract():
    path = RIG_ROOT / "maya_deformation_inherit_skin" / "capability.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["implementation"]["status"] == "implemented"
    assert spec["verification"]["level"] == "live_tested"
    assert spec["inputs"]["reference_rig_path"]["required"] is True
    assert spec["inputs"]["reference_group"]["required"] is True
    assert spec["inputs"]["mode"]["choices"] == ["diagnose_only", "apply_skin"]
    assert spec["execution_modes"] == ["local", "background"]
    assert spec["inputs"]["owner_map"]["default"] == {}
    assert spec["inputs"]["influence_map"]["default"] == {}
    assert "output_path" in spec["outputs"]


def test_deformation_inherit_skin_owner_ranking_prefers_matching_surface_and_count():
    from cgi_pipeline.capabilities.maya.rig.maya_deformation_inherit_skin.ownership import choose_owner

    target = {"name": "body_msh", "vertex_count": 100, "centroid": [0, 0, 0], "diagonal": 10}
    sources = [
        {"source": "|old|body", "name": "body", "vertex_count": 100,
         "centroid": [0, 0, 0], "diagonal": 10, "surface_distance": 0.01},
        {"source": "|old|eye", "name": "eye", "vertex_count": 20,
         "centroid": [4, 0, 0], "diagonal": 2, "surface_distance": 2.0},
    ]
    owner = choose_owner(target, sources)
    assert owner["source"] == "|old|body"
    assert owner["score"] > 0.9


def test_deformation_inherit_skin_owner_ranking_rejects_ambiguous_layers():
    from cgi_pipeline.capabilities.maya.rig.maya_deformation_inherit_skin.ownership import (
        OwnershipAmbiguous, choose_owner,
    )

    target = {"name": "surface", "vertex_count": 100, "centroid": [0, 0, 0], "diagonal": 1}
    candidates = [
        {"source": f"|old|layer{index}", "name": f"layer{index}", "vertex_count": 100,
         "centroid": [0, 0, 0], "diagonal": 1, "surface_distance": 0.01}
        for index in (1, 2)
    ]
    try:
        choose_owner(target, candidates)
    except OwnershipAmbiguous as exc:
        assert len(exc.candidates) == 2
    else:
        raise AssertionError("near-identical layers must require an explicit owner")


def test_deformation_inherit_skin_rejects_artifacts_outside_managed_sandbox(tmp_path, monkeypatch):
    from cgi_pipeline.capabilities.maya.rig.maya_deformation_inherit_skin import scene_ops

    # The test's temporary directory may itself live under the real projects/
    # root. Fix the managed root explicitly so "outside" has a stable meaning.
    monkeypatch.setattr(scene_ops, "REPOSITORY_ROOT", tmp_path / "managed")

    reference = tmp_path / "reference.ma"
    reference.write_text("// fixture", encoding="utf-8")
    context = types.SimpleNamespace(run_dir=str(tmp_path), source_path="", asset_name="fixture")
    try:
        scene_ops.output_paths({
            "reference_rig_path": str(reference),
            "mode": "diagnose_only",
            "info_dir": str(tmp_path / "outside"),
        }, context)
    except ValueError as exc:
        assert "managed projects or runs sandbox" in str(exc)
    else:
        raise AssertionError("artifact paths outside the managed sandbox must be rejected")
