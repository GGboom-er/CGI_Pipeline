"""
pipeline_compare_asset 独立对比结果契约测试。

验证 compare skill 会落标准 compare_result.json；结果文件只包含输入记录和
compare 结果，不嵌入 source_info。
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.asset_info_schema import make_empty_info, make_mesh_entry
from skills.pipeline_compare_asset.pipeline_compare_asset import execute


def _ok(cond, msg):
    assert cond, msg
    print(f"  [PASS] {msg}")


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def test_compare_result_output_contract():
    print("\n=== Test: compare_result 输出契约 ===")
    with tempfile.TemporaryDirectory() as tmp:
        pts = np.random.rand(8, 3)
        source = make_empty_info()
        source["source_file"] = "source.abc"
        source["meshes"]["cache|body|bodyShape"] = make_mesh_entry(8, pts.flatten().tolist())

        target = make_empty_info()
        target["source_file"] = "target.ma"
        target["meshes"]["cache|body|bodyShape"] = make_mesh_entry(8, pts.flatten().tolist())

        source_path = os.path.join(tmp, "source_info.json")
        target_path = os.path.join(tmp, "target_info.json")
        output_path = os.path.join(tmp, "source_vs_target_compare_result.json")
        _write_json(source_path, source)
        _write_json(target_path, target)

        receipt = execute({
            "parameters": {
                "input_source": source_path,
                "input_target": target_path,
                "output_path": output_path,
                "label_source": "tex",
                "label_target": "rig",
            }
        })

        _ok(receipt["status"] == "SUCCESS", "compare skill 成功")
        _ok(receipt["outputs"].get("output_path") == output_path, "output_path 指向 compare_result")
        _ok(os.path.isfile(output_path), "compare_result 文件存在")

        data = json.load(open(output_path, encoding="utf-8"))
        _ok(data.get("schema_version") == "compare_result.v1", "schema_version 正确")
        _ok(data.get("inputs", {}).get("input_source") == source_path, "记录 input_source")
        _ok("source_info" not in data, "不嵌入 source_info")
        report = data.get("compare", {})
        _ok("pairing_groups" in report, "包含 pairing_groups")
        _ok("target_only_dags" in report, "包含 target_only_dags")
        _ok(set(receipt["outputs"].keys()) == {"output_path"}, "receipt.outputs 只包含 output_path")
        sections = receipt.get("report_sections") or []
        section_titles = [section.get("title") for section in sections]
        overview = next((section for section in sections if section.get("title") == "对比概览"), {})
        _ok(bool(sections), "receipt 包含 report_sections")
        _ok("完成配对" in str(overview.get("summary", "")), "对比概览使用中文配置标签")
        for title in ("通过配对", "几何差异", "源侧独有", "目标独有"):
            _ok(title in section_titles, f"包含结构化章节: {title}")


def test_compare_result_defaults_to_info_dir():
    print("\n=== Test: compare_result 默认写入 info_dir ===")
    with tempfile.TemporaryDirectory() as tmp:
        info_dir = os.path.join(tmp, ".info")
        os.makedirs(info_dir, exist_ok=True)

        pts = np.random.rand(4, 3)
        source = make_empty_info()
        source["meshes"]["cache|body|bodyShape"] = make_mesh_entry(4, pts.flatten().tolist())
        target = make_empty_info()
        target["meshes"]["cache|body|bodyShape"] = make_mesh_entry(4, pts.flatten().tolist())

        source_path = os.path.join(tmp, "source_info.json")
        target_path = os.path.join(tmp, "target_info.json")
        _write_json(source_path, source)
        _write_json(target_path, target)

        receipt = execute({
            "parameters": {
                "input_source": source_path,
                "input_target": target_path,
                "info_dir": info_dir,
            }
        })

        expected = os.path.join(info_dir, "source_info_vs_target_info_compare_result.json")
        _ok(receipt["status"] == "SUCCESS", "compare skill 成功")
        _ok(receipt["outputs"].get("output_path") == expected, "默认 output_path 位于 info_dir")
        _ok(os.path.isfile(expected), "默认 compare_result 文件存在")


def test_compare_result_requires_sandbox_for_default_path():
    print("\n=== Test: compare_result 无沙盒时拒绝默认回退 ===")
    with tempfile.TemporaryDirectory() as tmp:
        pts = np.random.rand(4, 3)
        source = make_empty_info()
        source["meshes"]["cache|body|bodyShape"] = make_mesh_entry(4, pts.flatten().tolist())
        target = make_empty_info()
        target["meshes"]["cache|body|bodyShape"] = make_mesh_entry(4, pts.flatten().tolist())

        source_path = os.path.join(tmp, "source_info.json")
        target_path = os.path.join(tmp, "target_info.json")
        _write_json(source_path, source)
        _write_json(target_path, target)

        receipt = execute({
            "parameters": {
                "input_source": source_path,
                "input_target": target_path,
            }
        })

        old_default = os.path.join(tmp, "source_info_vs_target_info_compare_result.json")
        _ok(receipt["status"] == "ERROR", "缺少 output_path/info_dir 时返回 ERROR")
        _ok(not os.path.exists(old_default), "不会回退写入输入文件同目录")


if __name__ == "__main__":
    test_compare_result_output_contract()
    test_compare_result_defaults_to_info_dir()
    test_compare_result_requires_sandbox_for_default_path()
    print("\nALL COMPARE RESULT CONTRACT TESTS PASSED!")
