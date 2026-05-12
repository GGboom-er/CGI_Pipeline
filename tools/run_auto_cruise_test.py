"""
CGI Pipeline 全链路自动巡航测试 v3
===================================

完整链路：
  Phase 1 (Blender):  导出 ABC（几何真值源）+ 提取材质信息
  Phase 2 (Maya):     打开 rig 一次 → 场景内对比 → 同步拼装 → 修形 → 材质 → 后置场景内对比 → 存盘

输出结构：
  - 沙盒根目录只保留源备份、最终产出和唯一 MD 报告
  - 机器消费的 ABC / JSON / audit 统一放入 .info
"""

import datetime
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.task_status import SUCCESS_STATUSES, TERMINAL_STATUSES
from core.config_loader import load_project_config
from mcp_server.internals import _read_audit, _submit_chain


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoCruiseTest")

PROJECT = "ysj"
ASSET = "mihouwang"
CATEGORY = "chr"

X_RIG = r"X:\Project\ysj\pub\assets\chr\mihouwang\rig\rigMaster\ysj_chr_mihouwang_rig_rigMaster_v002.ma"
X_BLEND = r"X:\Project\ysj\pub\assets\chr\mihouwang\tex\texMaster\ysj_chr_mihouwang_tex_texMaster_v003.blend"

POLL_TIMEOUT_MIN = 300
POLL_TIMEOUT_MAX = 0
POLL_TIMEOUT_PER_MB = 5


def _geom_root(stage: str) -> str:
    cfg = load_project_config(PROJECT)
    roots = cfg.get("stages", {}).get(stage, {}).get("geom_roots", [])
    return roots[0] if roots else "cache"


def compute_poll_timeout(file_path: str, factor: float = 1.0) -> int:
    """按文件大小动态估算轮询上限；0 表示不设硬超时。"""
    try:
        size_mb = os.path.getsize(file_path) / 1024 / 1024
    except Exception:
        size_mb = 100
    est = int(size_mb * POLL_TIMEOUT_PER_MB * factor)
    if POLL_TIMEOUT_MAX <= 0:
        return 0
    return max(POLL_TIMEOUT_MIN, min(POLL_TIMEOUT_MAX, est))


def clean_old_sandboxes():
    logger.info("\n历史沙盒检查（只读，不自动删除）...")
    sandbox_dir = PROJECT_ROOT / "projects" / PROJECT
    if not sandbox_dir.exists():
        return
    count = sum(1 for d in sandbox_dir.iterdir() if d.is_dir() and "cruise_test" in d.name)
    logger.info(f"  发现 {count} 个历史巡航沙盒，本次保留。")


def create_sandbox() -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox = PROJECT_ROOT / "projects" / PROJECT / f"{ts}_{ASSET}_cruise_test"
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def get_info_dir(sandbox: Path) -> Path:
    info_dir = sandbox / ".info"
    info_dir.mkdir(parents=True, exist_ok=True)
    return info_dir


def stage_file_to_sandbox(src: str, sandbox: Path) -> str:
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src}")
    dst = sandbox / src_path.name
    if not dst.exists():
        size_mb = src_path.stat().st_size / 1024 / 1024
        logger.info(f"  拷贝到沙盒: {src_path.name} ({size_mb:.1f} MB)")
        shutil.copy2(str(src_path), str(dst))
    return str(dst)


def archive_audit(task_id: str, info_dir: Path) -> None:
    audit_path = PROJECT_ROOT / "audit" / f"{task_id}.json"
    if audit_path.exists():
        shutil.copy2(str(audit_path), str(info_dir / f"{task_id}_audit.json"))


def poll_task(task_id: str, timeout: int = POLL_TIMEOUT_MAX) -> dict:
    start_time = time.time()
    logger.info(f"  轮询任务 [{task_id}]...")
    while timeout <= 0 or time.time() - start_time < timeout:
        res = _read_audit(task_id)
        status = res.get("status", "NOT_FOUND")
        if status in TERMINAL_STATUSES:
            logger.info(f"  任务 [{task_id}] 结束: {status}")
            return res
        time.sleep(3)
    logger.error(f"  任务 [{task_id}] 轮询超时")
    return {"status": "TIMEOUT"}


def run_pipeline_skill_sync(skill_id: str, params: dict) -> dict:
    import importlib

    mod = importlib.import_module(f"skills.{skill_id}.{skill_id}")
    return mod.execute({
        "project": PROJECT,
        "asset_name": ASSET,
        "parameters": params,
    })


def submit_chain(source_path: str, chain: list, run_dir: str, suppress_report: bool = True) -> dict:
    payload = {
        "skill_id": "execute_chain",
        "project": PROJECT,
        "asset_name": ASSET,
        "source_path": source_path,
        "skill_chain": chain,
        "run_dir": run_dir,
    }
    if suppress_report:
        payload["suppress_report"] = True
    return _submit_chain(payload)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _info_summary(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = _read_json(path)
    textures = data.get("textures", {})
    if isinstance(textures, dict):
        texture_count = sum(len(v) for v in textures.values())
    else:
        texture_count = len(textures or [])
    warnings = data.get("meta", {}).get("orig_warnings", []) or []
    return {
        "mesh_count": len(data.get("meshes", {})),
        "texture_count": texture_count,
        "orig_warning_count": len(warnings),
    }


def _materials_summary(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = _read_json(path)
    materials = data.get("materials", {})
    face_assignments = 0
    if isinstance(materials, dict):
        for item in materials.values():
            for faces in (item.get("faces_by_mesh", {}) or {}).values():
                face_assignments += len(faces or [])
        material_count = len(materials)
    else:
        material_count = len(materials or [])
    return {"material_count": material_count, "face_assignments": face_assignments}


def _compare_summary(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = _read_json(path)
    compare = data.get("compare", {})
    paired = compare.get("paired", []) or []
    only_source = compare.get("only_a", []) or []
    only_target = compare.get("only_b", []) or []
    actions = {"IDENTICAL": 0, "ORIG_INJECT": 0, "MODIFIED": 0, "MERGE": 0, "SPLIT": 0}
    for item in paired:
        action = item.get("actionability")
        if action in actions:
            actions[action] += 1
    return {
        "paired": len(paired),
        "only_source": len(only_source),
        "only_target": len(only_target),
        "identical": actions["IDENTICAL"] + actions["ORIG_INJECT"],
        "matched_different": actions["MODIFIED"] + actions["MERGE"] + actions["SPLIT"],
        "blocking": len(only_source) + len(only_target) + actions["MODIFIED"] + actions["MERGE"] + actions["SPLIT"],
        "legacy_total_issues": compare.get("total_issues", 0),
        "actions": actions,
    }


def _chain_step_outputs(task_result: dict, skill_id: str) -> dict:
    for entry in task_result.get("entries", []):
        if entry.get("skill_id") != skill_id or entry.get("status") != "STEP_SUCCESS":
            continue
        detail = str(entry.get("detail", ""))
        if " [mem=" in detail:
            detail = detail.split(" [mem=", 1)[0]
        try:
            receipt = json.loads(detail)
        except Exception:
            continue
        return receipt.get("outputs", {}) or {}
    return {}


def _sync_summary(sync_outputs: dict, pre_compare_path: str) -> dict:
    keys = ("IDENTICAL", "ORIG_INJECT", "PAIRED", "UNPAIRED", "target_only")
    if any(k in sync_outputs for k in keys):
        return {k: sync_outputs.get(k, 0) for k in keys}
    if os.path.exists(pre_compare_path):
        compare = _read_json(pre_compare_path).get("compare", {})
        groups = compare.get("pairing_groups", []) or []
        summary = {k: 0 for k in keys}
        for group in groups:
            action = group.get("action")
            if action in summary:
                summary[action] += 1
        summary["target_only"] = len(compare.get("target_only_dags", []) or [])
        return summary
    return {k: "?" for k in keys}


def write_final_report(
    sandbox: Path,
    info_dir: Path,
    local_rig: str,
    local_blend: str,
    save_path: str,
    local_abc: str,
    materials_path: str,
    rig_info_pre_path: str,
    compare_result_path: str,
    rig_info_post_path: str,
    post_compare_path: str,
    pre_outputs: dict,
    post_outputs: dict,
    sync_outputs: dict,
    passed: bool,
) -> str:
    report_path = sandbox / f"{ASSET}_cruise_report.md"
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def rel(path: str) -> str:
        try:
            return str(Path(path).resolve().relative_to(sandbox.resolve())).replace("\\", "/")
        except Exception:
            return str(path)

    pre_info = _info_summary(rig_info_pre_path)
    post_info = _info_summary(rig_info_post_path)
    materials = _materials_summary(materials_path)
    pre_compare = _compare_summary(compare_result_path)
    post_compare = _compare_summary(post_compare_path)
    sync = _sync_summary(sync_outputs, compare_result_path)

    root_files = sorted({p.name for p in sandbox.iterdir() if p.is_file()} | {report_path.name})
    info_files = sorted(p.name for p in info_dir.iterdir() if p.is_file())

    lines = [
        f"# {ASSET} 巡航测试报告",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| 项目 | {PROJECT} |",
        f"| 资产 | {ASSET} |",
        f"| 状态 | {'PASS' if passed else 'FAIL'} |",
        f"| 生成时间 | {generated_at} |",
        f"| 沙盒 | `{sandbox}` |",
        "",
        "## 对外产物",
        "",
        "| 类型 | 文件 |",
        "|---|---|",
        f"| 绑定源备份 | `{rel(local_rig)}` |",
        f"| 贴图源备份 | `{rel(local_blend)}` |",
        f"| 同步后绑定文件 | `{rel(save_path)}` |",
        f"| 本报告 | `{report_path.name}` |",
        "",
        "## 流程结论",
        "",
        "| 阶段 | 结果 |",
        "|---|---|",
        f"| Blender 导出 | ABC `{rel(local_abc)}`，材质 {materials.get('material_count', '?')} 个，面赋予记录 {materials.get('face_assignments', '?')} 条 |",
        f"| 同步前对比 | paired={pre_compare.get('paired', '?')}，matched_different={pre_compare.get('matched_different', '?')}，only_source={pre_compare.get('only_source', '?')}，only_target={pre_compare.get('only_target', '?')} |",
        f"| 同步拼装 | IDENTICAL={sync.get('IDENTICAL', '?')}，ORIG_INJECT={sync.get('ORIG_INJECT', '?')}，PAIRED={sync.get('PAIRED', '?')}，UNPAIRED={sync.get('UNPAIRED', '?')}，target_only={sync.get('target_only', '?')} |",
        f"| 最终验证 | paired={post_compare.get('paired', '?')}，阻断差异={post_compare.get('blocking', '?')} |",
        "",
        "## 最终对比",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| identical | {post_compare.get('identical', '?')} |",
        f"| matched_different | {post_compare.get('matched_different', '?')} |",
        f"| only_source | {post_compare.get('only_source', '?')} |",
        f"| only_target | {post_compare.get('only_target', '?')} |",
        f"| MODIFIED | {post_compare.get('actions', {}).get('MODIFIED', '?')} |",
        f"| MERGE | {post_compare.get('actions', {}).get('MERGE', '?')} |",
        f"| SPLIT | {post_compare.get('actions', {}).get('SPLIT', '?')} |",
        "",
        "## 文件结构",
        "",
        f"- 根目录只保留对外文件：`{root_files}`",
        f"- 内部技术数据统一在 `.info/`：`{len(info_files)} 个文件`",
        "",
        "> `total_issues` 仍保留在内部 JSON 中用于兼容旧算法统计；最终 PASS/FAIL 只看 matched_different、only_source、only_target、MODIFIED、MERGE、SPLIT 这些阻断差异。",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


def run_test():
    logger.info("=" * 60)
    logger.info("CGI Pipeline 全链路自动巡航测试 v3")
    logger.info("=" * 60)

    from core.service_manager import shutdown_all as _shutdown_workers
    _shutdown_workers()

    logger.info("\nPhase 0: 创建沙盒并隔离源文件")
    clean_old_sandboxes()
    sandbox = create_sandbox()
    info_dir = get_info_dir(sandbox)
    logger.info(f"  沙盒目录: {sandbox}")
    logger.info(f"  内部数据目录: {info_dir}")

    local_rig = stage_file_to_sandbox(X_RIG, sandbox)
    local_blend = stage_file_to_sandbox(X_BLEND, sandbox)
    rig_stem = Path(X_RIG).stem
    tex_group = _geom_root("tex")
    rig_group = _geom_root("rig")

    logger.info("\nPhase 1: Blender 导出 ABC + 材质信息")
    local_abc = str(info_dir / f"{Path(X_BLEND).stem}.abc")
    materials_path = str(info_dir / f"{Path(X_BLEND).stem}_materials.json")
    res = submit_chain(local_blend, [
        {"skill_id": "blender_export_abc", "parameters": {"abc_path": local_abc, "cache_group": tex_group}},
        {"skill_id": "blender_extract_materials", "parameters": {"output_path": materials_path, "cache_group": tex_group}},
    ], run_dir=str(sandbox), suppress_report=True)
    if "task_id" not in res:
        logger.error(f"  Blender 提交失败: {res}")
        return False
    result = poll_task(res["task_id"], timeout=compute_poll_timeout(local_blend))
    if result.get("status") not in SUCCESS_STATUSES:
        logger.error(f"  Phase 1 失败: {result}")
        return False
    archive_audit(res["task_id"], info_dir)

    logger.info("\nPhase 2: Maya 场景内对比 + 同步拼装 + 后置验证 + 存盘")
    rig_info_pre_path = ""
    rig_info_post_path = ""
    compare_result_path = str(info_dir / f"{rig_stem}_pre_compare_result.json")
    post_compare_path = str(info_dir / f"{rig_stem}_post_compare_result.json")
    save_path = ""
    res = submit_chain(local_rig, [
        {
            "skill_id": "maya_compare_asset_in_scene",
            "parameters": {
                "input_source": local_abc,
                "output_path": compare_result_path,
                "cache_group": rig_group,
                "label_source": "tex",
                "label_target": "rig",
            },
        },
        {
            "skill_id": "maya_sync_rig_incremental",
            "parameters": {
                "source_abc": local_abc,
                "compare_result": compare_result_path,
                "cache_group": rig_group,
            },
        },
        {"skill_id": "maya_fix_shape_names", "parameters": {"target_group": rig_group}},
        {"skill_id": "maya_apply_materials", "parameters": {"materials_path": materials_path, "target_group": rig_group}},
        {
            "skill_id": "maya_compare_asset_in_scene",
            "parameters": {
                "input_source": local_abc,
                "output_path": post_compare_path,
                "cache_group": rig_group,
                "label_source": "tex",
                "label_target": "rig_post",
            },
        },
        {"skill_id": "save_scene", "parameters": {}},
    ], run_dir=str(sandbox), suppress_report=True)
    if "task_id" not in res:
        logger.error(f"  Phase 2 提交失败: {res}")
        return False
    phase2_result = poll_task(res["task_id"], timeout=compute_poll_timeout(local_rig, factor=1.5))
    if phase2_result.get("status") not in SUCCESS_STATUSES:
        logger.error(f"  Phase 2 失败: {phase2_result}")
        return False
    archive_audit(res["task_id"], info_dir)
    sync_outputs = _chain_step_outputs(phase2_result, "maya_sync_rig_incremental")
    save_outputs = _chain_step_outputs(phase2_result, "save_scene")
    save_path = save_outputs.get("output_path", "")
    pre_outputs = {"output_path": compare_result_path}
    post_outputs = {"output_path": post_compare_path}

    if not os.path.exists(compare_result_path) or not os.path.exists(post_compare_path):
        logger.error("  缺少场景内 compare_result 输出")
        return False
    if not save_path or not os.path.exists(save_path):
        logger.error(f"  缺少升版本后的 Maya 输出: {save_outputs}")
        return False

    post_compare_summary = _compare_summary(post_compare_path)
    post_blocking_issues = int(post_compare_summary.get("blocking", 0) or 0)
    passed = post_blocking_issues == 0
    report_path = write_final_report(
        sandbox, info_dir, local_rig, local_blend, save_path, local_abc,
        materials_path, rig_info_pre_path, compare_result_path,
        rig_info_post_path, post_compare_path, pre_outputs, post_outputs, sync_outputs, passed,
    )

    logger.info("\n" + "=" * 60)
    logger.info("全链路巡航测试结果")
    logger.info("=" * 60)
    logger.info(f"  沙盒目录: {sandbox}")
    logger.info(f"  唯一报告: {report_path}")
    logger.info(f"  根目录文件: {[p.name for p in sandbox.iterdir()]}")
    if passed:
        logger.info("  阻断差异已清零，巡航测试 PASS")
        return True
    logger.warning(f"  仍有 {post_blocking_issues} 个阻断差异，请查看报告")
    return False


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
