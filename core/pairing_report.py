"""配对分类报告 — 对比结果的用户视角聚合层。

职责：
  1. 接收 asset_info_schema.compare() 的结果
  2. 以 pairing_groups（sync 实际执行的 4 类组）为主视角呈现
  3. 保留算法层 7 个 actionability 标签分桶（buckets）用于审计
  4. 保留 outcome 4 去向（identical/matched_different/only_source/only_target）用于快速汇总
  5. 输出机器可读 JSON + 人类可读 MD

主视角：sync 执行组（4 类）
  - IDENTICAL    几何全等，sync 搬运（不建 layer，直接清单）
  - ORIG_INJECT  点数一致 + 小位移，sync 搬运 + 注坐标
  - PAIRED       空间配对连通分量（任意 M→N），sync 新建几何 + 投射权重
  - UNPAIRED     abc 独有，sync 新建 + Chamfer 借权重（进 _source_only 大 layer）
  - target_only  rig 独有（不进 pairing_groups），原位保留
"""

import json
import os
import datetime
from typing import Dict, List, Any, Optional


# 算法层 7 标签（审计用）
_BUCKET_META = [
    ("IDENTICAL",   "几何完全一致，沿用原绑定",          "paired"),
    ("ORIG_INJECT", "偷梁换柱：注入新坐标/UV，不传权重",  "paired"),
    ("MODIFIED",    "改过的物体：从源 rig 定向投射权重/BS", "paired"),
    ("MERGE",       "多源合一：tex 在多个 rig 上有重合",     "paired"),
    ("SPLIT",       "一源拆多：多个 tex 指向同一 rig",        "paired"),
    ("NEW",         "资产独有：SuperMesh 全局采样",          "only_a"),
    ("DELETE",      "绑定独有：最终场景中删除",               "only_b"),
]


# 下游视角：source 侧 mesh 的 4 种事实去向
_OUTCOME_META = [
    ("identical",         "source 在 target 中有几何等同对应（可直接复用）",
                          ["IDENTICAL", "ORIG_INJECT"]),
    ("matched_different", "source 在 target 中找到配对，但几何不完全一致",
                          ["MODIFIED", "MERGE", "SPLIT"]),
    ("only_source",       "仅 source 存在，target 中无对应",
                          ["NEW"]),
    ("only_target",       "仅 target 存在，source 中无对应",
                          ["DELETE"]),
]


# sync 执行组 4 类 meta
_GROUP_ACTION_META = [
    ("IDENTICAL",   "几何全等，sync 搬运（不建 layer）"),
    ("ORIG_INJECT", "点数一致 + 小位移，sync 搬运 + 注坐标"),
    ("PAIRED",      "空间配对连通分量（M→N），sync 新建 + 投射权重"),
    ("UNPAIRED",    "abc 独有，sync 新建 + Chamfer 借权重（_source_only）"),
]


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def generate(compare_result: Dict[str, Any], profile: Dict[str, Any],
             output_dir: Optional[str] = None,
             tag: str = "pairing") -> Dict[str, Any]:
    """生成按 pairing_groups 主视角 + actionability 附视角的配对报告。"""
    paired = compare_result.get("paired", [])
    only_a = compare_result.get("only_a", [])
    only_b = compare_result.get("only_b", [])
    pairing_groups = compare_result.get("pairing_groups", [])
    target_only_dags = compare_result.get("target_only_dags", [])

    # ── 1. sync 执行组主视角 ──
    groups_by_action: Dict[str, List[Dict[str, Any]]] = {
        k: [] for k, _ in _GROUP_ACTION_META
    }
    for g in pairing_groups:
        groups_by_action.setdefault(g["action"], []).append(g)
    group_summary = {k: len(groups_by_action.get(k, [])) for k, _ in _GROUP_ACTION_META}
    group_summary["target_only"] = len(target_only_dags)

    # ── 2. 算法 7 标签分桶（审计）──
    buckets: Dict[str, List[Dict[str, Any]]] = {label: [] for label, _, _ in _BUCKET_META}
    pairs_out = []
    for detail in paired:
        entry = {
            "dag_a": detail.get("dag_a"),
            "dag_b": detail.get("dag_b"),
            "name_a": detail.get("name_a", ""),
            "name_b": detail.get("name_b", ""),
            "vtx_a": detail.get("vtx_a", 0),
            "vtx_b": detail.get("vtx_b", 0),
            "actionability": detail.get("actionability", ""),
            "step": detail.get("step"),
            "match_pct_loose": detail.get("match_pct_loose", ""),
            "match_pct_exact": detail.get("match_pct_exact", ""),
            "max_offset": detail.get("max_offset", 0.0),
            "min_offset": detail.get("min_offset", 0.0),
        }
        label = entry["actionability"]
        if label in buckets:
            buckets[label].append(entry)
        pairs_out.append(entry)

    for e in only_a:
        buckets["NEW"].append({
            "dag": e.get("dag"), "name": e.get("name", ""), "vtx": e.get("vtx", 0),
        })
    for e in only_b:
        buckets["DELETE"].append({
            "dag": e.get("dag"), "name": e.get("name", ""), "vtx": e.get("vtx", 0),
        })

    summary = {label: len(buckets[label]) for label, _, _ in _BUCKET_META}
    summary["total_paired"] = len(paired)
    summary["total_only_a"] = len(only_a)
    summary["total_only_b"] = len(only_b)
    summary["total_issues"] = compare_result.get("total_issues", 0)

    # ── 3. outcome 4 去向 ──
    outcomes: Dict[str, List[Dict[str, Any]]] = {key: [] for key, _, _ in _OUTCOME_META}
    for key, _, labels in _OUTCOME_META:
        for label in labels:
            outcomes[key].extend(buckets.get(label, []))
    outcome_summary = {key: len(outcomes[key]) for key, _, _ in _OUTCOME_META}

    # ── 4. step 分布 ──
    step_dist = {}
    for p in paired:
        s = p.get("step")
        step_dist[f"step_{s}"] = step_dist.get(f"step_{s}", 0) + 1

    cls_cfg = profile.get("classification", {})

    report = {
        "generated_at": datetime.datetime.now().isoformat(),
        "profile": {
            "precision_exact": cls_cfg.get("precision_exact", 0.0001),
            "precision_loose": cls_cfg.get("precision_loose", 0.005),
        },
        "group_summary": group_summary,
        "pairing_groups": pairing_groups,
        "target_only_dags": target_only_dags,
        "summary": summary,
        "step_distribution": step_dist,
        "outcome_summary": outcome_summary,
        "outcomes": outcomes,
        "buckets": buckets,
        "pairs": pairs_out,
        "only_a": only_a,
        "only_b": only_b,
        "merge_groups": compare_result.get("merge_groups", []),
        "split_groups": compare_result.get("split_groups", []),
    }

    json_path = None
    md_path = None
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        json_path = os.path.join(output_dir, f"{tag}_classification.json")
        md_path = os.path.join(output_dir, f"{tag}_report.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_render_markdown(report,
                                     compare_result.get("label_a", "A"),
                                     compare_result.get("label_b", "B")))

    report["json_path"] = json_path
    report["md_path"] = md_path
    return report


# ═══════════════════════════════════════════════════════════════
# Markdown 渲染
# ═══════════════════════════════════════════════════════════════

def _render_markdown(report: Dict[str, Any], label_a: str = "A", label_b: str = "B") -> str:
    s = report["summary"]
    prof = report["profile"]
    step_dist = report.get("step_distribution", {})
    outcomes = report.get("outcomes", {})
    outcome_summary = report.get("outcome_summary", {})
    group_summary = report.get("group_summary", {})
    pairing_groups = report.get("pairing_groups", [])
    target_only_dags = report.get("target_only_dags", [])

    lines = []
    lines.append(f"# 资产对比清单（source={label_a}  target={label_b}）")
    lines.append("")
    lines.append(f"生成时间: {report['generated_at']}")
    lines.append("")
    lines.append(f"精度阈值: exact {prof['precision_exact']} cm / loose {prof['precision_loose']} cm")
    lines.append("")

    # ── 主视角：sync 执行组概览 ──
    lines.append("## sync 执行组概览")
    lines.append("")
    lines.append("| 组类型 | 数量 | 说明 |")
    lines.append("|--------|------|------|")
    for k, desc in _GROUP_ACTION_META:
        lines.append(f"| **{k}** | {group_summary.get(k, 0)} | {desc} |")
    lines.append(f"| `target_only` | {group_summary.get('target_only', 0)} | rig 独有，原位保留 |")
    lines.append("")

    # IDENTICAL 清单（绑定师无需审查）
    id_groups = [g for g in pairing_groups if g["action"] == "IDENTICAL"]
    if id_groups:
        lines.append(f"### IDENTICAL — {len(id_groups)} 组（绑定师无需审查）")
        lines.append("")
        lines.append(f"| abc ({label_a}) | rig ({label_b}) |")
        lines.append("|------|------|")
        for g in id_groups:
            a = g["abc_dags"][0] if g["abc_dags"] else ""
            r = g["rig_dags"][0] if g["rig_dags"] else ""
            lines.append(f"| {a.split('|')[-1]} | {r.split('|')[-1]} |")
        lines.append("")

    # ORIG_INJECT 清单（粗检即可）
    oi_groups = [g for g in pairing_groups if g["action"] == "ORIG_INJECT"]
    if oi_groups:
        lines.append(f"### ORIG_INJECT — {len(oi_groups)} 组（位置微调，粗检即可）")
        lines.append("")
        lines.append(f"| layer | abc ({label_a}) | rig ({label_b}) | reason |")
        lines.append("|-------|------|------|--------|")
        for g in oi_groups:
            a = g["abc_dags"][0] if g["abc_dags"] else ""
            r = g["rig_dags"][0] if g["rig_dags"] else ""
            lines.append(f"| `{g.get('layer_name','')}` | "
                         f"{a.split('|')[-1]} | {r.split('|')[-1]} | {g.get('reason','')} |")
        lines.append("")

    # PAIRED 分组细节（主要审查对象）
    paired_groups = [g for g in pairing_groups if g["action"] == "PAIRED"]
    if paired_groups:
        lines.append(f"### PAIRED — {len(paired_groups)} 组（重点审查）")
        lines.append("")
        for g in paired_groups:
            lines.append(f"#### Layer `{g.get('layer_name','')}`  ({g['group_id']})")
            lines.append("")
            lines.append(f"- reason: {g.get('reason','')}")
            lines.append(f"- abc ({label_a}): {len(g['abc_dags'])} 个")
            for a in g["abc_dags"]:
                lines.append(f"  - `{a}`")
            lines.append(f"- rig ({label_b}): {len(g['rig_dags'])} 个（将进 layer 且设 Reference）")
            for r in g["rig_dags"]:
                lines.append(f"  - `{r}`")
            lines.append("")

    # UNPAIRED 清单
    up_groups = [g for g in pairing_groups if g["action"] == "UNPAIRED"]
    if up_groups:
        lines.append(f"### UNPAIRED — {len(up_groups)} 组（新资产，无 rig 源）")
        lines.append("")
        lines.append("统一进 `_source_only` 大 layer；sync 通过 Chamfer 自动配对附近 rig mesh 借权重；")
        lines.append("超阈值将空权重，需人工绑定。")
        lines.append("")
        lines.append(f"| abc ({label_a}) |")
        lines.append("|------|")
        for g in up_groups:
            for a in g["abc_dags"]:
                lines.append(f"| `{a}` |")
        lines.append("")

    # target_only 清单
    if target_only_dags:
        lines.append(f"### target_only — {len(target_only_dags)} 个 rig 独有")
        lines.append("")
        lines.append("原位保留，统一进 `_target_only` 大 layer（设 Reference 可见不可选）。")
        lines.append("")
        lines.append(f"| rig ({label_b}) |")
        lines.append("|------|")
        for dag in target_only_dags:
            lines.append(f"| `{dag}` |")
        lines.append("")

    # ── 附 A：outcome 4 去向（快速汇总）──
    lines.append("---")
    lines.append("")
    lines.append(f"## 附 A：source ({label_a}) 每个 mesh 相对 target ({label_b}) 的归属")
    lines.append("")
    lines.append("| 去向 | 数量 | 含义 |")
    lines.append("|------|------|------|")
    for key, desc, _ in _OUTCOME_META:
        lines.append(f"| **{key}** | {outcome_summary.get(key, 0)} | {desc} |")
    lines.append("")

    # ── 附 B：算法 7 标签分桶 ──
    lines.append("## 附 B：配对算法内部分桶（审计用）")
    lines.append("")
    lines.append("| 标签 | 数量 | 含义 |")
    lines.append("|------|------|------|")
    for label, desc, _ in _BUCKET_META:
        lines.append(f"| {label} | {s.get(label, 0)} | {desc} |")
    lines.append("")
    if step_dist:
        lines.append("配对阶段分布（tex 侧按哪一步被配上）：")
        lines.append("")
        for k in sorted(step_dist.keys()):
            lines.append(f"- {k}: {step_dist[k]}")
        lines.append("")

    # MERGE / SPLIT 关系细节（审计）
    mg = report.get("merge_groups", [])
    if mg:
        lines.append(f"## 附 C：MERGE 关系（{len(mg)} 组）")
        lines.append("")
        for g in mg:
            lines.append(f"- `{g.get('target_a')}` ← 源: {g.get('sources_b', [])}")
        lines.append("")

    sg = report.get("split_groups", [])
    if sg:
        lines.append(f"## 附 D：SPLIT 关系（{len(sg)} 组）")
        lines.append("")
        for g in sg:
            lines.append(f"- `{g.get('source_b')}` → 目标: {g.get('targets_a', [])}")
        lines.append("")

    return "\n".join(lines)
