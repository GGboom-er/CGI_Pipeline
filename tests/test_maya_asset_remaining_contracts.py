"""Minimal host/path contract checks for the remaining Maya asset handlers."""

from __future__ import annotations

import sys
import types

from cgi_pipeline.catalog import get_capability
from cgi_pipeline.contracts import validate_params


class _Cmds:
    def file(self, *args, **kwargs):
        if kwargs.get("sceneName"): return ""
        if kwargs.get("modified"): return False
        return ""
    def ls(self, *args, **kwargs): return []
    def objExists(self, *args, **kwargs): return False
    def listRelatives(self, *args, **kwargs): return []
    def getAttr(self, *args, **kwargs): return 0
    def __getattr__(self, _name): return lambda *args, **kwargs: []


def _install_maya():
    maya = types.ModuleType("maya")
    maya.cmds = _Cmds()
    maya.mel = types.ModuleType("maya.mel")
    maya.mel.eval = lambda *_a, **_k: ""
    maya.OpenMaya = types.ModuleType("maya.OpenMaya")
    maya.api = types.ModuleType("maya.api")
    maya.api.OpenMaya = types.ModuleType("maya.api.OpenMaya")
    sys.modules.update({
        "maya": maya, "maya.cmds": maya.cmds, "maya.mel": maya.mel,
        "maya.OpenMaya": maya.OpenMaya, "maya.api": maya.api,
        "maya.api.OpenMaya": maya.api.OpenMaya,
    })


def test_remaining_maya_asset_handlers_reject_or_return_receipts(monkeypatch):
    _install_maya()
    from cgi_pipeline.capabilities.maya.asset.check_uvsets import check_uvsets
    from cgi_pipeline.capabilities.maya.asset.maya_apply_materials import maya_apply_materials
    from cgi_pipeline.capabilities.maya.asset.maya_build_asset_info import maya_build_asset_info
    from cgi_pipeline.capabilities.maya.asset.maya_build_mesh_from_abc import maya_build_mesh_from_abc
    from cgi_pipeline.capabilities.maya.asset.maya_check_textures import maya_check_textures
    from cgi_pipeline.capabilities.maya.asset.maya_compare_asset_in_scene import maya_compare_asset_in_scene
    from cgi_pipeline.capabilities.maya.asset.maya_conform_normals import maya_conform_normals
    from cgi_pipeline.capabilities.maya.asset.maya_export_abc import maya_export_abc
    from cgi_pipeline.capabilities.maya.asset.maya_import_abc import maya_import_abc
    from cgi_pipeline.capabilities.maya.asset.save_scene import save_scene
    from cgi_pipeline.capabilities.maya.asset.simplify_uvsets import simplify_uvsets

    assert check_uvsets.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_apply_materials.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_build_asset_info.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_build_mesh_from_abc.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_check_textures.execute({"parameters": {}})["status"] == "SUCCESS"
    assert maya_compare_asset_in_scene.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_conform_normals.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_export_abc.execute({"parameters": {}})["status"] == "ERROR"
    assert maya_import_abc.execute({"parameters": {}})["status"] == "ERROR"
    assert save_scene.execute({"parameters": {}})["status"] == "ERROR"
    assert simplify_uvsets.execute({"parameters": {}})["status"] == "ERROR"


def test_build_asset_info_derives_foreground_task_sandbox(monkeypatch, tmp_path):
    _install_maya()
    from cgi_pipeline.capabilities.maya.asset.maya_build_asset_info import maya_build_asset_info

    monkeypatch.setattr(maya_build_asset_info, "create_run_dir", lambda *_a, **_k: tmp_path)
    path = maya_build_asset_info._resolve_info_path(
        {"task_id": "api-test", "project": "default", "asset_name": "fixture"},
        {},
    )
    assert path == str(tmp_path / ".info" / "asset_info.json")


def test_build_mesh_filter_omission_uses_valid_full_build_default():
    spec = get_capability("maya.asset.maya_build_mesh_from_abc")

    params = validate_params(spec, {"abc_path": "fixture.abc"})

    assert params["mesh_filter"] == []


def test_build_mesh_parent_group_is_stable_across_duplicate_source_roots(monkeypatch):
    _install_maya()
    from cgi_pipeline.capabilities.maya.asset.maya_build_mesh_from_abc import maya_build_mesh_from_abc

    class HierarchyCmds:
        def __init__(self):
            self.nodes = set()

        def objExists(self, node):
            return node in self.nodes

        def group(self, *, empty, name, parent=""):
            assert empty is True
            path = f"{parent}|{name}" if parent else f"|{name}"
            self.nodes.add(path)
            return path

        def ls(self, node, *, long):
            assert long is True
            if node.startswith("|"):
                return [node] if node in self.nodes else []
            return sorted(
                (path for path in self.nodes if path.rsplit("|", 1)[-1] == node),
                reverse=True,
            )

    cmds = HierarchyCmds()
    monkeypatch.setattr(maya_build_mesh_from_abc, "cmds", cmds)

    body = maya_build_mesh_from_abc._ensure_hierarchy(
        ["Group", "cache", "body_grp"], root_group="cache"
    )
    hair = maya_build_mesh_from_abc._ensure_hierarchy(
        ["Group", "cache", "hair_grp"], root_group="cache"
    )

    assert body == "|cache|body_grp"
    assert hair == "|cache|hair_grp"
    assert not any("|cache|Group|cache" in path for path in cmds.nodes)


def test_texture_check_writes_structured_report(monkeypatch, tmp_path):
    cmds = _Cmds()
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    from cgi_pipeline.capabilities.maya.asset.maya_check_textures import maya_check_textures

    monkeypatch.setattr(maya_check_textures, "create_run_dir", lambda *_a, **_k: tmp_path)
    result = maya_check_textures.execute({"task_id": "api-test", "parameters": {}})
    assert result["status"] == "SUCCESS"
    assert result["output"]["result"] == {"total": 0, "missing": 0, "textures": []}
    assert result["output"]["report_path"] == str(tmp_path / ".info" / "texture_report.md")
    assert (tmp_path / ".info" / "texture_report.md").is_file()
