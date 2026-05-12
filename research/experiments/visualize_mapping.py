import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from dccs.maya.worker import MayaCommandPortWorker

def run_visualizer():
    worker = MayaCommandPortWorker(port=7002)
    try:
        worker.start()
    except Exception as e:
        print(f"无法连接到 Maya: {e}")
        return
        
    code = """
import sys
if r'y:\\GGbommer\\scripts\\CGI_Pipeline' not in sys.path:
    sys.path.insert(0, r'y:\\GGbommer\\scripts\\CGI_Pipeline')

import maya.cmds as cmds
from core.asset_info_schema import compare, make_empty_info, make_mesh_entry

result = {'status': 'SUCCESS'}
try:
    if not cmds.objExists('AA') or not cmds.objExists('HH'):
        result['status'] = 'ERROR'
        result['error'] = '场景中找不到名为 AA 或 HH 的模型！请检查命名。'
    else:
        # 提取用户场景中的实际数据
        pts_rig = cmds.xform('AA.vtx[*]', q=True, ws=True, t=True)
        pts_asset = cmds.xform('HH.vtx[*]', q=True, ws=True, t=True)

        info_rig = make_empty_info()
        info_rig["meshes"]["AA"] = make_mesh_entry(len(pts_rig)//3, pts_rig)

        info_asset = make_empty_info()
        info_asset["meshes"]["HH"] = make_mesh_entry(len(pts_asset)//3, pts_asset)

        # 运行比较：暂时关闭 CPD，仅使用纯空间映射
        print("====== 正在执行管线几何引擎解算 (无 CPD 极速版) ======")
        comp_result = compare(info_asset, info_rig, label_a="HH", label_b="AA", enable_cpd=False)

        paired = comp_result.get("paired", [])
        if paired:
            p = paired[0]
            action = p.get("actionability")
            mapping = p.get("subset_mapping", [])
            free = p.get("free_a_indices", [])
            
            # 清理旧的连线
            if cmds.objExists("Mapping_Proof_Lines"):
                cmds.delete("Mapping_Proof_Lines")
            lines_group = cmds.group(em=True, name="Mapping_Proof_Lines")
            
            # 可视化连线
            count = 0
            if action in ["PARTIAL_MATCH", "REORDER", "IDENTICAL"]:
                for asset_idx, rig_idx in enumerate(mapping):
                    if rig_idx != -1:
                        p1 = cmds.xform(f'HH.vtx[{asset_idx}]', q=True, ws=True, t=True)
                        p2 = cmds.xform(f'AA.vtx[{rig_idx}]', q=True, ws=True, t=True)
                        crv = cmds.curve(d=1, p=[p1, p2])
                        cmds.parent(crv, lines_group)
                        count += 1
                        
            # 高亮选取游离点
            if free:
                cmds.select([f'HH.vtx[{i}]' for i in free])
            else:
                cmds.select(clear=True)
                
            msg = f"解算完成！级别: {action}。生成了 {count} 条对应连线。游离靶点: {len(free)} 个。"
            print(msg)
            result['message'] = msg
        else:
            result['message'] = "匹配彻底失败，掉入 DELETE / SPATIAL_VOTING 级别！"
            print(result['message'])
except Exception as e:
    import traceback
    result['status'] = 'ERROR'
    result['error'] = traceback.format_exc()
"""

    payload = {
        'task_id': 'visualize_mapping_user_meshes',
        'skill_id': 'exec_code',
        'parameters': {
            'code': code,
            'description': '在用户的 AA 和 HH 上生成可视化映射'
        }
    }
    
    try:
        print("正在发送可视化脚本到 Maya GUI...")
        res = worker.run_skill(payload)
        print("Maya 返回:", res)
    finally:
        worker.shutdown()

if __name__ == "__main__":
    run_visualizer()
