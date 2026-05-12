"""
体素场方案验证：
1. 把源 rig mesh 体素化为 SDF + 权重场
2. 目标顶点在场中三线性插值采样
3. 用 SDF 符号做内外判定，用梯度做法线匹配
"""
import sys
import json
import numpy as np
import time
from scipy.ndimage import distance_transform_edt
from scipy.interpolate import RegularGridInterpolator

sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')

# 加载数据
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/scene_data.json') as f:
    src_data = json.load(f)
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/target_meshes.json') as f:
    tgt_data = json.load(f)

# ============================================================
# Step 1: 构建体素场
# ============================================================

# 合并所有源 mesh
all_verts = []
all_faces = []
all_weights = []
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
    print(f"Source: {name} - {v.shape[0]}v")

super_verts = np.vstack(all_verts)
super_faces = np.vstack(all_faces)
super_weights = np.vstack(all_weights)

print(f"SuperMesh: {super_verts.shape[0]}v, {super_faces.shape[0]}f, {n_joints}j")

# 确定体素网格范围（扩展一点 padding）
padding = 1.0
bbox_min = super_verts.min(axis=0) - padding
bbox_max = super_verts.max(axis=0) + padding

# 体素分辨率：用 igl 计算平均边长
import igl
edges = igl.edges(super_faces)
edge_lengths = np.linalg.norm(super_verts[edges[:, 0]] - super_verts[edges[:, 1]], axis=1)
avg_edge = np.mean(edge_lengths)
voxel_size = avg_edge / 2.0  # 半个边长的分辨率

grid_dims = np.ceil((bbox_max - bbox_min) / voxel_size).astype(int) + 1
print(f"Voxel grid: {grid_dims} (voxel_size={voxel_size:.3f}, avg_edge={avg_edge:.3f})")

# 构建网格坐标
x = np.linspace(bbox_min[0], bbox_min[0] + (grid_dims[0]-1)*voxel_size, grid_dims[0])
y = np.linspace(bbox_min[1], bbox_min[1] + (grid_dims[1]-1)*voxel_size, grid_dims[1])
z = np.linspace(bbox_min[2], bbox_min[2] + (grid_dims[2]-1)*voxel_size, grid_dims[2])

# 所有体素中心点
xx, yy, zz = np.meshgrid(x, y, z, indexing='ij')
grid_points = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])
n_grid = grid_points.shape[0]
print(f"Grid points: {n_grid}")

# ============================================================
# Step 2: 计算 SDF + 在每个体素点采样权重
# ============================================================

t0 = time.time()

# 用 igl 计算 signed distance（精确 SDF）
# 同时得到最近面 ID 和最近点
sdf, face_ids, closest_pts, _ = igl.signed_distance(
    grid_points, super_verts, super_faces.astype(np.int64),
    sign_type=igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER
)
print(f"SDF computed: {time.time()-t0:.2f}s")
print(f"  SDF range: [{sdf.min():.3f}, {sdf.max():.3f}]")
print(f"  Inside: {(sdf > 0).sum()}, Outside: {(sdf < 0).sum()}")

# 用最近面的重心坐标插值权重
t0 = time.time()
fvi = super_faces[face_ids]  # (n_grid, 3)
A = super_verts[fvi[:, 0]]
B = super_verts[fvi[:, 1]]
C = super_verts[fvi[:, 2]]
bary = igl.barycentric_coordinates(closest_pts, A, B, C)

# 插值权重
grid_weights = (bary[:, 0:1] * super_weights[fvi[:, 0]] +
                bary[:, 1:2] * super_weights[fvi[:, 1]] +
                bary[:, 2:3] * super_weights[fvi[:, 2]])
print(f"Weights interpolated: {time.time()-t0:.2f}s")

# Reshape 为 3D 网格
sdf_grid = sdf.reshape(grid_dims)
weight_grids = []
for j in range(n_joints):
    wg = grid_weights[:, j].reshape(grid_dims)
    weight_grids.append(wg)

# ============================================================
# Step 3: 构建插值器
# ============================================================

# SDF 插值器
sdf_interp = RegularGridInterpolator((x, y, z), sdf_grid,
                                      method='linear', bounds_error=False, fill_value=0.0)

# 权重插值器（每个骨骼一个）
weight_interps = []
for j in range(n_joints):
    interp = RegularGridInterpolator((x, y, z), weight_grids[j],
                                      method='linear', bounds_error=False, fill_value=0.0)
    weight_interps.append(interp)

# ============================================================
# Step 4: 对目标 mesh 采样
# ============================================================

results = {}
for name, data in tgt_data.items():
    t0 = time.time()
    verts = np.array(data['positions'], dtype=np.float64)

    # 采样 SDF
    target_sdf = sdf_interp(verts)

    # 采样权重
    target_weights = np.zeros((verts.shape[0], n_joints), dtype=np.float64)
    for j in range(n_joints):
        target_weights[:, j] = weight_interps[j](verts)

    # 归一化
    target_weights = np.maximum(target_weights, 0)
    row_sums = target_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    target_weights /= row_sums

    elapsed = time.time() - t0

    print(f"\n--- {name} ({verts.shape[0]}v) --- [{elapsed:.3f}s]")
    print(f"  SDF: min={target_sdf.min():.3f}, max={target_sdf.max():.3f}")
    print(f"  Inside(SDF>0): {(target_sdf > 0).sum()}/{verts.shape[0]}")

    for ji, jname in enumerate(joints):
        col = target_weights[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"  {jname}: max={col.max():.4f}, affected={affected}/{verts.shape[0]}")

    results[name] = {
        'weights': target_weights.tolist(),
        'joints': joints,
        'method': 'voxel_sdf_trilinear',
    }

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/voxel_result.json', 'w') as f:
    json.dump(results, f)

print("\nSaved to _maya_export/voxel_result.json")
