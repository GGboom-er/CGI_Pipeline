"""maya_sync_rig_incremental 的纯 Python 契约层。

这个模块不能导入 maya.cmds，方便在普通 Python 测试里验证输入、
compare_result 和动作统计契约。
"""

from dataclasses import dataclass
import json
import os
from typing import Any


SKILL_ID = "maya_sync_rig_incremental"
VALID_GROUP_ACTIONS = ("IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED")


class SyncContractError(ValueError):
    """输入或 compare_result 不满足 sync 执行契约。"""


@dataclass(frozen=True)
class SyncInputs:
    compare_result_path: str
    compare_result_data: dict
    source_abc: str
    source_info: str
    cache_group: str
    rig_path: str
    project: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_text(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(item).strip() for item in value if str(item).strip())
    return _text(value)


def parse_sync_inputs(payload: dict) -> SyncInputs:
    params = payload.get("parameters") or {}
    if not isinstance(params, dict):
        params = {}
    compare_result = params.get("compare_result")
    compare_result_data = compare_result if isinstance(compare_result, dict) else {}
    return SyncInputs(
        compare_result_path="" if compare_result_data else _text(compare_result),
        compare_result_data=compare_result_data,
        source_abc=_text(params.get("source_abc") or params.get("abc_path")),
        source_info=_text(params.get("source_info") or params.get("tex_json")),
        cache_group=_candidate_text(params.get("cache_group")) or "cache",
        rig_path=_text(payload.get("source_path")),
        project=_text(payload.get("project") or params.get("project")),
    )


def validate_sync_inputs(inputs: SyncInputs) -> list[str]:
    errors = []
    if not inputs.compare_result_path and not inputs.compare_result_data:
        errors.append(
            "缺少必填参数: compare_result。请先用 maya_compare_asset_in_scene 或 pipeline_compare_asset 生成对比结果。"
        )
    if not inputs.source_abc and not inputs.source_info:
        errors.append("缺少必填参数: source_abc 或 source_info。拼装推荐使用 source_abc。")
    if not inputs.rig_path:
        errors.append("缺少必填参数: source_path（target 侧 rig 场景）")
    if not inputs.cache_group:
        errors.append("缺少必填参数: cache_group")
    return errors


def validate_input_files(inputs: SyncInputs) -> list[str]:
    errors = []
    required_paths = []
    if inputs.compare_result_path:
        required_paths.append(("compare_result", inputs.compare_result_path))
    if inputs.source_abc:
        required_paths.append(("source_abc", inputs.source_abc))
    elif inputs.source_info:
        required_paths.append(("source_info", inputs.source_info))

    for label, path in required_paths:
        if path and not os.path.isfile(path):
            errors.append(f"{label} 文件不存在: {path}")
    return errors


def _list_of_strings(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncContractError(f"{field_name} 必须是 list")
    bad = [item for item in value if not isinstance(item, str)]
    if bad:
        raise SyncContractError(f"{field_name} 只能包含字符串")
    return value


def validate_compare_result_payload(data: dict) -> tuple[dict, dict]:
    if not isinstance(data, dict):
        raise SyncContractError("compare_result 顶层必须是 dict")
    if data.get("schema_version") != "compare_result.v1":
        raise SyncContractError(f"不支持的 compare_result schema: {data.get('schema_version')}")

    report = data.get("compare")
    if not isinstance(report, dict):
        raise SyncContractError("compare_result 缺少 compare 字典")

    groups = report.get("pairing_groups")
    if not isinstance(groups, list):
        raise SyncContractError("compare_result.compare.pairing_groups 必须是 list")
    target_only = _list_of_strings(
        report.get("target_only_dags", []),
        "compare_result.compare.target_only_dags",
    )
    report["target_only_dags"] = target_only

    for idx, group in enumerate(groups):
        if not isinstance(group, dict):
            raise SyncContractError(f"pairing_groups[{idx}] 必须是 dict")
        group_id = group.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise SyncContractError(f"pairing_groups[{idx}].group_id 缺失或类型错误")
        action = group.get("action")
        if action not in VALID_GROUP_ACTIONS:
            raise SyncContractError(
                f"pairing_groups[{idx}].action={action!r} 不在允许集合 {list(VALID_GROUP_ACTIONS)}"
            )
        group["abc_dags"] = _list_of_strings(
            group.get("abc_dags", []),
            f"pairing_groups[{idx}].abc_dags",
        )
        group["rig_dags"] = _list_of_strings(
            group.get("rig_dags", []),
            f"pairing_groups[{idx}].rig_dags",
        )
        if action in ("IDENTICAL", "ORIG_INJECT", "PAIRED") and not group["rig_dags"]:
            raise SyncContractError(f"pairing_groups[{idx}] action={action} 但 rig_dags 为空")
        if action in ("IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED") and not group["abc_dags"]:
            raise SyncContractError(f"pairing_groups[{idx}] action={action} 但 abc_dags 为空")
        if action == "UNPAIRED" and group["rig_dags"]:
            raise SyncContractError(f"pairing_groups[{idx}] action=UNPAIRED 但 rig_dags 非空")

    legacy_source_info = data.get("source_info") or {"meshes": {}, "textures": {}, "source_file": ""}
    if not isinstance(legacy_source_info, dict):
        raise SyncContractError("compare_result.source_info 类型错误")
    return report, legacy_source_info


def load_compare_result_file(compare_result_path: str) -> tuple[dict, dict, dict]:
    with open(compare_result_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    report, legacy_source_info = validate_compare_result_payload(data)
    return data, report, legacy_source_info


def load_compare_result_value(compare_result: Any) -> tuple[dict, dict, dict]:
    if isinstance(compare_result, dict):
        report, legacy_source_info = validate_compare_result_payload(compare_result)
        return compare_result, report, legacy_source_info
    return load_compare_result_file(_text(compare_result))


def summarize_sync_actions(report: dict) -> dict[str, int]:
    groups = report.get("pairing_groups") or []
    counts = {action: 0 for action in VALID_GROUP_ACTIONS}
    for group in groups:
        action = group.get("action")
        if action in counts:
            counts[action] += 1
    counts["target_only"] = len(report.get("target_only_dags") or [])
    counts["groups"] = len(groups)
    return counts


def format_action_summary(counts: dict[str, int]) -> str:
    return (
        f"IDENTICAL: {counts.get('IDENTICAL', 0)} | "
        f"ORIG_INJECT: {counts.get('ORIG_INJECT', 0)} | "
        f"PAIRED: {counts.get('PAIRED', 0)} | "
        f"UNPAIRED: {counts.get('UNPAIRED', 0)} | "
        f"target_only: {counts.get('target_only', 0)}"
    )


def _action_label(action: str) -> str:
    return action


def _short_dag(dag: str) -> str:
    text = str(dag or "")
    parts = text.strip("|").split("|")
    return parts[-1] if parts else text


def _group_item(group: dict) -> dict:
    abc_dags = group.get("abc_dags") or []
    rig_dags = group.get("rig_dags") or []
    return {
        "group": group.get("group_id", ""),
        "source": ", ".join(_short_dag(x) for x in abc_dags[:3]),
        "target": ", ".join(_short_dag(x) for x in rig_dags[:3]),
        "layer": group.get("layer_name", ""),
    }


def build_sync_report_sections(report: dict, counts: dict[str, int],
                               compare_result_path: str = "",
                               source_abc: str = "",
                               source_info: str = "",
                               cache_group: str = "") -> list[dict]:
    """把 sync 执行指令转成统一报告可渲染的结构化折叠段。"""
    overview = []
    for action in VALID_GROUP_ACTIONS:
        overview.append({"action": _action_label(action), "count": counts.get(action, 0)})
    overview.append({"action": _action_label("target_only"), "count": counts.get("target_only", 0)})

    return [
        {
            "title": "ACTION_SUMMARY",
            "summary": format_action_summary(counts),
            "items": overview,
        },
    ]
