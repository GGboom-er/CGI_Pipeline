# -*- coding: utf-8 -*-
"""workflow 模板变量解析测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tasks import (
    _resolve_template_vars,
    _resolve_workflow_source_candidate,
    _select_segment_source_path,
)


def _check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
        return True
    print(f"  [FAIL] {name}: {detail}")
    return False


def test_nested_outputs():
    print("\n=== Test: outputs 嵌套 result 字段 ===")
    resolved = _resolve_template_vars(
        {
            "source_path": "{{outputs.resolve_files.result.source_path}}",
            "rig_stem": "{{outputs.resolve_files.result.rig_path | stem}}",
            "mixed": "rig={{outputs.resolve_files.result.rig_path | basename}}",
        },
        {
            "resolve_files": {
                "result": {
                    "source_path": "X:/Project/ysj/pub/assets/chr/a/tex/texMaster/a.blend",
                    "rig_path": "X:/Project/ysj/pub/assets/chr/a/rig/rigMaster/a.ma",
                }
            }
        },
        {},
        {},
    )
    ok = True
    ok &= _check("source_path 嵌套解析", resolved["source_path"].endswith("a.blend"), resolved)
    ok &= _check("stem 过滤器可用于嵌套输出", resolved["rig_stem"] == "a", resolved)
    ok &= _check("混合字符串可解析嵌套输出", resolved["mixed"] == "rig=a.ma", resolved)
    return ok


def test_multiple_config_placeholders():
    print("\n=== Test: 多个 config 占位符同字符串解析 ===")
    resolved = _resolve_template_vars(
        {
            "cache_group": "{{config.stages.rig.geom_roots.0}};{{config.stages.rig.geom_roots.1}}",
            "mixed": "roots={{config.stages.tex.geom_roots.0}};{{config.stages.tex.geom_roots.1}}",
        },
        {},
        {},
        {
            "stages": {
                "rig": {"geom_roots": ["|Group|Geometry|cache", "|*|geo"]},
                "tex": {"geom_roots": ["|Group|cache", "|*|geo"]},
            }
        },
    )
    ok = True
    ok &= _check("完整字符串含多个 config 占位符", resolved["cache_group"] == "|Group|Geometry|cache;|*|geo", resolved)
    ok &= _check("混合字符串含多个 config 占位符", resolved["mixed"] == "roots=|Group|cache;|*|geo", resolved)
    return ok


class _FakeResolver:
    def __init__(self, project_config):
        self.project_config = project_config

    def resolve_by_stage(self, category, asset_name, pipeline=None, ext_filter=None):
        return {
            "asset": asset_name,
            "stage": "uv",
            "task": "uvMaster",
            "version": "ysj_chr_xycrowdbig_uv_uvMaster_v001.blend",
            "path": "X:/Project/ysj/pub/assets/chr/xycrowdbig/uv/uvMaster/ysj_chr_xycrowdbig_uv_uvMaster_v001.blend",
            "valid": category == "chr" and pipeline == "model" and ext_filter == [".blend"],
        }


def test_workflow_source_resolution():
    print("\n=== Test: workflow source_resolution 资产名解析 ===")
    path, meta = _resolve_workflow_source_candidate(
        {
            "workflow_id": "blender_to_maya_full_build",
            "source_resolution": {
                "pipeline": "model",
                "ext_filter": [".blend"],
                "required": True,
            },
        },
        {},
        "xycrowdbig",
        {"category": "chr"},
        resolver_cls=_FakeResolver,
    )
    ok = True
    ok &= _check("解析出 blend 源文件", path.endswith(".blend"), path)
    ok &= _check("限定 model pipeline 与 blend 后缀", meta.get("valid") is True, meta)
    ok &= _check("记录解析阶段", meta.get("stage") == "uv", meta)
    return ok


def test_segment_source_path_selection():
    print("\n=== Test: workflow 分段 source_path 选择 ===")
    ok = True
    ok &= _check(
        "首段默认使用 workflow source_path",
        _select_segment_source_path(0, [{"skill_id": "blender_export_abc"}], "asset.blend", {}) == "asset.blend",
    )
    ok &= _check(
        "后续段不继承首段 DCC 源文件",
        _select_segment_source_path(1, [{"skill_id": "maya_build_mesh_from_abc"}], "asset.blend", {}) == "",
    )
    ok &= _check(
        "显式 source_path 优先",
        _select_segment_source_path(
            2,
            [{"skill_id": "maya_compare_asset_in_scene", "source_path": "rig.ma"}],
            "asset.blend",
            {},
        ) == "rig.ma",
    )
    return ok


if __name__ == "__main__":
    print("=== workflow template var tests ===")
    if not test_nested_outputs():
        raise SystemExit(1)
    if not test_multiple_config_placeholders():
        raise SystemExit(1)
    if not test_workflow_source_resolution():
        raise SystemExit(1)
    if not test_segment_source_path_selection():
        raise SystemExit(1)
    print("\n✅ all pass")
