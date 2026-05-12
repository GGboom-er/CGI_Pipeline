# core/asset_info_schema.py
# ── 资产信息统一格式 + 全局几何匹配引擎 ──
#
# 定义 mesh info JSON 的数据结构、校验、对比逻辑。
# Maya 和 Blender 各自用自己的 API 采集，但输出必须符合此格式。
#
# 匹配策略：纯几何匹配（全局 KDTree），不依赖路径/名称。
# 统一标尺：厘米（cm）、Y 轴向上、世界空间坐标。

import math
from typing import TypedDict, List, Dict, Optional, Literal, Any


# ═══════════════════════════════════════════
# compare() 返回结构契约（TypedDict，运行时零开销）
#
# 消费者：pipeline_compare_asset skill、maya_sync_rig_incremental、
# core/pairing_report。
#
# 主输出是 pairing_groups（连通分量列表）——sync 消费它按组执行。
# 其余字段（paired/only_a/only_b/merge_groups/split_groups/hierarchy/
# textures）保留用于报告渲染和审计。
# ═══════════════════════════════════════════

# compare 产出的顶层 action（贴在 pairing_group 上）
#   IDENTICAL    — Step 1/2 几何全等，1→1，sync 走搬运
#   ORIG_INJECT  — Step 1/2 点数一致+小位移，1→1，sync 走搬运+注坐标
#   PAIRED       — Step 3 空间配对，M→N 连通分量，sync 新建+投射权重
#   UNPAIRED     — abc 独有，sync 新建+Chamfer 借权重
GroupAction = Literal["IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED"]

# paired[i].actionability 的 Step 3 细分标签（仅用于审计/报告，sync 不消费）
#   MODIFIED — Step 3 一对一有变化
#   MERGE    — Step 3 多 rig 合到一个 abc（在 merge_groups 也有记录）
#   SPLIT    — Step 3 一个 rig 拆到多 abc（在 split_groups 也有记录）
Actionability = Literal[
    "IDENTICAL", "ORIG_INJECT",
    "MODIFIED", "MERGE", "SPLIT",
]


class PairDetail(TypedDict, total=False):
    """compare_result["paired"] 数组中每一项的形状。

    必有字段：dag_a / dag_b / name_a / name_b / vtx_a / vtx_b /
             actionability / step

    几何字段（IDENTICAL / ORIG_INJECT / MODIFIED 有；MERGE / SPLIT 只有命中率）：
      match_pct_exact / match_pct_loose / max_offset / min_offset /
      precision_used / total_vertices

    自比场景 (info_a is info_b) 下 _compute_pair_detail 有显式兜底：
    match_pct_exact == match_pct_loose == "100.0000%"，max_offset == 0.0。
    不会出现几何字段缺失导致下游 KeyError。
    """
    dag_a: str
    dag_b: str
    name_a: str
    name_b: str
    vtx_a: int
    vtx_b: int
    actionability: Actionability
    step: Optional[int]
    match_pct_exact: str
    match_pct_loose: str
    max_offset: float
    min_offset: float
    precision_used: str
    total_vertices: int


class OnlyEntry(TypedDict, total=False):
    """compare_result["only_a"] / ["only_b"] 数组中每一项。"""
    dag: str
    name: str
    vtx: int


class MergeGroup(TypedDict):
    """一个 tex mesh 由多个 rig mesh 合并而来。"""
    target_a: str
    sources_b: List[str]


class SplitGroup(TypedDict):
    """一个 rig mesh 被拆分到多个 tex mesh。"""
    source_b: str
    targets_a: List[str]


class PairingGroup(TypedDict):
    """compare_result["pairing_groups"] 中每一项。

    连通分量语义：abc_dags 和 rig_dags 里所有 mesh 是通过 Step 3 空间
    配对关系彼此连通的一整团。M→N 的资产重组也合到同一组。

    - IDENTICAL / ORIG_INJECT 组：abc_dags 和 rig_dags 各 1 个
    - PAIRED 组：可以是任意 M→N（M≥1, N≥1）
    - UNPAIRED 组：abc_dags 1 个，rig_dags 为空，layer_name 为空
                  （sync 会统一进 _source_only 大 layer）
    """
    group_id: str
    action: GroupAction
    abc_dags: List[str]
    rig_dags: List[str]
    layer_name: str
    reason: str


class HierarchyReport(TypedDict):
    matched: int
    only_a: List[str]
    only_b: List[str]


class CompareResult(TypedDict, total=False):
    """core.asset_info_schema.compare() 的完整返回契约。

    主输出（sync 消费）：
      pairing_groups    — 连通分量组列表
      target_only_dags  — rig 独有清单（不属于任何组）

    审计/报告字段（保留）：
      total_issues / label_a/b / source_a/b
      paired / only_a / only_b / merge_groups / split_groups
      hierarchy / textures
    """
    total_issues: int
    label_a: str
    label_b: str
    source_a: str
    source_b: str
    paired: List[PairDetail]
    only_a: List[OnlyEntry]
    only_b: List[OnlyEntry]
    hierarchy: HierarchyReport
    textures: Dict[str, Any]
    merge_groups: List[MergeGroup]
    split_groups: List[SplitGroup]
    pairing_groups: List[PairingGroup]
    target_only_dags: List[str]


# 每个 mesh 必须包含的字段
MESH_FIELDS = {
    "vertices": int,
    "vert_positions": list,
}

# 顶层必须包含的字段
TOP_FIELDS = {
    "source_file": str,
    "meshes": dict,
    "textures": dict,
}


def make_empty_info():
    """创建空的 asset_info 结构"""
    return {
        "source_file": "",
        "meshes": {},
        "textures": {},
    }


def make_mesh_entry(vertices, vert_positions, materials=None, **_extra):
    """创建单个 mesh 的标准数据条目。

    必填：vertices, vert_positions
    可选：materials（材质名列表）
    拓扑/UV 坐标/法线等完整几何数据从 ABC 获取，不存 JSON。
    """
    entry = {
        "vertices": vertices,
        "vert_positions": vert_positions,
    }
    if materials is not None:
        entry["materials"] = materials
    return entry


def validate(info):
    """校验 JSON 结构是否完整，返回 (ok, errors)"""
    errors = []
    if not isinstance(info, dict):
        return False, ["根节点不是 dict"]

    for field, ftype in TOP_FIELDS.items():
        if field not in info:
            if field == "source_file":
                continue
            errors.append(f"缺少顶层字段: {field}")
        elif not isinstance(info[field], ftype):
            if field == "textures" and isinstance(info[field], list):
                continue
            errors.append(f"顶层字段 {field} 类型错误: 期望 {ftype.__name__}, 实际 {type(info[field]).__name__}")

    meshes = info.get("meshes", {})
    for dag, entry in meshes.items():
        if not isinstance(entry, dict):
            errors.append(f"mesh {dag} 不是 dict")
            continue
        for field, ftype in MESH_FIELDS.items():
            if field not in entry:
                errors.append(f"mesh {dag} 缺少字段: {field}")

    return len(errors) == 0, errors


# ═══════════════════════════════════════════
# 对比逻辑
#
# 纯几何匹配：全局 KDTree，不依赖路径/名称
#
# 匹配精度（欧氏距离，cm）：
#   精确  < 0.0001cm  （浮点误差级）
#   宽松  < 0.005cm   （精度差异级）
# ═══════════════════════════════════════════

_PRECISION_EXACT = 0.0001   # 精确匹配阈值（默认值，可被 profile 覆盖）
_PRECISION_LOOSE = 0.005    # 宽松匹配阈值（默认值，可被 profile 覆盖）


def _resolve_profile(profile):
    """懒加载默认 profile，避免顶层导入循环。profile=None → 默认值。"""
    if profile is not None:
        return profile
    try:
        from core.config_loader import get_default_rig_sync_profile
        return get_default_rig_sync_profile()
    except Exception:
        # fallback：裸默认值（保证即使 config_loader 不可用也能工作）
        return {
            "pairing": {
                "enable_cpd": True,
                "cpd_point_diff_threshold": 0.8,
                "cpd_max_iterations": 30,
                "cpd_tolerance": 0.001,
                "chamfer_threshold": 5.0,
                "bbox_iou_min": 0.01,
                "name_bonus": 0.0,
                "kdtree_round_decimals": 4,
                "auto_approve_threshold": 0.95,
                "review_threshold": 0.80,
                "strict_mode": False,
            },
            "classification": {
                "precision_exact": _PRECISION_EXACT,
                "precision_loose": _PRECISION_LOOSE,
                "reorder_require_100pct": True,
                "partial_match_min_pct": 0.99,
            },
        }


def compare(info_a, info_b, label_a="A", label_b="B",
            check_positions=True, enable_cpd=None, profile=None, **_kwargs):
    """三步漏斗配对：S1 路径匹配 → S2 等点数匹配 → S3 空间深度分析。

    产出 pairing_groups（连通分量组列表）供 sync 消费。每个组 4 种标签：
      IDENTICAL    — Step 1/2 几何全等（1→1）
      ORIG_INJECT  — Step 1/2 点数对+小位移（1→1）
      PAIRED       — Step 3 空间配对（任意 M→N 连通分量，含 MODIFIED/MERGE/SPLIT）
      UNPAIRED     — abc 独有（无 rig 源）

    rig 独有的 mesh 不进 pairing_groups，单独列在 target_only_dags。

    返回结构契约：见本文件顶部 `CompareResult` TypedDict。

    参数 `check_positions=False` 或任一侧 `meshes` 为空时走 early-return：
    所有 mesh 都落到 only_a/only_b（且各自产出 UNPAIRED 组或进 target_only_dags）。
    """
    meshes_a = info_a.get("meshes", {})
    meshes_b = info_b.get("meshes", {})

    profile = _resolve_profile(profile)
    pairing_cfg = profile.get("pairing", {})
    if enable_cpd is None:
        # P0-B 决策：默认关闭 CPD 全局预对齐（大场景爆内存；未来降到 S3 小对级别再启用）
        enable_cpd = False

    result = {
        "total_issues": 0,
        "label_a": label_a,
        "label_b": label_b,
        "source_a": info_a.get("source_file", ""),
        "source_b": info_b.get("source_file", ""),
        "paired": [],
        "only_a": [],
        "only_b": [],
        "hierarchy": {"matched": 0, "only_a": [], "only_b": []},
        "textures": {},
        "merge_groups": [],
        "split_groups": [],
    }

    if not check_positions or not meshes_a or not meshes_b:
        result["only_a"] = [{"dag": k, "vtx": v.get("vertices", 0)} for k, v in meshes_a.items()]
        result["only_b"] = [{"dag": k, "vtx": v.get("vertices", 0)} for k, v in meshes_b.items()]
        result["total_issues"] = len(meshes_a) + len(meshes_b)
        result["pairing_groups"] = _build_pairing_groups(result)
        result["target_only_dags"] = [e["dag"] for e in result["only_b"]]
        return result

    # 白名单过滤：_live_ 驱动体克隆不参与配对
    filtered_b = {dag: entry for dag, entry in meshes_b.items()
                  if '_live_' not in dag.lower()}

    # 可选 CPD 全局预对齐（默认关闭）
    if enable_cpd and meshes_a and filtered_b:
        cpd_diff_thr = pairing_cfg.get("cpd_point_diff_threshold", 0.8)
        cpd_max_iter = pairing_cfg.get("cpd_max_iterations", 30)
        cpd_tol = pairing_cfg.get("cpd_tolerance", 0.001)
        try:
            from .non_rigid_registration import align_pose_non_rigid
            import numpy as np
            pts_a_list = [np.array(e["vert_positions"]).reshape(-1, 3)
                          for e in meshes_a.values()
                          if e.get("vert_positions")]
            b_items = [(d, np.array(e["vert_positions"]).reshape(-1, 3))
                       for d, e in filtered_b.items()
                       if e.get("vert_positions")]
            if pts_a_list and b_items:
                cloud_a = np.vstack(pts_a_list)
                cloud_b = np.vstack([pts for _, pts in b_items])
                diff_ratio = abs(len(cloud_a) - len(cloud_b)) / max(len(cloud_a), len(cloud_b))
                if diff_ratio < cpd_diff_thr:
                    w_val = min(0.9, diff_ratio * 1.5)
                    cloud_b_aligned = align_pose_non_rigid(
                        cloud_b, cloud_a,
                        max_iterations=cpd_max_iter, tolerance=cpd_tol, w=w_val,
                    )
                    offset = 0
                    for dag_b, pts in b_items:
                        n = len(pts)
                        filtered_b[dag_b]["vert_positions"] = cloud_b_aligned[offset:offset+n].flatten().tolist()
                        offset += n
        except Exception:
            pass

    # ── Step 1: 按规范化路径匹配 ──
    s1_pairs, s1_remain_a, s1_remain_b, skip_set = _step1_path_match(
        meshes_a, filtered_b, profile=profile)

    # ── Step 2: 严格等点数候选竞争 ──
    s2_pairs, s2_remain_a, s2_remain_b = _step2_vertex_count_match(
        s1_remain_a, s1_remain_b, meshes_a, filtered_b,
        profile=profile, skip_set=skip_set)

    # ── Step 3: 空间深度分析 ──
    s3 = _step3_spatial_analysis(
        s2_remain_a, s2_remain_b, meshes_a, filtered_b, profile=profile)

    # 合并所有配对结果
    all_pairs = list(s1_pairs) + list(s2_pairs) + list(s3["pairs"])
    for p in all_pairs:
        dag_a = p["dag_a"]
        dag_b = p["dag_b"]
        detail = {
            "dag_a": dag_a,
            "dag_b": dag_b,
            "name_a": _dag_display(dag_a),
            "name_b": _dag_display(dag_b),
            "vtx_a": p.get("vtx_a", meshes_a.get(dag_a, {}).get("vertices", 0)),
            "vtx_b": p.get("vtx_b", filtered_b.get(dag_b, {}).get("vertices", 0)),
            "actionability": p["actionability"],
            "step": p.get("step"),
        }
        for k in ("match_pct_exact", "match_pct_loose", "max_offset",
                  "min_offset", "precision_used", "total_vertices"):
            if k in p:
                detail[k] = p[k]
        result["paired"].append(detail)
        if p["actionability"] != "IDENTICAL":
            result["total_issues"] += 1

    # NEW 和 DELETE
    for dag_a in s3["new_list"]:
        result["only_a"].append({
            "dag": dag_a,
            "name": _dag_display(dag_a),
            "vtx": meshes_a[dag_a].get("vertices", 0),
        })
    for dag_b in s3["delete_list"]:
        result["only_b"].append({
            "dag": dag_b,
            "name": _dag_display(dag_b),
            "vtx": filtered_b[dag_b].get("vertices", 0),
        })

    result["total_issues"] += len(result["only_a"]) + len(result["only_b"])
    result["merge_groups"] = s3["merge_groups"]
    result["split_groups"] = s3["split_groups"]

    # 配对后的层级统计
    unmatched_a_dags = [e["dag"] for e in result["only_a"]]
    unmatched_b_dags = [e["dag"] for e in result["only_b"]]
    _compare_hierarchy(result, meshes_a, meshes_b, unmatched_a_dags, unmatched_b_dags)

    _compare_textures(result, info_a, info_b)

    # pairing_groups: 连通分量（把所有 abc↔rig 边合并成组）
    result["pairing_groups"] = _build_pairing_groups(result)
    # target_only: rig 独有——所有 meshes_b 减去已参与 pairing_groups 的 rig
    consumed_rigs = set()
    for g in result["pairing_groups"]:
        consumed_rigs.update(g.get("rig_dags", []))
    result["target_only_dags"] = [
        e["dag"] for e in result["only_b"]
        if e["dag"] not in consumed_rigs
    ]

    return result


# ═══════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════


# 连通分量分组：把 paired + merge + split + only_a 的所有 abc↔rig 边做并查集
# 每组一条记录 {group_id, action, abc_dags, rig_dags, layer_name, reason}
#   action = IDENTICAL | ORIG_INJECT | PAIRED | UNPAIRED
#   IDENTICAL/ORIG_INJECT 是 Step 1/2 一对一产出，天然单节点组
#   PAIRED 是 Step 3 产物，分量可能是 1→1 / N→1 / 1→N / M→N
#   UNPAIRED 是 only_a（abc 独有，没 rig 边）
def _build_pairing_groups(result):
    import hashlib

    # Step 1/2 单节点组：IDENTICAL 和 ORIG_INJECT 直接产出，不进并查集
    single_node_actions = {"IDENTICAL", "ORIG_INJECT"}
    step_s3_actions = {"MODIFIED", "MERGE", "SPLIT"}

    groups = []
    group_seq = 0

    def _mk_gid():
        nonlocal group_seq
        group_seq += 1
        return f"g{group_seq:04d}"

    # 先处理 IDENTICAL / ORIG_INJECT 的 1→1 组
    s3_paired_edges = []  # [(abc_dag, rig_dag, reason)]
    for p in result.get("paired", []):
        act = p.get("actionability", "")
        if act in single_node_actions:
            groups.append({
                "group_id": _mk_gid(),
                "action": act,
                "abc_dags": [p["dag_a"]],
                "rig_dags": [p["dag_b"]],
                "layer_name": _make_layer_name([p["dag_a"]]),
                "reason": f"Step 1/2 路径+几何一致 ({act})",
            })
        elif act in step_s3_actions:
            s3_paired_edges.append((p["dag_a"], p["dag_b"], act))

    # Step 3 的 merge / split 边也加入并查集
    for mg in result.get("merge_groups", []):
        for src in mg.get("sources_b", []):
            s3_paired_edges.append((mg["target_a"], src, "MERGE"))
    for sg in result.get("split_groups", []):
        for tgt in sg.get("targets_a", []):
            s3_paired_edges.append((tgt, sg["source_b"], "SPLIT"))

    # 并查集：abc dag 前缀 A|，rig dag 前缀 B| 避免命名冲突
    parent = {}

    def _find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def _union(x, y):
        rx, ry = _find(x), _find(y)
        if rx != ry:
            parent[rx] = ry

    edge_reasons = {}  # root -> set of reasons
    for abc, rig, reason in s3_paired_edges:
        ka, kb = f"A|{abc}", f"B|{rig}"
        parent.setdefault(ka, ka)
        parent.setdefault(kb, kb)
        _union(ka, kb)
        root = _find(ka)
        edge_reasons.setdefault(root, set()).add(reason)

    # 按连通分量聚合
    components = {}  # root -> {"abc": [...], "rig": [...]}
    for node in parent:
        root = _find(node)
        comp = components.setdefault(root, {"abc": [], "rig": []})
        if node.startswith("A|"):
            comp["abc"].append(node[2:])
        else:
            comp["rig"].append(node[2:])

    for root, comp in components.items():
        abc_dags = sorted(comp["abc"])
        rig_dags = sorted(comp["rig"])
        reasons = sorted(edge_reasons.get(root, set()))
        groups.append({
            "group_id": _mk_gid(),
            "action": "PAIRED",
            "abc_dags": abc_dags,
            "rig_dags": rig_dags,
            "layer_name": _make_layer_name(abc_dags),
            "reason": "Step 3 空间配对：" + "/".join(reasons) if reasons else "Step 3 空间配对",
        })

    # only_a → UNPAIRED
    for entry in result.get("only_a", []):
        groups.append({
            "group_id": _mk_gid(),
            "action": "UNPAIRED",
            "abc_dags": [entry["dag"]],
            "rig_dags": [],
            "layer_name": "",  # UNPAIRED 统一进 _source_only，不需要独立 layer
            "reason": "abc 独有（Step 3 未配对）",
        })

    return groups


def _make_layer_name(abc_dags, max_len=30):
    """abc 短名用 __ 连接，超长截断 + hash 后缀。单个 mesh 直接返回短名。"""
    import hashlib
    shorts = [_dag_display(d) for d in abc_dags]
    if len(shorts) == 1:
        return shorts[0]
    joined = "__".join(shorts)
    if len(joined) <= max_len:
        return joined
    # 截断：保留前 N 个短名 + 数字提示 + hash 后缀
    suffix = hashlib.md5(joined.encode("utf-8")).hexdigest()[:4]
    kept = []
    used = 0
    for s in shorts:
        if used + len(s) + 2 > max_len - 12:  # 给 +Nmore_xxxx 留空间
            break
        kept.append(s)
        used += len(s) + 2
    more = len(shorts) - len(kept)
    if more > 0:
        return "__".join(kept) + f"__+{more}more_{suffix}"
    return joined[:max_len - 5] + f"_{suffix}"


def _dag_display(dag):
    """从 DAG 路径提取可读的显示名（最后一段，去命名空间）"""
    parts = dag.strip("|").split("|")
    last = parts[-1] if parts else dag
    return last.split(":")[-1]


def _normalize_dag(dag):
    """Step 1 路径匹配的规范化：对每段剥 RIG_ 前缀和 namespace。"""
    parts = dag.strip("|").split("|")
    norm = []
    for seg in parts:
        seg = seg.split(":")[-1]
        if seg.startswith("RIG_"):
            seg = seg[4:]
        norm.append(seg)
    return "|".join(norm)


def _judge_action(detail, profile=None):
    """Step 1/2 的闸口判定：仅三种结果
    - "IDENTICAL"    : pct_exact == 100% 且 max_offset < precision_exact
    - "ORIG_INJECT"  : pct_loose == 100% 且 max_offset < precision_loose
    - None           : 不达标，调用方决定滑到下一 Step
    要求 detail 已包含 match_pct_exact / match_pct_loose / max_offset 字段。
    """
    profile = _resolve_profile(profile)
    cls_cfg = profile.get("classification", {})
    exact_thr = cls_cfg.get("precision_exact", _PRECISION_EXACT)
    loose_thr = cls_cfg.get("precision_loose", _PRECISION_LOOSE)

    vtx_a = detail.get("vtx_a", 0)
    vtx_b = detail.get("vtx_b", 0)
    if vtx_a != vtx_b or vtx_a == 0:
        return None

    max_offset = detail.get("max_offset", 999)

    def _parse_pct(s):
        try:
            return float(str(s).strip().rstrip("%")) / 100.0
        except (ValueError, AttributeError, TypeError):
            return 0.0

    pct_exact = _parse_pct(detail.get("match_pct_exact", "0%"))
    pct_loose = _parse_pct(detail.get("match_pct_loose", "0%"))

    if pct_exact >= 1.0 and max_offset < exact_thr:
        return "IDENTICAL"
    if pct_loose >= 1.0 and max_offset < loose_thr:
        return "ORIG_INJECT"
    return None


# ═══════════════════════════════════════════
# 三步漏斗配对
# ═══════════════════════════════════════════

def _compute_pair_detail(entry_a, entry_b, profile=None):
    """对已配对的一对 mesh 跑逐点距离，返回带统一字段的 detail dict。
    点数不一致时返回 None（调用方跳过 _judge_action 进入 Step 3 分析）。
    """
    count_a = entry_a.get("vertices", 0)
    count_b = entry_b.get("vertices", 0)
    if count_a == 0 or count_b == 0 or count_a != count_b:
        return None

    pos_a = entry_a.get("vert_positions", [])
    pos_b = entry_b.get("vert_positions", [])
    if not pos_a or not pos_b:
        return None

    pos_result = _compare_positions(pos_a, pos_b, profile=profile)
    detail = {
        "vtx_a": count_a,
        "vtx_b": count_b,
    }
    if pos_result is None:
        # 完全精确一致
        detail.update({
            "match_pct_exact": "100.0000%",
            "match_pct_loose": "100.0000%",
            "max_offset": 0.0,
            "min_offset": 0.0,
        })
    else:
        detail.update(pos_result)
    return detail


def _step1_path_match(meshes_a, meshes_b, profile=None):
    """Step 1: 按规范化 DAG 绝对路径匹配。

    规则：
    - tex 用 _normalize_dag 后查找 rig 侧同路径
    - 路径命中 + 点数一致 → _judge_action 分 IDENTICAL/ORIG_INJECT
    - 路径命中 + 点数不一致 → 跳过 Step 2 直接进 Step 3（tex 留在 remaining）
    - 其他情况 → tex 进 Step 2 池

    返回 (pairs, remaining_a, remaining_b, skip_step2_for)
      - pairs:          [{dag_a, dag_b, actionability, detail_fields...}]
      - remaining_a:    未匹配到路径 或 匹配到但未达标的 tex dag 列表（送 Step 2）
      - remaining_b:    未被消耗的 rig dag 列表
      - skip_step2_for: 集合，路径命中但点数不一致的 tex，Step 2 应跳过直接送 Step 3
    """
    # 建立 rig 侧规范化路径 → dag 的索引
    rig_norm_index = {}
    for dag_b in meshes_b.keys():
        norm = _normalize_dag(dag_b)
        rig_norm_index.setdefault(norm, []).append(dag_b)

    pairs = []
    used_b = set()
    remaining_a = []
    skip_step2_for = set()

    for dag_a in meshes_a.keys():
        norm_a = _normalize_dag(dag_a)
        candidates = [d for d in rig_norm_index.get(norm_a, []) if d not in used_b]
        if not candidates:
            remaining_a.append(dag_a)
            continue

        # 规范化后同路径可能有多个（小概率，如命名冲突），取第一个稳定处理
        dag_b = candidates[0]
        entry_a = meshes_a[dag_a]
        entry_b = meshes_b[dag_b]

        count_a = entry_a.get("vertices", 0)
        count_b = entry_b.get("vertices", 0)

        # 点数不一致 → 跳 Step 2，直接送 Step 3
        if count_a != count_b:
            remaining_a.append(dag_a)
            skip_step2_for.add(dag_a)
            continue

        detail = _compute_pair_detail(entry_a, entry_b, profile=profile)
        if detail is None:
            # 理论不该走到（上面已查点数）；保守滑到 Step 2
            remaining_a.append(dag_a)
            continue

        action = _judge_action(detail, profile=profile)
        if action in ("IDENTICAL", "ORIG_INJECT"):
            pairs.append({
                "dag_a": dag_a,
                "dag_b": dag_b,
                "step": 1,
                "actionability": action,
                **detail,
            })
            used_b.add(dag_b)
        else:
            # 路径命中 + 点数一致但距离不过：让 Step 2 在同点数池里找其他 rig 再竞争
            remaining_a.append(dag_a)

    remaining_b = [d for d in meshes_b.keys() if d not in used_b]
    return pairs, remaining_a, remaining_b, skip_step2_for


def _step2_vertex_count_match(remaining_a, remaining_b, meshes_a, meshes_b,
                              profile=None, skip_set=None):
    """Step 2: 在剩余 rig 池里按严格等点数拉候选，按 pct_loose 降序取 Top1。

    - 仅达标（IDENTICAL/ORIG_INJECT）才消耗 rig
    - 不达标的 tex 和未消耗的 rig 全部送 Step 3
    - skip_set 内的 tex（Step 1 路径命中但点数不一致）直接透传到 Step 3，不参与 Step 2

    返回 (pairs, remaining_a_out, remaining_b_out)
    """
    skip_set = skip_set or set()

    # 按 rig 点数建立候选索引
    rig_by_count = {}
    for dag_b in remaining_b:
        cnt = meshes_b[dag_b].get("vertices", 0)
        if cnt <= 0:
            continue
        rig_by_count.setdefault(cnt, []).append(dag_b)

    pairs = []
    used_b = set()
    remaining_a_out = []

    for dag_a in remaining_a:
        if dag_a in skip_set:
            remaining_a_out.append(dag_a)
            continue

        entry_a = meshes_a[dag_a]
        count_a = entry_a.get("vertices", 0)
        candidates = [d for d in rig_by_count.get(count_a, []) if d not in used_b]
        if not candidates:
            remaining_a_out.append(dag_a)
            continue

        # 对所有候选算 detail，按 pct_loose 降序
        scored = []
        for dag_b in candidates:
            detail = _compute_pair_detail(entry_a, meshes_b[dag_b], profile=profile)
            if detail is None:
                continue
            pct_loose_str = detail.get("match_pct_loose", "0%")
            try:
                pct_loose = float(pct_loose_str.strip("%")) / 100.0
            except (ValueError, AttributeError):
                pct_loose = 0.0
            scored.append((pct_loose, dag_b, detail))

        if not scored:
            remaining_a_out.append(dag_a)
            continue

        scored.sort(key=lambda x: -x[0])
        best_pct, best_dag_b, best_detail = scored[0]

        action = _judge_action(best_detail, profile=profile)
        if action in ("IDENTICAL", "ORIG_INJECT"):
            pairs.append({
                "dag_a": dag_a,
                "dag_b": best_dag_b,
                "step": 2,
                "actionability": action,
                **best_detail,
            })
            used_b.add(best_dag_b)
        else:
            remaining_a_out.append(dag_a)

    remaining_b_out = [d for d in remaining_b if d not in used_b]
    return pairs, remaining_a_out, remaining_b_out


def _step3_spatial_analysis(remaining_a, remaining_b, meshes_a, meshes_b, profile=None):
    """Step 3: 剩余 tex + 剩余 rig 空间深度分析。

    分类：
    - MODIFIED: tex 在某 rig 上命中率 ≥ 30%（含 ≥75%），标为"改过的物体"
    - NEW     : tex 在所有 rig 上命中率 < 30%，标为"资产独有"
    - DELETE  : Step 3 结束后未被消耗的 rig
    - MERGE   : 一个 tex 对应多个 rig 源
    - SPLIT   : 多个 tex 指向同一个 rig

    本函数仅检测 MODIFIED + MERGE/SPLIT 关系；NEW 通过剩余 tex 未被 MODIFIED 命中推定；
    DELETE 由剩余 rig 推定。
    返回 dict：
      {
        "pairs":        [{dag_a, dag_b, step:3, actionability: "MODIFIED", match_pct_loose, max_offset, ...}],
        "new_list":     [dag_a, ...],          # tex 独有
        "delete_list":  [dag_b, ...],          # rig 独有
        "merge_groups": [{target_a, sources_b}],
        "split_groups": [{source_b, targets_a}],
      }
    """
    profile = _resolve_profile(profile)
    cls_cfg = profile.get("classification", {})
    loose_thr = cls_cfg.get("precision_loose", _PRECISION_LOOSE)
    # Step 3 分类阈值（可下放到 profile，本轮直接硬编码 0.30/0.75）
    modified_min = 0.30

    pairs = []
    new_list = []
    merge_groups = []
    split_groups = []

    if not remaining_a or not remaining_b:
        # tex 全是 NEW，rig 全是 DELETE
        return {
            "pairs": pairs,
            "new_list": list(remaining_a),
            "delete_list": list(remaining_b),
            "merge_groups": merge_groups,
            "split_groups": split_groups,
        }

    try:
        from scipy.spatial import cKDTree
        import numpy as np
    except ImportError:
        return {
            "pairs": pairs,
            "new_list": list(remaining_a),
            "delete_list": list(remaining_b),
            "merge_groups": merge_groups,
            "split_groups": split_groups,
        }

    # 建 rig 侧全局 KDTree（顶点级）
    all_pts = []
    pt_labels = []
    b_keys = []
    for bi, dag_b in enumerate(remaining_b):
        pos = meshes_b[dag_b].get("vert_positions", [])
        if not pos:
            continue
        pts = np.array(pos, dtype=np.float64).reshape(-1, 3)
        all_pts.append(pts)
        pt_labels.extend([len(b_keys)] * len(pts))
        b_keys.append(dag_b)

    if not all_pts:
        return {
            "pairs": pairs,
            "new_list": list(remaining_a),
            "delete_list": list(remaining_b),
            "merge_groups": merge_groups,
            "split_groups": split_groups,
        }

    all_pts = np.vstack(all_pts)
    pt_labels = np.array(pt_labels, dtype=np.int32)
    tree = cKDTree(all_pts)

    # 对每个 tex 统计命中各 rig 的占比
    a_hit = {}           # dag_a → {b_idx: hit_count}
    a_best = {}          # dag_a → (best_pct, best_b_idx, max_offset)

    for dag_a in remaining_a:
        pos_a = meshes_a[dag_a].get("vert_positions", [])
        if not pos_a:
            a_best[dag_a] = (0.0, None, 0.0)
            continue
        pts_a = np.array(pos_a, dtype=np.float64).reshape(-1, 3)
        total = len(pts_a)

        # 用 loose_thr 命中判定
        indices_list = tree.query_ball_point(pts_a, r=loose_thr)
        hit_counts = {}
        for inds in indices_list:
            if not inds:
                continue
            labels = set(int(pt_labels[i]) for i in inds)
            for bi in labels:
                hit_counts[bi] = hit_counts.get(bi, 0) + 1

        a_hit[dag_a] = hit_counts
        if not hit_counts:
            a_best[dag_a] = (0.0, None, 0.0)
            continue

        best_bi, best_cnt = max(hit_counts.items(), key=lambda kv: kv[1])
        best_pct = best_cnt / total if total > 0 else 0.0

        # 计算 max_offset 用最近邻 (k=1)
        dists, _ = tree.query(pts_a, k=1)
        max_offset = float(np.max(dists))
        a_best[dag_a] = (best_pct, best_bi, max_offset)

    # 配对决策
    used_b = set()
    # 先按 best_pct 降序处理，保证高质量配对优先消耗 rig
    ordered = sorted(remaining_a, key=lambda d: -a_best[d][0])
    for dag_a in ordered:
        best_pct, best_bi, max_offset = a_best[dag_a]
        if best_bi is None or best_pct < modified_min:
            new_list.append(dag_a)
            continue
        dag_b = b_keys[best_bi]
        if best_bi in used_b:
            # 该 rig 已被其他 tex 取走 → 记为 split
            existing = next((s for s in split_groups if s["source_b"] == dag_b), None)
            if existing is None:
                # 找到它之前的配对
                prev = next((p for p in pairs if p["dag_b"] == dag_b), None)
                targets = [prev["dag_a"]] if prev else []
                targets.append(dag_a)
                split_groups.append({"source_b": dag_b, "targets_a": targets})
            else:
                existing["targets_a"].append(dag_a)
            # split 场景下 tex 仍标 MODIFIED（指向同一 rig），下游自行处理
            pairs.append({
                "dag_a": dag_a,
                "dag_b": dag_b,
                "step": 3,
                "actionability": "SPLIT",
                "vtx_a": meshes_a[dag_a].get("vertices", 0),
                "vtx_b": meshes_b[dag_b].get("vertices", 0),
                "match_pct_loose": f"{best_pct:.4%}",
                "max_offset": round(max_offset, 4),
            })
            continue

        used_b.add(best_bi)

        # 检测 merge：tex 命中多个 rig（每个 rig 命中率都 ≥ modified_min）
        hit_counts = a_hit.get(dag_a, {})
        total = meshes_a[dag_a].get("vertices", 0) or 1
        significant_rigs = [bi for bi, c in hit_counts.items() if c / total >= modified_min]
        if len(significant_rigs) > 1:
            merge_groups.append({
                "target_a": dag_a,
                "sources_b": [b_keys[bi] for bi in significant_rigs],
            })
            action = "MERGE"
        else:
            action = "MODIFIED"

        pairs.append({
            "dag_a": dag_a,
            "dag_b": dag_b,
            "step": 3,
            "actionability": action,
            "vtx_a": meshes_a[dag_a].get("vertices", 0),
            "vtx_b": meshes_b[dag_b].get("vertices", 0),
            "match_pct_loose": f"{best_pct:.4%}",
            "max_offset": round(max_offset, 4),
        })

    delete_list = [b_keys[i] for i in range(len(b_keys)) if i not in used_b]
    # 还包括 remaining_b 中没进 KDTree 的（无 vert_positions）
    seen = set(b_keys)
    for dag_b in remaining_b:
        if dag_b not in seen:
            delete_list.append(dag_b)

    return {
        "pairs": pairs,
        "new_list": new_list,
        "delete_list": delete_list,
        "merge_groups": merge_groups,
        "split_groups": split_groups,
    }


def _global_kdtree_match(meshes_a, meshes_b, profile=None):
    """
    全局 KDTree 匹配：把 B 侧所有顶点放进一棵大树，A 侧逐 mesh 查询。
    返回：(pairs, unmatched_a, unmatched_b)
    """
    profile = _resolve_profile(profile)
    pairing_cfg = profile.get("pairing", {})
    cls_cfg = profile.get("classification", {})
    round_decimals = pairing_cfg.get("kdtree_round_decimals", 4)
    name_bonus = pairing_cfg.get("name_bonus", 0.0)
    loose_thr = cls_cfg.get("precision_loose", _PRECISION_LOOSE)

    # ── 预过滤：从 B 侧剔除白名单节点（不参与 KDTree 配对竞争） ──
    # 仅过滤 rig 专有驱动节点（_live_）
    filtered_b = {}
    for dag, entry in meshes_b.items():
        if '_live_' not in dag.lower():
            filtered_b[dag] = entry

    try:
        from scipy.spatial import cKDTree
        import numpy as np
    except ImportError:
        return [], list(meshes_a.keys()), list(filtered_b.keys()), [], []

    # ── 1. 构建 B 侧全局索引 ──
    b_keys = list(filtered_b.keys())
    all_pts = []
    pt_labels = []

    for bi, bk in enumerate(b_keys):
        pos = filtered_b[bk].get("vert_positions", [])
        if not pos:
            continue
        pts = np.round(np.array(pos).reshape(-1, 3), round_decimals)
        all_pts.append(pts)
        pt_labels.extend([bi] * len(pts))

    if not all_pts:
        return [], list(meshes_a.keys()), list(filtered_b.keys()), [], []

    all_pts = np.vstack(all_pts)
    pt_labels = np.array(pt_labels, dtype=np.int32)
    tree = cKDTree(all_pts)

    # ── 2. A 侧逐 mesh 查询，计算得分矩阵 ──
    a_keys = list(meshes_a.keys())
    score_data = {}  # (ai, bi) → {loose_count, exact_count, max_offset}

    for ai, ak in enumerate(a_keys):
        pos_a = meshes_a[ak].get("vert_positions", [])
        if not pos_a:
            continue
        pts_a = np.round(np.array(pos_a).reshape(-1, 3), round_decimals)

        # 使用 query_ball_point 解决重叠网格的命中分流问题
        indices_list = tree.query_ball_point(pts_a, r=loose_thr)

        hit_counts = {}
        for indices in indices_list:
            if not indices:
                continue
            # 一个 A 点可能在距离内匹配到多个 B 侧顶点，取唯一的 B 侧 mesh 标签
            labels = np.unique(pt_labels[indices])
            for bi_val in labels:
                hit_counts[bi_val] = hit_counts.get(bi_val, 0) + 1

        for bi_val, loose_count in hit_counts.items():
            # 由于 query_ball_point 不返回距离，我们简化为：只要落入 loose 球内就算 loose_count。
            # 精确匹配率后续 _compare_positions 会再算，这里直接用于 KDTree 打分。
            key = (ai, int(bi_val))
            score_data[key] = {
                "loose": loose_count,
                "exact": loose_count,  # 简化，依赖 _compare_positions 细算
                "max_offset": loose_thr,  # 简化
            }
            
    merge_groups = []
    split_groups = []
    
    a_matches = {ai: [] for ai in range(len(a_keys))}
    b_matches = {bi: [] for bi in range(len(b_keys))}
    
    for (ai, bi), stats in score_data.items():
        vtx_a = meshes_a[a_keys[ai]].get("vertices", 0)
        vtx_b = filtered_b[b_keys[bi]].get("vertices", 0)
        max_count = max(vtx_a, vtx_b)
        if max_count == 0: continue
        score = stats["loose"] / max_count
        if score > 0.3:
            a_matches[ai].append(bi)
            b_matches[bi].append(ai)
            
    for ai, bis in a_matches.items():
        if len(bis) > 1:
            merge_groups.append({
                "target_a": a_keys[ai],
                "sources_b": [b_keys[bi] for bi in bis]
            })
            
    for bi, ais in b_matches.items():
        if len(ais) > 1:
            split_groups.append({
                "source_b": b_keys[bi],
                "targets_a": [a_keys[ai] for ai in ais]
            })

    # ── 3. 贪心 1-to-1 配对（按得分从高到低）──
    candidates = []
    for (ai, bi), stats in score_data.items():
        vtx_a = meshes_a[a_keys[ai]].get("vertices", 0)
        vtx_b = filtered_b[b_keys[bi]].get("vertices", 0)
        max_count = max(vtx_a, vtx_b)
        if max_count == 0:
            continue
            
        # 基础分数：几何匹配度
        score = stats["loose"] / max_count
        
        # 名字奖励：当存在多个完全重叠的物体（如 head1 和 hairbasemesh）时，优先配合同名的
        name_a = _dag_display(a_keys[ai])
        name_b = _dag_display(b_keys[bi])
        name_bonus_val = name_bonus if name_a == name_b else 0.0

        candidates.append((score + name_bonus_val, score, ai, bi, stats))

    # 按得分降序
    candidates.sort(key=lambda x: -x[0])

    used_a = set()
    used_b = set()
    pairs = []

    for sort_score, base_score, ai, bi, stats in candidates:
        if ai in used_a or bi in used_b:
            continue
        # 最低 10% 匹配率才认为有关联
        if base_score < 0.1:
            continue

        used_a.add(ai)
        used_b.add(bi)

        vtx_a = meshes_a[a_keys[ai]].get("vertices", 0)
        vtx_b = filtered_b[b_keys[bi]].get("vertices", 0)
        max_count = max(vtx_a, vtx_b)

        pairs.append({
            "dag_a": a_keys[ai],
            "dag_b": b_keys[bi],
            "match_score": round(score, 4),
            "match_pct": f"{score * 100:.2f}%",
            "exact_pct": f"{(stats['exact'] / max_count) * 100:.2f}%" if max_count > 0 else "0%",
            "max_offset": stats["max_offset"],
        })

    unmatched_a = [a_keys[i] for i in range(len(a_keys)) if i not in used_a]
    unmatched_b = [b_keys[i] for i in range(len(b_keys)) if i not in used_b]

    return pairs, unmatched_a, unmatched_b, merge_groups, split_groups


def _compare_partial_positions(pos_a, pos_b, profile=None):
    """
    点数不同时的局部重合度精确计算 (Tier 3 & Tier 4)。
    返回字典，包含 match_pct_loose, is_partial, subset_mapping 等。
    """
    if not pos_a or not pos_b:
        return None

    try:
        from scipy.spatial import cKDTree
        import numpy as np
    except ImportError:
        return None

    profile = _resolve_profile(profile)
    loose_thr = profile.get("classification", {}).get("precision_loose", _PRECISION_LOOSE)

    pts_a = np.array(pos_a).reshape(-1, 3)
    pts_b = np.array(pos_b).reshape(-1, 3)

    tree_b = cKDTree(pts_b)
    # A 中的每个点去 B 里找最近邻
    dists, indices = tree_b.query(pts_a, k=1)

    # 我们用 loose_thr 来界定是否属于重合点
    matched_mask = dists < loose_thr
    matched_count = np.sum(matched_mask)
    
    if matched_count == 0:
        return None
        
    pct_a = matched_count / len(pts_a)
    pct_b = matched_count / len(pts_b)
    
    # 局部匹配阈值：至少 20% 重合才认为是有意义的局部匹配
    if pct_a < 0.2 and pct_b < 0.2:
        return None
        
    # 构建严格的映射表: subset_mapping: a_idx -> b_idx. 未匹配填 -1
    subset_mapping = np.full(len(pts_a), -1, dtype=np.int32)
    subset_mapping[matched_mask] = indices[matched_mask]
    
    free_a_indices = np.where(~matched_mask)[0].tolist()
    
    return {
        "match_pct_loose": f"{max(pct_a, pct_b):.4%}",
        "max_offset": float(np.max(dists[matched_mask])) if matched_count > 0 else 0.0,
        "is_partial": True,
        "subset_mapping": subset_mapping.tolist(),
        "free_a_indices": free_a_indices,
        "precision_used": f"局部重合({matched_count}/{len(pts_a)} A, {matched_count}/{len(pts_b)} B)",
    }


def _compare_positions(pos_a, pos_b, profile=None):
    """
    逐点位置对比（index-to-index，要求点数一致）。

    返回 None 表示完全一致（精确匹配 100%），否则返回差异详情。
    """
    if not pos_a or not pos_b:
        return None

    profile = _resolve_profile(profile)
    cls_cfg = profile.get("classification", {})
    exact_thr = cls_cfg.get("precision_exact", _PRECISION_EXACT)
    loose_thr = cls_cfg.get("precision_loose", _PRECISION_LOOSE)
    round_decimals = profile.get("pairing", {}).get("kdtree_round_decimals", 4)

    total = min(len(pos_a), len(pos_b)) // 3
    if total == 0:
        return None

    exact_count = 0
    loose_count = 0
    max_dist = 0.0
    min_dist = float('inf')

    for i in range(total):
        idx = i * 3
        # 精度对齐后再计算距离
        dx = round(pos_a[idx], round_decimals) - round(pos_b[idx], round_decimals)
        dy = round(pos_a[idx + 1], round_decimals) - round(pos_b[idx + 1], round_decimals)
        dz = round(pos_a[idx + 2], round_decimals) - round(pos_b[idx + 2], round_decimals)
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)

        if dist < exact_thr:
            exact_count += 1
            loose_count += 1
        elif dist < loose_thr:
            loose_count += 1

        if dist > max_dist:
            max_dist = dist
        if dist > 0 and dist < min_dist:
            min_dist = dist

    if min_dist == float('inf'):
        min_dist = 0.0

    # 完全精确一致 → 返回 None（无需报告）
    if exact_count == total:
        return None

    pct_exact = exact_count / total
    pct_loose = loose_count / total
    passed = (loose_count == total)

    if passed:
        precision_used = "宽松通过"
    else:
        precision_used = f"未通过({total - loose_count}顶点>{loose_thr}cm)"

    return {
        "match_pct_exact": f"{pct_exact:.4%}",
        "match_pct_loose": f"{pct_loose:.4%}",
        "max_offset": round(max_dist, 3),
        "min_offset": round(min_dist, 3),
        "precision_used": precision_used,
        "total_vertices": total,
    }


# ═══════════════════════════════════════════
# 扩展对比：层级 / UV / 贴图
# ═══════════════════════════════════════════

def _dag_leaf(dag):
    """取 DAG 路径最后一段（去 Shape、去命名空间）"""
    parts = dag.strip("|").split("|")
    # 去掉尾部 shape 段（通常含 Shape 后缀）
    if len(parts) > 1 and "Shape" in parts[-1]:
        parts = parts[:-1]
    return parts[-1].split(":")[-1] if parts else dag


def _compare_hierarchy(result, meshes_a, meshes_b, unmatched_a, unmatched_b):
    """基于几何配对后的 DAG 名称匹配统计。"""
    h = result["hierarchy"]
    # 从 paired 中统计名称一致 vs 不一致
    for p in result["paired"]:
        leaf_a = _dag_leaf(p["dag_a"])
        leaf_b = _dag_leaf(p["dag_b"])
        if leaf_a == leaf_b:
            h["matched"] += 1
        # 名称不同不算错误，只记录在 paired 中

    h["only_a"] = [_dag_leaf(dag) for dag in unmatched_a]
    h["only_b"] = [_dag_leaf(dag) for dag in unmatched_b]





def _compare_textures(result, info_a, info_b):
    """对比两侧贴图文件列表。"""
    tex_a = info_a.get("textures", {})
    tex_b = info_b.get("textures", {})

    # 展平为文件名集合
    files_a = set()
    for dir_path, names in (tex_a.items() if isinstance(tex_a, dict) else []):
        for n in names:
            files_a.add(n)

    files_b = set()
    for dir_path, names in (tex_b.items() if isinstance(tex_b, dict) else []):
        for n in names:
            files_b.add(n)

    result["textures"] = {
        "a_count": len(files_a),
        "b_count": len(files_b),
        "only_a": len(files_a - files_b),
        "only_b": len(files_b - files_a),
        "only_a_list": sorted(files_a - files_b),
        "only_b_list": sorted(files_b - files_a),
    }
