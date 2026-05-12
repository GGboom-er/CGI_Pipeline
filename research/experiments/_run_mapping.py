"""使用 DeformationField（igl 最近面投射 + 法线过滤 + Winding Number）计算映射。"""
import json
import numpy as np
import sys
import logging

logging.basicConfig(level=logging.INFO)
sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')

from core.deformation_field import DeformationField

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/scene_data.json') as f:
    src_data = json.load(f)
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/target_meshes.json') as f:
    tgt_data = json.load(f)

log = []

# 构建 rig_data 格式
joints = src_data[list(src_data.keys())[0]]['joints']
meshes = []
for name, data in src_data.items():
    verts = np.array(data['positions'], dtype=np.float64)
    faces = np.array(data['faces'], dtype=np.intp)
    weights = np.array(data['weights'], dtype=np.float64)
    meshes.append({
        'name': name,
        'vertices': verts,
        'faces': faces,
        'weights': weights,
    })
    log.append(f'Source: {name} - {verts.shape[0]}v, {faces.shape[0]}f, {weights.shape[1]}j')

rig_data = {'all_joints': joints, 'meshes': meshes}
field = DeformationField(rig_data)
log.append(f'SuperMesh built: {field._super_verts.shape[0]} verts, {field._super_faces.shape[0]} faces')

# 对每个目标 mesh 采样
mapping_result = {}
for name, data in tgt_data.items():
    verts = np.array(data['positions'], dtype=np.float64)
    faces = np.array(data['faces'], dtype=np.intp)

    weights, stats = field.sample_weights(
        verts, faces,
        use_winding=True,
        max_normal_angle_deg=90.0,
        idw_k=8,
        idw_blend=0.2,
        smooth_iterations=1,
    )

    log.append(f'\n--- {name} ({verts.shape[0]}v) ---')
    log.append(f'  Ray hit: {stats["n_ray_hit"]}/{stats["n_total"]}')
    log.append(f'  Time: {stats["time"]:.2f}s')

    for ji, jname in enumerate(joints):
        col = weights[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            log.append(f'  {jname}: max={col.max():.4f}, affected={affected}/{verts.shape[0]}')

    mapping_result[name] = {
        'weights': weights.tolist(),
        'joints': joints,
    }

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/mapping_result.json', 'w') as f:
    json.dump(mapping_result, f)

log.append('\nSaved to _maya_export/mapping_result.json')

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/log.txt', 'w') as f:
    f.write('\n'.join(log))

print('\n'.join(log))
