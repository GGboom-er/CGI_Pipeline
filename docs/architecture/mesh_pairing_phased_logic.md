# Mesh 配对逻辑当前规范

更新时间: 2026-05-14

本文只说明 mesh 如何配对和分类。运行时 Workflow 看 `compare_and_assembly_pipeline_plan.md`；输出契约看对应 API 的 manifest 和 `core.receipt`。

## 1. 核心原则

- 渐进筛选：先走低成本确定性匹配，再分析剩余复杂情况。
- 消耗式匹配：只有达标的 rig mesh 才从候选池弹出；未达标对象留给下一阶段。
- 算法标签和报告字段分层：算法层保留执行需要的细标签，报告层只展示用户能直接判断的四类事实。
- 不引入含糊的 REVIEW / REJECT 作为最终执行标签；每个 source mesh 都应落到明确去向。

## 2. 三步漏斗

### Step 1: cache 下绝对路径匹配

假设资产没改，只是 rig 侧可能加了 `RIG_` 前缀。

```text
for each source_mesh:
  在 rig 池查找去掉 RIG_ 前缀后的同路径 DAG
  -> 找不到：进入 Step 2
  -> 找到但点数不同：进入 Step 3
  -> 找到且点数一致：算逐点距离
       pct_exact = 100% -> IDENTICAL，消耗 rig
       pct_loose = 100% 且 max_dist < 0.005cm -> ORIG_INJECT，消耗 rig
       其他 -> 进入 Step 2，rig 不消耗
```

路径规范化示例：

```text
cache|body_Grp|body == cache|RIG_body_Grp|RIG_body
```

### Step 2: 同点数候选全比对

假设资产改了名字或挪了层级，但几何没动。

```text
for each Step 1 剩余 source_mesh:
  候选 = 未消耗 rig 池中点数严格一致的 mesh
  -> 候选 0 个：进入 Step 3
  -> 候选 >= 1 个：逐个算距离，按 pct_loose 取 Top1
       pct_exact = 100% -> IDENTICAL，消耗 rig
       pct_loose = 100% 且 max_dist < 0.005cm -> ORIG_INJECT，消耗 rig
       其他 -> 进入 Step 3，rig 不消耗
```

### Step 3: 空间分析和复杂分类

输入是 Step 1/2 后仍未配对的 source 池和未消耗 rig 池。

```text
for each 剩余 source_mesh:
  对剩余 rig 做空间 KDTree 匹配
  统计距离 < 0.005cm 的 source 顶点命中率

  命中率 >= 75% -> MODIFIED
  命中率 < 30% -> NEW
  30% <= 命中率 < 75% -> MODIFIED

  一个 source 命中多个 rig -> MERGE
  多个 source 的最佳匹配是同一个 rig -> SPLIT

Step 3 结束后未被消耗的 rig_mesh -> DELETE
```

## 3. 算法标签

| 标签 | 来源 | 含义 | Sync 执行方向 |
|---|---|---|---|
| `IDENTICAL` | Step 1/2 | 点数、点序、位置完全一致 | 保留绑定，只调整层级/命名 |
| `ORIG_INJECT` | Step 1/2 | 全点在 loose 阈值内 | 注入新点位/UV 到 ShapeOrig |
| `MODIFIED` | Step 3 | 原 rig 存在，但拓扑或局部形态变化 | 定向投射或形变继承 |
| `NEW` | Step 3 | source 新增，rig 无对应物 | 新建 mesh 并继承或采样绑定 |
| `DELETE` | Step 3 | rig 独有，source 已不存在 | 清理或保留待审 |
| `MERGE` | Step 3 | 多个 rig/source 关系合并 | 多源权重/BS 关系处理 |
| `SPLIT` | Step 3 | 一个 rig/source 拆成多个目标 | 一源多目标投射 |

这些标签是算法和 sync 的内部执行依据，不等于 `REPORT.md` 的主字段。

## 4. 报告和 sync 分组

报告层使用四类事实字段：

| 报告字段 | 包含 |
|---|---|
| `matched_same` | `IDENTICAL`、`ORIG_INJECT` 等已配对且可接受对象 |
| `matched_different` | 已配对但需要后续处理或审查的对象 |
| `only_source` | source 独有对象 |
| `only_target` | rig/target 独有对象 |

sync 阶段可以继续按执行动作聚合：

```text
pairing_groups:
  IDENTICAL
  ORIG_INJECT
  PAIRED
  UNPAIRED
target_only
```

`pairing_groups` 是同步执行分组，`matched_*` 是报告摘要。两者不能混成一套字段。

## 5. 配置参数

从 rig sync profile 读取：

```text
classification.precision_exact = 0.0001
classification.precision_loose = 0.005
step3.modified_threshold = 0.75
step3.new_threshold = 0.30
```

单位按 Maya 场景厘米口径理解。

## 6. 已落地状态

- 三步漏斗已进入 `cgi_pipeline.core.asset_info_schema` 和 compare/sync 测试。
- `pairing_report.py` 负责把算法结果聚合成报告和审计可用的 source 侧事实。
- `maya_sync_rig_incremental` 消费 compare_result，不在同步阶段重算配对。
- 多源 PAIRED displayLayer 命名已按 source mesh 组合，过长时退到 `A_GRP_Layer`。

## 7. 待继续验证

- 多语义 welded mesh 仍需要 Live BS / skin 继承进一步产品化验证。
- 口腔、嘴唇、牙齿这类近层几何不能只靠静态空间匹配，需要 ROM 或控制器姿态证据。
