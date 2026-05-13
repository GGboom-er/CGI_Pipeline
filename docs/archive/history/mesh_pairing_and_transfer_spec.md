# Mesh 智能配对与绑定传递 — 需求推演文档

> ⚠️ **历史设计文档（P0-A 时代）**
>
> 本文是 P0-A 阶段的需求推演与实现规划，里面的动作标签（AUTO_SAFE /
> SPATIAL_VOTING / REORDER / PARTIAL_MATCH / SYNC_ORIG 等）已在当前
> compare 引擎中合并/重命名为：IDENTICAL / ORIG_INJECT / MODIFIED /
> MERGE / SPLIT / NEW / DELETE。
>
> 真相参考：
> - 代码：`core/asset_info_schema.py::compare()` 及其三步漏斗
> - skill：`skills/pipeline_compare_asset/SKILL.md`
> - 全局：`AGENTS.md`
>
> 保留本文是因为推导过程与分类哲学仍有参考价值。新工作请以代码和上述
> SKILL.md 为准。

## 1. 目标

给定一组**已绑定的 rig mesh**（权重库，含 SkinCluster + BlendShape + 层级结构），和一批**新进来的 asset mesh**（来自 tex/asset 文件），自动找到最优配对方式并传递绑定信息。

**最终目标**：rig 文件中 `cache` 组内的资产与 tex 的 `cache` 组内的资产完全一致 — 层级、名称、点数、点序号、mesh 完全一一对应，没有任何多余或缺失。

---

## 2. 现有系统能力

| 模块 | 位置 | 能力 |
|------|------|------|
| `pipeline_compare_asset` | `skills/pipeline_compare_asset/` | DCC 无关的 JSON-vs-JSON 对比，全局 KDTree 配对，输出 instructions JSON |
| `asset_info_schema.compare()` | `core/asset_info_schema.py` | 核心匹配引擎：KDTree 几何配对 + 名称奖励 + actionability 分级 |
| `maya_compare_mesh_topology` | `skills/maya_compare_mesh_topology/` | Maya 场景内拓扑对比 |
| `maya_sync_rig_incremental` | `skills/maya_sync_rig_incremental/` | 消费 instructions，执行 Fast Path / Spatial Voting / BS 传递 |

**现有 actionability 分级**：
- `IDENTICAL`：点数一致 + 位置阈值 < 0.0001cm → 直接复用（orig 注入）
- `AUTO_SAFE`：点数一致 + 宽松匹配 ≥ 95% + max_offset < 0.005cm → 注入顶点 + UV
- `REVIEW`：点数不同 或 匹配率不足 → 进入 Voting Pool（空间投射）
- `SPATIAL_VOTING`：无配对源 → 全局 SuperMesh 投射
- `DELETE`：仅存于 rig 侧 → 删除

---

## 3. 配对策略分层（从廉价到昂贵）

### Tier 0: 完美匹配（零成本复用）

**条件**：名称一致 + 点数一致 + 逐点位置 < 0.0001cm

**操作**：
- 保留原 deformer stack 不动
- 仅调整 DAG 层级/命名（去 RIG_ 前缀，移到正确父级）
- 注入新 UV（如有变化）

**现有实现**：`IDENTICAL` → `deferred_ops`（orig 注入 + UV 更新）✅ 已有

---

### Tier 1: 改名/移层级（名称不同但几何完全一致）

**条件**：名称不同 但 点数一致 + 逐点位置 < 0.005cm

**判定方式**：
1. 全局 KDTree 配对（不依赖名称）→ 找到几何重合的 pair
2. 逐点 index-to-index 对比确认位置阈值

**操作**：同 Tier 0（orig 注入），额外需要 rename

**现有实现**：`AUTO_SAFE` 路径 ✅ 已有（KDTree 配对 + 名称奖励解决歧义）

---

### Tier 2: 点序变化（点数一致，位置能对应但序号乱了）

**条件**：点数一致 + KDTree 逐点匹配率 ≥ 95% + 但 index-to-index 对比失败（max_offset 大）

**判定方式**：
1. 点数一致 → 尝试 index-to-index 对比
2. 失败 → 用 KDTree 对每个新顶点找最近旧顶点，建立 `new_idx → old_idx` 映射表
3. 验证映射是否为双射（1-to-1），且最大距离 < 0.005cm

**操作**：
- 按映射表重排 SkinCluster 权重：`new_weights[new_i] = old_weights[mapping[new_i]]`
- 按映射表重排 BlendShape delta：`new_delta[new_i] = old_delta[mapping[new_i]]`
- 不需要空间插值，精确传递

**现有实现**：❌ 缺失 — 当前 `AUTO_SAFE` 要求 max_offset < 0.005，如果点序乱了会被降级到 REVIEW

**需要新增**：在 `_compare_positions` 失败后，增加 KDTree 双射映射检测

---

### Tier 3: 局部修改（点数不同，部分区域可精确匹配）

**条件**：名称配对成功 + 点数不同 + KDTree 匹配率 ≥ 75%

**典型场景**：
- 衣服删了袖子（点数减少，剩余部分位置一致）
- 模型加了细节（点数增加，大部分位置一致）

**判定方式**：
1. KDTree 全局配对确认是"同一物体"
2. 用 `query_ball_point(r=0.005)` 找到新 mesh 中能精确匹配旧 mesh 的顶点子集
3. 匹配到的部分：直接按映射传递权重/BS
4. 未匹配的部分：从已匹配的邻居扩散（Laplacian）或从源 mesh 表面投射（重心插值）

**操作**：
- 匹配子集：精确映射传递（同 Tier 2）
- 未匹配子集：
  - 优先：定向投射到配对源 mesh 表面（trimesh nearest + 重心插值）— 已有 `_directed_weight_transfer`
  - 兜底：Laplacian 扩散（匹配点作为锚点，未匹配点作为 free）

**现有实现**：⚠️ 部分有 — `_directed_weight_transfer` 做了表面投射，但没有"精确子集 + 扩散"的混合策略

**需要增强**：
- 对匹配子集用精确映射（不走插值）
- 对未匹配子集用 Laplacian 扩散（以匹配子集为锚点）

---

### Tier 4: 合并/拆分（mesh 数量变化）

**条件**：
- 新 mesh 在 KDTree 中匹配到多个旧 mesh（合并：两只鞋 → 一只）
- 或一个旧 mesh 被多个新 mesh 匹配（拆分：一只 → 两只）

**典型场景**：
- 两只鞋合并为一个 mesh
- 一件连体衣拆分为上衣 + 裤子
- 一只手的模型需要从两只手的 rig mesh 获取权重

**判定方式**：
1. KDTree 配对时，一个 A 侧 mesh 的顶点分散命中多个 B 侧 mesh → 合并
2. 多个 A 侧 mesh 的最佳匹配指向同一个 B 侧 mesh → 拆分
3. 当前贪心 1-to-1 配对会丢失这种关系 → 需要增加 1-to-N / N-to-1 检测

**操作**：
- 合并场景：多个源 mesh 的权重/BS 按空间位置合并到一个新 mesh
  - 每个新顶点找最近的源 mesh 表面点，取该源的权重
  - 骨骼列表取所有源的并集
  - BS 按源分别投射，合并到同一个 BS 节点
- 拆分场景：一个源 mesh 的权重/BS 按空间位置分配到多个新 mesh
  - 每个新 mesh 的顶点投射到同一个源表面

**现有实现**：⚠️ 部分有 — SuperMesh 空间投票制天然支持多源合并（所有 rig mesh 拼成一个大 mesh 做投射），但缺少显式的 1-to-N 配对识别

**需要增强**：
- 配对阶段：允许 1-to-N 和 N-to-1 关系
- 执行阶段：已有 SuperMesh 机制基本够用，但需要优先使用定向投射（限定源范围）

---

### Tier 5: 全局空间传递（兜底）

**条件**：完全找不到几何配对（新增 mesh），或匹配率 < 75%

**操作**：
- 所有 rig mesh 构建 SuperMesh
- 新 mesh 每个顶点投射到 SuperMesh 表面
- 法线过滤 + 层深度判断 → 选择最合理的源面
- 重心插值权重 + BS delta

**现有实现**：✅ 已有（Spatial Voting 全流程）

**可选增强**：
- 对投射距离过远的顶点（无有效源面），用 Laplacian 扩散从已投射的邻居扩散
- 即：投射成功的点作为锚点，投射失败的点作为 free → Jacobi 扩散

---

## 4. 配对决策流程图

```
新 mesh 进入
    │
    ├─ 在 instructions JSON 中有明确配对？
    │   ├─ YES → 使用指定的 source_dag
    │   └─ NO  → 全局 KDTree 配对
    │
    ▼
检查配对质量
    │
    ├─ 点数一致？
    │   ├─ YES → index-to-index 位置对比
    │   │   ├─ max_offset < 0.0001 → [Tier 0] IDENTICAL
    │   │   ├─ max_offset < 0.005  → [Tier 1] AUTO_SAFE (orig 注入)
    │   │   ├─ 失败 → KDTree 双射映射检测
    │   │   │   ├─ 映射成功 (≥95% 双射) → [Tier 2] REORDER (映射传递)
    │   │   │   └─ 映射失败 → [Tier 3] 定向投射
    │   │   └─
    │   └─ NO → 点数不同
    │       ├─ KDTree 匹配率 ≥ 75%？
    │       │   ├─ YES → [Tier 3/4] 局部匹配 + 定向投射/扩散
    │       │   └─ NO  → [Tier 5] 全局 SuperMesh 投射
    │       └─
    └─
无配对 (only_a)
    └─ [Tier 5] 全局 SuperMesh 投射
```

---

## 5. 传递内容清单

| 传递项 | Tier 0-1 | Tier 2 | Tier 3-4 | Tier 5 |
|--------|----------|--------|----------|--------|
| SkinCluster 权重 | 保留原始 | 按映射精确传递 | 匹配子集精确 + 剩余基于稀疏矩阵 Laplacian 扩散 | 全量空间重心插值 |
| BlendShape delta | 保留原始 | 按映射精确传递 | 匹配子集精确 + 剩余基于稀疏矩阵 Laplacian 向量扩散 | 全量空间重心插值 |
| BlendShape 权重值 | 保留原始 | 直接复制 | 直接复制 | 直接复制 |
| BS 上游驱动连接 | 保留原始 | 重建连接 | 重建连接 | 重建连接 |
| Live Target (活体BS) | 保留原始 | 深度复刻 | 深度复刻 | 深度复刻 |
| UV | 注入新 UV | 注入新 UV | 注入新 UV | 从 tex 数据写入 |
| 层级/命名 | 调整 | 调整 | 新建 | 新建 |

---

## 6. 需要新增/修改的模块

### 6.1 修改：`core/asset_info_schema.py`

- `_judge_action()` 增加 `REORDER` 级别（点数一致但点序不同）
- `_global_kdtree_match()` 增加 1-to-N / N-to-1 检测（返回 `merge_groups` 和 `split_groups`）
- `_compare_positions()` 失败后增加 KDTree 双射映射检测

### 6.2 修改：`maya_sync_rig_incremental`

- Tier 1 路径增加 `REORDER` 处理：建立映射表 → 按映射传递权重/BS
- Tier 3 路径增加"精确子集 + Laplacian 扩散"混合策略
- Tier 4 路径增加 N-to-1 / 1-to-N 的显式处理（限定源范围的定向投射）

### 6.3 新增：拉普拉斯扩散求解工具 (`laplacian_diffuse.py`)

基于网络研究与本地测试（`test_search.py`），对于网格上 Dirichlet 边界条件（固定锚点）的拉普拉斯扩散方程 $\Delta W = 0$，**弃用低效且容易数值发散的 Numpy Jacobi 迭代算法**，必须采用数学上更严谨、计算速度更快的稀疏矩阵直接求解器 `scipy.sparse.linalg.spsolve`。

**性能验证结论**：
在 2500 顶点的局部网格测试中，**Numpy Jacobi 迭代** 耗时数秒且仅达到近似平衡；而构建 $(I - A_{free}) x_{free} = A_{anchor} x_{anchor}$ 并使用 **Scipy Sparse Solver (spsolve)** 耗时仅 **0.0169秒**，且权重结果处于完美的理论最小值（全局最小 Dirichlet 能量）。

放置位置：`core/laplacian_diffuse.py`

**执行签名**：
```python
def laplacian_diffuse_scipy(adjacency, anchor_indices, anchor_weights, num_verts, num_joints):
    \"\"\"基于 scipy.sparse 与 spsolve 的精确拉普拉斯权重扩散，耗时毫秒级，返回 (num_verts, num_joints) 权重矩阵\"\"\"
```

---

## 7. 边界情况推演

### 7.1 一只手 vs 两只手

**场景**：新 mesh 是完整身体（含两只手），rig 库中手是单独的 mesh（左手、右手各一个）

**处理**：
- KDTree 配对时，新 mesh 的顶点会命中多个 rig mesh（左手、右手、身体等）
- 进入 Tier 4（合并场景）或 Tier 5（SuperMesh）
- SuperMesh 天然包含所有 rig mesh，投射时每个顶点自动找到最近的源面
- 法线过滤 + 层深度确保不会从"内侧"错误投射

**反向场景**：新 mesh 只有一只手，rig 库中是两只手的完整 mesh
- KDTree 配对时，新 mesh 只匹配到 rig mesh 的一部分顶点
- 进入 Tier 3（局部匹配）→ 定向投射到配对源的表面
- 由于只有一只手，投射距离对另一只手的区域会很远，自然被过滤

### 7.2 衣服删了袖子

**场景**：衣服删了袖子（点数减少，剩余部分位置一致）
- KDTree 配对时，新 mesh 完美匹配旧 mesh 的一个子集
- 进入 Tier 3（局部匹配）
- 匹配的子集：直接用 KDTree 的 1-to-1 双射建立索引映射，精确复制权重和 BS。
- 这是绝对无损的传递。

### 7.3 眼皮与眼睫毛防串缝问题（极限靠近或穿插）

**场景**：角色闭眼时，上下眼皮和上下眼睫毛物理距离极度接近甚至穿插。传统的基于距离的 KDTree 空间查询极易发生“上睫毛吸取下眼皮权重”的致命错误，导致睁眼时睫毛拉丝撕裂。

**解法：法线夹角过滤 (Pass 1 核心价值)**
- 我们的解算器不仅判断距离，更基于**三轮级联投射**。
- 上睫毛的法线朝向斜上，下眼皮的法线朝向斜下。
- **Pass 1 (射线双向投射)** 会严格计算目标点法线与击中面法线的夹角。如果夹角大于设定的 `max_normal_angle_deg`（默认 90 度），该击中面将被**直接判定为非法并抛弃**。
- 这意味着，哪怕上睫毛深陷在下眼皮内部（距离为 0），解算器也会因为法线相反而无视下眼皮，最终正确穿透并找到上方表面法线一致的上眼皮！
- 这是纯空间解法中解决眼皮/嘴唇等缝隙问题的终极杀招。

**场景**：rig 中衣服 5000 点，新 asset 衣服 4200 点（删了袖子）

**处理**：
- 点数不同 → 不走 Tier 0/1/2
- KDTree 匹配率高（4200/5000 = 84%）→ Tier 3
- 4200 个新顶点中大部分能在旧 mesh 表面找到精确对应 → 定向投射
- 少量边缘顶点（袖口截断处）投射距离稍大但仍在阈值内

### 7.3 鞋子合并

**场景**：rig 中左鞋 + 右鞋各一个 mesh，新 asset 合并为一个 mesh

**处理**：
- KDTree 配对时，新 mesh 的顶点分散命中左鞋和右鞋两个 rig mesh
- 1-to-N 检测识别出这是合并场景
- 执行时：两个源 mesh 都参与定向投射，骨骼列表取并集
- 或直接进入 SuperMesh 投射（已有机制天然支持）

### 7.4 完全没改（理想情况）

**场景**：tex 和 rig 的 cache 组完全一致

**处理**：
- 所有 mesh 都是 `IDENTICAL`
- 仅做 DAG 层级/命名调整
- 零权重计算，最快路径

### 7.5 名称改了但 mesh 没动

**场景**：资产改了组名或 mesh 名，但几何完全一致

**处理**：
- 名称不匹配 → 但 KDTree 几何配对成功
- 逐点对比 → max_offset < 0.0001 → `IDENTICAL`
- 走 Tier 0 路径，额外 rename

---

## 8. 性能考量（顶尖影视级极限架构）

由于我们的定位是**行业顶尖 CG 团队**，架构设计的核心哲学从“自动化极速跑批”转变为**“不计计算成本、追求绝对极限形变质量”**。
- **配对精度优先**：在点序重排（REORDER）的双射检测中，放弃使用贪心 KDTree。全面启用基于 `scipy.optimize.linear_sum_assignment` 的全局最优二分图匹配（匈牙利算法）。即便面对百万顶点可能需要解算数小时，也必须确保找出全局代价最小的唯一映射，杜绝贪心算法带来的局部错乱。
- **平滑精度优先**：扩散算法全面放弃一阶图拉普拉斯。引入基于表面测地距离的 **Cotangent Laplacian (余切拉普拉斯)** 并求解 **Bounded Biharmonic Weights (双调和权重, $\Delta^2 W = 0$)**。计算刚度矩阵耗时巨大，但换来的是布线无关、绝对丝滑的 $C^1$ 级物理过渡。
- **插值精度优先**：废弃 `trimesh.nearest` 表面投影。全面引入 **3D Mean Value Coordinates (均值体积坐标)** 或调和四面体笼计算，彻底解决多层衣物、内脏口腔的穿透问题。
- **总体**：单角色高精度同步可能耗时数十分钟甚至数小时，但产出的绑定质量将处于学术与工业理论的巅峰。

---

## 9. 实施优先级

| 优先级 | 任务 | 影响 |
|--------|------|------|
| P0 | 提取 Laplacian 扩散为可复用工具函数 | 为 Tier 3/5 增强提供基础 |
| P1 | 增加 Tier 2 (REORDER) 检测与传递 | 覆盖"点序变化"场景 |
| P2 | Tier 3 增强：精确子集 + Laplacian 扩散混合 | 覆盖"局部修改"场景 |
| P3 | 配对阶段增加 1-to-N / N-to-1 检测 | 覆盖"合并/拆分"场景 |
| P4 | Tier 5 增强：投射失败点用 Laplacian 兜底 | 提升全局投射质量 |

---

## 10. 不变量（Invariants）

无论走哪条路径，最终结果必须满足：

1. `cache` 组下的 mesh 数量 = tex 侧 mesh 数量（不多不少）
2. 每个 mesh 的点数 = tex 侧对应 mesh 的点数
3. 每个 mesh 的顶点位置 = tex 侧对应 mesh 的顶点位置
4. 每个 mesh 的 UV = tex 侧对应 mesh 的 UV
5. 每个 mesh 的层级路径 = tex 侧对应 mesh 的层级路径
6. 每个 mesh 的名称 = tex 侧对应 mesh 的名称
7. 所有有 SkinCluster 的 mesh 权重和 = 1.0（归一化）
8. 所有 BlendShape target 名称、权重值、驱动连接完整保留
9. Live Target 的活体连接完整重建（含蒙皮克隆）

---

## 11. 与现有流程的关系

```
[Blender 侧]                    [对比引擎]                     [Maya 侧]
blender_build_asset_info  ──→  pipeline_compare_asset  ──→  maya_sync_rig_incremental
       ↓                              ↓                              ↓
  _asset_info.json              instructions.json            执行配对 + 传递
  (mesh 几何 + UV + 材质)       (每个 mesh 的 action)        (Tier 0~5 分层处理)
```

**本文档的改动不改变这个流程**，只是：
1. 对比引擎输出更精细的 action 类型（增加 `REORDER`、`MERGE`、`SPLIT`）
2. 同步引擎消费新 action 类型，增加对应处理路径
3. 在 Tier 3/5 中引入 Laplacian 扩散作为增强手段

---

## 12. 反驳与风险

| 风险 | 应对 |
|------|------|
| 双调和权重扩散 (BBW) 的质量矩阵极其庞大 | 使用超大内存服务器（如 256GB+ RAM 节点）专供后台 Celery 队列进行解算。 |
| 匈牙利算法在百万面资产上发生 O(N³) 灾难计算耗时 | 可通过将模型先切分为大拓扑块（Clustering/Voxelization）再在块内进行局部最优匹配进行优化，保证质量不降。 |
| 1-to-N 配对可能误判（两个不相关的 mesh 恰好空间重叠） | 要求匹配率 ≥ 30% 才认为有关联，且用名称奖励辅助 |
| 合并场景下 BS 传递复杂（多源 BS 合并到一个节点） | 每个源的 BS 独立创建为单独的 blendShape 节点，不强制合并 |
| 动画师导出时带了微小 Pose 导致所有匹配失效 | 在几何配对前，前置引入 **Non-Rigid ICP (非刚性迭代最近点)**。将旧模型当做弹性体吸附贴合到新模型上再进行采样，免疫 Pose 污染。 |

---

## 13. 强建议与规范执行标准

根据我们的定位与追求极限的核心理念，无论计算代价多高，任何下游工程师的实装必须严格遵守以下执行规范：

> [!IMPORTANT]
> **🔴 强制质量红线（绝对最优解）**
> 1. **强制使用全局最优二分图匹配**：在寻找点序双射关系时，绝对禁止使用 KDTree 贪心抢占。必须构建权值距离矩阵，调用 `scipy.optimize.linear_sum_assignment` (匈牙利算法) 找出全局代价最小的唯一解。我们宁愿机器算一天，也不允许一个顶点匹配错位。
> 2. **强制使用余切双调和扩散 (BBW)**：所有针对孤岛顶点、新增拓扑的平滑计算，禁止使用基于一阶图连接的普通拉普拉斯。必须解析网格拓扑计算测地线余切权重 (Cotangent Weights)，构建刚度矩阵求解双调和方程 ($\Delta^2 W = 0$)，以追求绝对无视布线乱象的 $C^1$ 级丝滑过渡。

> [!TIP]
> **🟢 架构规范**
> 1. **3D 均值坐标体积插值优先 (Volumetric MVC)**：在处理 Tier 5 全局投射或双层复杂结构合并时，废弃 2D 的 `trimesh.nearest` 表面投射。必须在旧空间中生成三维网格（或调和体积坐标），对新顶点进行全方位的立体空间积分采样，彻底消灭表面投影带来的内外穿透死角。
> 2. **执行解耦**：`pipeline_compare_asset` 仅负责输出纯数据字典（Instructions JSON），不得包含任何 DCC 相关的 API 调用。Maya 端仅作为“执行器”，读取 JSON 并盲跑操作。
> 3. **BlendShape 形变向量强制同参扩散**：新增拓扑的 BS Delta，必须复用双调和权重 (BBW) 矩阵的分解结果，对位移向量 X、Y、Z 分别进行极致平滑的向量求解，使其如丝绸般跟随周围肌肉组织运动。
> 4. **完全新资产注入（无视旧UV与材质冲突）**：为了保持资产整体性，在遇到点序重排或局部修改时，绝对不要试图修补或保留旧的局部顶点数据。必须**彻底清理掉 shape 和 orig 的局部点坐标信息**，然后强行将新 ABC 文件中的**全新世界坐标、全新 UV 信息**注入 Orig 节点。合并后的材质冲突直接无视，因为后置的管道工具会使用新的 Blender 映射关系重新赋予材质。
> 5. **法线绝对保真（OpenMaya 强注）**：当针对 `orig` 节点进行拓扑注入时，强制提取 ABC 源模型数据中的 Vertex Normals，通过底层的 `OpenMaya.MFnMesh.setNormals` 一比一写入并锁定（Freeze Normals），实现所见即所得的极高保真度。
> 6. **强制非刚性配准兜底 (Non-Rigid ICP)**：如果检测到新旧模型之间存在系统性的微小偏移（Pose 被动画师改动），必须前置唤醒 Non-Rigid ICP 算法，将旧模型当做柔性体弹射吸附到新模型轮廓上再取样。我们绝不容忍因为美术导出失误导致模型精度下降。

> [!WARNING]
> **🟡 安全审计标准与自动化熔断 (Fail-fast Threshold)**
> 每次执行同步操作前，必须通过 `make_receipt` 进行操作上报，并且将任何超过 10 秒的矩阵运算打上时间戳日志。如果在 Tier 4 的合并拆分投射中，出现了跨层误采样（比如大腿内侧投射到了另一条腿），应立即启动光线投射深度分类（Layer Depth Classification）作为硬性屏蔽墙。
> **熔断机制**：必须在最终报告输出前做统计检查。如果检测到一个 Mesh 有 **超过 50% 的顶点** 是依赖 Laplacian 扩散或 SuperMesh 全局投射兜底算出来的（即未找到 0.01cm 以内的精确匹配源），系统必须拉响警报，强制将 Receipt 标记为 `ERROR` 阻断发布，并提示：“模型拓扑改动超过 50% 阈值，多一半的点无精确来源，防炸毁熔断已触发，请人工校验”。绝对不允许这种面目全非的模型被悄无声息地同步并流向动画环节。
