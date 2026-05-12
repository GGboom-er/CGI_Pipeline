"""
验证：SDF → Marching Cubes 重建封闭 mesh → pyFM 对应 → 权重传递

流程：
1. 从体素 SDF 场用 Marching Cubes 提取 iso=0 等值面 → 封闭流形 mesh
2. 在重建 mesh 顶点上从体素权重场插值得到权重
3. 用 pyFM 建立 重建mesh ↔ 目标mesh 的对应关系
4. 通过对应关系传递权重
"""
import sys
import json
import numpy as np
import time

sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline/research/pyFM')
sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')

import igl
from scipy.interpolate import RegularGridInterpolator
from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping

# ============================================================
# Step 1: 构建体素 SDF 场（复用 _test_voxel.py 的逻辑）
# ============================================================

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/scene_data.json') as f:
    src_data = json.load(f)
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/target_meshes.json') as f:
    tgt_data = json.load(f)

# 合并源 mesh
all_verts, all_faces, all_weights = [], [], []
vert_offset = 0
joints = src_data[list(src_data.keys())[0]]['joints']
n_joints = len(joints)

for name, data in src_data.items():
    v = np.array(data['positions'], dtype=np.float64)
    f = np.array(data['faces'], dtype=np.intp)
    w = np.array(data['weights'], dtype=np.float64)
    all_verts.append(v)
    all_faces.append(f + vert_offset)
    all_weights.append(w)
    vert_offset += v.shape[0]

super_verts = np.vstack(all_verts)
super_faces = np.vstack(all_faces)
super_weights = np.vstack(all_weights)
print(f"SuperMesh: {super_verts.shape[0]}v, {super_faces.shape[0]}f")

# 体素网格
padding = 1.0
bbox_min = super_verts.min(axis=0) - padding
bbox_max = super_verts.max(axis=0) + padding

edges = igl.edges(super_faces)
edge_lengths = np.linalg.norm(super_verts[edges[:, 0]] - super_verts[edges[:, 1]], axis=1)
avg_edge = np.mean(edge_lengths)
voxel_size = avg_edge / 2.0

grid_dims = np.ceil((bbox_max - bbox_min) / voxel_size).astype(int) + 1
x = np.linspace(bbox_min[0], bbox_min[0] + (grid_dims[0]-1)*voxel_size, grid_dims[0])
y = np.linspace(bbox_min[1], bbox_min[1] + (grid_dims[1]-1)*voxel_size, grid_dims[1])
z = np.linspace(bbox_min[2], bbox_min[2] + (grid_dims[2]-1)*voxel_size, grid_dims[2])

# 计算 SDF
xx, yy, zz = np.meshgrid(x, y, z, indexing='ij')
grid_points = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

t0 = time.time()
sdf, face_ids, closest_pts, _ = igl.signed_distance(
    grid_points, super_verts, super_faces.astype(np.int64),
    sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER
)
print(f"SDF computed: {time.time()-t0:.2f}s")

# 体素权重场
fvi = super_faces[face_ids]
A = super_verts[fvi[:, 0]]
B = super_verts[fvi[:, 1]]
C = super_verts[fvi[:, 2]]
bary = igl.barycentric_coordinates(closest_pts, A, B, C)
grid_weights = (bary[:, 0:1] * super_weights[fvi[:, 0]] +
                bary[:, 1:2] * super_weights[fvi[:, 1]] +
                bary[:, 2:3] * super_weights[fvi[:, 2]])

sdf_grid = sdf.reshape(grid_dims)

# 权重插值器
weight_interps = []
for j in range(n_joints):
    wg = grid_weights[:, j].reshape(grid_dims)
    interp = RegularGridInterpolator((x, y, z), wg,
                                      method='linear', bounds_error=False, fill_value=0.0)
    weight_interps.append(interp)

# ============================================================
# Step 2: Marching Cubes 从 SDF=0 提取封闭 mesh
# ============================================================

t0 = time.time()

# 用 trimesh 的 marching cubes
import trimesh
from trimesh.voxel.ops import matrix_to_marching_cubes

# sdf_grid: 正值=内部，负值=外部（igl 的约定）
# marching_cubes 需要 volume > threshold 为内部
mc_mesh = matrix_to_marching_cubes(sdf_grid > 0)

# matrix_to_marching_cubes 返回的是单位坐标，需要缩放回世界坐标
mc_verts = np.array(mc_mesh.vertices)
mc_faces = np.array(mc_mesh.faces)

# 缩放：matrix_to_marching_cubes 的坐标范围是 [0, grid_dims]
# 映射回世界坐标
mc_verts[:, 0] = mc_verts[:, 0] / (grid_dims[0]-1) * (x[-1] - x[0]) + x[0]
mc_verts[:, 1] = mc_verts[:, 1] / (grid_dims[1]-1) * (y[-1] - y[0]) + y[0]
mc_verts[:, 2] = mc_verts[:, 2] / (grid_dims[2]-1) * (z[-1] - z[0]) + z[0]

print(f"Marching Cubes: {mc_verts.shape[0]}v, {mc_faces.shape[0]}f [{time.time()-t0:.2f}s]")
print(f"  Is watertight: {mc_mesh.is_watertight}")

# 在重建 mesh 顶点上插值权重
mc_weights = np.zeros((mc_verts.shape[0], n_joints), dtype=np.float64)
for j in range(n_joints):
    mc_weights[:, j] = weight_interps[j](mc_verts)

# 归一化
mc_weights = np.maximum(mc_weights, 0)
row_sums = mc_weights.sum(axis=1, keepdims=True)
row_sums[row_sums == 0] = 1.0
mc_weights /= row_sums

print(f"  MC mesh weights: shape={mc_weights.shape}")
print(f"  Non-zero joints per vert: {(mc_weights > 0.01).sum(axis=1).mean():.1f} avg")

# ============================================================
# Step 3: pyFM 建立 MC_mesh ↔ TT 的对应
# ============================================================

tt = tgt_data['TT']
tt_verts = np.array(tt['positions'], dtype=np.float64)
tt_faces = np.array(tt['faces'], dtype=np.intp)

print(f"\nSource (MC): {mc_verts.shape[0]}v, {mc_faces.shape[0]}f")
print(f"Target (TT): {tt_verts.shape[0]}v, {tt_faces.shape[0]}f")

t0 = time.time()
mesh_mc = TriMesh(mc_verts, mc_faces.astype(np.intp))
mesh_tt = TriMesh(tt_verts, tt_faces)
print(f"TriMesh created: {time.time()-t0:.2f}s")

# FM
fm = FunctionalMapping(mesh_mc, mesh_tt)

t0 = time.time()
fm.preprocess(n_ev=(30, 30), n_descr=100, descr_type='WKS', verbose=True)
print(f"Preprocess: {time.time()-t0:.2f}s")

t0 = time.time()
fm.fit(w_descr=1.0, w_lap=0.1, w_dcomm=0.01, w_orient=0.0, verbose=True)
print(f"Fit: {time.time()-t0:.2f}s")

# 基础 p2p
p2p_basic = fm.get_p2p()
print(f"p2p basic: range=[{p2p_basic.min()}, {p2p_basic.max()}] (MC has {mc_verts.shape[0]} verts)")

# ICP 精修
t0 = time.time()
fm.icp_refine(verbose=True)
print(f"ICP refine: {time.time()-t0:.2f}s")

fm.change_FM_type('icp')
p2p_icp = fm.get_p2p()

# ZoomOut 精修
t0 = time.time()
fm.zoomout_refine(nit=10, step=5, verbose=True)
print(f"ZoomOut: {time.time()-t0:.2f}s")

fm.change_FM_type('zoomout')
p2p_zo = fm.get_p2p()

# ============================================================
# Step 4: 通过 p2p 传递权重
# ============================================================

print("\n=== 权重传递结果 ===")

for method_name, p2p in [('FM_basic', p2p_basic), ('FM_icp', p2p_icp), ('FM_zoomout', p2p_zo)]:
    # p2p: target vert i → source vert p2p[i]
    transferred = mc_weights[p2p]

    # 归一化
    transferred = np.maximum(transferred, 0)
    rs = transferred.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    transferred /= rs

    print(f"\n--- {method_name} ---")
    print(f"  p2p range: [{p2p.min()}, {p2p.max()}]")
    # 检查 p2p 分布是否合理（不应该全映射到同一小区域）
    unique_targets = len(np.unique(p2p))
    print(f"  Unique source verts used: {unique_targets}/{mc_verts.shape[0]}")

    for ji, jname in enumerate(joints):
        col = transferred[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"  {jname}: max={col.max():.4f}, affected={affected}/{tt_verts.shape[0]}")

# 保存最佳结果
best_p2p = p2p_zo
best_weights = mc_weights[best_p2p]
best_weights = np.maximum(best_weights, 0)
rs = best_weights.sum(axis=1, keepdims=True)
rs[rs == 0] = 1.0
best_weights /= rs

result = {
    'TT': {
        'weights': best_weights.tolist(),
        'joints': joints,
        'method': 'mc_mesh_pyFM_zoomout',
    }
}

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/mc_fm_result.json', 'w') as f:
    json.dump(result, f)

print(f"\nSaved to _maya_export/mc_fm_result.json")
print(f"MC mesh saved for reference: {mc_verts.shape[0]}v, {mc_faces.shape[0]}f")
