# -*- coding: utf-8 -*-
"""材质贴图语义过滤测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.material_semantics import is_non_color_texture_path, should_use_as_color_texture


def _check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
        return True
    print(f"  [FAIL] {name}: {detail}")
    return False


if __name__ == "__main__":
    print("=== material semantics tests ===")
    ok = True
    ok &= _check(
        "AO 不作为 color 贴图",
        is_non_color_texture_path("X:/Project/ysj/sourceimages/chr/xycrowdbig/uv/uvMaster/xycrowdbig_AO_1003.png"),
    )
    ok &= _check(
        "normal 不作为 color 贴图",
        not should_use_as_color_texture("X:/Project/ysj/sourceimages/chr/xycrowdbig/uv/uvMaster/xycrowdbig_nor.1003.png"),
    )
    ok &= _check(
        "无语义材质图允许作为 color",
        should_use_as_color_texture("X:/Project/ysj/sourceimages/chr/body/skin_1001.png"),
    )
    ok &= _check(
        "albedo 允许作为 color",
        should_use_as_color_texture("X:/Project/ysj/sourceimages/chr/body/body_albedo_1001.png"),
    )
    if not ok:
        raise SystemExit(1)
    print("\nall pass")
