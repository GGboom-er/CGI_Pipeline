# -*- coding: utf-8 -*-
"""workflow 模板变量解析测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tasks import _resolve_template_vars


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


if __name__ == "__main__":
    print("=== workflow template var tests ===")
    if not test_nested_outputs():
        raise SystemExit(1)
    if not test_multiple_config_placeholders():
        raise SystemExit(1)
    print("\n✅ all pass")
