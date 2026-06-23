# CGI Pipeline 知识库

本文件是 `docs/ai_startup/` 固定必读包的第 3 份文件，也是当前稳定事实入口。每个任务开工前必须先读 `AGENTS.md` 和 `docs/ai_startup/` 固定必读包；上下文压缩、重新登录或新会话接手后也执行同一顺序。不要从历史任务流水账反推当前规范。

## 维护规则

- 每个任务开始前，AI 必须先主动读取 `AGENTS.md` 和 `docs/ai_startup/` 固定必读包，用本文清理和确认用户需求场景；不要等用户被动要求。
- 当前有效规则写在本文或本文链接的权威文档里。
- 新增稳定规则时，同步更新本文或对应权威文档，并在 `tasks/lessons.md` 追加一条避坑记录。
- 跨 skill 复用的经验和红线必须回归 `skills/build_pipeline_skill/SKILL.md`，单个 skill 文档不能成为唯一知识源。
- `tasks/todo.md` 是任务流水账和未完成项，不是规范来源。
- `tasks/lessons.md` 是问题索引库；按关键词 `rg` 查询，不整篇当启动上下文。
- `docs/archive/`、`backups/`、`projects/`、`.info/` 都是历史或运行产物，不指导当前实现。

## 恢复顺序

1. `AGENTS.md`：仓库级配置入口，确认必须读取启动包。
2. `docs/ai_startup/00_STARTUP_PROTOCOL.md`：AI 会话启动、问题闭环和经验归档归属。
3. `docs/ai_startup/01_PROJECT_FOUNDATION.md`：项目全局框架、八个视角、新手地图和能力边界。
4. `docs/ai_startup/02_KNOWLEDGE_BASE.md`：当前最小事实集。
5. `docs/ai_startup/03_DOC_INDEX.md`：文档地图。
6. 相关专项文档或 `skills/{skill_id}/SKILL.md`。
7. 用 `rg` 在 `tasks/lessons.md` 中查本次问题关键词。

## 当前运行事实

- 运行平台是 Windows、Maya 2025、Blender 4.x、conda 环境 `cgi_pipeline`。
- 发布盘和服务器源资产只读；所有 AI 产物写任务沙盒：`projects/{project}/{YYYYMMDD_HHMMSS}_{asset}`。
- 机器中间产物统一放沙盒 `.info/`；用户只读 `REPORT.md` 和最终 `.ma`。
- DCC 任务异步执行，提交后必须轮询终态；修改 `core/`、workflow 或 skill 运行代码后要重启对应 worker。
- `resolve_asset_files` 是主 workflow 的输入门：缺 tex/rig 时返回 `ERROR` 和 `missing_inputs`，workflow 立即中断，后续 DCC skill 不运行。
- 不含 `resolve_asset_files` 的源文件型 workflow 若要支持只传资产名，必须在 workflow JSON 声明 `source_resolution`，由运行层按资产名解析源文件。
- workflow 分段只允许首段默认继承全局 `source_path`；后续 DCC 段必须显式写 `source_path`，否则从空场景开始，避免 Maya 误打开 Blender 源文件。
- Warm Pool worker 收到空 `source_path` 时必须显式新建空场景，不能沿用上一次任务遗留在 DCC 内存里的场景。
- Alembic archive 顶层 `ABC` 不是业务层级，读 ABC 生成 DAG 时必须剥离，Maya 拼装结果不应出现 `|ABC` 顶层。
- 材质链只把真实 color/albedo/diffuse 贴图接入 color；AO、normal、roughness 等非 color 贴图不得被强行连接成 color 贴图。

## 后续开发基线

- 先以 `AGENTS.md` 和 `docs/ai_startup/` 固定必读包恢复上下文，再打开对应专项权威文档；不要从 `tasks/todo.md` 或历史报告反推当前规则。
- 运行时边界只改 `docs/architecture/pipeline_runtime_contract_v1.md`。
- skill 生成、更新、输出字段和报告 Details 规则只改 `skills/build_pipeline_skill/SKILL.md`。
- ABC/DAG、材质贴图语义、路径中间产物、标准执行记录这类跨 skill 规则优先写 `skills/build_pipeline_skill/SKILL.md`，再按需同步专项文档。
- 报告渲染规则只改 `docs/architecture/runtime_task_report.md`。
- 对比、拼装、层级修复、displayLayer 和主 workflow 规则只改 `docs/architecture/compare_and_assembly_pipeline_plan.md`。
- mesh 配对算法和算法标签只改 `docs/architecture/mesh_pairing_phased_logic.md`。
- Skin/BS/动态 BlendShape 继承专题只改 `docs/architecture/deformation_inheritance_plan_and_validation.md`。
- 当前测试入口只改 `tests/README.md`。
- 代码改动若改变 workflow、skill `output`、报告字段、测试门禁或主资产验证结论，必须同步更新本文和所属专项文档。

## Skill 契约

- 唯一规范是 `skills/build_pipeline_skill/SKILL.md`。
- 每个 `skills/{skill_id}/SKILL.md` frontmatter 必须包含 `tier` 和 `pairs_with`。
- `tier` 只允许 `read`、`write`、`destructive`；MCP 动态 tool annotations 只读这个字段，不再按 `skill_id` 前缀猜测。
- `pairs_with` 只写稳定关联的已存在 skill_id；没有稳定链路时写 `[]`。
- 每个 skill 必须通过 `core.receipt.make_receipt(...)` 返回标准执行记录。
- workflow 只读取上游 `output` 字段，不能读取 `input`、`summary`、`items`、`report_sections` 或报告正文。
- `input` 只记录实际生效参数，用于审计和排障。
- `output` 是下游连接数据和报告 `Details` 的唯一事实来源。
- 文件产物优先写 `output.output_path`；语义别名如 `abc_path`、`materials_path` 只在确实能减少歧义时保留。
- `summary/items/report_content` 是历史兼容字段；新增或重构 skill 不再设计这些字段。
- 旧 `report_sections` 兼容字段已移除；compare/sync/hierarchy/resolve 明细统一写入 `output.*_items` 或语义化 `output` 列表字段。

## 报告契约

- 唯一用户报告是 `{sandbox}/REPORT.md`。
- 报告使用 Markdown 正文，不依赖 HTML 折叠。
- 最终报告不得出现 `report:block` marker、`Raw Detail`、独立 `Input` / `Output` 大块或完整机器 JSON。
- 每个 step 只暴露：`Step N/Total | skill_name | STATUS | elapsed`，展开后进入 `Details`。
- 报告层只渲染 receipt `output` 里的核心统计、路径和短明细；大对象写 `.info`，报告只给路径和计数。

## 主 workflow

当前主线是 `tex_to_rig_verify_and_sync`：

```text
resolve_asset_files
-> blender_export_abc
-> blender_extract_materials
-> maya_check_asset_hierarchy
-> maya_fix_asset_hierarchy
-> maya_compare_asset_in_scene
-> maya_sync_rig_incremental
-> maya_check_asset_hierarchy
-> maya_fix_shape_names
-> maya_apply_materials
-> maya_compare_asset_in_scene
-> save_scene
```

规则：

- 拼装前 check 一次，fix 一次；拼装后再 check 一次。
- `|*|geo` 这类旧绑定根必须迁移为 `|Group|Geometry|RIG_geo`，新 cache 输出到 `|Group|Geometry|cache`。
- `maya_fix_asset_hierarchy` 消费前置 check 的 `output.result`，不自行重新发现另一套目标。
- `maya_sync_rig_incremental` 消费前置 compare_result；同步阶段不能独立重算另一份对比。
- 多源配对 displayLayer 用 source 资产 mesh 名组合；过长时退到 `A_GRP_Layer`。单源 PAIRED 使用 `资产名_Layer`。

## 最近批量验证结论

- `cdfbaixingG` 已跑通主 workflow，并用于报告格式、层级修复和配对命名验证。
- `mihouwang`、`ciweiguai`、`maYouB` 已跑通过早期真实巡航，覆盖基础拼装、材质和旧 `|*|geo` 归一场景。
- `cdfbaixingJ`、`cdfbaixingL` 于 2026-05-14 跑通主 workflow：均 `WORKFLOW_SUCCESS`，post compare 为 22/22 通过配对、0 阻断差异。
- `cdfbaixingH` 当前缺 `rig/rigMaster`，跑主 workflow 时应在 Step 1 中断并报告缺失路径。
- `xycrowdbig` 当前不符合主 workflow 输入约定，缺 `tex/texMaster` 与 `rig/rigMaster`，跑主 workflow 时应在 Step 1 中断。
- `xycrowdbig` 于 2026-05-14 跑通 `blender_to_maya_full_build`：按资产名解析到 `uv/uvMaster` `.blend`，构建 14 mesh，层级为 `|Group|cache` 且无 `|ABC` 顶层；材质赋予 5 个纯色材质球，不创建 AO file 节点；最终 `.ma` 输出到沙盒根目录，UV 检查为 14/14 `map1` 有数据、0 个需清理。
- Live BS / 动态 BlendShape 产品化仍是未完成专题，不要当作已经完全并入主 workflow。
- cdfBaiXingG 当前嘴唇/Live BS 复刻对象必须锁定为 `M_Head_base -> cdfBaiXingG_body1_live_0`；`cdfBaiXingG_body1` 只用于最终可见输出验证，不作为本轮映射目标。
- cdfBaiXingG 嘴唇近层串权已做 topology support 参数回归与逐点审计：13 个 motion accepted 进一步收紧为 11 个 strict write 点；Maya 前台试写确认这 11 点会影响 final body（max delta 0.3036cm），但不能靠调参解决整圈张嘴粘连，后续应查 Live BS/拓扑表情表达或 residual 约束。
- cdfBaiXingG v019 追加审计确认 final body 与 live target 逐点一致，deformer 链未断，整体 upper/lower lip separation 不弱于 source；下一步应查局部 residual/correspondence，不再扩大 skin patch。
- cdfBaiXingG v019 strict Jaw25 residual 审计确认 `M_Head_base -> cdfBaiXingG_body1_live_0` 的合法 corrective 候选为 0；risky 高残差点不能生成 BS target，必须先提高局部 correspondence 可信度。
- cdfBaiXingG v021/v022 skin-only 复核确认：语义拓扑候选 113 点宽写后 37 点改善、76 点退化；v022 仅保留实际写回正收益 26 点，neutral 0 偏移但整体视觉未闭环。后续不能继续扩大 skin patch，必须做 LBS 矩阵级权重反求或多姿态局部 correspondence 重建。
- cdfBaiXingG v023 验证 LBS 矩阵级反求：`point * bindPreMatrix * worldMatrix` 可重建 Maya Jaw25 输出（max≤7.84e-06）；写入 78 个反求 skin 点后实际 78/78 改善、0 退化，ROI p95 0.0414→0.0341。该方向成立，但当前仍是 Jaw25 单姿态结果，产品化前要做多姿态回归。
- cdfBaiXingG v024 视觉门控确认：射线/相反法线/折叠检测可减少部分坏形，但不能单独判定 owner；已沉淀 `core/visual_surface_gate.py`，下一步应做 patch-level constrained LBS inverse，把 ray/edge/normal 作为 penalty，而不是继续逐点回滚。
- cdfBaiXingG v025/v026 确认：patch-level smooth-only 候选未超过 v024，不能写 Maya；v026 离散融合已实际写回 `cdfBaiXingG_body1_live_0_skinCluster` 78 点，权重 roundtrip=0，实际 Jaw25 mean 0.0514→0.0390、p95 0.1102→0.0937、visual fail 993→992。收益为正但仍非最终解决，下一步必须用 Maya 实际依赖图多姿态输出做验收/回归。
- cdfBaiXingG v027 权重源复核：旧 target matrix probe 不能再评估当前场景，fresh target probe 才能重建当前 skinCluster；source 侧 `M_Head_base` 当前场景 envelope=0 采样会重建失败，应继续使用 v023 已验证 source inputGeometry。v027 只做 weight-only source prior 小步回拉，78 点 roundtrip=0，skin-only p95 0.06635→0.05724，说明正确方向是 source/current 双先验 + fresh target matrix + actual graph 验收。
- cdfBaiXingG v028/v029 手绘对照确认：用户手绘权重可作为“低穿插参考”，但不能直接当 source 真值；LBS source pose error 与视觉穿插目标会冲突，后续 mouth skin 必须拆成 owner label、视觉风险、pose error、source/current 先验的多目标验收。
- cdfBaiXingG v031/v032 skin-only 复核确认：连续面/穿插分类必须先看 mesh graph 拓扑距离，法线/射线只作风险证据；硬禁上下唇 influence 的 patch solver 使 skin-only p95 变差，不能写 Maya。v032 alpha=1.0 离线 skin-only 误差≈0，但 2026-05-15 在 MCP foreground 7099 实写 78 点后，Jaw25 actual visual fail 813→894、collision risk 0→2；实际依赖图反证 v032，不通过。后续候选必须 actual-graph-in-loop 回归验收。
- cdfBaiXingG v032 根因审计确认：v032 已回滚；坏形核心不是 skinPercent 写错，而是 source-prior 将 11 个写入点做了上下唇 dominant-family flip，其中 `upper_lip->lower_lip` 8 点平均位移 0.88cm、最大 1.294cm。候选生成还使用了 stale `fresh_matrix` 作为 current，后续必须用 Maya 写前回读 `before_rows` 做门控，并保存每点 source provenance。
- cdfBaiXingG 嘴唇 clean baseline 已切回 `v020_semanticLiveBS.ma`：重新导出 `v020_clean_actual_probe.npz` 时发现 source LBS 重建曾因 `bindPreMatrix` 物理索引/逻辑索引错位而失败；改用 `MFnSkinCluster.indexForInfluenceObject()` 后 source neutral LBS max≈1.7e-06、target jaw25 p95≈0.001。clean quick sweep 曾在旧 family 口径下 accepted=0；修正 DAG influence 只按叶子 joint 名匹配后 accepted=2，但仍不足以写 Maya，说明旧 v027/v032 写入候选主要来自污染/错误数据，不应继续沿用。
- cdfBaiXingG 9194/1165 点级归属审计确认：完整 DAG 路径匹配会让 `head` 命中所有带 `M_Head_*` 祖先的 lip 关节，必须只用 influence leaf 名做 family scoring。`v020_lip_sheet_ownership_probe.json` 显示纯 source unary 下 9194 最近证据偏 lower，但 source-unary + target 拓扑 MRF 在 pairwise≥0.8 时可把 9194 翻回 upper；后续不能再把 target 当前错误权重当 seed，应改为 source 权重 unary + target mesh graph 连续性求 owner。
- cdfBaiXingG Graph Cut owner solver 已抽象为 `core/source_owner_solver.py`：真实 clean v020 probe 中 9194 在 pairwise=0 为 lower、pairwise≥0.8 翻为 upper；9150 始终 lower 且为 contact 风险点；1165 对 motion_weight 敏感，必须进入逐点回归。下一步只把 owner label 作为 family gate 生成权重候选，不直接写 Maya。
- cdfBaiXingG 测地方案复核确认：需要测地信息，但先作为 owner confidence/prior，不替代 source unary + Graph Cut。`core/source_owner_solver.py` 已支持默认关闭的 `geodesic_prior_weight`；真实 clean v020 上 Dijkstra 与 Graph Cut 一致率 0.9857，9194/1165/9150 与 heat method 同向；potpourri3d heat 后端保留为诊断，不作生产硬依赖。
- cdfBaiXingG 权重分离复核确认：`tools/source_owner_weight_separation_probe.py` 显示映射层和 owner-selected source 权重层已能分开上下唇；9194 当前 target 权重几乎全是 lower_lip，但 owner 为 upper_lip，选中 source 8960 为 upper_lip 1.0。全 ROI owner/source family match=0.9118，lip match=0.9421；safe candidate 仅 ROI 0.4723 / lip 0.4313，不能整圈写 Maya，只能进入候选与 actual graph 验收。
- cdfBaiXingG v035/v036 actual graph 验收确认：`cdfBaiXingG_body1_live_0_skinCluster.envelope=0` 会造成“权重写入成功但点位不动”的假阴性；统一 `envelope=1` 后，owner-gated 92 点写入使 write 点 motion error mean `0.1380->0.0237`、p95 `0.5903->0.0648`，9194 `1.6578->0.0629` 且无新增 collision。v036 已另存为 `ysj_chr_cdfBaiXingG_rig_rigMaster_v036_ownerGatedSkin_envelopeOn_jaw25.ma` 供人工复验；剩余高误差点多为 unsafe_base，不能盲目扩大写入。
- cdfBaiXingG v037/v038 actual graph 验收确认：v037 放行写前已有 collision risk 的点，motion 改善但实际图接触变差，ROI collision `38->41`、write collision `9->12`，不推荐；v038 回滚 8 个实际新增 collision 点后 accepted=119、rollback=8、write p95 `1.2279->0.0761`、ROI collision `38->31`、新增 accepted collision=0。当前 skin-only 推荐复验场景为 `ysj_chr_cdfBaiXingG_rig_rigMaster_v038_collisionFilteredSkin_envelopeOn_jaw25.ma`；9346 这类“motion 可改善但会造 collision”的点必须保留回滚，后续进入 patch-level 多目标求解。
- cdfBaiXingG v039/v040 patch-level actual graph 验收确认：v039 单点 9346 patch solver 通过实际图，9346 `1.6672->0.5604` 且 clear->clear；v040 扩展同 patch 写 9337/9340/9346 三点，Jaw25 ROI mean `0.02533->0.01834`、p99 `0.25313->0.19381`、max `1.66716->0.86657`，accepted collision `0->0`，visual fail `246->232`。但多姿态回归发现 9340 在 jaw 5/10/15 会新增 collision_risk，v040 不作为最终推荐。
- cdfBaiXingG v041/v042 多姿态 skin 回归确认：v041 回退 9340 后只写 9337/9346，已写点无新增 collision，但 v038 基础权重在低角度仍有 ROI collision 增量；v042 继续回滚 9139/9177/9344/9380 到原始权重，jaw 5/10/15/25 ROI collision 分别为 `48->46`、`49->44`、`46->39`、`38->30`，source drift=0、neutral delta=0、new accepted collision=0。v042 是保守安全对照版本，`ysj_chr_cdfBaiXingG_rig_rigMaster_v042_lowAngleRollbackSkin_envelopeOn_jaw25.ma` 保留供回退。
- cdfBaiXingG v043 contact-gated alpha 回归确认：新增 `core/multipose_contact_alpha.py`，对 `9139/9177/9340/9344/9380` 在 v042 safe 与 v038/v040 risk 之间做多姿态 alpha 搜索，得到 `0.35/0.75/0.5/0.5/0.75`；MCP foreground 7099 写入 7 点并真实采样 0/5/10/15/20/25/30 度，`source drift=0`、`neutral delta=0`、`new accepted collision=0`。Jaw25 ROI mean `0.02533->0.01922`、max `1.66716->0.86657`，collision `38->30`，当前推荐人工复验场景为 `ysj_chr_cdfBaiXingG_rig_rigMaster_v043_alphaRegressionSkin_envelopeOn_jaw25.ma`；代价是 15 度后 visual fail 比 v042 多约 1 个，后续应把 visual penalty 并入 alpha/patch solver。
- cdfBaiXingG v044-v053 unresolved skin 回归确认：v044 对 v043 剩余 25 个高误差点做 source-prior，离线 24 点通过但 Maya 多姿态暴露 `748/9175/9176` 新增 collision，不能用；v045 剔除这 3 点后真实多姿态 SUCCESS；v046 对 748 做 alpha=0.25 rescue 后仍 SUCCESS。用户随后指出 `8796/8949/9232/9233/9288/9289` 仍错权，审计确认 Graph Cut/current 权重都被 lower_lip 先验带偏；v048 新增 lip 局部 geodesic landmark descriptor，把 6 点映射到 upper source并实写验证 SUCCESS。v049-v052 建立无人工点号的 preflight 风险识别：hard=30、borderline=21、weak=10、expanded=61。v053 从 v048 干净基线读取这 61 个自动风险点，source/current alpha 门控接受 53 点，MCP foreground 7099 实写 `ysj_chr_cdfBaiXingG_rig_rigMaster_v053_autoRiskGatedSkin_envelopeOn_jaw25.ma`；v048→v053 多姿态 actual graph SUCCESS，`source drift=0`、`neutral delta=0`、`accepted collision added=0`，Jaw25 ROI mean `0.033071->0.023111`、p95 `0.088673->0.081519`、collision `30->12`、visual fail `226->205`。当前推荐人工复验场景为 v053；剩余 `9175/9176/9386` 已经被逐点/成对 alpha 搜索拒绝，不能再硬写 Skin。
- cdfBaiXingG `M_Head_base -> cdfBaiXingG_body2` v061-v068 复刻回归确认：correspondence map 能自动区分用户确认的上下唇错点，但旧 v057 NPZ 不能再当当前 base；必须从 Maya 实时回读 base 权重。v062 修复全矩阵 top-k prune 误改未接受点后，v065 使用 `alpha=0.35` 取得当前最佳折中：Jaw25 user p95 `0.3692->0.2723`，lip p95 `0.3551->0.3388`，lip close `42->46`，lip surface fail `47->57`。v066-v068 证明同 owner 拓扑高斯可改善局部 user/accepted p95，但 lip surface fail 升至 79/76/75，不能作为默认写入。当前推荐人工复验场景为 `ysj_chr_cdfBaiXingG_rig_rigMaster_v065_alpha035Weights.ma`，Maya 已选中 `CDFDIAG_BODY2_MHEAD_V065_Jaw25LipSurfaceFail_SET` 57 个剩余风险点。
- cdfBaiXingG `M_Head_base -> A` v072-v075 全通道审计确认：原始 `A` 已重新创建 `A_MHead_transfer_skinCluster`，写权 row_l1 max≈`7.63e-10`，数学 LBS vs Maya max≈`7.63e-06`，说明 Maya 写权和 LBS 矩阵闭合可靠；但 209 个 influence 的 R/T/S 全通道位移复刻 all-channel p95≈`0.1494`，translate p95≈`0.2705` 暴露眼睑等细骨骼误差。后续不能继续只调映射参数，应进入 source-target correspondence、expected displacement bank 和 constrained inverse solve。
- cdfBaiXingG `M_Head_base -> A` v076/v077 反求复核确认：v076 raw inverse 离线 all-channel p95 可降到≈`0.03065`，但 Jaw25 视觉炸眼皮；真实 DG 诊断显示 moving_lid=0，根因是 correspondence 把 target 眼皮映射到 source 嘴唇，raw 为拟合错误 expected 位移把 89 个 eye/lid 点改成 mouth/lip 权重。v077 加 source/target 权重语义一致性门，block 269 个冲突点，guard RT all-channel p95≈`0.04088`，已保存 `v077_A_inverseSemanticGuard.ma` 并创建 raw/guard 诊断 set。后续推荐先人工看 v077 guard，不再推荐 raw。
- cdfBaiXingG `M_Head_base -> A` v078 基准已重置为 `Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test.ma`：需求只做 Skin 权重复刻，source=`M_Head_base`，target=`A`。v076/v077 不能继续作为数据基线，因为 v076 输入复用了 body2 的 `body2_mhead_correspondence_map_v061.npz`，部分 A 眼皮点真实最近 source 是眼皮但旧 map 指到嘴唇。下一轮必须从 `test.ma` 重新构建 A-specific correspondence，并把低置信点显式标出。
- cdfBaiXingG `M_Head_base -> A` v081/v082 重启确认：用户人工复验判定 v078-v080 全部视觉不合格；v081 仅保留为 Maya DG 失败定位，v082 已执行非破坏性归档，当前项目根只把 `test.ma` 作为输入基线，`.info` 只保留 `a_weight_transfer_v081_failure_probe` 和 `a_weight_transfer_v082_reboot`。下一步必须先做 correspondence validator，未证明 source provenance 的点不得进入权重反求。
- cdfBaiXingG `M_Head_base -> A` v082 correspondence validator 首跑确认：`A` 28925 点中 19211 点为 `M_Head_base` 支撑域外 unsupported，9714 点在支撑域内；其中 high_confidence=6692、low_confidence_actionable=3022、strict_block=1367。Maya 前台 7002 已创建 `CDFDIAG_V082_A_*` 诊断集并默认选中 low confidence，不写权重、不改几何。后续权重求解必须先处理 strict/low-confidence provenance，禁止直接对这些点反求。
- cdfBaiXingG `M_Head_base -> A` v084 constrained inverse 确认：在 v083 core / one-ring ROI 内，LBS basis 对 Maya skinCluster Jaw25 输出重建 p95≈`8.63e-06`；SLSQP 非负归一局部反求通过 Maya foreground 7002 实际 DG Jaw25 验收，最佳 `v084_inverse_motion_accepted` 将 supported motion p95 `0.013281->0.004658`、strict p95 `0.027131->0.008273`。这证明局部反求路线有效，但仍只是 Jaw25 单姿态，下一步必须做多姿态 DG 回归。
- cdfBaiXingG `M_Head_base -> A` v084/v085 多姿态回归确认：v084 motion 在 Jaw 5-30 度全部正收益，但 Cheek 位移姿态累计 494 个相对 hybrid 回归点；v085 把这些点整行回退到 hybrid 后回归点清零，但 12 姿态 supported p95 max 从 `0.005378` 退到 `0.011309`。当前结论是 v084 为最佳 Jaw 候选，v085 为安全对照；下一步应做 per-row / per-pose alpha gate，不应整行硬回退。
- cdfBaiXingG `M_Head_base -> A` v086 per-row/per-pose alpha gate 确认：基于 Maya 实际 DG 误差向量对 `v084_inverse_motion_accepted` 与 `hybrid_m0010_cc3` 做逐点 alpha 搜索。三套 v086 均把 `>0.005` 多姿态回归点压到 0，且比 v085 保留更多 Jaw 收益；当前最佳人工复验候选为 `v086_alphaGate_safe005`，12 姿态 supported p95 max `0.009740`、mean `0.002961`、strict max `0.017078`，Jaw25 p95 `0.008297`。若视觉仍不合格，下一步做 patch-level surface objective，不再回到最近点/GraphCut/无约束平滑。
- cdfBaiXingG `M_Head_base -> A` v087 全 influence 口径修正：Jaw 只是一种姿态，最终目标是 209 个 influence 的整张 Skin 权重矩阵。v087 从干净 `test.ma` 重新导出全 influence R/T/S 位移画像，support=9714、high_confidence=6692、low_confidence=3022、unsupported=19211；离线 all-channel p95：baseHybrid≈`0.006007`，raw RT/RTS≈`0.005806` 但改 6800+ 点只作诊断，guard strict/balanced≈`0.00588-0.00595` 且只改 28/54 点。Maya 已保存 `test_v087_A_allInfluence_compare.ma`，创建 base/raw/guard 对照体和 lowConfidence/unsupported/topology/semantic 诊断 set。
- cdfBaiXingG `M_Head_base -> A` v087 真实 Maya DG 多姿态验收：新增 27 个真实控制器姿态（Jaw/Mouth/Cheek/UpCheek/Lid/Lip/Nose/Chin），并关闭 source `M_Head_base_blendShape.envelope` 做 skin-only 复核。结果显示离线 raw/guard 略优不能定版：`baseHybrid` supported p95 max=`0.022191`、mean=`0.002409` 仍最稳；raw 有 55 个唯一回归点，guard strict 有 18 个，guard balanced/RTS 有 31 个。因此 v087 guard 候选只保留诊断价值，不能作为默认权重。后续必须 actual-DG-in-loop，并加入 patch-level surface objective，而不是扩大 raw 或继续调最近点/GraphCut/平滑。
- cdfBaiXingG `M_Head_base -> A` v088 射线/可见性探针确认：新增只读 visibility probe，验证 source first-hit、target segment blocked、target normal layer hit 与真实 DG 错误的相关性。`visibility_risk` 共 703 点，对 actual high error >0.015 的 precision≈0.279、recall≈0.189、lift≈7.78；对候选回归的 recall≈0.345、lift≈14.21。红框 `9095-9150` 命中 59/112，`9263-9318` 命中 43/112。结论：射线/法线近层证据有用，但只能作为 correspondence penalty / hard-risk set，不能单独删映射或决定权重。诊断场景为 `test_v088_A_visibility_probe.ma`。
- cdfBaiXingG `M_Head_base -> A` v089b 最终候选测试确认：v089 连续场直接反求 30 分钟超时，不可作为生产链路；v089b 改为 fast continuous field 先验 + v087 raw inverse + v088 visibility + 语义/拓扑/edge + actual-DG-in-loop 门控。离线和 Maya 写回均完成，写权 row_l1 max≈`4.8e-08`。真实 Maya DG 27 姿态中 `field_prior` 明确失败（1269 唯一回归点），`actual_safe_rt` 仍有 17 个唯一回归点，`final_alpha035` 有 1 个；当前唯一推荐候选为 `A_V089B_004_visibilityGuardRT`，0 个 `>0.005` 唯一回归点，supported p95 max `0.022191->0.022112`、mean `0.002409->0.002399`。输出场景为 `test_v089b_A_fastField_compare.ma`。
- cdfBaiXingG `M_Head_base -> A` v090 patch-level surface objective 首轮确认：v090 在 v089b 基础上把顶点位移、边长应变、面积应变、法线变化纳入真实 Maya DG 27 姿态表面目标。过程中修复两类测试污染：全矩阵 top-k prune 误改未接受点、展示偏移进入 skin bind。当前唯一零新增退化面候选为 `A_V090_002_strictBlend035`，对应 `v090_strict_patch_blend035`，surface score max `0.098668->0.098645`、mean `0.049075->0.049071`、regressed tri=0、improved tri=16。`balanced/broad` 收益更大但退化面明显增多，只作诊断。输出场景为 `test_v090_A_patchSurface_candidates_eval.ma`。
- 形变继承 Skin 下一阶段准则：当前 owner/topology/correspondence transfer 是“映射型传权”，不是完整“反求型拟合”。反求路线必须先证明 `b = C(source displacement)` 可信，再用 SciPy/CVXPY 类受约束最小二乘求 target 权重，并以 v075 全通道指标、Maya actual graph、neutral delta、source drift、surface/collision gate 共同验收。
- 真实资产覆盖后续应继续增加隐藏 mesh 多、缺 Orig、多语义 merge 和更复杂绑定结构的样本。

## 文档分层

- `docs/ai_startup/01_PROJECT_FOUNDATION.md`：项目全局框架、新手地图、八个视角和能力边界。
- `docs/architecture/pipeline_runtime_contract_v1.md`：运行时总契约。
- `docs/architecture/runtime_task_report.md`：报告渲染契约。
- `docs/architecture/compare_and_assembly_pipeline_plan.md`：对比和拼装主线。
- `docs/architecture/mesh_pairing_phased_logic.md`：mesh 配对算法。
- `docs/architecture/deformation_inheritance_plan_and_validation.md`：形变继承专题现状和验证。
- `skills/build_pipeline_skill/SKILL.md`：新增和改造 skill 的唯一规范。
- `tests/README.md`：当前轻量门禁清单。

## 验证入口

- 当前轻量门禁清单维护在 `tests/README.md`。
- 知识库、报告、workflow 输入中断、层级修复、compare/sync 契约和 Live BS 相关改动都必须按该清单补测。
- 真实 DCC 行为变化不能只靠静态测试；需要补 Maya/Blender workflow 或真实资产巡航。
