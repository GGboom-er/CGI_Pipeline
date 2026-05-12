"""
深入检查 mihouwang_head1 的 BS 结构：
- 总共多少 target
- 哪些是 Live Target（inputGeomTarget 有连接）
- Live Target 的 mesh 是否在 cache 组下（是否被同步影响）
- 激活 Live Target 后的变形连续性
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
logger = logging.getLogger('VerifyLiveBS')

PROJECT = "ysj"
ASSET = "mihouwang"

sandbox_dir = PROJECT_ROOT / "projects" / PROJECT
sandboxes = sorted(
    [d for d in sandbox_dir.iterdir() if d.is_dir() and "cruise_test" in d.name],
    key=lambda x: x.name, reverse=True
)
sandbox = sandboxes[0]
synced_file = sandbox / "ysj_chr_mihouwang_rig_rigMaster_v002_synced.ma"
logger.info(f"验证文件: {synced_file}")

VERIFY_CODE = r'''
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import numpy as np

BS_DISP_THRESHOLD = 0.3
BS_TEAR_RATIO = 0.03

# 找 mihouwang_head1
cache_nodes = cmds.ls("*|cache", long=True, type="transform")
cache_node = cache_nodes[0]

# 找所有有 BS 的 mesh
all_meshes = cmds.listRelatives(cache_node, allDescendents=True, type="mesh", fullPath=True) or []
vis_meshes = [m for m in all_meshes if not cmds.getAttr(m + ".intermediateObject")]

report_lines = []
errors = []
total_live_targets = 0
total_static_targets = 0
live_torn = 0

for shape in vis_meshes:
    transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
    short_name = transform.split("|")[-1]

    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    bs_nodes = cmds.ls(history, type="blendShape")
    if not bs_nodes:
        continue

    report_lines.append(f"\n=== {short_name} ===")

    for bs in bs_nodes:
        aliases = cmds.aliasAttr(bs, q=True) or []
        target_names = [aliases[i] for i in range(0, len(aliases), 2)]
        report_lines.append(f"  BS节点: {bs} ({len(target_names)} targets)")

        # 检查每个 target 是否是 Live Target
        sel_bs = om2.MSelectionList()
        sel_bs.add(bs)
        fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
        it_plug = fn_bs.findPlug("inputTarget", False)
        geom_indices = it_plug.getExistingArrayAttributeIndices()
        if not geom_indices:
            continue
        geom_idx = geom_indices[0]
        itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)
        target_indices = itg_plug.getExistingArrayAttributeIndices()

        # 构建 idx -> name
        idx_to_name = {}
        for i in range(0, len(aliases), 2):
            attr_ref = aliases[i+1]
            try:
                wi = int(attr_ref.split("[")[1].rstrip("]"))
                idx_to_name[wi] = aliases[i]
            except:
                pass

        live_targets = []
        static_targets = []

        for t_idx in target_indices:
            t_name = idx_to_name.get(t_idx, f"target_{t_idx}")
            tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
            iti_array_plug = tgt_plug.child(0)
            item_indices = iti_array_plug.getExistingArrayAttributeIndices()

            is_live = False
            for item_idx in item_indices:
                iti_plug = iti_array_plug.elementByLogicalIndex(item_idx)
                for ci in range(iti_plug.numChildren()):
                    child = iti_plug.child(ci)
                    attr_name = om2.MFnAttribute(child.attribute()).name
                    if attr_name == "inputGeomTarget" and child.isConnected:
                        conns = cmds.listConnections(child.name(), source=True, destination=False, shapes=True) or []
                        if conns:
                            is_live = True
                            live_targets.append({"name": t_name, "live_mesh": conns[0]})
                            break
                if is_live:
                    break

            if not is_live:
                static_targets.append(t_name)

        total_live_targets += len(live_targets)
        total_static_targets += len(static_targets)

        if live_targets:
            report_lines.append(f"    Live Targets ({len(live_targets)}):")
            for lt in live_targets[:5]:
                report_lines.append(f"      - {lt['name']} -> {lt['live_mesh']}")
            if len(live_targets) > 5:
                report_lines.append(f"      ... 及其他 {len(live_targets)-5} 个")
        if static_targets:
            report_lines.append(f"    Static Targets ({len(static_targets)}):")
            for st in static_targets[:5]:
                report_lines.append(f"      - {st}")
            if len(static_targets) > 5:
                report_lines.append(f"      ... 及其他 {len(static_targets)-5} 个")

    # 对有 Live Target 的 mesh 做变形连续性检测
    # 激活所有 BS target（包括 Live）逐个检查
    sel = om2.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    fn_mesh = om2.MFnMesh(dag)
    num_verts = fn_mesh.numVertices
    num_edges = fn_mesh.numEdges
    edge_verts = np.array([fn_mesh.getEdgeVertices(e) for e in range(num_edges)], dtype=np.int32)

    # 保存并清零所有 BS 权重
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
    fn2 = om2.MFnMesh(sel2.getDagPath(0))
    base_pts = np.array([[p.x, p.y, p.z] for p in fn2.getPoints(om2.MSpace.kObject)], dtype=np.float64)

    torn_list = []
    checked = 0
    for attr, val in saved_weights.items():
        try:
            cmds.setAttr(attr, 1.0)
        except:
            continue

        sel3 = om2.MSelectionList()
        sel3.add(shape)
        fn3 = om2.MFnMesh(sel3.getDagPath(0))
        deformed_pts = np.array([[p.x, p.y, p.z] for p in fn3.getPoints(om2.MSpace.kObject)], dtype=np.float64)

        displacements = deformed_pts - base_pts
        max_disp = np.linalg.norm(displacements, axis=1).max()

        if max_disp > 0.01:
            checked += 1
            d_diffs = np.linalg.norm(
                displacements[edge_verts[:, 0]] - displacements[edge_verts[:, 1]], axis=1
            )
            tear_count = int(np.sum(d_diffs > BS_DISP_THRESHOLD))
            tear_ratio = tear_count / max(num_edges, 1)
            if tear_ratio > BS_TEAR_RATIO:
                tgt_name = attr.split(".")[-1]
                torn_list.append(f"{tgt_name}: {tear_count}/{num_edges} (ratio={tear_ratio:.4f})")
                live_torn += 1

        try:
            cmds.setAttr(attr, 0.0)
        except:
            pass

    # 恢复
    for attr, val in saved_weights.items():
        try:
            cmds.setAttr(attr, val)
        except:
            pass

    report_lines.append(f"    变形检测: {checked} targets checked, {len(torn_list)} torn")
    if torn_list:
        for t in torn_list[:5]:
            report_lines.append(f"      TORN: {t}")
        errors.extend([f"{short_name}: {t}" for t in torn_list])

summary = "\n".join([
    f"总计: Live={total_live_targets}, Static={total_static_targets}",
    f"撕裂: {live_torn}",
    "",
] + report_lines)

result = {
    "summary": summary,
    "errors": errors,
    "status": "PASS" if not errors else "FAIL",
    "stats": {
        "live_targets": total_live_targets,
        "static_targets": total_static_targets,
        "torn": live_torn,
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


logger.info("提交 Live BS 深度检测任务...")
res = submit_maya_chain(str(synced_file), [
    {
        'skill_id': 'exec_code',
        'parameters': {
            'code': VERIFY_CODE,
            'description': 'Live BS 深度检测',
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
            logger.info("Live BS 深度检测结果")
            logger.info("=" * 60)
            logger.info(verify_result.get('summary', '(无摘要)'))
            logger.info(f"\n最终状态: {verify_result.get('status', '?')}")
