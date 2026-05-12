"""
验证 Fast Path mesh 的 BS 变形连续性（激活 BS 后检测撕裂）

重点：对走 IDENTICAL/AUTO_SAFE 路径的有 BS 的 mesh，
激活每个 BS target 后检查边连接顶点的位移是否连续。
"""
import sys, os, time, json, logging
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
logger = logging.getLogger('VerifyBSTearing')

PROJECT = "ysj"
ASSET = "mihouwang"

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
import numpy as np

BS_DISP_THRESHOLD = 0.3   # 边两端位移差异阈值 (cm)
BS_TEAR_RATIO = 0.03      # 突变边比例阈值

# 找到 cache 组下所有有 BS 的 mesh
cache_nodes = cmds.ls("*|cache", long=True, type="transform")
if not cache_nodes:
    result = {"status": "ERROR", "errors": ["未找到 cache 组"]}
else:
    cache_node = cache_nodes[0]
    all_meshes = cmds.listRelatives(cache_node, allDescendents=True, type="mesh", fullPath=True) or []
    vis_meshes = [m for m in all_meshes if not cmds.getAttr(m + ".intermediateObject")]

    mesh_results = {}
    errors = []
    total_bs_meshes = 0
    total_targets_checked = 0
    torn_meshes = 0

    for shape in vis_meshes:
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        bs_nodes = cmds.ls(history, type="blendShape")
        if not bs_nodes:
            continue

        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        short_name = transform.split("|")[-1]
        total_bs_meshes += 1

        # 获取 mesh 拓扑信息
        sel = om2.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn_mesh = om2.MFnMesh(dag)
        num_verts = fn_mesh.numVertices
        num_edges = fn_mesh.numEdges

        # 预收集所有边的顶点对
        edge_verts = np.array([fn_mesh.getEdgeVertices(e) for e in range(num_edges)], dtype=np.int32)

        # 获取基础位置（所有 BS 权重为 0 时）
        # 先保存当前所有 BS 权重
        saved_weights = {}
        for bs in bs_nodes:
            aliases = cmds.aliasAttr(bs, q=True) or []
            for i in range(0, len(aliases), 2):
                attr = f"{bs}.{aliases[i]}"
                try:
                    saved_weights[attr] = cmds.getAttr(attr)
                    cmds.setAttr(attr, 0.0)
                except:
                    pass

        # 获取基础位置
        sel2 = om2.MSelectionList()
        sel2.add(shape)
        dag2 = sel2.getDagPath(0)
        fn2 = om2.MFnMesh(dag2)
        base_pts = np.array([[p.x, p.y, p.z] for p in fn2.getPoints(om2.MSpace.kObject)], dtype=np.float64)

        torn_targets = []
        targets_checked = 0

        for bs in bs_nodes:
            aliases = cmds.aliasAttr(bs, q=True) or []
            target_names = [aliases[i] for i in range(0, len(aliases), 2)]

            for tgt_name in target_names:
                attr = f"{bs}.{tgt_name}"
                try:
                    cmds.setAttr(attr, 1.0)
                except:
                    continue

                # 获取变形后位置
                sel3 = om2.MSelectionList()
                sel3.add(shape)
                dag3 = sel3.getDagPath(0)
                fn3 = om2.MFnMesh(dag3)
                deformed_pts = np.array([[p.x, p.y, p.z] for p in fn3.getPoints(om2.MSpace.kObject)], dtype=np.float64)

                # 计算位移
                displacements = deformed_pts - base_pts
                disp_lens = np.linalg.norm(displacements, axis=1)
                max_disp = disp_lens.max()

                # 只检查有实际位移的目标
                if max_disp > 0.01:
                    targets_checked += 1
                    # 检查边连续性
                    d_diffs = np.linalg.norm(
                        displacements[edge_verts[:, 0]] - displacements[edge_verts[:, 1]], axis=1
                    )
                    tear_count = int(np.sum(d_diffs > BS_DISP_THRESHOLD))
                    tear_ratio = tear_count / max(num_edges, 1)

                    if tear_ratio > BS_TEAR_RATIO:
                        torn_targets.append({
                            "target": tgt_name,
                            "bs_node": bs,
                            "tear_edges": tear_count,
                            "total_edges": num_edges,
                            "ratio": round(tear_ratio, 4),
                            "max_disp": round(float(max_disp), 4),
                        })

                # 恢复
                try:
                    cmds.setAttr(attr, 0.0)
                except:
                    pass

        # 恢复所有原始权重
        for attr, val in saved_weights.items():
            try:
                cmds.setAttr(attr, val)
            except:
                pass

        total_targets_checked += targets_checked

        if torn_targets:
            torn_meshes += 1
            mesh_results[short_name] = {
                "status": "TORN",
                "targets_checked": targets_checked,
                "torn_targets": torn_targets[:10],
            }
            errors.append(f"{short_name}: {len(torn_targets)} 个 BS 目标撕裂")
        else:
            mesh_results[short_name] = {
                "status": "OK",
                "targets_checked": targets_checked,
            }

    # 汇总
    summary_lines = [
        f"有 BS 的 mesh: {total_bs_meshes}",
        f"检查的 BS 目标总数: {total_targets_checked}",
        f"撕裂 mesh: {torn_meshes}",
        "",
        "各 mesh 详情:",
    ]
    for name, info in mesh_results.items():
        if info["status"] == "OK":
            summary_lines.append(f"  {name}: {info['targets_checked']} targets checked -> OK")
        else:
            summary_lines.append(f"  {name}: TORN! ({len(info['torn_targets'])} targets)")
            for t in info["torn_targets"][:3]:
                summary_lines.append(f"    - {t['target']}: {t['tear_edges']}/{t['total_edges']} edges (ratio={t['ratio']})")

    if errors:
        summary_lines.append(f"\n错误 ({len(errors)}):")
        for e in errors:
            summary_lines.append(f"  - {e}")

    result = {
        "summary": "\n".join(summary_lines),
        "mesh_results": mesh_results,
        "errors": errors,
        "status": "PASS" if not errors else "FAIL",
        "stats": {
            "total_bs_meshes": total_bs_meshes,
            "total_targets_checked": total_targets_checked,
            "torn_meshes": torn_meshes,
        }
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


logger.info("提交 BS 变形连续性检测任务...")
res = submit_maya_chain(str(synced_file), [
    {
        'skill_id': 'exec_code',
        'parameters': {
            'code': VERIFY_CODE,
            'description': 'BS 变形连续性检测（撕裂检测）',
        }
    },
])

if 'task_id' not in res:
    logger.error(f"提交失败: {res}")
    sys.exit(1)

result = poll_task(res['task_id'])
if result.get('status') not in ('SUCCESS', 'CHAIN_SUCCESS'):
    logger.error(f"验证失败: {result}")
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
            logger.info("BS 变形连续性检测结果")
            logger.info("=" * 60)
            logger.info(verify_result.get('summary', '(无摘要)'))
            logger.info(f"\n最终状态: {verify_result.get('status', '?')}")
