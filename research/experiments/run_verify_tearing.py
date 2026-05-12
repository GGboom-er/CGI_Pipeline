"""
验证同步后 VOTING POOL mesh 的蒙皮权重和 BS 是否正确传递（检测撕裂）

撕裂检测逻辑：
1. 蒙皮：遍历所有边，检查边两端顶点的权重向量差异（L2距离）。
   如果大量边的权重突变超过阈值，说明投射权重不连续 → 撕裂。
2. BS：激活每个目标后，检查边两端顶点的位移向量差异。
   如果相邻顶点位移方向/幅度突变，说明 BS delta 不连续 → 撕裂。
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
logger = logging.getLogger('VerifyTearing')

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

# VOTING POOL 处理过的 mesh（从报告中提取）
# 这些是同步时走了空间投射的 mesh，最容易出现权重撕裂
VOTING_POOL_MESHES = [
    "mihouwang_hair9",
    "mihouwang_hair42",
    "mihouwang_hair13",
    "mihouwang_hair4",
    "mihouwang_hair20",
    "mihouwang_hair26",
    "mihouwang_hair19",
    "mihouwang_clothes3",
    "mihouwang_hair27",
    "mihouwang_hairsui1_hairbasemesh",
    "mihouwang_body1_hairbasemesh",
]

VERIFY_CODE = r'''
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import numpy as np
import json

WEIGHT_EDGE_THRESHOLD = 0.8   # 边两端权重向量 L2 距离超过此值视为"突变"
WEIGHT_TEAR_RATIO = 0.05      # 突变边占总边数比例超过此值视为"撕裂"
BS_DISP_THRESHOLD = 0.5       # BS 位移差异阈值 (cm)
BS_TEAR_RATIO = 0.05          # BS 突变边比例阈值

VOTING_POOL_MESHES = ''' + json.dumps(VOTING_POOL_MESHES) + r'''

results = {}
errors = []

# 找到 cache 组
cache_nodes = cmds.ls("*|cache", long=True, type="transform")
if not cache_nodes:
    errors.append("未找到 cache 组")
    result = {"status": "ERROR", "errors": errors, "results": {}}
else:
    cache_node = cache_nodes[0]

    for mesh_name in VOTING_POOL_MESHES:
        # 查找 mesh transform
        candidates = cmds.ls(f"*|{mesh_name}", long=True, type="transform")
        # 过滤只保留 cache 下的
        candidates = [c for c in candidates if c.startswith(cache_node)]
        if not candidates:
            results[mesh_name] = {"status": "NOT_FOUND"}
            continue

        transform = candidates[0]
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, noIntermediate=True)
        if not shapes:
            results[mesh_name] = {"status": "NO_SHAPE"}
            continue

        shape = shapes[0]
        mesh_result = {"skin_tear": None, "bs_tear": None}

        # ═══ 蒙皮撕裂检测 ═══
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        skin_nodes = cmds.ls(history, type="skinCluster")

        if skin_nodes:
            skin = skin_nodes[0]
            try:
                sel = om2.MSelectionList()
                sel.add(shape)
                dag = sel.getDagPath(0)
                fn_mesh = om2.MFnMesh(dag)

                num_verts = fn_mesh.numVertices
                num_edges = fn_mesh.numEdges

                # 获取所有顶点权重
                sel_skin = om2.MSelectionList()
                sel_skin.add(skin)
                fn_skin = oma2.MFnSkinCluster(sel_skin.getDependNode(0))

                comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
                om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)
                weights, num_inf = fn_skin.getWeights(dag, comp)

                # 转为 numpy 矩阵 [num_verts x num_inf]
                w_matrix = np.array(weights).reshape(num_verts, num_inf)

                # 收集所有边的顶点对，向量化计算
                edge_verts = np.array([fn_mesh.getEdgeVertices(e) for e in range(num_edges)], dtype=np.int32)
                diffs = np.linalg.norm(w_matrix[edge_verts[:, 0]] - w_matrix[edge_verts[:, 1]], axis=1)
                tear_edges = int(np.sum(diffs > WEIGHT_EDGE_THRESHOLD))

                tear_ratio = tear_edges / max(num_edges, 1)
                is_torn = tear_ratio > WEIGHT_TEAR_RATIO

                mesh_result["skin_tear"] = {
                    "total_edges": num_edges,
                    "tear_edges": tear_edges,
                    "tear_ratio": round(tear_ratio, 4),
                    "threshold": WEIGHT_TEAR_RATIO,
                    "is_torn": is_torn,
                }

                if is_torn:
                    errors.append(f"{mesh_name}: 蒙皮撕裂 ({tear_edges}/{num_edges} 边突变, ratio={tear_ratio:.4f})")

            except Exception as e:
                mesh_result["skin_tear"] = {"error": str(e)}
                errors.append(f"{mesh_name}: 蒙皮检测异常 - {e}")
        else:
            mesh_result["skin_tear"] = {"status": "NO_SKIN"}

        # ═══ BlendShape 撕裂检测 ═══
        bs_nodes = cmds.ls(history, type="blendShape")
        if bs_nodes:
            bs_results = []
            for bs in bs_nodes:
                try:
                    # 获取基础位置
                    sel_bs = om2.MSelectionList()
                    sel_bs.add(shape)
                    dag_bs = sel_bs.getDagPath(0)
                    fn_bs = om2.MFnMesh(dag_bs)
                    base_pts = np.array(fn_bs.getPoints(om2.MSpace.kObject)).reshape(-1, 4)[:, :3]

                    num_edges_bs = fn_bs.numEdges

                    # 获取所有目标
                    aliases = cmds.aliasAttr(bs, q=True) or []
                    target_names = [aliases[i] for i in range(0, len(aliases), 2)]

                    bs_torn_targets = []
                    for tgt in target_names[:10]:  # 最多检查10个目标
                        attr = f"{bs}.{tgt}"
                        old_val = cmds.getAttr(attr)
                        cmds.setAttr(attr, 1.0)

                        # 获取变形后位置
                        sel_d = om2.MSelectionList()
                        sel_d.add(shape)
                        dag_d = sel_d.getDagPath(0)
                        fn_d = om2.MFnMesh(dag_d)
                        deformed_pts = np.array(fn_d.getPoints(om2.MSpace.kObject)).reshape(-1, 4)[:, :3]

                        # 计算位移
                        displacements = deformed_pts - base_pts
                        disp_lens = np.linalg.norm(displacements, axis=1)

                        # 只检查有位移的目标
                        max_disp = disp_lens.max()
                        if max_disp > 0.001:
                            # 向量化边检测
                            edge_verts_bs = np.array([fn_bs.getEdgeVertices(e) for e in range(num_edges_bs)], dtype=np.int32)
                            d_diffs = np.linalg.norm(displacements[edge_verts_bs[:, 0]] - displacements[edge_verts_bs[:, 1]], axis=1)
                            tear_edges_bs = int(np.sum(d_diffs > BS_DISP_THRESHOLD))

                            bs_ratio = tear_edges_bs / max(num_edges_bs, 1)
                            if bs_ratio > BS_TEAR_RATIO:
                                bs_torn_targets.append({
                                    "target": tgt,
                                    "tear_edges": tear_edges_bs,
                                    "ratio": round(bs_ratio, 4),
                                })

                        cmds.setAttr(attr, old_val)

                    if bs_torn_targets:
                        bs_results.append({"node": bs, "torn_targets": bs_torn_targets})
                        errors.append(f"{mesh_name}: BS撕裂 {len(bs_torn_targets)} 个目标")

                except Exception as e:
                    bs_results.append({"node": bs, "error": str(e)})
                    errors.append(f"{mesh_name}: BS检测异常 - {e}")

            mesh_result["bs_tear"] = bs_results if bs_results else {"status": "OK"}
        else:
            mesh_result["bs_tear"] = {"status": "NO_BS"}

        results[mesh_name] = mesh_result

    # 汇总
    skin_tested = sum(1 for r in results.values() if isinstance(r.get("skin_tear"), dict) and "total_edges" in r.get("skin_tear", {}))
    skin_torn = sum(1 for r in results.values() if isinstance(r.get("skin_tear"), dict) and r.get("skin_tear", {}).get("is_torn"))
    bs_tested = sum(1 for r in results.values() if isinstance(r.get("bs_tear"), dict) and r.get("bs_tear", {}).get("status") != "NO_BS" and not isinstance(r.get("bs_tear"), list))
    bs_torn = sum(1 for r in results.values() if isinstance(r.get("bs_tear"), list) and len(r["bs_tear"]) > 0)

    summary_lines = [
        f"VOTING POOL mesh 撕裂检测:",
        f"  蒙皮: 检测 {skin_tested} 个, 撕裂 {skin_torn} 个",
        f"  BS: 检测 {len([r for r in results.values() if 'bs_tear' in r and r['bs_tear'] != {'status': 'NO_BS'}])} 个, 撕裂 {bs_torn} 个",
        f"",
        f"各 mesh 详情:",
    ]
    for name, r in results.items():
        skin_info = r.get("skin_tear", {})
        if isinstance(skin_info, dict) and "tear_ratio" in skin_info:
            skin_str = f"skin: {skin_info['tear_edges']}/{skin_info['total_edges']} 突变边 (ratio={skin_info['tear_ratio']}) {'❌撕裂' if skin_info['is_torn'] else '✓OK'}"
        elif isinstance(skin_info, dict) and skin_info.get("status") == "NO_SKIN":
            skin_str = "skin: 无"
        else:
            skin_str = f"skin: {skin_info}"

        bs_info = r.get("bs_tear", {})
        if isinstance(bs_info, dict) and bs_info.get("status") in ("OK", "NO_BS"):
            bs_str = f"bs: {bs_info.get('status', '?')}"
        elif isinstance(bs_info, list) and len(bs_info) > 0:
            bs_str = f"bs: ❌{len(bs_info)} 个节点有撕裂"
        else:
            bs_str = f"bs: ✓OK"

        summary_lines.append(f"  {name}: {skin_str} | {bs_str}")

    if errors:
        summary_lines.append(f"\n❌ 错误 ({len(errors)}):")
        for e in errors[:20]:
            summary_lines.append(f"  - {e}")

    result = {
        "summary": "\n".join(summary_lines),
        "results": {k: v for k, v in list(results.items())[:5]},  # 只返回前5个详情避免太大
        "errors": errors,
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


logger.info("提交撕裂检测任务...")
res = submit_maya_chain(str(synced_file), [
    {
        'skill_id': 'exec_code',
        'parameters': {
            'code': VERIFY_CODE,
            'description': '蒙皮/BS 撕裂检测',
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
            logger.info("蒙皮/BS 撕裂检测结果")
            logger.info("=" * 60)
            logger.info(verify_result.get('summary', '(无摘要)'))
            logger.info(f"\n最终状态: {verify_result.get('status', '?')}")
