"""检查 synced 文件中的垃圾节点"""
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
logger = logging.getLogger('CheckGarbage')

PROJECT = "ysj"
ASSET = "mihouwang"

sandbox_dir = PROJECT_ROOT / "projects" / PROJECT
sandboxes = sorted(
    [d for d in sandbox_dir.iterdir() if d.is_dir() and "cruise_test" in d.name],
    key=lambda x: x.name, reverse=True
)
sandbox = sandboxes[0]
synced_file = sandbox / "ysj_chr_mihouwang_rig_rigMaster_v002_synced.ma"

VERIFY_CODE = r'''
import maya.cmds as cmds

report = []

# 1. 检查 unknown 节点
unknown = cmds.ls(type="unknown") or []
unknown_plugins = cmds.unknownPlugin(query=True, list=True) or []
report.append(f"Unknown 节点: {len(unknown)}")
report.append(f"Unknown 插件: {len(unknown_plugins)}")
if unknown_plugins:
    for p in unknown_plugins[:10]:
        report.append(f"  - {p}")

# 2. 检查空 transform（无 shape 无子节点）
all_transforms = cmds.ls(type="transform", long=True) or []
empty_transforms = []
for t in all_transforms:
    children = cmds.listRelatives(t, children=True, fullPath=True) or []
    if not children:
        # 排除 joint 和 locator
        if cmds.nodeType(t) == "transform":
            shapes = cmds.listRelatives(t, shapes=True) or []
            if not shapes:
                empty_transforms.append(t)

report.append(f"\n空 transform: {len(empty_transforms)}")
if empty_transforms:
    for e in empty_transforms[:10]:
        report.append(f"  - {e}")
    if len(empty_transforms) > 10:
        report.append(f"  ... 及其他 {len(empty_transforms)-10} 个")

# 3. 检查 displayLayer
layers = cmds.ls(type="displayLayer") or []
layers = [l for l in layers if l != "defaultLayer"]
report.append(f"\nDisplay Layers: {len(layers)}")
for l in layers:
    members = cmds.editDisplayLayerMembers(l, query=True) or []
    report.append(f"  - {l}: {len(members)} members")

# 4. 检查 RIG_ 前缀残留
rig_prefix_nodes = cmds.ls("RIG_*", long=True) or []
report.append(f"\nRIG_ 前缀残留: {len(rig_prefix_nodes)}")
if rig_prefix_nodes:
    for n in rig_prefix_nodes[:10]:
        report.append(f"  - {n} ({cmds.nodeType(n)})")
    if len(rig_prefix_nodes) > 10:
        report.append(f"  ... 及其他 {len(rig_prefix_nodes)-10} 个")

# 5. 检查 backup/orig 残留
backup_nodes = cmds.ls("*_backup*", "*_orig*", "*_BACKUP*", long=True) or []
# 过滤掉正常的 Orig shape（intermediate object）
real_backup = []
for n in backup_nodes:
    if cmds.objectType(n) == "mesh":
        if cmds.getAttr(n + ".intermediateObject"):
            continue  # 正常的 orig shape
    real_backup.append(n)
report.append(f"\nBackup/Orig 残留: {len(real_backup)}")
if real_backup:
    for n in real_backup[:10]:
        report.append(f"  - {n}")

# 6. 检查 unused shading nodes
# 简单检查：没有连接到任何 mesh 的 material
all_mats = cmds.ls(materials=True) or []
unused_mats = []
for mat in all_mats:
    if mat in ("lambert1", "particleCloud1", "standardSurface1"):
        continue
    sg = cmds.listConnections(mat, type="shadingEngine") or []
    if not sg:
        unused_mats.append(mat)
    else:
        has_members = False
        for s in sg:
            members = cmds.sets(s, query=True) or []
            if members:
                has_members = True
                break
        if not has_members:
            unused_mats.append(mat)

report.append(f"\n未使用材质: {len(unused_mats)}")
if unused_mats:
    for m in unused_mats[:10]:
        report.append(f"  - {m}")

# 7. 顶层结构
top_nodes = cmds.ls(assemblies=True) or []
report.append(f"\n顶层节点: {len(top_nodes)}")
for n in top_nodes:
    report.append(f"  - {n}")

# 8. cache 组下的 mesh 数量
cache_nodes = cmds.ls("*|cache", long=True, type="transform")
if cache_nodes:
    cache_meshes = cmds.listRelatives(cache_nodes[0], allDescendents=True, type="mesh", fullPath=True) or []
    vis = [m for m in cache_meshes if not cmds.getAttr(m + ".intermediateObject")]
    report.append(f"\ncache 组 mesh: {len(vis)} (visible) / {len(cache_meshes)} (total)")

result = {"summary": "\n".join(report), "status": "OK"}
'''

def poll_task(task_id, timeout=600):
    start_time = time.time()
    logger.info(f"  轮询任务 [{task_id}]...")
    while time.time() - start_time < timeout:
        res = _read_audit(task_id)
        status = res.get('status', 'NOT_FOUND')
        if status in ('SUCCESS', 'ERROR', 'BLOCKED', 'AUDIT_FAILED', 'CHAIN_AUDIT_FAILED', 'WORKFLOW_AUDIT_FAILED', 'CHAIN_ABORTED', 'CHAIN_SUCCESS'):
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

logger.info("提交垃圾检测任务...")
res = submit_maya_chain(str(synced_file), [
    {'skill_id': 'exec_code', 'parameters': {'code': VERIFY_CODE, 'description': '垃圾节点检测'}}
])

if 'task_id' not in res:
    logger.error(f"提交失败: {res}")
    sys.exit(1)

result = poll_task(res['task_id'])
chain_results = result.get('chain_results', [])
for cr in chain_results:
    detail = cr.get('detail', '')
    if isinstance(detail, str):
        try: detail = json.loads(detail)
        except: pass
    if isinstance(detail, dict):
        outputs = detail.get('outputs', {})
        verify_result = outputs.get('result', {})
        if verify_result:
            logger.info("\n" + "=" * 60)
            logger.info("垃圾节点检测结果")
            logger.info("=" * 60)
            logger.info(verify_result.get('summary', ''))
