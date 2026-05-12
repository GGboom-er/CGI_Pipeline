"""
验证 v2：FM 在对称形状上的改进
- 增加特征值数量（50→80）
- 启用 orientation preservation
- 尝试 HKS 描述子（对对称形状可能更好）
- 加入简单的 landmark（上下极点）
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
# 复用体素场 + MC mesh（和 v1 相同）
# ============================================================

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/scene_data.json') as f:
    src_data = json.load(f)
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/target_meshes.json') as f:
    tgt_data = json.load(f)

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

xx, yy, zz = np.meshgrid(x, y, z, indexing='ij')
grid_points = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

sdf, face_ids, closest_pts, _ = igl.signed_distance(
    grid_points, super_verts, super_faces.astype(np.int64),
    sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER
)

fvi = super_faces[face_ids]
A = super_verts[fvi[:, 0]]
B = super_verts[fvi[:, 1]]
C = super_verts[fvi[:, 2]]
bary = igl.barycentric_coordinates(closest_pts, A, B, C)
grid_weights = (bary[:, 0:1] * super_weights[fvi[:, 0]] +
                bary[:, 1:2] * super_weights[fvi[:, 1]] +
                bary[:, 2:3] * super_weights[fvi[:, 2]])

sdf_grid = sdf.reshape(grid_dims)

weight_interps = []
for j in range(n_joints):
    wg = grid_weights[:, j].reshape(grid_dims)
    interp = RegularGridInterpolator((x, y, z), wg,
                                      method='linear', bounds_error=False, fill_value=0.0)
    weight_interps.append(interp)

# Marching Cubes
from trimesh.voxel.ops import matrix_to_marching_cubes
mc_mesh = matrix_to_marching_cubes(sdf_grid > 0)
mc_verts = np.array(mc_mesh.vertices)
mc_faces = np.array(mc_mesh.faces)

mc_verts[:, 0] = mc_verts[:, 0] / (grid_dims[0]-1) * (x[-1] - x[0]) + x[0]
mc_verts[:, 1] = mc_verts[:, 1] / (grid_dims[1]-1) * (y[-1] - y[0]) + y[0]
mc_verts[:, 2] = mc_verts[:, 2] / (grid_dims[2]-1) * (z[-1] - z[0]) + z[0]

mc_weights = np.zeros((mc_verts.shape[0], n_joints), dtype=np.float64)
for j in range(n_joints):
    mc_weights[:, j] = weight_interps[j](mc_verts)
mc_weights = np.maximum(mc_weights, 0)
row_sums = mc_weights.sum(axis=1, keepdims=True)
row_sums[row_sums == 0] = 1.0
mc_weights /= row_sums

print(f"MC mesh: {mc_verts.shape[0]}v, {mc_faces.shape[0]}f (watertight={mc_mesh.is_watertight})")

# ============================================================
# TT mesh
# ============================================================

tt = tgt_data['TT']
tt_verts = np.array(tt['positions'], dtype=np.float64)
tt_faces = np.array(tt['faces'], dtype=np.intp)
print(f"TT mesh: {tt_verts.shape[0]}v, {tt_faces.shape[0]}f")

# ============================================================
# 自动 Landmark 检测：用空间极值点打破对称性
# ============================================================

def find_landmarks_nearest(verts_src, verts_tgt, n_landmarks=8):
    """
    用 TT 的极值点，在 MC mesh 上找最近点作为 landmark。
    这样保证 landmark 对在空间上是真正对应的。
    """
    from scipy.spatial import cKDTree

    # 在 TT 上选特征点：极值 + 中心
    tgt_indices = []
    for axis in range(3):
        tgt_indices.append(np.argmax(verts_tgt[:, axis]))
        tgt_indices.append(np.argmin(verts_tgt[:, axis]))
    # 加上离中心最近的点
    center = verts_tgt.mean(axis=0)
    tgt_indices.append(np.argmin(np.linalg.norm(verts_tgt - center, axis=1)))
    tgt_indices = list(dict.fromkeys(tgt_indices))  # 去重保序

    # 在 MC mesh 上找最近点
    tree = cKDTree(verts_src)
    landmarks = []
    for ti in tgt_indices:
        _, si = tree.query(verts_tgt[ti])
        landmarks.append((si, ti))

    return np.array(landmarks, dtype=np.intp)

landmarks = find_landmarks_nearest(mc_verts, tt_verts)
print(f"\nLandmarks ({landmarks.shape[0]} pairs):")

for i in range(landmarks.shape[0]):
    s, t = landmarks[i]
    d = np.linalg.norm(mc_verts[s] - tt_verts[t])
    print(f"  [{i}] MC[{s}]={mc_verts[s].round(2)} → TT[{t}]={tt_verts[t].round(2)} dist={d:.2f}")

# ============================================================
# FM 方案 A：更多特征值 + orientation + landmarks
# ============================================================

print("\n=== 方案 A: n_ev=50, WKS, w_orient=1.0, landmarks ===")
mesh_mc = TriMesh(mc_verts, mc_faces.astype(np.intp))
mesh_tt = TriMesh(tt_verts, tt_faces)

fm_a = FunctionalMapping(mesh_mc, mesh_tt)

t0 = time.time()
fm_a.preprocess(n_ev=(50, 50), n_descr=100, descr_type='WKS',
                landmarks=landmarks, subsample_step=1, verbose=True)
print(f"Preprocess: {time.time()-t0:.2f}s")

t0 = time.time()
fm_a.fit(w_descr=1.0, w_lap=0.1, w_dcomm=0.01, w_orient=1.0, verbose=True)
print(f"Fit: {time.time()-t0:.2f}s")

p2p_a = fm_a.get_p2p()
unique_a = len(np.unique(p2p_a))
print(f"p2p: unique={unique_a}/{mc_verts.shape[0]}, range=[{p2p_a.min()}, {p2p_a.max()}]")

# ICP + ZoomOut
fm_a.icp_refine(nit=20, verbose=True)
fm_a.change_FM_type('icp')
p2p_a_icp = fm_a.get_p2p()
unique_a_icp = len(np.unique(p2p_a_icp))
print(f"ICP: unique={unique_a_icp}/{mc_verts.shape[0]}")

fm_a.zoomout_refine(nit=15, step=5, verbose=True)
fm_a.change_FM_type('zoomout')
p2p_a_zo = fm_a.get_p2p()
unique_a_zo = len(np.unique(p2p_a_zo))
print(f"ZoomOut: unique={unique_a_zo}/{mc_verts.shape[0]}")

# ============================================================
# FM 方案 B：HKS 描述子
# ============================================================

print("\n=== 方案 B: n_ev=50, HKS, w_orient=1.0, landmarks ===")
mesh_mc2 = TriMesh(mc_verts, mc_faces.astype(np.intp))
mesh_tt2 = TriMesh(tt_verts, tt_faces)

fm_b = FunctionalMapping(mesh_mc2, mesh_tt2)

t0 = time.time()
fm_b.preprocess(n_ev=(50, 50), n_descr=100, descr_type='HKS',
                landmarks=landmarks, subsample_step=1, verbose=True)
print(f"Preprocess: {time.time()-t0:.2f}s")

t0 = time.time()
fm_b.fit(w_descr=1.0, w_lap=0.1, w_dcomm=0.01, w_orient=1.0, verbose=True)
print(f"Fit: {time.time()-t0:.2f}s")

p2p_b = fm_b.get_p2p()
unique_b = len(np.unique(p2p_b))
print(f"p2p: unique={unique_b}/{mc_verts.shape[0]}")

fm_b.icp_refine(nit=20, verbose=True)
fm_b.change_FM_type('icp')
fm_b.zoomout_refine(nit=15, step=5, verbose=True)
fm_b.change_FM_type('zoomout')
p2p_b_zo = fm_b.get_p2p()
unique_b_zo = len(np.unique(p2p_b_zo))
print(f"ZoomOut: unique={unique_b_zo}/{mc_verts.shape[0]}")

# ============================================================
# 输出最佳结果
# ============================================================

print("\n=== 最终权重传递对比 ===")

candidates = [
    ('A_basic', p2p_a),
    ('A_icp', p2p_a_icp),
    ('A_zoomout', p2p_a_zo),
    ('B_zoomout', p2p_b_zo),
]

for name, p2p in candidates:
    transferred = mc_weights[p2p]
    transferred = np.maximum(transferred, 0)
    rs = transferred.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    transferred /= rs

    unique = len(np.unique(p2p))
    print(f"\n--- {name} (unique={unique}) ---")
    for ji, jname in enumerate(joints):
        col = transferred[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"  {jname}: max={col.max():.4f}, affected={affected}/{tt_verts.shape[0]}")

# 保存最佳
best_p2p = p2p_a_zo
best_weights = mc_weights[best_p2p]
best_weights = np.maximum(best_weights, 0)
rs = best_weights.sum(axis=1, keepdims=True)
rs[rs == 0] = 1.0
best_weights /= rs

result = {
    'TT': {
        'weights': best_weights.tolist(),
        'joints': joints,
        'method': 'mc_fm_v2_landmarks_orient',
    }
}

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/mc_fm_v2_result.json', 'w') as f:
    json.dump(result, f)

print(f"\nSaved to _maya_export/mc_fm_v2_result.json")
