import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.dcc_factory import create_worker

def test_maya_aa_hh():
    print("启动 Maya Worker 进行测试...")
    worker = create_worker('maya')
    try:
        worker.start()
    except AttributeError:
        pass # WarmWorkerProxy doesn't need start
    
    code = """
import sys
if r'y:\\GGbommer\\scripts\\CGI_Pipeline' not in sys.path:
    sys.path.insert(0, r'y:\\GGbommer\\scripts\\CGI_Pipeline')

import maya.cmds as cmds
from core.asset_info_schema import compare, make_empty_info, make_mesh_entry

result = {'status': 'SUCCESS'}

try:
    if not cmds.objExists('AA') or not cmds.objExists('HH'):
        # 自动生成测试网格
        if cmds.objExists('AA'): cmds.delete('AA')
        if cmds.objExists('HH'): cmds.delete('HH')
        
        cmds.polySphere(name='AA', radius=5, subdivisionsX=10, subdivisionsY=10)
        cmds.duplicate('AA', name='HH')
        
        # 破坏 HH 的结构：删掉一半的面 (局部拓扑删减)
        cmds.delete('HH.f[0:40]') # 删掉顶部 40 个面
        
        result['message'] = "场景中没有 AA 和 HH，已自动为您生成并执行局部拓扑删减！"
    
    # 提取顶点
    pts_aa = cmds.xform('AA.vtx[*]', q=True, ws=True, t=True)
    pts_hh = cmds.xform('HH.vtx[*]', q=True, ws=True, t=True)
    
    num_aa = len(pts_aa) // 3
    num_hh = len(pts_hh) // 3
    
    info_aa = make_empty_info()
    info_aa["meshes"]["AA"] = make_mesh_entry(num_aa, pts_aa)
    
    info_hh = make_empty_info()
    info_hh["meshes"]["HH"] = make_mesh_entry(num_hh, pts_hh)
        
    # 运行引擎：AA作为源(Rig)，HH作为新资产(Asset)
    # 注意 compare 的参数: info_a 是 Asset, info_b 是 Rig
    # 我们把 HH 当做新 Asset，AA 当做旧 Rig
    comp_result = compare(info_hh, info_aa, label_a="HH(Asset)", label_b="AA(Rig)", enable_cpd=True)
    
    # 为了让报告更简短直观，我们精简一下返回的数据
    paired = comp_result.get("paired", [])
    if paired:
        p = paired[0]
        action = p.get("actionability")
        subset = p.get("subset_mapping", [])
        free = p.get("free_a_indices", [])
        matched_count = len([x for x in subset if x != -1])
        
        result['compare_output'] = {
            'Action': action,
            'Rig(AA) 顶点数': num_aa,
            'Asset(HH) 顶点数': num_hh,
            '成功找到匹配子集点数': matched_count,
            '新资产游离点数': len(free)
        }
    else:
        result['compare_output'] = "完全匹配失败，掉入 DELETE/SPATIAL_VOTING"

except Exception as e:
    import traceback
    result['status'] = 'ERROR'
    result['error'] = traceback.format_exc()
"""

    payload = {
        'task_id': 'test_aa_hh',
        'skill_id': 'exec_code',
        'project': 'default',
        'asset_name': 'test',
        'source_path': '',
        'parameters': {
            'code': code,
            'description': '测试 AA 到 HH 的几何比较'
        }
    }
    
    try:
        res = worker.run_skill(payload)
        detail = res.get('detail', '{}')
        try:
            detail_obj = json.loads(detail)
            print(json.dumps(detail_obj, indent=2, ensure_ascii=False))
        except:
            print("Worker 返回:", detail)
    finally:
        try:
            worker.shutdown()
        except AttributeError:
            pass

if __name__ == "__main__":
    test_maya_aa_hh()
