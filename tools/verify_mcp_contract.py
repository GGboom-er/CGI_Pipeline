"""CGI Pipeline MCP 契约扫描。

只做静态和纯函数级检查，不启动 DCC，不依赖 Celery。
"""

from __future__ import annotations

import json
import ast
import pathlib
import re
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
ALLOWED_RECEIPT_OUTPUT_KEYS = {"output_path", "report_path", "result"}


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_skill_params() -> dict[str, set[str]]:
    skills: dict[str, set[str]] = {}
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = _read_text(skill_md)
        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        if end < 0:
            continue
        data = yaml.safe_load(text[3:end].strip()) or {}
        skill_id = data.get("skill_id")
        if skill_id:
            skills[skill_id] = set((data.get("parameters") or {}).keys())
    return skills


def _resolve_template_vars(params: dict, outputs: dict, extra_params: dict, config: dict | None = None) -> dict:
    """与 core.tasks._resolve_template_vars 保持一致的轻量副本，避免导入 Celery。"""

    config = config or {}
    placeholder_re = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
    base_re = re.compile(
        r"^(?P<kind>outputs|input|config)\.(?P<path>[\w\.]+?)"
        r"(?P<tail>(?:\s*\|\s*[^|]+)*)$"
    )
    filter_re = re.compile(
        r"\|\s*replace\(\s*"
        r"(?P<a>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*,\s*"
        r"(?P<b>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*\)"
    )

    def _unquote(s: str) -> str:
        s = s.strip()
        if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
            return bytes(s[1:-1], "utf-8").decode("unicode_escape")
        return s

    def _resolve_base(kind: str, path: str):
        keys = path.split(".")
        if kind == "outputs":
            if len(keys) < 2:
                return None, False
            step_outputs = outputs.get(keys[0], {})
            field = ".".join(keys[1:])
            if isinstance(step_outputs, dict) and field in step_outputs:
                return step_outputs[field], True
            curr = step_outputs
            for key in keys[1:]:
                if isinstance(curr, dict) and key in curr:
                    curr = curr[key]
                elif isinstance(curr, list) and key.isdigit() and int(key) < len(curr):
                    curr = curr[int(key)]
                else:
                    return None, False
            return curr, True
        if kind == "input":
            curr = extra_params
        else:
            curr = config
        for key in keys:
            if isinstance(curr, dict) and key in curr:
                curr = curr[key]
            elif isinstance(curr, list) and key.isdigit() and int(key) < len(curr):
                curr = curr[int(key)]
            else:
                return None, False
        return curr, True

    def _eval_expr(expr: str):
        match = base_re.match(expr.strip())
        if not match:
            return None, False
        value, ok = _resolve_base(match.group("kind"), match.group("path"))
        if not ok:
            return None, False
        for token in (match.group("tail") or "").split("|")[1:]:
            token = token.strip()
            replace_match = filter_re.fullmatch("|" + token)
            if replace_match:
                value = str(value).replace(_unquote(replace_match.group("a")), _unquote(replace_match.group("b")))
            elif token == "basename":
                value = str(value).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            elif token == "stem":
                basename = str(value).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                value = pathlib.Path(basename).stem
            elif token == "dirname":
                norm = str(value).replace("\\", "/").rstrip("/")
                value = norm.rsplit("/", 1)[0] if "/" in norm else ""
            elif token:
                return None, False
        return value, True

    def _resolve_string(value: str):
        full = placeholder_re.fullmatch(value.strip())
        if full:
            resolved, ok = _eval_expr(full.group(1))
            return resolved if ok else value
        return placeholder_re.sub(lambda m: str(_eval_expr(m.group(1))[0]) if _eval_expr(m.group(1))[1] else m.group(0), value)

    return {k: _resolve_string(v) if isinstance(v, str) else v for k, v in params.items()}


def check_workflow_params(errors: list[str]) -> None:
    skills = _load_skill_params()
    if len(skills) < 1:
        errors.append("未读取到任何 SKILL.md frontmatter")
        return
    for wf_path in sorted((ROOT / "workflows").glob("*.json")):
        workflow = json.loads(_read_text(wf_path))
        for idx, step in enumerate(workflow.get("steps", [])):
            skill_id = step.get("skill_id", "")
            if skill_id not in skills:
                errors.append(f"{wf_path.name} step {idx}: skill_id 不存在: {skill_id}")
                continue
            params = set((step.get("parameters") or {}).keys())
            extra = sorted(params - skills[skill_id])
            if extra:
                errors.append(f"{wf_path.name} step {idx} ({skill_id}): 未声明参数 {extra}")


def check_template_replace(errors: list[str]) -> None:
    params = {
        "materials": "{{outputs.export_abc.output_path | replace('.abc', '_materials.json')}}",
        "scene": "{{outputs.export_abc.output_path | replace('.abc', '.ma')}}",
        "mixed": "out={{outputs.export_abc.output_path | replace(\".abc\", \".json\")}}",
        "stem": "{{input.source_path | stem}}",
        "nested": "{{outputs.resolve_files.result.rig_path | stem}}",
    }
    resolved = _resolve_template_vars(
        params,
        {
            "export_abc": {"output_path": "Y:/run/test.abc"},
            "resolve_files": {"result": {"rig_path": "Y:/run/rig_scene.ma"}},
        },
        {"source_path": "Y:/run/asset.blend"},
        {},
    )
    expected = {
        "materials": "Y:/run/test_materials.json",
        "scene": "Y:/run/test.ma",
        "mixed": "out=Y:/run/test.json",
        "stem": "asset",
        "nested": "rig_scene",
    }
    if resolved != expected:
        errors.append(f"模板 replace 解析失败: {resolved!r}")


def check_text_patterns(errors: list[str]) -> None:
    scan_files = [
        *list((ROOT / "core").glob("*.py")),
        *list((ROOT / "dccs").glob("*/*.py")),
        *list((ROOT / "mcp_server").glob("*.py")),
        *list((ROOT / "dashboard").glob("*.py")),
        *list((ROOT / "skills").glob("*/*.py")),
        *list((ROOT / "skills").glob("*/*.md")),
        *list((ROOT / "workflows").glob("*.json")),
        ROOT / "config" / "pipeline_manifest.json",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "AI_ONBOARDING.md",
        ROOT / ".env",
        ROOT / ".env.example",
    ]
    hard_timeout = re.compile(r"\b(time_limit|soft_time_limit)\s*=|_poll_timeout\s*=|IPC_TIMEOUT_SEC=1800")
    forbidden = {
        "from skills.compare_asset": "旧 compare_asset 导入",
        "skills/compare_asset.py": "旧 compare_asset 文件名注释",
        "suggest_ai_publish_path": "旧 Ai_pub 路径建议 API",
        "SKILLS_REGISTRY": "旧 registry.json 文件注册入口",
        "suggested_actions": "后台人工 suggested_actions 语义",
        "Ai_pub": "旧 Ai_pub 文档语义",
        "needs_attention_holds_scene\": true": "旧 hold manifest 开关",
        "hold_on_needs_attention\": true": "旧 hold chain 开关",
    }
    for path in scan_files:
        if not path.exists():
            continue
        text = _read_text(path)
        rel = path.relative_to(ROOT)
        if hard_timeout.search(text):
            errors.append(f"{rel}: 存在硬超时配置")
        for needle, label in forbidden.items():
            if needle in text:
                errors.append(f"{rel}: {label}")


def check_workflow_resume_order(errors: list[str]) -> None:
    text = _read_text(ROOT / "core" / "tasks.py")
    fail_guard = "if seg_status not in ('SUCCESS', 'CHAIN_SUCCESS'):"
    persist_call = "persist_outputs(task_id, all_outputs)"
    mark_call = "mark_segment_done(task_id, seg_idx)"
    fail_idx = text.find(fail_guard)
    persist_idx = text.find(persist_call)
    mark_idx = text.find(mark_call)
    if fail_idx < 0 or persist_idx < 0 or mark_idx < 0:
        errors.append("core/tasks.py: workflow 段完成标记逻辑缺失")
        return
    if not (fail_idx < persist_idx < mark_idx):
        errors.append("core/tasks.py: 失败段可能先被标记完成，断点恢复会跳过失败段")


def check_workflow_internal_outputs(errors: list[str]) -> None:
    """对比/拼装 workflow 的机器中间产物必须进 .info。"""
    required_by_workflow = {
        "tex_to_rig_verify_and_sync.json": {
            "export_abc": ("abc_path", ".abc"),
            "extract_materials": ("output_path", "_materials.json"),
            "compare_pre": ("output_path", "_pre_compare_result.json"),
            "verify": ("output_path", "_post_compare_result.json"),
        },
        "tex_to_rig_verify.json": {
            "export_abc": ("abc_path", ".abc"),
            "compare": ("output_path", "_compare_result.json"),
        },
        "blender_tex_export.json": {
            "export_abc": ("abc_path", ".abc"),
            "extract_materials": ("output_path", "_materials.json"),
            "build_info": ("info_path", "_info.json"),
        },
        "blender_to_maya_full_build.json": {
            "export_abc": ("abc_path", ".abc"),
            "extract_materials": ("output_path", "_materials.json"),
        },
    }

    for wf_name, required in required_by_workflow.items():
        wf_path = ROOT / "workflows" / wf_name
        workflow = json.loads(_read_text(wf_path))
        steps = {step.get("step_id"): step for step in workflow.get("steps", [])}
        if wf_name in ("tex_to_rig_verify.json", "tex_to_rig_verify_and_sync.json"):
            first = (workflow.get("steps") or [{}])[0]
            if first.get("skill_id") != "resolve_asset_files":
                errors.append(f"{wf_name}: 主入口第一步必须是 resolve_asset_files")
            wf_text = json.dumps(workflow, ensure_ascii=False)
            if "{{input.rig_path" in wf_text:
                errors.append(f"{wf_name}: 不应直接依赖 input.rig_path，必须走 resolve_files 输出")
        for step_id, (param_name, suffix) in required.items():
            step = steps.get(step_id)
            if not step:
                errors.append(f"{wf_name}: 缺少步骤 {step_id}")
                continue
            value = (step.get("parameters") or {}).get(param_name, "")
            if "{{input.info_dir}}" not in value or suffix not in value:
                errors.append(f"{wf_name} {step_id}.{param_name}: 内部产物未写入 .info: {value}")

        for step in workflow.get("steps", []):
            params = step.get("parameters") or {}
            if step.get("skill_id") in (
                "blender_build_asset_info",
                "maya_build_asset_info",
                "maya_compare_asset_in_scene",
                "maya_sync_rig_incremental",
            ):
                cache_group = params.get("cache_group", "")
                if "{{config." not in cache_group:
                    errors.append(
                        f"{wf_name} {step.get('step_id')}.cache_group: 组名必须来自项目配置: {cache_group}"
                    )


def check_tex_to_rig_hierarchy_presync(errors: list[str]) -> None:
    """把旧 |*|geo 预同步归一化链路固化到总门禁。"""
    wf_path = ROOT / "workflows" / "tex_to_rig_verify_and_sync.json"
    workflow = json.loads(_read_text(wf_path))
    steps = workflow.get("steps", [])
    order = {step.get("step_id"): index for index, step in enumerate(steps)}

    required_steps = [
        "check_hierarchy_pre",
        "fix_hierarchy_pre",
        "check_hierarchy_ready",
        "compare_pre",
        "sync",
        "check_hierarchy_post",
    ]
    missing = [step_id for step_id in required_steps if step_id not in order]
    if missing:
        errors.append(f"{wf_path.name}: 缺少层级预同步步骤 {missing}")
        return

    if not (order["check_hierarchy_pre"] < order["fix_hierarchy_pre"] < order["check_hierarchy_ready"] < order["compare_pre"] < order["sync"]):
        errors.append(f"{wf_path.name}: 层级 check/fix/check 必须发生在 compare/sync 之前")

    step_map = {step.get("step_id"): step for step in steps}
    check_pre = step_map["check_hierarchy_pre"]
    if check_pre.get("source_path") != "{{outputs.resolve_files.result.rig_path}}":
        errors.append(f"{wf_path.name}: check_hierarchy_pre 必须负责打开 rig 沙盒副本")

    for step_id in ("check_hierarchy_pre", "check_hierarchy_ready", "check_hierarchy_post"):
        params = step_map[step_id].get("parameters") or {}
        if params.get("block_extra_top_nodes") is not False:
            errors.append(f"{wf_path.name} {step_id}: 额外非空顶层节点默认只能报告，不能阻断")

    expected_active = "{{outputs.fix_hierarchy_pre.result.active_rig_root}};{{config.stages.rig.geom_roots.0}}"
    if (step_map["compare_pre"].get("parameters") or {}).get("cache_group") != expected_active:
        errors.append(f"{wf_path.name}: compare_pre 必须优先读取 fix 输出 active_rig_root")
    if (step_map["sync"].get("parameters") or {}).get("cache_group") != expected_active:
        errors.append(f"{wf_path.name}: sync 必须与 compare_pre 使用同一个 active_rig_root")
    if (step_map["sync"].get("parameters") or {}).get("compare_result") != "{{outputs.compare_pre.compare_result}}":
        errors.append(f"{wf_path.name}: sync 必须直传 compare_pre.compare_result，避免再读盘漂移")

    check_text = _read_text(ROOT / "skills" / "maya_check_asset_hierarchy" / "maya_check_asset_hierarchy.py")
    for needle in (
        "legacy_geo_roots",
        "active_rig_root",
        "active_rig_mesh_count",
        "block_extra_top_nodes",
        'phase == "pre_sync"',
    ):
        if needle not in check_text:
            errors.append(f"maya_check_asset_hierarchy: 缺少预同步事实字段/判定 {needle}")

    fix_text = _read_text(ROOT / "skills" / "maya_fix_asset_hierarchy" / "maya_fix_asset_hierarchy.py")
    normalize_idx = fix_text.find("normalized_roots, normalized_created, renamed_tops, preserved_tops = _normalize_legacy_geo_roots")
    ensure_idx = fix_text.find("required_root, created_groups = _ensure_transform_path(required_root)")
    if normalize_idx < 0 or ensure_idx < 0 or normalize_idx > ensure_idx:
        errors.append("maya_fix_asset_hierarchy: 必须先归一旧 |*|geo，再创建标准 cache 容器")
    for needle in ('"RIG_geo"', '_as_bool(params.get("delete_extra_top_nodes"), False)', '_safe_delete_top_nodes(check_result)'):
        if needle not in fix_text:
            errors.append(f"maya_fix_asset_hierarchy: 缺少绑定保护逻辑 {needle}")
    if 'check_result.get("extra_top_nodes") or []' in fix_text:
        errors.append("maya_fix_asset_hierarchy: 禁止按 extra_top_nodes 删除顶层节点")

    sync_text = _read_text(ROOT / "skills" / "maya_sync_rig_incremental" / "maya_sync_rig_incremental.py")
    for needle in (
        'cmds.objExists("|Group")',
        'cmds.ls("|Group|Geometry"',
        "def _register_rig_lookup",
        "def _strip_rig_parts",
        "keep_abs_index=rig_start",
    ):
        if needle not in sync_text:
            errors.append(f"maya_sync_rig_incremental: 缺少 RIG_geo/root DAG 防回归逻辑 {needle}")
    if 'cmds.ls("Geometry", long=True' in sync_text:
        errors.append("maya_sync_rig_incremental: 禁止随便选择任意 Geometry 节点创建 cache")


def check_cli_worker_interface(errors: list[str]) -> None:
    """CLI 仍会显式 start worker，所有 create_worker 返回值必须支持该接口。"""
    text = _read_text(ROOT / "core" / "dcc_factory.py")
    if "class WarmWorkerProxy" not in text:
        errors.append("core/dcc_factory.py: 缺少 WarmWorkerProxy")
        return
    warm_start = re.search(r"class WarmWorkerProxy:.*?\n    def start\(self\):", text, re.S)
    if not warm_start:
        errors.append("core/dcc_factory.py: WarmWorkerProxy 必须提供 start() 兼容旧 CLI/测试入口")


def check_default_output_fallbacks(errors: list[str]) -> None:
    """默认输出路径不得回退到输入文件同目录。"""
    checks = {
        "pipeline_compare_asset": {
            "path": ROOT / "skills" / "pipeline_compare_asset" / "pipeline_compare_asset.py",
            "needles": [
                "base_dir = os.path.dirname(os.path.abspath(input_b))",
                "def _default_compare_result_path",
            ],
        },
        "maya_export_abc": {
            "path": ROOT / "skills" / "maya_export_abc" / "maya_export_abc.py",
            "needles": [
                "src_dir = os.path.dirname(scene)",
                "candidate = os.path.join(src_dir",
                "abc_path = candidate",
            ],
        },
        "blender_export_abc": {
            "path": ROOT / "skills" / "blender_export_abc" / "blender_export_abc.py",
            "needles": [
                "candidate = os.path.join(os.path.dirname(src)",
                "abc_path = candidate",
            ],
        },
        "pipeline_export_abc_auto": {
            "path": ROOT / "skills" / "pipeline_export_abc_auto" / "pipeline_export_abc_auto.py",
            "needles": [
                "build_publish_path",
                "with_suffix('.abc')",
            ],
        },
        "blender_extract_materials": {
            "path": ROOT / "skills" / "blender_extract_materials" / "blender_extract_materials.py",
            "needles": [
                "os.path.splitext(source_path)[0] + '_materials.json'",
            ],
        },
    }
    for skill_id, spec in checks.items():
        text = _read_text(spec["path"])
        for needle in spec["needles"]:
            if needle in text:
                errors.append(f"{skill_id}: 默认输出仍可能回退源目录/发布目录: {needle}")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def check_receipt_output_contract(errors: list[str]) -> None:
    """所有 skill receipt.outputs 顶层只能是 output_path/report_path/result。"""
    for path in sorted((ROOT / "skills").glob("*/*.py")):
        try:
            tree = ast.parse(_read_text(path))
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(ROOT)}: Python 解析失败: {exc}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) != "make_receipt":
                continue
            output_kw = next((kw for kw in node.keywords if kw.arg == "outputs"), None)
            if output_kw is None:
                continue
            rel = path.relative_to(ROOT)
            value = output_kw.value
            if not isinstance(value, ast.Dict):
                errors.append(
                    f"{rel}:{node.lineno}: outputs 必须是字面量 dict，"
                    "特殊结构化数据放入 {'result': {...}}"
                )
                continue

            for key in value.keys:
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    errors.append(f"{rel}:{node.lineno}: outputs 存在动态 key")
                    continue
                if key.value not in ALLOWED_RECEIPT_OUTPUT_KEYS:
                    errors.append(
                        f"{rel}:{node.lineno}: 非法 outputs 顶层字段 {key.value!r}，"
                        "只允许 output_path/report_path/result"
                    )


def main() -> int:
    errors: list[str] = []
    check_workflow_params(errors)
    check_template_replace(errors)
    check_text_patterns(errors)
    check_workflow_resume_order(errors)
    check_workflow_internal_outputs(errors)
    check_tex_to_rig_hierarchy_presync(errors)
    check_cli_worker_interface(errors)
    check_default_output_fallbacks(errors)
    check_receipt_output_contract(errors)
    if errors:
        print("[FAIL] MCP 契约扫描发现问题:")
        for item in errors:
            print(f"  - {item}")
        return 1
    print("[PASS] MCP 契约扫描通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
