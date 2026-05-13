# CGI Pipeline v2.0 待办清单 (TODO)

## 技能标准化整理

- [x] 修复 6 个 .py 放错位置的技能（maya_check_textures, maya_conform_normals, rename_asset, validate_publish, maya_export_abc, pipeline_export_abc_auto）
- [x] 补齐 7 个缺失的 SKILL.md（maya_check_textures, maya_conform_normals, rename_asset, validate_publish, pipeline_export_abc_auto, copy_files, blender_exec_code）
- [x] 修正 2 个 SKILL.md 格式不标准（maya_master_cleanup, pipeline_compare_asset）
- [x] ~~审查 registry.json 中所有技能条目与实际 .py 的一致性~~ (v2.0已废弃 registry.json，改为全动态目录扫描)
- [x] ~~同步 Gemini skills 目录与项目 skills 目录~~ (已全部集成至 MCP 工具原生暴露，无需在 .gemini 中冗余存放)

## 代码质量

- [x] `server.py` 拆分为 5 个模块
- [x] 删除 `core/path_solver.py.bak`
- [x] 底层引擎性能优化 — WarmWorkerProxy 常驻进程池
- [x] 精度对齐 — 统一全链路 4 位小数
- [x] 审查记录: 移除Ai_pub代理逻辑，统一使用runs沙盒，清理多余脚本与目录
- [x] 修复测试环境中的 Celery Worker 启动错误 (由 mayapy 与 miniconda 环境错乱引起)
- [x] 在对比引擎中过滤合法的 _live_ 绑定驱动体，避免验证误报
- [x] 审查记录: 2026-05-11 扫描 41 个 skill 目录，发现 7 个未注册目录、13 个回执 skill_id 旧短名、5 类非法路径输出 key、15 个吞异常风险点

## 新增技能（2026-05）

- [x] `maya_build_mesh_from_abc` — PyAlembic 纯数据构建 mesh（含 UV + DAG 层级还原）
- [x] `maya_apply_materials` — 消费 _materials.json 按面赋予材质球
- [x] `blender_extract_materials` — Blender 侧 per-face 材质采集（UDIM 按象限拆分）
- [x] 全链路实测通过：Blender→Maya 构建 73 mesh + 28 材质（ciweiguai 53.2MB）

## 稳定性修复（2026-05）

- [x] Worker 热重载注册表 — tasks.py 三处注入 `_reload_skill_registry()` 防止新技能不被识别
- [x] service_manager.shutdown_all() 增强 — 通过 PID 文件清理残留 Worker
- [x] 巡航测试预检 — run_auto_cruise_test.py 启动时先杀旧 Worker
- [x] 修复 3 个旧工作流 skill_id 引用错误（full_cleanup_and_save, rig_full_cleanup, tex_to_rig_verify）
- [x] qc_and_publish.json 移除不存在的 publish_asset 步骤

## 待办

- [x] 2026-05-11 运行时总规范 v1：确认所有源文件只读、沙盒命名、统一 MD 报告、`.info` 中间产物、最终输出按升版本
- [x] 2026-05-11 skill 拆解 01：完成 `blender_build_asset_info` 职责、输入、输出、问题和改造目标文档
- [x] 对比/拼装 pipeline 专项 Phase 1：`tex_to_rig_verify`、`blender_tex_export`、`blender_to_maya_full_build` 的 JSON/ABC/materials/compare_result 显式落任务沙盒 `.info`
- [x] 更新 `tex_to_rig_verify` 只读对比 workflow，显式传 `info_path/output_path` 到 `.info`
- [x] 审计 `blender_tex_export` 与 `blender_to_maya_full_build`，统一 ABC/JSON/materials 输出目录
- [x] 收紧 `blender_build_asset_info` 默认输出策略：未显式传路径时优先使用任务沙盒 `.info`，禁止回退源文件同目录
- [x] 移除 `blender_build_asset_info` 的 MTime 缓存复用分支，每次任务重新采集本次 `_info.json`
- [x] 拆分 Blender 几何/材质职责：`blender_build_asset_info` 只采几何，`blender_export_abc` 不再隐式写 `_materials.json`
- [x] 2026-05-12 skill 拆解 02：完成 `maya_build_asset_info` 职责收紧，只采唯一有效 ShapeOrig 几何，缺失 Orig 输出空几何
- [x] 2026-05-12 skill 拆解 03：完成 `pipeline_compare_asset` 默认输出收紧，compare_result 只写任务沙盒 `.info`
- [x] 2026-05-12 skill 拆解 04：完成 ABC 导出类默认输出收紧，`blender_export_abc` / `maya_export_abc` / `pipeline_export_abc_auto` 只写任务沙盒 `.info`
- [x] 2026-05-12 skill 拆解 05：完成材质采集链路收紧，`blender_extract_materials` 只写任务沙盒 `.info`
- [x] 2026-05-12 docs 文档治理审查：完成 `docs` 内容盘点、冲突分类和整理方案
- [x] 2026-05-13 docs 文档治理落地：已备份 `docs/` 到 `backups/docs_cleanup_20260513_010733`，归档历史方案/备份/临时代码，主 `docs/` 只保留权威规范、专项拆解和参考入口
- [x] 2026-05-13 docs 治理验证：MCP 契约扫描、对比/同步契约、沙盒、报告、workflow 模板变量等轻量测试通过
- [x] 同步 MCP Tool 描述、SKILL.md 与 `AGENTS.md` 中的 `.info` 路径规范
- [x] 2026-05-12 文档同步：README、AGENTS、运行时契约、对比/拼装专项文档已对齐场景内 compare + compare_result 驱动 sync
- [ ] 实现 `publish_asset` 技能（标准化发布流程：版本递增 + 拷贝到 pub 目录 + 元数据写入）
- [ ] 巡航测试覆盖更多资产（当前仅 mihouwang + ciweiguai）
- [x] 2026-05-13 Worker 健康检查机制：提交前检查 PID + Celery 队列心跳，默认 5 秒探测窗口，心跳丢失自动重启 Worker，并暴露 `pipeline_service_status` / `pipeline_restart_worker`
- [x] 2026-05-11 skill 契约修复：40 个运行时 skill 全注册，frontmatter 类型、回执路径 key、workflow skill 引用、DCC undo 块基础问题已修复
- [x] 2026-05-11 异常处理小修：只读查询、UDIM 材质、UV 精简、任务报告的小型吞异常点改为显式降级或 warning
- [x] 2026-05-11 后台 workflow 契约修复：移除人工 hold 主路径，统一 `AUDIT_FAILED` 失败态，取消 DCC IPC 硬超时
- [x] 2026-05-11 工作流门禁：新增 `tools/verify_mcp_contract.py`，覆盖参数契约、模板 replace、硬超时、旧导入与失败段恢复顺序
- [x] 2026-05-11 对比-同步契约：`pipeline_compare_asset` 输出 `compare_result`，`maya_sync_rig_incremental` 消费前置结果，workflow 保留 pre/post 两次对比
- [x] 2026-05-11 mihouwang 巡航测试 PASS：pre compare_result 驱动 sync，post 阻断差异 0，输出沙盒升版本 ma
- [x] 2026-05-11 巡航输出整理：根目录只留源备份、最终产物和唯一报告，ABC/JSON/audit 统一归档 `.info`
- [x] 专项治理 `maya_sync_rig_incremental` 第一轮：拆出纯契约层、收紧 compare_result/action 校验、统一失败阶段与回执、补单测覆盖
- [x] 2026-05-12 sync 专项巡航复测：mihouwang 全链路 PASS，post 阻断差异 0，输出沙盒升版本 ma
- [x] 2026-05-12 对比/拼装拆分重构：公共 Maya rig 采集、场景内对比 skill、sync 消费 compare_result、主 workflow 线路优化
- [x] 2026-05-12 对比/拼装真实 DCC 验证：Maya/Blender 巡航 PASS，post 阻断差异 0，沙盒升版本保存
- [x] 对齐 `pipeline_manifest.json` 中 Ai_pub 表述与当前任务沙盒事实
- [x] 2026-05-12 MCP 前台多端口安全门：foreground 调用必须显式传 `foreground_port`，省略端口时返回可用端口列表并阻止误连。
- [x] 2026-05-12 MCP 前台入口文档固化：README、AGENTS、CLAUDE、AI_ONBOARDING、operator skill 与 schema 描述均声明当前 Maya 场景必须走 `maya_exec_code`/具名 Tool + 显式端口。
- [x] 2026-05-12 ciweiguai + MaYouB 巡航兼容：报告四字中文映射、rig 几何根候选解析、Orig 图关系识别已验证。
- [x] 2026-05-12 巡航最终报告补齐：唯一 MD 报告包含每步 skill 逻辑、状态、耗时、summary、输出路径与关键明细。
- [x] 2026-05-12 运行时报告落地：调度层实时写 `REPORT.md`，step start/finish 通过 block upsert 更新同一报告。
- [x] 2026-05-12 复杂技能报告结构化：对比/拼装返回 `receipt.report_sections`，统一报告按折叠章节展示明细。
- [x] 2026-05-12 真实巡航复测：ciweiguai 与 maYouB 全链路 PASS，另用 maYouB 只读对比验证 `REPORT.md` 结构化折叠章节落地。
- [x] 2026-05-13 运行时报告修正：移除 HTML 折叠展示与重复旧报告块，文件流转表补齐节点、参数、输入、输出、状态、耗时。
- [x] 2026-05-13 workflow 入口节点化：新增 `resolve_asset_files`，`tex_to_rig_verify*` 支持只传资产名自动解析 tex/rig 或显式路径透传。
- [x] 2026-05-13 skill SOP 输出契约：所有 receipt.outputs 顶层统一为 `output_path/report_path/result`，特殊参数走 `outputs.result.xxx`，并加入静态门禁。
- [x] 2026-05-13 ABC reader 测试契约修复：`test_abc_reader` / `create_test_abc` 改按当前 `u_array`、`v_array`、`uv_indices` 验证。
- [x] 2026-05-13 测试入口整理：旧 `registry.json`、`compare_asset`、`runs/assets` 手工脚本移入 `tests/archive/legacy_manual/`，新增 `tests/README.md`
- [x] 2026-05-13 旧 API 残留修复：Dashboard 技能列表改动态 registry，`rename_asset` 移除 `suggest_ai_publish_path` 依赖
- [x] 2026-05-13 workflow 多占位符修复：同一字符串内多个 `{{config...}}` 正确解析，ciweiguai/maYouB 资产名入口 workflow PASS。
- [x] 2026-05-13 运行时报告聚合修复：跨 DCC segment 文件流转与打开场景不再互相覆盖，对比摘要改为用户视角问题数。
- [x] 2026-05-13 maYouB 同步层级核查：新 mesh 生成于绑定内 `Group/Geometry/cache`，旧 `geo` 被保留为 `RIG_geo`。
- [x] 2026-05-13 新增 `maya_check_asset_hierarchy`：按项目配置检查 required root、顶层散落节点和 cache 有效 mesh。
- [x] 2026-05-13 标准执行记录规范：报告只渲染 skill/input/output/status/elapsed_sec，对比字段改为 matched_same 等四类。
- [x] 2026-05-13 skill 规范收敛：`skills/CONVENTION.md` 成为唯一规范，开发指南和运行时总规范只保留引用。
- [x] 2026-05-13 skill 构建规则最终收敛：`build_pipeline_skill` 成为唯一规则入口，`CONVENTION.md` 仅保留兼容跳转。
- [x] 2026-05-13 skill 规范单文档化：删除跳转页，唯一规范正文固定为 `skills/build_pipeline_skill/SKILL.md`。
- [x] 2026-05-13 新增 `maya_fix_asset_hierarchy`：创建标准几何根、迁移非标准 cache 内容，并清理顶层 Group 以外锁定节点。
- [x] 2026-05-13 层级修复链路收紧：`maya_fix_asset_hierarchy` 改为消费 `maya_check_asset_hierarchy.outputs.result`，不再自行二次发现修复目标。
- [x] 2026-05-13 两分支合并：标准执行记录、compare_result 直传、层级 check/fix 已合入 `tex_to_rig_verify_and_sync`，maYouB workflow 巡航 PASS。
- [x] 2026-05-13 旧 `|*|geo` 预同步归一：`|MaYou_B|geo` 改为 `|Group|Geometry|RIG_geo`，同步输出 `|Group|Geometry|cache`，maYouB workflow PASS。
- [x] 2026-05-13 问题塌陷到代码：层级预同步顺序、RIG_geo DAG 映射、额外顶层非阻断、CLI WarmWorkerProxy.start 兼容均进入总门禁/测试。
- [x] 2026-05-13 输出布局回归修复：workflow 收尾只把 MD/HTML 记为 reports，JSON/ABC 机器产物保留在 outputs 与 `.info`，根目录重复副本自动清理。
