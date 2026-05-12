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


if __name__ == "__main__":
    print("=== workflow template var tests ===")
    if not test_nested_outputs():
        raise SystemExit(1)
    print("\n✅ all pass")
