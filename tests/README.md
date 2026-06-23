# 测试入口说明

本目录没有统一 test runner。当前轻量门禁按改动范围单独运行。

## 当前锁定门禁

### P0 核心门禁（每次提交必跑）

启动必读包、项目基石、知识库、报告、workflow 输入、层级和主 compare/sync 相关改动至少运行：

```bash
python tests/test_docs_knowledge_contract.py
python tests/test_workflow_input_contract.py
python tests/test_task_report_writer.py
python tests/test_write_task_report.py
python tests/test_hierarchy_presync_contract.py
python tests/test_compare_contract.py
python tests/test_pipeline_compare.py
python tests/test_compare_result_contract.py
python tests/test_rig_sync_profile.py
python tests/test_sync_contract.py
python tests/test_sync_action_dispatch.py
python tests/test_resolve_asset_files.py
python tests/test_run_archive.py
python tests/test_workflow_template_vars.py
python tests/test_abc_reader.py
python tests/test_cli_worker_contract.py
python tests/test_service_manager_health.py
```

### P1 算法门禁（改动 core/ 算法模块时运行）

```bash
python tests/test_bootstrap.py
python tests/test_path.py
python tests/test_material_semantics.py
python tests/test_topology_support_matcher.py
python tests/test_strictness_and_dehydration.py
```

### P2 形变继承门禁（改动 deformation/skin/BS 时运行）

```bash
python tests/test_deformation_inherit_skin_contract.py
python tests/test_live_bs_transfer.py
python tests/test_wrap4d_bs.py
```

### DCC 集成测试（需 Maya/Blender 环境，非自动门禁）

```bash
python tests/test_e2e.py
python tests/test_sync_maya_integration.py
python tests/test_maya_load.py
python tests/test_sync.py
python tests/test_sync_vectorize.py
python tests/test_geometry_accuracy.py
```

交付前至少对改动过的 Python 文件执行 `python -m py_compile ...`。如果改动涉及真实 Maya/Blender 场景行为，再补对应 DCC 巡航或真实资产 workflow。

## Skill 元数据门禁

修改 `skills/*/SKILL.md`、MCP 动态 tool 注册或 Notes skill 摘要时运行：

```bash
python tools/skill_metadata_audit.py
python cli.py list-skills --json
python tools/sync_notes_skill_index.py
```

需要刷新 Notes 摘要时再执行：

```bash
python tools/sync_notes_skill_index.py --write
```

## 归档说明

- `archive/` 中是旧手工脚本和实验产物，保留用于追溯，不作为当前契约或自动验证入口。
- `archive/experimental/` 中是实验性测试（debug 脚本、旧里程碑、射线投射实验等），按需查阅。
