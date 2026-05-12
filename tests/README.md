# 测试入口说明

本目录没有统一 test runner。当前轻量门禁按改动范围单独运行：

```bash
python tests/test_pipeline_compare.py
python tests/test_rig_sync_profile.py
python tests/test_compare_result_contract.py
python tests/test_sync_contract.py
python tests/test_sync_action_dispatch.py
python tests/test_resolve_asset_files.py
python tests/test_run_archive.py
python tests/test_task_report_writer.py
python tests/test_workflow_template_vars.py
python tests/test_abc_reader.py
```

`archive/legacy_manual/` 中是旧手工脚本，保留用于追溯，不作为当前契约或自动验证入口。
这些脚本包含旧 `registry.json`、旧 `compare_asset`、旧 `runs/assets` 或旧 API 路径，复用前必须先按当前 `pipeline_compare_asset`、任务沙盒和 `.info` 契约改造。
