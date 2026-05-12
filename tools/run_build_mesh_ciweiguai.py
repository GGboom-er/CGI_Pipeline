"""
ciweiguai: Blender 导出 ABC + 材质 → Maya PyAlembic 构建 + 材质赋予 + 保存
==========================================================================
测试 blender_to_maya_full_build 工作流的完整链路。
"""
import sys
import os
import time
import shutil
import logging
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.service_manager as _sm
import atexit
try:
    atexit.unregister(_sm.shutdown_all)
except Exception:
    pass

from mcp_server.internals import _submit_chain, _read_audit

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('BuildMeshTest')

PROJECT = "ysj"
ASSET = "ciweiguai"
X_BLEND = r"X:\Project\ysj\pub\assets\chr\ciweiguai\tex\texMaster\ysj_chr_ciweiguai_tex_texMaster_v001.blend"
POLL_TIMEOUT = 600


def create_sandbox():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox = PROJECT_ROOT / "projects" / PROJECT / f"{ts}_{ASSET}_build_mesh_test"
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def stage_file(src: str, sandbox: Path) -> str:
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src}")
    dst = sandbox / src_path.name
    if not dst.exists():
        logger.info(f"  拷贝到沙盒: {src_path.name} ({src_path.stat().st_size / 1024 / 1024:.1f} MB)")
        shutil.copy2(str(src_path), str(dst))
    return str(dst)


def poll_task(task_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    start_time = time.time()
    logger.info(f"  轮询任务 [{task_id}]...")
    while time.time() - start_time < timeout:
        res = _read_audit(task_id)
        status = res.get('status', 'NOT_FOUND')
        if status in ['SUCCESS', 'ERROR', 'BLOCKED', 'CHAIN_ABORTED', 'CHAIN_SUCCESS']:
            logger.info(f"  任务 [{task_id}] 结束: {status}")
            return res
        time.sleep(3)
    logger.error(f"  任务 [{task_id}] 轮询超时！")
    return {'status': 'TIMEOUT'}


def run_test():
    logger.info("=" * 60)
    logger.info("Blender->Maya PyAlembic 构建测试 (ciweiguai)")
    logger.info("=" * 60)

    # ── Phase 0: 沙盒 ──
    sandbox = create_sandbox()
    logger.info(f"  沙盒: {sandbox}")
    local_blend = stage_file(X_BLEND, sandbox)

    blend_stem = Path(X_BLEND).stem
    local_abc = str(sandbox / f"{blend_stem}.abc")
    local_materials = str(sandbox / f"{blend_stem}_materials.json")
    save_path = str(sandbox / f"{blend_stem}_built.ma")

    # ── Phase 1: Blender 导出 ABC + 材质 ──
    logger.info("\nPhase 1: Blender 导出 ABC + 采集材质")
    res = _submit_chain({
        'skill_id': 'execute_chain',
        'project': PROJECT,
        'asset_name': ASSET,
        'source_path': local_blend,
        'skill_chain': [
            {
                'skill_id': 'blender_export_abc',
                'parameters': {'abc_path': local_abc}
            },
            {
                'skill_id': 'blender_extract_materials',
                'parameters': {'output_path': local_materials}
            },
        ],
    })
    if 'task_id' not in res:
        logger.error(f"  Blender 提交失败: {res}")
        return False
    result = poll_task(res['task_id'])
    if result.get('status') not in ('SUCCESS', 'CHAIN_SUCCESS'):
        logger.error(f"  Phase 1 失败: {result}")
        return False
    logger.info(f"  ABC: {local_abc}")
    logger.info(f"  材质: {local_materials}")

    # ── Phase 2: Maya PyAlembic 构建 + 材质赋予 + 保存 ──
    logger.info("\nPhase 2: Maya 构建 mesh + 材质赋予 + 保存")
    res = _submit_chain({
        'skill_id': 'execute_chain',
        'project': PROJECT,
        'asset_name': ASSET,
        'source_path': '',
        'skill_chain': [
            {
                'skill_id': 'maya_build_mesh_from_abc',
                'parameters': {'abc_path': local_abc}
            },
            {
                'skill_id': 'maya_apply_materials',
                'parameters': {'materials_path': local_materials}
            },
            {
                'skill_id': 'save_scene',
                'parameters': {'save_path': save_path}
            },
        ],
    })
    if 'task_id' not in res:
        logger.error(f"  Maya 提交失败: {res}")
        return False
    result = poll_task(res['task_id'])
    if result.get('status') not in ('SUCCESS', 'CHAIN_SUCCESS'):
        logger.error(f"  Phase 2 失败: {result}")
        return False

    # ── 结果 ──
    logger.info("\n" + "=" * 60)
    logger.info("结果:")
    logger.info(f"  沙盒: {sandbox}")
    sandbox_files = [f.name for f in sandbox.iterdir()]
    logger.info(f"  文件: {sandbox_files}")

    if Path(save_path).exists():
        size_mb = Path(save_path).stat().st_size / 1024 / 1024
        logger.info(f"  Maya 文件: {save_path} ({size_mb:.1f} MB)")
        logger.info("  PASS")
        return True
    else:
        logger.warning("  Maya 文件未生成，请检查日志。")
        return False


if __name__ == '__main__':
    success = run_test()
    sys.exit(0 if success else 1)
