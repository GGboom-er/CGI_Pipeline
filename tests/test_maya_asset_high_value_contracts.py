"""Receipt/adapter contracts for high-value Maya asset capabilities.

These checks intentionally stop at the host boundary.  They prove that the
local implementation is present and emits a canonical receipt; viewport,
file-system and scene graph success paths still require a live Maya session.
"""

from __future__ import annotations

import builtins
import importlib
import os
import sys
import types


class _Cmds:
    def file(self, *args, **kwargs):
        if kwargs.get("sceneName"): return ""
        if kwargs.get("modified"): return False
        if kwargs.get("reference"): return []
        return ""
    def ls(self, *args, **kwargs): return []
    def listRelatives(self, *args, **kwargs): return []
    def polyEvaluate(self, *args, **kwargs): return 0
    def playbackOptions(self, *args, **kwargs): return 1.0
    def currentTime(self, *args, **kwargs): return 1.0
    def currentUnit(self, *args, **kwargs): return "cm" if kwargs.get("linear") else "film"
    def objExists(self, *args, **kwargs): return False
    def pluginInfo(self, *args, **kwargs): return []
    def getAttr(self, *args, **kwargs): return 0
    def __getattr__(self, _name):
        return lambda *args, **kwargs: []


def _install_maya():
    cmds = _Cmds()
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    maya.mel = types.ModuleType("maya.mel")
    maya.mel.eval = lambda *_a, **_k: ""
    maya.OpenMaya = types.ModuleType("maya.OpenMaya")
    maya.OpenMayaUI = types.ModuleType("maya.OpenMayaUI")
    sys.modules.update({
        "maya": maya,
        "maya.cmds": cmds,
        "maya.mel": maya.mel,
        "maya.OpenMaya": maya.OpenMaya,
        "maya.OpenMayaUI": maya.OpenMayaUI,
    })
    return cmds


def test_high_value_maya_implementations_emit_receipts(monkeypatch):
    _install_maya()
    from cgi_pipeline.capabilities.maya.asset.exec_code import exec_code
    from cgi_pipeline.capabilities.maya.asset.maya_get_scene_info import maya_get_scene_info
    from cgi_pipeline.capabilities.maya.asset.maya_freeze_transforms import maya_freeze_transforms
    from cgi_pipeline.capabilities.maya.asset.maya_capture_viewport import maya_capture_viewport
    from cgi_pipeline.capabilities.maya.asset.rename_asset import rename_asset
    from cgi_pipeline.capabilities.maya.asset.validate_publish import validate_publish
    from cgi_pipeline.capabilities.maya.asset.maya_master_cleanup import maya_master_cleanup

    success = exec_code.execute({"parameters": {"code": "result={'ok': True}"}})
    assert success["status"] == "SUCCESS" and success["output"]["result"]["ok"] is True
    payload = {"task_id": "fixture", "workspace_id": "business", "run_dir": "Y:/sandbox/fixture", "source_path": "Y:/sandbox/input.ma",
               "parameters": {"code": "result = dict(context); context['run_dir'] = 'local-change'"}}
    contextual = exec_code.execute(payload)
    assert contextual["output"]["result"]["run_dir"] == payload["run_dir"] == "Y:/sandbox/fixture"
    assert contextual["output"]["result"]["source_path"] == payload["source_path"]
    assert contextual["output"]["result"]["task_id"] == "fixture"
    assert contextual["output"]["result"]["workspace_id"] == "business"
    assert maya_get_scene_info.execute({"parameters": {}})["status"] == "SUCCESS"
    assert maya_freeze_transforms.execute({"parameters": {}})["status"] == "SUCCESS"
    assert maya_capture_viewport.execute({"parameters": {}})["status"] in {"SUCCESS", "ERROR"}
    assert rename_asset.execute({"parameters": {}})["status"] == "ERROR"
    monkeypatch.setattr(validate_publish, "_generate_report", lambda *args: ("", ""))
    assert validate_publish.execute({"parameters": {}})["status"] in {"SUCCESS", "AUDIT_FAILED"}
    assert maya_master_cleanup.execute({"parameters": {"mode": "check"}})["status"] in {"SUCCESS", "AUDIT_FAILED", "ERROR"}


def test_validate_publish_sanitizes_windows_report_name(tmp_path):
    _install_maya()
    from cgi_pipeline.capabilities.maya.asset.validate_publish import validate_publish

    result_path, _ = validate_publish._generate_report(
        {"passed": True, "checks": []}, "?", "bad:name", "asset*", "", 0.0, str(tmp_path)
    )
    assert not set('<>:"/\\|?*').intersection(os.path.basename(result_path))
    assert os.path.isfile(result_path)


def test_rename_asset_loads_without_filelock_and_uses_stage_task(monkeypatch, tmp_path):
    _install_maya()
    module_name = "cgi_pipeline.capabilities.maya.asset.rename_asset.rename_asset"
    package_name = "cgi_pipeline.capabilities.maya.asset.rename_asset"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, package_name, raising=False)
    monkeypatch.delitem(sys.modules, "cgi_pipeline.core.asset_resolver", raising=False)

    real_import = builtins.__import__
    attempted_filelock = []

    def import_without_filelock(name, *args, **kwargs):
        if name == "filelock":
            attempted_filelock.append(name)
            raise ModuleNotFoundError("filelock disabled for Maya host test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_filelock)
    rename_asset = importlib.import_module(module_name)
    assert attempted_filelock == []

    class RenameCmds:
        def __init__(self):
            self.scene = str(tmp_path / "input.ma")

        def file(self, *args, **kwargs):
            if kwargs.get("query") and kwargs.get("sceneName"):
                return self.scene
            if "rename" in kwargs:
                self.scene = kwargs["rename"]
                return self.scene
            if kwargs.get("save"):
                return self.scene
            return ""

    cmds = RenameCmds()
    monkeypatch.setattr(rename_asset, "cmds", cmds)
    monkeypatch.setattr(
        rename_asset,
        "load_project_config",
        lambda _project: {"stages": {"rig": {"primary_task": "rigMaster"}}},
    )
    monkeypatch.setattr(rename_asset, "is_protected_path", lambda _path: False)

    receipt = rename_asset.execute({"parameters": {
        "project": "ysj",
        "category": "chr",
        "asset_name": "pl193fixture",
        "stage": "rig",
        "output_dir": str(tmp_path),
    }})

    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["result"]["standard_name"] == (
        "ysj_chr_pl193fixture_rig_rigMaster_v001.ma"
    )


class _BoundedSceneCmds(_Cmds):
    def __init__(self, mesh_count=0, failed_mesh_count=0, material_count=0, reference_count=0):
        self.mesh_count = mesh_count
        self.failed_mesh_count = failed_mesh_count
        self.material_count = material_count
        self.reference_count = reference_count

    def file(self, *args, **kwargs):
        if kwargs.get("reference"):
            return [f"reference{index}.ma" for index in range(self.reference_count)]
        return super().file(*args, **kwargs)

    def ls(self, *args, **kwargs):
        if kwargs.get("type") == "mesh":
            return [f"mesh{index}" for index in range(self.mesh_count)]
        if kwargs.get("type") == "shadingEngine":
            return [f"sg{index}" for index in range(self.material_count)]
        if kwargs.get("materials"):
            return list(args[0])
        return []

    def listRelatives(self, node, **kwargs):
        return [node + "Parent"] if kwargs.get("parent") else []

    def polyEvaluate(self, node, **kwargs):
        index = int(node.removeprefix("mesh"))
        if index < self.failed_mesh_count:
            raise RuntimeError("fixture topology failure")
        return index + 1

    def listConnections(self, node, **kwargs):
        return ["material" + node.removeprefix("sg").split(".")[0]]

    def nodeType(self, node):
        return "lambert"


def _inspect_scene(monkeypatch, cmds):
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    module = importlib.import_module(
        "cgi_pipeline.capabilities.maya.asset.maya_get_scene_info.maya_get_scene_info"
    )
    return module.execute({"parameters": {}})


def test_scene_info_discloses_every_bounded_collection_and_partial_totals(monkeypatch):
    receipt = _inspect_scene(monkeypatch, _BoundedSceneCmds(32, 21, 21, 11))
    assert receipt["status"] == "PARTIAL"
    result = receipt["output"]["result"]
    for name, total, returned, limit in [
        ("materials", 21, 20, 20), ("references", 11, 10, 10),
        ("warnings", 21, 20, 20), ("top_meshes_by_faces", 11, 10, 10),
    ]:
        assert len(result[name]) == returned
        assert result["collections"][name] == {
            "total": total, "returned": returned, "limit": limit, "truncated": True
        }
    assert result["statistics"]["meshes"] == 32
    assert result["statistics"]["meshes_evaluated"] == 11
    assert result["statistics"]["mesh_query_failures"] == 21
    assert result["statistics"]["geometry_totals_complete"] is False
    assert result["statistics"]["total_faces"] == sum(range(22, 33))


def test_scene_info_empty_scene_reports_complete_empty_collections(monkeypatch):
    receipt = _inspect_scene(monkeypatch, _BoundedSceneCmds())
    assert receipt["status"] == "SUCCESS"
    result = receipt["output"]["result"]
    assert result["statistics"]["geometry_totals_complete"] is True
    for row in result["collections"].values():
        assert row["total"] == row["returned"] == 0
        assert row["truncated"] is False
