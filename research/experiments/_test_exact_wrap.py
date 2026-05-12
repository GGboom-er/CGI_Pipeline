"""
精确 Wrap 映射：纯表面投射 + 重心坐标，无模糊。

核心思路：
1. 每个目标顶点 → igl 最近面投射 → 精确重心坐标
2. 构建稀疏映射矩阵 M (n_target × n_source)
3. 权重传递：W_target = M @ W_source
4. BS 传递：delta_target = M @ delta_source × 局部缩放因子

不使用：IDW 混合、Winding Number、法线过滤
"""
import sys
import json
import numpy as np
import time
from scipy import sparse

sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')

import igl

# ============================================================
# 加载数据
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
    print(f"Source: {name} - {v.shape[0]}v, {f.shape[0]}f")

super_verts = np.vstack(all_verts)
super_faces = np.vstack(all_faces)
super_weights = np.vstack(all_weights)
n_src = super_verts.shape[0]
print(f"SuperMesh: {n_src}v, {super_faces.shape[0]}f, {n_joints}j")


# ============================================================
# 构建精确映射矩阵 M
# ============================================================

def build_mapping_matrix(target_verts, source_verts, source_faces):
    """
    构建稀疏映射矩阵 M (n_target × n_source)。

    M[i, :] 的非零项是目标顶点 i 在源面上投影点的重心坐标。
    即 target_pos[i] ≈ M[i] @ source_pos（在投影意义下）。

    有了 M：
    - 权重传递：W_target = M @ W_source
    - BS 传递：delta_target = M @ delta_source
    """
    t0 = time.time()
    n_tgt = target_verts.shape[0]
    n_src = source_verts.shape[0]

    Q = np.asarray(target_verts, dtype=np.float64)
    V = np.asarray(source_verts, dtype=np.float64)
    F = np.asarray(source_faces, dtype=np.int64)

    # igl 精确最近面投射
    sq_dists, face_ids, closest_pts = igl.point_mesh_squared_distance(Q, V, F)

    # 重心坐标
    fvi = F[face_ids]  # (n_tgt, 3)
    A = V[fvi[:, 0]]
    B = V[fvi[:, 1]]
    C = V[fvi[:, 2]]
    bary = igl.barycentric_coordinates(closest_pts, A, B, C)

    # 裁剪负值（数值误差）
    bary = np.maximum(bary, 0.0)
    bary_sum = bary.sum(axis=1, keepdims=True)
    bary_sum[bary_sum == 0] = 1.0
    bary /= bary_sum

    # 构建稀疏矩阵
    row_indices = np.repeat(np.arange(n_tgt), 3)
    col_indices = fvi.ravel()
    values = bary.ravel()

    M = sparse.csr_matrix((values, (row_indices, col_indices)),
                           shape=(n_tgt, n_src))

    elapsed = time.time() - t0
    dists = np.sqrt(sq_dists)

    print(f"  Mapping matrix built: {n_tgt}×{n_src} sparse, "
          f"nnz={M.nnz}, {elapsed:.3f}s")
    print(f"  Projection distances: min={dists.min():.4f}, "
          f"max={dists.max():.4f}, mean={dists.mean():.4f}")

    return M, dists, face_ids, closest_pts


def compute_local_scale(target_verts, target_faces,
                        source_verts, source_faces, face_ids, closest_pts):
    """
    计算局部缩放因子：目标面积 / 源面积。

    对每个目标顶点，比较它所在三角面的面积与源投影面的面积之比。
    用于 BS delta 缩放 — 如果目标区域比源区域大，delta 应该放大。
    """
    # 目标面面积
    tgt_v0 = target_verts[target_faces[:, 0]]
    tgt_v1 = target_verts[target_faces[:, 1]]
    tgt_v2 = target_verts[target_faces[:, 2]]
    tgt_areas = 0.5 * np.linalg.norm(
        np.cross(tgt_v1 - tgt_v0, tgt_v2 - tgt_v0), axis=1)

    # 每个顶点的平均面积（Voronoi 近似）
    n_tgt = target_verts.shape[0]
    tgt_vert_area = np.zeros(n_tgt)
    for i in range(3):
        np.add.at(tgt_vert_area, target_faces[:, i], tgt_areas / 3.0)

    # 源面面积
    src_v0 = source_verts[source_faces[:, 0]]
    src_v1 = source_verts[source_faces[:, 1]]
    src_v2 = source_verts[source_faces[:, 2]]
    src_areas = 0.5 * np.linalg.norm(
        np.cross(src_v1 - src_v0, src_v2 - src_v0), axis=1)

    # 每个目标顶点对应的源面面积
    src_face_area_at_target = src_areas[face_ids]

    # 缩放因子 = sqrt(目标顶点面积 / 源面面积)
    # 用 sqrt 因为面积是二维量，delta 是一维位移
    scale = np.sqrt(np.maximum(tgt_vert_area, 1e-10) /
                    np.maximum(src_face_area_at_target, 1e-10))

    # 限制缩放范围，避免极端值
    scale = np.clip(scale, 0.5, 2.0)

    print(f"  Local scale: min={scale.min():.3f}, max={scale.max():.3f}, "
          f"mean={scale.mean():.3f}")

    return scale


# ============================================================
# 对每个目标 mesh 计算映射
# ============================================================

results = {}

for name, data in tgt_data.items():
    print(f"\n{'='*60}")
    print(f"Target: {name}")
    print(f"{'='*60}")

    tgt_verts = np.array(data['positions'], dtype=np.float64)
    tgt_faces = np.array(data['faces'], dtype=np.intp)
    print(f"  {tgt_verts.shape[0]}v, {tgt_faces.shape[0]}f")

    # 构建映射矩阵
    M, dists, face_ids, closest_pts = build_mapping_matrix(
        tgt_verts, super_verts, super_faces)

    # 权重传递
    transferred_weights = M @ super_weights
    transferred_weights = np.maximum(transferred_weights, 0)
    row_sums = transferred_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    transferred_weights /= row_sums

    # 局部缩放因子（用于 BS）
    local_scale = compute_local_scale(
        tgt_verts, tgt_faces, super_verts, super_faces, face_ids, closest_pts)

    # 输出权重统计
    print(f"\n  Weights:")
    for ji, jname in enumerate(joints):
        col = transferred_weights[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"    {jname}: max={col.max():.4f}, affected={affected}/{tgt_verts.shape[0]}")

    # 保存映射矩阵（稀疏格式）用于后续 BS 传递
    results[name] = {
        'weights': transferred_weights.tolist(),
        'joints': joints,
        'method': 'exact_wrap_projection',
        'local_scale': local_scale.tolist(),
        'projection_distances': dists.tolist(),
        # 映射矩阵的 COO 格式（用于 Maya 端重建）
        'mapping_matrix': {
            'rows': M.tocoo().row.tolist(),
            'cols': M.tocoo().col.tolist(),
            'vals': M.tocoo().data.tolist(),
            'shape': list(M.shape),
        },
    }

# ============================================================
# BS 传递
# ============================================================

# 加载 BS delta
try:
    with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/bs_deltas.json') as f:
        bs_data = json.load(f)
    print(f"\n{'='*60}")
    print(f"BS Delta Transfer")
    print(f"{'='*60}")
    print(f"  BS targets found: {list(bs_data.keys())}")

    for tgt_name, tgt_result in results.items():
        M_data = tgt_result['mapping_matrix']
        M = sparse.csr_matrix(
            (M_data['vals'], (M_data['rows'], M_data['cols'])),
            shape=tuple(M_data['shape']))
        local_scale = np.array(tgt_result['local_scale'])

        tgt_result['bs_deltas'] = {}

        for bs_name, bs_info in bs_data.items():
            # bs_info 应该包含每个源 mesh 的 delta
            # 合并到 SuperMesh 级
            super_delta = np.zeros((n_src, 3), dtype=np.float64)

            if isinstance(bs_info, dict):
                # 格式：{mesh_name: [[dx,dy,dz], ...]}
                offset = 0
                for src_name, src_d in src_data.items():
                    n_v = len(src_d['positions'])
                    if src_name in bs_info:
                        delta = np.array(bs_info[src_name], dtype=np.float64)
                        if delta.shape[0] == n_v:
                            super_delta[offset:offset+n_v] = delta
                    offset += n_v
            elif isinstance(bs_info, list):
                # 格式：直接是 SuperMesh 级的 delta
                d = np.array(bs_info, dtype=np.float64)
                if d.shape[0] == n_src:
                    super_delta = d

            # 映射传递
            transferred_delta = (M @ super_delta)

            # 应用局部缩放
            transferred_delta *= local_scale[:, np.newaxis]

            # 统计
            magnitude = np.linalg.norm(transferred_delta, axis=1)
            if magnitude.max() > 1e-5:
                affected = int((magnitude > 1e-3).sum())
                n_tgt = M.shape[0]
                print(f"  {tgt_name}/{bs_name}: max_mag={magnitude.max():.4f}, "
                      f"affected={affected}/{n_tgt}")
                tgt_result['bs_deltas'][bs_name] = transferred_delta.tolist()

except FileNotFoundError:
    print("\n  No bs_deltas.json found, skipping BS transfer.")
except Exception as e:
    print(f"\n  BS transfer error: {e}")

# ============================================================
# 保存结果
# ============================================================

# 清理不需要序列化的大数据
for name, r in results.items():
    if 'mapping_matrix' in r:
        # 保留映射矩阵信息但简化
        mm = r['mapping_matrix']
        r['mapping_matrix_info'] = {
            'shape': mm['shape'],
            'nnz': len(mm['vals']),
        }
        del r['mapping_matrix']

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/exact_wrap_result.json', 'w') as f:
    json.dump(results, f)

print(f"\nSaved to _maya_export/exact_wrap_result.json")
