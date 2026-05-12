# -*- coding: utf-8 -*-
"""resolve_asset_files 纯 Python 契约测试。"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.asset_resolver import AssetResolver
from skills.resolve_asset_files.resolve_asset_files import _find_latest_by_stage, execute


def _check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
        return True
    print(f"  [FAIL] {name}: {detail}")
    return False


def test_explicit_paths():
    print("\n=== Test: 显式路径透传 ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "ysj_chr_ciweiguai_tex_texMaster_v001.blend"
        rig = root / "ysj_chr_ciweiguai_rig_rigMaster_v002.ma"
        src.write_text("blend", encoding="utf-8")
        rig.write_text("maya", encoding="utf-8")
        receipt = execute({
            "project": "ysj",
            "asset_name": "ciweiguai",
            "source_path": str(src),
            "extra_params": {"rig_path": str(rig)},
            "parameters": {},
        })
        result = receipt.get("outputs", {}).get("result", {})
        ok = True
        ok &= _check("执行成功", receipt.get("status") == "SUCCESS", receipt)
        ok &= _check("source_path 透传", result.get("source_path") == str(src), result)
        ok &= _check("rig_path 透传", result.get("rig_path") == str(rig), result)
        ok &= _check("source 版本解析", result.get("source_version") == 1, result)
        ok &= _check("rig 版本解析", result.get("rig_version") == 2, result)
        ok &= _check("结构化 result 输出", set(receipt.get("outputs", {}).keys()) == {"result"}, receipt)
        return ok


def test_stage_latest_helper():
    print("\n=== Test: 阶段最新版本解析 ===")
    with tempfile.TemporaryDirectory() as tmp:
        server = Path(tmp) / "Project" / "ysj"
        stage_dir = server / "pub" / "assets" / "chr" / "ciweiguai" / "tex" / "texMaster"
        stage_dir.mkdir(parents=True)
        (stage_dir / "ysj_chr_ciweiguai_tex_texMaster_v001.blend").write_text("1", encoding="utf-8")
        (stage_dir / "ysj_chr_ciweiguai_tex_texMaster_v003.blend").write_text("3", encoding="utf-8")
        (stage_dir / "ysj_chr_ciweiguai_tex_texMaster_v999.ma").write_text("wrong ext", encoding="utf-8")
        resolver = AssetResolver({
            "project_name": "ysj",
            "server_root": str(server),
            "path_roots": {"assets": "pub/assets"},
            "path_pattern_asset": "{category}/{asset}/{stage}/{task}",
            "stages": {"tex": {"primary_task": "texMaster"}},
        })
        latest, searched = _find_latest_by_stage(
            resolver, "chr", "ciweiguai", "tex", "", (".blend",)
        )
        ok = True
        ok &= _check("找到最新 blend", latest and latest.name.endswith("_v003.blend"), latest)
        ok &= _check("记录搜索路径", len(searched) == 1 and searched[0]["exists"], searched)
        return ok


if __name__ == "__main__":
    print("=== resolve_asset_files unit tests ===")
    all_ok = True
    all_ok &= test_explicit_paths()
    all_ok &= test_stage_latest_helper()
    if not all_ok:
        raise SystemExit(1)
    print("\n✅ all pass")
