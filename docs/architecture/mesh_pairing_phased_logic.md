# 配对逻辑总表（问答敲定版 · 2026-05-09）

> 本文档是 User 与 Assistant 通过多轮问答确定的**三步配对判定链路**，
> 替代 `mesh_pairing_and_transfer_spec.md` 原"Tier 0-5 混杂判定"。
> 本文档只管**怎么配对**（判定），不管**怎么执行**（Maya 端怎么写权重/BS）。

## 核心理念

1. **渐进筛选，不是盲目全比** —— 每步用更宽松的假设重新捞剩余资产
2. **越早确定越低成本** —— S1 路径一致最乐观，S2 名字改了，S3 真动拓扑
3. **消耗式匹配** —— 只有达标的 rig 才从池里弹出，未达标的留到下一步重新竞争
4. **不引入 REVIEW/REJECT 分级** —— 每个 tex 最终必须有明确执行标签

---

## Step 1：cache 下绝对路径匹配

**假设**：资产什么都没改，只是 Rig 侧加了 `RIG_` 前缀。

```
for each tex_mesh:
    rig 侧查找 DAG（仅去 RIG_ 前缀后完全一致）
    ├─ 未找到           → 此 tex 进 Step 2 池
    └─ 找到 rig_mesh ──┐
                       ▼
    点数严格一致？
    ├─ 不一致           → 此 tex 进 Step 3 池（跳过 Step 2，点数变了按名字找也没意义）
    └─ 一致 ──────────┐
                       ▼
    逐点距离计算 → 输出 pct_exact, pct_loose, max_dist, min_dist
    ├─ pct_exact = 100%         → IDENTICAL（消耗 rig，不动）
    ├─ pct_loose = 100% AND max_dist < 0.005cm
    │                            → ORIG_INJECT（消耗 rig，偷梁换柱：注入新点/UV）
    └─ 其他                      → 此 tex 进 Step 2 池（rig 未消耗，留待 Step 2 竞争）
```

**关键规则**：
- 路径规范化：`cache|body_Grp|body` == `cache|RIG_body_Grp|RIG_body`（仅去 `RIG_` 前缀）
- 距离阈值：`0.0001cm`（exact）/ `0.005cm`（loose）
- ORIG_INJECT 双重门槛：`pct_loose = 100%` **且** `max_dist < 0.005cm`

---

## Step 2：按点数找候选，全比对取最高

**假设**：资产改了名字（或挪了层级），但几何完全没动。

**输入**：Step 1 剩余 tex 池 + 未被消耗的 rig 池

```
for each 剩余 tex_mesh:
    候选 = rig 池里所有「点数严格 == tex」的 rig_mesh
    ├─ 候选 0 个         → 此 tex 进 Step 3 池
    └─ 候选 ≥ 1 个 ─────┐
                         ▼
    对每个候选都算距离（用 Step 1 的同一套指标）
    按 pct_loose 降序取 Top1
    ├─ Top1 pct_exact = 100%                  → IDENTICAL（改名，消耗 rig）
    ├─ Top1 pct_loose = 100% AND max_dist < 0.005cm
    │                                          → ORIG_INJECT（改名，消耗 rig）
    └─ Top1 未达标                              → 此 tex 进 Step 3 池（rig 不消耗）
```

**冲突处理**：
- 两个 tex 的 Top1 指向同一个 rig → 留到 Step 3 的 MERGE/SPLIT 判定解决
  （典型场景：两只手合成一只手 → 空间映射求权重和）
- 只有达标的 rig 才从池里弹出，不达标的 rig 继续留给 Step 3

**同位置多 mesh 问题**（body vs hairbasemesh）：
- **暂不处理**（Step 2 阶段保留为已知风险）
- 未来引入时用 DAG 父级分组名区分

---

## Step 3：深度分析（空间匹配 + 分类标签）

**输入**：Step 1+2 后仍未配对的 tex + 仍未被消耗的 rig

**场景划分**：每个剩余 tex 最终产出下列标签之一。

```
for each 剩余 tex_mesh:
    对所有剩余 rig 做空间 KDTree 匹配
    (tex 顶点 → 最近 rig 顶点，统计命中率)

    按 KDTree 命中率 (距离 < 0.005cm 的 tex 顶点占比) 分类：

    ├─ 命中率 ≥ 75%                       → MODIFIED
    │   解释：原 rig 确实存在，只是改了部分拓扑
    │   处理：标记为 MODIFIED，记录 paired rig
    │   下游：走 Tier 3 定向投射（从该 rig 采权重 + Laplacian 扩散新点）
    │
    ├─ 命中率 < 30%                       → NEW
    │   解释：资产新增，原 rig 里没对应物
    │   处理：标记为 NEW
    │   下游：走 Tier 5 SuperMesh 全局采样
    │
    └─ 30% ≤ 命中率 < 75%                → MODIFIED（归入 MODIFIED 侧）
        解释：部分区域重合，保守当成改了部分拓扑
        下游：同 MODIFIED

MERGE/SPLIT 检测：
    - 一个 tex 命中多个 rig (分散命中) → 标 MERGE（多源合一）
    - 多个 tex 的最佳匹配是同一个 rig → 标 SPLIT（一源拆多）

Step 3 结束后：
    所有未被消耗的 rig_mesh → DELETE 标签
```

---

## 最终输出标签（给 Maya 端执行）

| 标签 | 来源 | 含义 | Maya 端做什么 |
|------|------|------|-------------|
| `IDENTICAL` | Step 1/2 | 点数+点序+位置完全一致 | 仅调整层级/命名（若需），不动 deformer |
| `ORIG_INJECT` | Step 1/2 | 100% 顶点 < 0.005cm | 清空 shape/orig 局部点 → 注入新坐标+UV → 挂到新 DAG 层级 |
| `MODIFIED` | Step 3 | 有源 rig，拓扑改了部分 | Tier 3 定向投射（从 paired rig 采权重 + Laplacian 扩散新点） |
| `NEW` | Step 3 | 资产独有 | Tier 5 SuperMesh 全局采样 |
| `DELETE` | Step 3 | 绑定独有（未被消耗的 rig） | 删除 |
| `MERGE` | Step 3 | 多源合一 | 从多个 rig 空间映射求权重和 |
| `SPLIT` | Step 3 | 一源拆多 | 从一个 rig 空间投射到多个目标 |

---

## 关键配置参数

从 `rig_sync_profile` 读：

```
classification.precision_exact   = 0.0001   # IDENTICAL 门槛
classification.precision_loose   = 0.005    # ORIG_INJECT 门槛 / KDTree 命中判定
step3.modified_threshold         = 0.75     # MODIFIED vs NEW 分界
step3.new_threshold              = 0.30     # NEW 判定下限
```

---

## 与 mihouwang E2E 的预期对照

| 配对 | 现算法 | 新算法 |
|------|-------|--------|
| 75 对完全一致 | AUTO | Step 1 → IDENTICAL |
| clothes3 ↔ clothes3（同名、4551、匹配 53.8%、max 0.314cm） | REJECT 0.644 | Step 1：pct_loose 不过 → Step 2：同点数候选可能是自己，Top1 还是不过 → Step 3：KDTree 命中率（53%）→ MODIFIED |
| hair42 ↔ hair17_hairbasemesh（88.6% 匹配） | REVIEW | Step 1：路径不匹配 → Step 2：429 点候选竞争，若 hair17_hairbasemesh 不是 Top1（或是但不达标）→ Step 3：若命中率高则 MODIFIED，否则 NEW |
| only_a: hair42_hairbasemesh, body1 | "新增"无后续 | Step 3：命中率低 → NEW |
| only_b: clothes4 | "删除"无后续 | Step 3：未被消耗 → DELETE |

---

## 实现范围（本轮 P0-B 只做第一部分）

**第一部分（P0-B 本轮）**：
- 重写 `core/asset_info_schema.py` 的 `_global_kdtree_match` 为三步链路
- 产出 IDENTICAL / ORIG_INJECT / MODIFIED / NEW / DELETE / MERGE / SPLIT 标签
- 删除 `pairing_report.py` 的 REVIEW/REJECT 置信度分级，改为 Tier 分布统计

**第二部分（下一轮 P0-C）**：
- 让 `maya_sync_rig_incremental` 消费新标签
- 打通 MODIFIED 走 Tier 3、NEW 走 Tier 5 的执行路径
- MERGE/SPLIT 的 Maya 端处理
