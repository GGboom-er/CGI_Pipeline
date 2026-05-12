"""
验证同步后文件的变形完整性（SkinCluster + BlendShape）
"""
import sys
import os
import time
import json
import logging
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
logger = logging.getLogger('VerifyDeformation')

PROJECT = "ysj"
ASSET = "mihouwang"

# 找到最新的巡航测试沙盒
sandbox_dir = PROJECT_ROOT / "projects" / PROJECT
sandboxes = sorted(
    [d for d in sandbox_dir.iterdir() if d.is_dir() and "cruise_test" in d.name],
    key=lambda x: x.name, reverse=True
)
if not sandboxes:
    logger.error("未找到巡航测试沙盒")
    sys.exit(1)

sandbox = sandboxes[0]
synced_file = sandbox / "ysj_chr_mihouwang_rig_rigMaster_v002_synced.ma"
if not synced_file.exists():
    logger.error(f"同步文件不存在: {synced_file}")
    sys.exit(1)

logger.info(f"验证文件: {synced_file}")

VERIFY_CODE = r'''
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import json

errors = []
warnings = []
stats = {
    "total_meshes": 0,
    "meshes_with_skin": 0,
    "meshes_with_bs": 0,
    "skin_ok": 0,
    "skin_fail": 0,
    "bs_ok": 0,
    "bs_fail": 0,
}

# 找到 cache 组下所有 mesh
cache_nodes = cmds.ls("*|cache", long=True, type="transform")
if not cache_nodes:
    errors.append("未找到 cache 组")
    result = {"errors": errors, "stats": stats}
else:
    cache_node = cache_nodes[0]
    all_meshes = cmds.listRelatives(cache_node, allDescendents=True, type="mesh", fullPath=True) or []

    # 过滤 intermediate
    vis_meshes = [m for m in all_meshes if not cmds.getAttr(m + ".intermediateObject")]
    stats["total_meshes"] = len(vis_meshes)

    for shape in vis_meshes:
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        short_name = transform.split("|")[-1]

        # 检查 SkinCluster
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        skin_nodes = cmds.ls(history, type="skinCluster")

        if skin_nodes:
            stats["meshes_with_skin"] += 1
            skin = skin_nodes[0]
            try:
                # 验证 skinCluster 有效性
                influences = cmds.skinCluster(skin, q=True, influence=True) or []
                if not influences:
                    errors.append(f"{short_name}: skinCluster 无影响骨骼")
                    stats["skin_fail"] += 1
                    continue

                # 验证权重归一化
                sel = om2.MSelectionList()
                sel.add(skin)
                fn_skin = oma2.MFnSkinCluster(sel.getDependNode(0))

                sel2 = om2.MSelectionList()
                sel2.add(shape)
                dag = sel2.getDagPath(0)
                num_verts = om2.MFnMesh(dag).numVertices

                # 抽样检查前100个顶点的权重和
                check_count = min(num_verts, 100)
                comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                om2.MFnSingleIndexedComponent(comp).addElements(list(range(check_count)))

                weights, num_inf = fn_skin.getWeights(dag, comp)

                bad_verts = 0
                for i in range(check_count):
                    w_sum = sum(weights[i*num_inf:(i+1)*num_inf])
                    if abs(w_sum - 1.0) > 0.01:
                        bad_verts += 1

                if bad_verts > 0:
                    errors.append(f"{short_name}: {bad_verts}/{check_count} 顶点权重未归一化")
                    stats["skin_fail"] += 1
                else:
                    stats["skin_ok"] += 1

            except Exception as e:
                errors.append(f"{short_name}: skinCluster 验证异常 - {e}")
                stats["skin_fail"] += 1

        # 检查 BlendShape
        bs_nodes = cmds.ls(history, type="blendShape")
        if bs_nodes:
            stats["meshes_with_bs"] += 1
            for bs in bs_nodes:
                try:
                    targets = cmds.blendShape(bs, q=True, target=True) or []
                    weight_count = cmds.blendShape(bs, q=True, weightCount=True)
                    if weight_count and weight_count > 0:
                        stats["bs_ok"] += 1
                    else:
                        errors.append(f"{short_name}: blendShape {bs} 无目标")
                        stats["bs_fail"] += 1
                except Exception as e:
                    errors.append(f"{short_name}: blendShape 验证异常 - {e}")
                    stats["bs_fail"] += 1

    # 汇总
    summary_lines = [
        f"总 mesh: {stats['total_meshes']}",
        f"有 SkinCluster: {stats['meshes_with_skin']} (OK: {stats['skin_ok']}, FAIL: {stats['skin_fail']})",
        f"有 BlendShape: {stats['meshes_with_bs']} (OK: {stats['bs_ok']}, FAIL: {stats['bs_fail']})",
    ]

    if errors:
        summary_lines.append(f"\n错误 ({len(errors)}):")
        for e in errors[:20]:
            summary_lines.append(f"  - {e}")

    result = {
        "summary": "\n".join(summary_lines),
        "stats": stats,
        "errors": errors[:50],
        "status": "PASS" if not errors else "FAIL",
    }
'''

def poll_task(task_id, timeout=600):
    start_time = time.time()
    logger.info(f"  轮询任务 [{task_id}]...")
    while time.time() - start_time < timeout:
        res = _read_audit(task_id)
        status = res.get('status', 'NOT_FOUND')
        if status in ('SUCCESS', 'ERROR', 'BLOCKED', 'NEEDS_ATTENTION', 'CHAIN_ABORTED', 'CHAIN_SUCCESS'):
            logger.info(f"  任务 [{task_id}] 结束: {status}")
            return res
        time.sleep(3)
    return {'status': 'TIMEOUT'}


def submit_maya_chain(source_path, chain):
    payload = {
        'skill_id': 'execute_chain',
        'project': PROJECT,
        'asset_name': ASSET,
        'source_path': source_path,
        'skill_chain': chain,
    }
    return _submit_chain(payload)


res = submit_maya_chain(str(synced_file), [
    {
        'skill_id': 'exec_code',
        'parameters': {
            'code': VERIFY_CODE,
            'description': '验证变形完整性 (SkinCluster + BlendShape)',
        }
    },
])

if 'task_id' not in res:
    logger.error(f"提交失败: {res}")
    sys.exit(1)

result = poll_task(res['task_id'])
if result.get('status') not in ('SUCCESS', 'CHAIN_SUCCESS'):
    logger.error(f"验证失败: {result}")
    # 打印详细错误
    chain_results = result.get('chain_results', [])
    for cr in chain_results:
        detail = cr.get('detail', '')
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except:
                pass
        logger.error(f"  Step {cr.get('step')}: {detail}")
    sys.exit(1)

# 解析结果
chain_results = result.get('chain_results', [])
for cr in chain_results:
    detail = cr.get('detail', '')
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except:
            pass
    if isinstance(detail, dict):
        outputs = detail.get('outputs', {})
        verify_result = outputs.get('result', {})
        if verify_result:
            logger.info("\n" + "=" * 60)
            logger.info("变形验证结果")
            logger.info("=" * 60)
            logger.info(verify_result.get('summary', '(无摘要)'))
            logger.info(f"\n状态: {verify_result.get('status', '?')}")
