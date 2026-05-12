"""
精确 Wrap v2：分级投射策略

近点（距离 < 阈值）→ 直接最近面投射（精确）
远点（距离 > 阈值）→ 法线射线投射 → 拓扑扩散兜底

解决远点投射到错误源面的问题。
"""
import sys
import json
import numpy as np
import time
from scipy import sparse
from scipy.spatial import cKDTree

sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')
import igl

# ============================================================
# 加载数据
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
    print(f"Source: {name} - {v.shape[0]}v, {f.shape[0]}f")

super_verts = np.vstack(all_verts)
super_faces = np.vstack(all_faces)
super_weights = np.vstack(all_weights)
n_src = super_verts.shape[0]

# 计算平均边长作为距离阈值参考
edges = igl.edges(super_faces)
edge_lengths = np.linalg.norm(super_verts[edges[:, 0]] - super_verts[edges[:, 1]], axis=1)
avg_edge = np.mean(edge_lengths)
print(f"SuperMesh: {n_src}v, {super_faces.shape[0]}f, avg_edge={avg_edge:.3f}")


# ============================================================
# 核心：分级投射
# ============================================================

def compute_vertex_normals(verts, faces):
    """计算顶点法线"""
    face_normals = igl.per_face_normals(verts, faces, np.array([0.0, 0.0, 1.0]))
    vert_normals = np.zeros_like(verts)
    for i in range(3):
        np.add.at(vert_normals, faces[:, i], face_normals)
    norms = np.linalg.norm(vert_normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vert_normals /= norms
    return vert_normals


def ray_mesh_intersect(origins, directions, verts, faces):
    """
    批量射线-mesh 求交（使用 embree 或 igl）。
    返回：hit_mask, hit_face_ids, hit_points, hit_distances
    """
    n = origins.shape[0]
    hit_mask = np.zeros(n, dtype=bool)
    hit_face_ids = np.full(n, -1, dtype=np.intp)
    hit_points = np.zeros((n, 3), dtype=np.float64)
    hit_dists = np.full(n, np.inf)

    V = np.asarray(verts, dtype=np.float64)
    F = np.asarray(faces, dtype=np.int32)
    O = np.asarray(origins, dtype=np.float64)
    D = np.asarray(directions, dtype=np.float64)

    # 逐条射线用 igl（没有批量接口，但数据量小可以接受）
    # 用简单的参数化射线-三角形求交
    # 改用 scipy + 手动实现 Möller–Trumbore
    from scipy.spatial import cKDTree

    # 预计算面质心用于快速筛选候选面
    centroids = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
    tree = cKDTree(centroids)

    for i in range(n):
        o = O[i]
        d = D[i]
        d_norm = np.linalg.norm(d)
        if d_norm < 1e-10:
            continue
        d = d / d_norm

        # 找射线方向上的候选面（在射线附近的面）
        # 用较大搜索半径
        k = min(64, len(centroids))
        _, cand_ids = tree.query(o, k=k)

        best_t = np.inf
        best_fi = -1
        best_pt = None

        for fi in cand_ids:
            # Möller–Trumbore
            v0 = V[F[fi, 0]]
            v1 = V[F[fi, 1]]
            v2 = V[F[fi, 2]]
            e1 = v1 - v0
            e2 = v2 - v0
            h = np.cross(d, e2)
            a = np.dot(e1, h)
            if abs(a) < 1e-10:
                continue
            f_val = 1.0 / a
            s = o - v0
            u = f_val * np.dot(s, h)
            if u < 0.0 or u > 1.0:
                continue
            q = np.cross(s, e1)
            v = f_val * np.dot(d, q)
            if v < 0.0 or u + v > 1.0:
                continue
            t = f_val * np.dot(e2, q)
            if t > 1e-6 and t < best_t:
                best_t = t
                best_fi = fi
                best_pt = o + t * d

        if best_fi >= 0:
            hit_mask[i] = True
            hit_face_ids[i] = best_fi
            hit_points[i] = best_pt
            hit_dists[i] = best_t

    return hit_mask, hit_face_ids, hit_points, hit_dists


def build_adjacency(faces, n_verts):
    """构建顶点邻接表"""
    adj = [set() for _ in range(n_verts)]
    for f in faces:
        adj[f[0]].update([f[1], f[2]])
        adj[f[1]].update([f[0], f[2]])
        adj[f[2]].update([f[0], f[1]])
    return adj


def diffuse_from_anchors(verts, faces, anchor_mask, anchor_weights, iterations=20):
    """
    从锚点向外扩散权重（Laplacian 平滑，锚点固定）。
    """
    n = verts.shape[0]
    n_j = anchor_weights.shape[1]
    weights = np.zeros((n, n_j), dtype=np.float64)
    weights[anchor_mask] = anchor_weights[anchor_mask]

    # 构建 Laplacian 邻接
    adj = build_adjacency(faces, n)

    free_mask = ~anchor_mask
    free_indices = np.where(free_mask)[0]

    for it in range(iterations):
        new_weights = weights.copy()
        for vi in free_indices:
            neighbors = list(adj[vi])
            if neighbors:
                new_weights[vi] = weights[neighbors].mean(axis=0)
        weights = new_weights

    return weights


def tiered_projection(tgt_verts, tgt_faces, src_verts, src_faces, src_weights,
                      close_threshold=None):
    """
    分级投射策略：

    Tier 1: 最近面投射（距离 < close_threshold）→ 精确重心坐标
    Tier 2: 法线方向射线投射 → 命中源面则用重心坐标
    Tier 3: 反向法线射线（从源面方向看）
    Tier 4: 从已确定点沿目标 mesh 拓扑扩散
    """
    t0 = time.time()
    n_tgt = tgt_verts.shape[0]
    n_j = src_weights.shape[1]

    if close_threshold is None:
        close_threshold = avg_edge * 1.0  # 1 个边长以内算"近"

    result_weights = np.zeros((n_tgt, n_j), dtype=np.float64)
    confidence = np.zeros(n_tgt, dtype=np.float64)
    tier_label = np.zeros(n_tgt, dtype=np.intp)  # 记录每个点用了哪一级

    Q = np.asarray(tgt_verts, dtype=np.float64)
    V = np.asarray(src_verts, dtype=np.float64)
    F = np.asarray(src_faces, dtype=np.int64)

    # 目标法线
    tgt_normals = compute_vertex_normals(tgt_verts, tgt_faces)
    # 源面法线
    src_face_normals = igl.per_face_normals(V, F, np.array([0.0, 0.0, 1.0]))

    # ═══ Tier 1: 最近面投射 ═══
    sq_dists, face_ids, closest_pts = igl.point_mesh_squared_distance(Q, V, F)
    dists = np.sqrt(sq_dists)

    fvi = F[face_ids]
    A, B, C = V[fvi[:, 0]], V[fvi[:, 1]], V[fvi[:, 2]]
    bary = igl.barycentric_coordinates(closest_pts, A, B, C)
    bary = np.maximum(bary, 0.0)
    bary_sum = bary.sum(axis=1, keepdims=True)
    bary_sum[bary_sum == 0] = 1.0
    bary /= bary_sum

    # 法线一致性检查：目标法线和源面法线夹角
    dot_normals = np.sum(tgt_normals * src_face_normals[face_ids], axis=1)

    # Tier 1 接受条件：距离近 AND 法线不完全相反
    tier1_mask = (dists < close_threshold) & (dot_normals > -0.5)

    tier1_idx = np.where(tier1_mask)[0]
    for idx in tier1_idx:
        fi = face_ids[idx]
        v0, v1, v2 = F[fi]
        b = bary[idx]
        result_weights[idx] = b[0]*src_weights[v0] + b[1]*src_weights[v1] + b[2]*src_weights[v2]
        confidence[idx] = 1.0 - dists[idx] / close_threshold
        tier_label[idx] = 1

    n_tier1 = tier1_mask.sum()
    print(f"  Tier 1 (close projection): {n_tier1}/{n_tgt} "
          f"(threshold={close_threshold:.3f})")

    # ═══ Tier 2: 法线方向射线 ═══
    remaining = ~tier1_mask
    remaining_idx = np.where(remaining)[0]

    if len(remaining_idx) > 0:
        # 正向射线（沿法线反方向，即朝源面方向）
        ray_origins = Q[remaining_idx]
        ray_dirs = -tgt_normals[remaining_idx]  # 朝内（朝源面）

        hit_mask, hit_fids, hit_pts, hit_dists = ray_mesh_intersect(
            ray_origins, ray_dirs, V, F)

        # 也试反方向
        hit_mask2, hit_fids2, hit_pts2, hit_dists2 = ray_mesh_intersect(
            ray_origins, tgt_normals[remaining_idx], V, F)

        # 取距离更近的那个方向
        for li, gi in enumerate(remaining_idx):
            best_hit = False
            best_fid = -1
            best_pt = None

            if hit_mask[li] and hit_mask2[li]:
                if hit_dists[li] <= hit_dists2[li]:
                    best_hit, best_fid, best_pt = True, hit_fids[li], hit_pts[li]
                else:
                    best_hit, best_fid, best_pt = True, hit_fids2[li], hit_pts2[li]
            elif hit_mask[li]:
                best_hit, best_fid, best_pt = True, hit_fids[li], hit_pts[li]
            elif hit_mask2[li]:
                best_hit, best_fid, best_pt = True, hit_fids2[li], hit_pts2[li]

            if best_hit:
                # 计算重心坐标
                fi = best_fid
                v0, v1, v2 = F[fi]
                bc = igl.barycentric_coordinates(
                    best_pt.reshape(1, 3),
                    V[v0].reshape(1, 3),
                    V[v1].reshape(1, 3),
                    V[v2].reshape(1, 3)
                )[0]
                bc = np.maximum(bc, 0.0)
                bc_sum = bc.sum()
                if bc_sum > 0:
                    bc /= bc_sum
                result_weights[gi] = bc[0]*src_weights[v0] + bc[1]*src_weights[v1] + bc[2]*src_weights[v2]
                confidence[gi] = 0.8
                tier_label[gi] = 2

    n_tier2 = (tier_label == 2).sum()
    print(f"  Tier 2 (ray cast): {n_tier2}/{n_tgt}")

    # ═══ Tier 3: 对仍未命中的点，用最近面但降低置信度 ═══
    still_missing = (tier_label == 0)
    still_missing_idx = np.where(still_missing)[0]

    if len(still_missing_idx) > 0:
        for idx in still_missing_idx:
            fi = face_ids[idx]
            v0, v1, v2 = F[fi]
            b = bary[idx]
            result_weights[idx] = b[0]*src_weights[v0] + b[1]*src_weights[v1] + b[2]*src_weights[v2]
            confidence[idx] = max(0.3, 1.0 - dists[idx] / (close_threshold * 5))
            tier_label[idx] = 3

    n_tier3 = (tier_label == 3).sum()
    print(f"  Tier 3 (fallback nearest): {n_tier3}/{n_tgt}")

    # ═══ Tier 4: 拓扑扩散平滑 ═══
    # 对低置信度的点，从高置信度邻居扩散
    if n_tier3 > 0:
        high_conf_mask = confidence > 0.7
        if high_conf_mask.sum() > 0:
            smoothed = diffuse_from_anchors(
                tgt_verts, tgt_faces, high_conf_mask, result_weights, iterations=10)

            # 混合：低置信度点用扩散结果补充
            for idx in still_missing_idx:
                blend = 1.0 - confidence[idx]  # 置信度越低，越依赖扩散
                result_weights[idx] = (confidence[idx] * result_weights[idx] +
                                       blend * smoothed[idx])

    # 归一化
    result_weights = np.maximum(result_weights, 0)
    row_sums = result_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    result_weights /= row_sums

    elapsed = time.time() - t0
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Tier distribution: T1={n_tier1}, T2={n_tier2}, T3={n_tier3}")

    return result_weights, confidence, tier_label, dists


# ============================================================
# 运行
# ============================================================

results = {}

for name, data in tgt_data.items():
    print(f"\n{'='*60}")
    print(f"Target: {name}")
    print(f"{'='*60}")

    tgt_verts = np.array(data['positions'], dtype=np.float64)
    tgt_faces = np.array(data['faces'], dtype=np.intp)
    print(f"  {tgt_verts.shape[0]}v, {tgt_faces.shape[0]}f")

    weights, confidence, tiers, dists = tiered_projection(
        tgt_verts, tgt_faces, super_verts, super_faces, super_weights)

    print(f"\n  Weights:")
    for ji, jname in enumerate(joints):
        col = weights[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"    {jname}: max={col.max():.4f}, affected={affected}/{tgt_verts.shape[0]}")

    print(f"\n  Confidence: min={confidence.min():.3f}, mean={confidence.mean():.3f}")
    print(f"  Distance: min={dists.min():.3f}, max={dists.max():.3f}, mean={dists.mean():.3f}")

    results[name] = {
        'weights': weights.tolist(),
        'joints': joints,
        'method': 'tiered_projection_v2',
        'confidence': confidence.tolist(),
        'tier_labels': tiers.tolist(),
    }

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/tiered_wrap_result.json', 'w') as f:
    json.dump(results, f)

print(f"\nSaved to _maya_export/tiered_wrap_result.json")
