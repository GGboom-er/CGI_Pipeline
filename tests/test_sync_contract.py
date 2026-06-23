"""
maya_sync_rig_incremental 的纯契约测试。

不启动 Maya，只验证输入解析、compare_result.v1 结构、动作统计和失败语义。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skills.maya_sync_rig_incremental.sync_contract import (
    SyncContractError,
    build_sync_output_details,
    format_action_summary,
    load_compare_result_file,
    load_compare_result_value,
    parse_sync_inputs,
    summarize_sync_actions,
    validate_input_files,
    validate_sync_inputs,
)


def _ok(cond, msg):
    assert cond, msg
    print(f"  [PASS] {msg}")


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def _valid_compare_result():
    return {
        "schema_version": "compare_result.v1",
        "inputs": {
            "input_source": "source.abc",
            "input_target": "target.ma",
            "label_source": "tex",
            "label_target": "rig",
        },
        "compare": {
            "pairing_groups": [
                {
                    "group_id": "g0001",
                    "action": "IDENTICAL",
                    "abc_dags": ["cache|body|bodyShape"],
                    "rig_dags": ["|Group|Geometry|cache|body|bodyShape"],
                    "layer_name": "body",
                    "reason": "same",
                },
                {
                    "group_id": "g0002",
                    "action": "UNPAIRED",
                    "abc_dags": ["cache|new|newShape"],
                    "rig_dags": [],
                    "layer_name": "",
                    "reason": "source only",
                },
            ],
            "target_only_dags": ["|Group|Geometry|cache|old|oldShape"],
        },
    }


def test_parse_and_validate_inputs():
    print("\n=== Test 1: 输入解析与别名兼容 ===")
    payload = {
        "source_path": "target.ma",
        "project": "ysj",
        "parameters": {
            "compare_result": "compare.json",
            "abc_path": "source.abc",
            "cache_group": "|Group|Geometry|cache",
        },
    }
    inputs = parse_sync_inputs(payload)
    _ok(inputs.source_abc == "source.abc", "兼容老 abc_path")
    _ok(inputs.source_info == "", "未传 source_info 时为空")
    _ok(inputs.cache_group == "|Group|Geometry|cache", "cache_group 透传")
    _ok(validate_sync_inputs(inputs) == [], "必填参数齐全")

    bad = parse_sync_inputs({"parameters": {}})
    errors = validate_sync_inputs(bad)
    _ok(any("compare_result" in e for e in errors), "缺 compare_result 会报错")
    _ok(any("source_abc" in e for e in errors), "缺 source 数据会报错")
    _ok(any("source_path" in e for e in errors), "缺 target rig source_path 会报错")


def test_input_file_validation():
    print("\n=== Test 2: 输入文件存在性 ===")
    with tempfile.TemporaryDirectory() as tmp:
        compare_path = os.path.join(tmp, "compare_result.json")
        abc_path = os.path.join(tmp, "source.abc")
        open(compare_path, "w", encoding="utf-8").write("{}")
        open(abc_path, "w", encoding="utf-8").write("")

        inputs = parse_sync_inputs({
            "source_path": "target.ma",
            "parameters": {
                "compare_result": compare_path,
                "source_abc": abc_path,
            },
        })
        _ok(validate_input_files(inputs) == [], "存在的 compare/source 文件通过")

        missing = parse_sync_inputs({
            "source_path": "target.ma",
            "parameters": {
                "compare_result": compare_path,
                "source_abc": os.path.join(tmp, "missing.abc"),
            },
        })
        _ok(any("source_abc 文件不存在" in e for e in validate_input_files(missing)),
            "缺 source_abc 会定位到文件存在性错误")


def test_compare_result_contract():
    print("\n=== Test 3: compare_result 契约 ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "compare_result.json")
        _write_json(path, _valid_compare_result())
        data, report, legacy_source = load_compare_result_file(path)
        _ok(data["schema_version"] == "compare_result.v1", "schema_version 正确")
        _ok(len(report["pairing_groups"]) == 2, "读取 pairing_groups")
        _ok(legacy_source["meshes"] == {}, "不嵌 source_info 时使用空 source_info 兼容结构")

        malformed = _valid_compare_result()
        malformed["compare"]["pairing_groups"][0]["action"] = "MODIFIED"
        bad_path = os.path.join(tmp, "bad_compare_result.json")
        _write_json(bad_path, malformed)
        try:
            load_compare_result_file(bad_path)
        except SyncContractError as exc:
            _ok("不在允许集合" in str(exc), "非法 action 被阻断")
        else:
            raise AssertionError("非法 action 未被阻断")

        data2, report2, _ = load_compare_result_value(_valid_compare_result())
        _ok(data2["schema_version"] == "compare_result.v1", "支持直接传 compare_result dict")
        _ok(len(report2["pairing_groups"]) == 2, "dict 输入读取 pairing_groups")


def test_action_summary():
    print("\n=== Test 4: 动作统计 ===")
    _, report, _ = load_compare_result_file_from_dict(_valid_compare_result())
    counts = summarize_sync_actions(report)
    _ok(counts["IDENTICAL"] == 1, "IDENTICAL 统计正确")
    _ok(counts["UNPAIRED"] == 1, "UNPAIRED 统计正确")
    _ok(counts["target_only"] == 1, "target_only 统计正确")
    summary = format_action_summary(counts)
    _ok("IDENTICAL: 1" in summary and "target_only: 1" in summary, "摘要格式稳定")


def test_output_details_use_action_labels():
    print("\n=== Test 5: 同步 output 明细 ===")
    _, report, _ = load_compare_result_file_from_dict(_valid_compare_result())
    counts = summarize_sync_actions(report)
    details = build_sync_output_details(
        report,
        counts,
        compare_result_path="compare_result.json",
        source_abc="source.abc",
        cache_group="|Group|Geometry|cache",
    )
    _ok(details.get("action_summary") == format_action_summary(counts), "同步摘要写入 output.action_summary")
    actions = [item.get("action") for item in details.get("action_items", [])]
    _ok("IDENTICAL" in actions and "target_only" in actions, "同步概览使用 action 标签")
    _ok(details.get("compare_result_path") == "compare_result.json", "compare_result_path 写入 output")


def load_compare_result_file_from_dict(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "compare_result.json")
        _write_json(path, data)
        return load_compare_result_file(path)


if __name__ == "__main__":
    test_parse_and_validate_inputs()
    test_input_file_validation()
    test_compare_result_contract()
    test_action_summary()
    test_output_details_use_action_labels()
    print("\nALL SYNC CONTRACT TESTS PASSED!")
