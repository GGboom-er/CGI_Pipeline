"""
空间投射引擎 — 基于 cvWrap 算法的 TNB 局部坐标系投射。

核心流程：
  1. 对新资产每个顶点，找到源 mesh 上法线方向一致的最近三角面
  2. 计算重心坐标 (barycentric coords)
  3. 用重心坐标插值源权重/delta
  4. 对投射失败的点，标记为需要扩散的 free 顶点

相比旧方案 (KDTree K=3 + 高斯加权) 的改进：
  - 法线过滤：防止跨层污染（背心不会从身体内表面捞权重）
  - 重心坐标：精确的三角面内插值，不依赖距离衰减
  - 法线偏移：保留源表面的法线方向信息
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)


def triangulate_faces(face_indices, face_counts):
    """
    将多边形面列表转为三角面 (N, 3) 数组。
    同时返回每个三角面对应的原始面索引。
    """
    triangles = []
    original_face_idx = []
    idx = 0
    for fi, count in enumerate(face_counts):
        verts = face_indices[idx:idx + count]
        for i in range(1, count - 1):
            triangles.append([verts[0], verts[i], verts[i + 1]])
            original_face_idx.append(fi)
        idx += count
    return np.array(triangles, dtype=np.intp), np.array(original_face_idx, dtype=np.intp)


def compute_face_normals(verts, faces):
    """计算每个三角面的单位法线。"""
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return normals / norms


def compute_face_centroids(verts, faces):
    """计算每个三角面的质心。"""
    return (verts[faces[:, 0]] + verts[faces[:, 1]] + verts[faces[:, 2]]) / 3.0


def compute_vertex_normals(verts, faces):
    """从面法线平均得到顶点法线。"""
    face_normals = compute_face_normals(verts, faces)
    N = verts.shape[0]
    vertex_normals = np.zeros((N, 3))
    for i in range(3):
        np.add.at(vertex_normals, faces[:, i], face_normals)
    norms = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vertex_normals / norms


def find_closest_triangle_with_normal(query_points, query_normals,
                                       src_verts, src_faces, src_face_normals,
                                       max_normal_angle_deg=90.0, k_candidates=8):
    """
    对每个 query 点，找到源 mesh 上法线方向一致的最近三角面。

    Parameters
    ----------
    query_points : (Q, 3)
    query_normals : (Q, 3)
    src_verts : (V, 3)
    src_faces : (F, 3)
    src_face_normals : (F, 3)
    max_normal_angle_deg : float
        法线夹角阈值（度），超过则拒绝
    k_candidates : int
        KDTree 候选数量

    Returns
    -------
    matched_face_idx : (Q,) int, -1 表示未匹配
    bary_coords : (Q, 3) float, 重心坐标
    distances : (Q,) float, 投射距离
    """
    from scipy.spatial import cKDTree

    Q = query_points.shape[0]
    centroids = compute_face_centroids(src_verts, src_faces)

    tree = cKDTree(centroids)
    max_angle_rad = np.radians(max_normal_angle_deg)

    matched_face_idx = np.full(Q, -1, dtype=np.intp)
    bary_coords = np.zeros((Q, 3), dtype=np.float64)
    distances = np.full(Q, np.inf, dtype=np.float64)

    k = min(k_candidates, len(centroids))
    dists_all, indices_all = tree.query(query_points, k=k)

    if k == 1:
        dists_all = dists_all[:, np.newaxis]
        indices_all = indices_all[:, np.newaxis]

    for qi in range(Q):
        qn = query_normals[qi]
        qn_norm = np.linalg.norm(qn)
        if qn_norm < 1e-10:
            # 无法线信息，取最近面
            fi = indices_all[qi, 0]
            matched_face_idx[qi] = fi
            bary_coords[qi] = _compute_bary(query_points[qi], src_verts, src_faces[fi])
            distances[qi] = dists_all[qi, 0]
            continue

        qn = qn / qn_norm

        for ci in range(k):
            fi = indices_all[qi, ci]
            fn = src_face_normals[fi]
            cos_angle = np.dot(qn, fn)
            if cos_angle < np.cos(max_angle_rad):
                continue
            # 法线通过，计算重心坐标
            bary = _compute_bary(query_points[qi], src_verts, src_faces[fi])
            matched_face_idx[qi] = fi
            bary_coords[qi] = bary
            distances[qi] = dists_all[qi, ci]
            break

    return matched_face_idx, bary_coords, distances


def barycentric_weight_transfer(matched_face_idx, bary_coords, src_faces, src_weights):
    """
    用重心坐标从源三角面插值权重。

    Parameters
    ----------
    matched_face_idx : (Q,) int, -1 表示未匹配
    bary_coords : (Q, 3)
    src_faces : (F, 3)
    src_weights : (V, J)

    Returns
    -------
    new_weights : (Q, J)
    valid_mask : (Q,) bool
    """
    Q = len(matched_face_idx)
    J = src_weights.shape[1]
    new_weights = np.zeros((Q, J), dtype=np.float64)
    valid_mask = matched_face_idx >= 0

    valid_idx = np.where(valid_mask)[0]
    if len(valid_idx) == 0:
        return new_weights, valid_mask

    fi = matched_face_idx[valid_idx]
    tri_verts = src_faces[fi]  # (n_valid, 3)
    bc = bary_coords[valid_idx]  # (n_valid, 3)

    w0 = src_weights[tri_verts[:, 0]]  # (n_valid, J)
    w1 = src_weights[tri_verts[:, 1]]
    w2 = src_weights[tri_verts[:, 2]]

    new_weights[valid_idx] = (bc[:, 0:1] * w0 +
                              bc[:, 1:2] * w1 +
                              bc[:, 2:3] * w2)

    return new_weights, valid_mask


def barycentric_delta_transfer(matched_face_idx, bary_coords, src_faces, src_deltas):
    """
    用重心坐标从源三角面插值 BS delta。

    Parameters
    ----------
    matched_face_idx : (Q,) int
    bary_coords : (Q, 3)
    src_faces : (F, 3)
    src_deltas : (V, 3) 或 (V,) — 单个 BS target 的 delta

    Returns
    -------
    new_deltas : (Q, 3) 或 (Q,)
    valid_mask : (Q,) bool
    """
    Q = len(matched_face_idx)
    valid_mask = matched_face_idx >= 0
    valid_idx = np.where(valid_mask)[0]

    src_deltas = np.asarray(src_deltas)
    if src_deltas.ndim == 1:
        src_deltas = src_deltas[:, np.newaxis]
    D = src_deltas.shape[1]

    new_deltas = np.zeros((Q, D), dtype=np.float64)
    if len(valid_idx) == 0:
        return new_deltas.squeeze(), valid_mask

    fi = matched_face_idx[valid_idx]
    tri_verts = src_faces[fi]
    bc = bary_coords[valid_idx]

    d0 = src_deltas[tri_verts[:, 0]]
    d1 = src_deltas[tri_verts[:, 1]]
    d2 = src_deltas[tri_verts[:, 2]]

    new_deltas[valid_idx] = bc[:, 0:1] * d0 + bc[:, 1:2] * d1 + bc[:, 2:3] * d2

    if D == 1:
        return new_deltas.flatten(), valid_mask
    return new_deltas, valid_mask


def geodesic_anchor_weights(verts, faces, anchor_indices, max_geodesic_dist=None):
    """
    计算每个顶点到锚点集合的测地距离。
    用于细长结构（头发、睫毛）的锚点衰减。

    Returns
    -------
    geo_dist : (N,) 到最近锚点的测地距离
    """
    try:
        import potpourri3d as pp3d
        solver = pp3d.MeshHeatMethodDistanceSolver(verts, faces)
        geo_dist = solver.compute_distance_multisource(anchor_indices)
        return geo_dist
    except ImportError:
        from scipy.spatial import cKDTree
        logger.warning("potpourri3d not available, falling back to Euclidean distance.")
        tree = cKDTree(verts[anchor_indices])
        dists, _ = tree.query(verts, k=1)
        return dists


# ═══════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════

def _compute_bary(point, verts, face):
    """计算点 P 在三角面 (v0, v1, v2) 上的重心坐标。"""
    a = verts[face[0]]
    b = verts[face[1]]
    c = verts[face[2]]

    v0 = b - a
    v1 = c - a
    v2 = point - a

    d00 = np.dot(v0, v0)
    d01 = np.dot(v0, v1)
    d11 = np.dot(v1, v1)
    d20 = np.dot(v2, v0)
    d21 = np.dot(v2, v1)

    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-12:
        return np.array([1.0 / 3, 1.0 / 3, 1.0 / 3])

    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w

    # Clamp to valid range
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    w = max(0.0, min(1.0, w))
    s = u + v + w
    if s > 0:
        u /= s
        v /= s
        w /= s

    return np.array([u, v, w])


# ═══════════════════════════════════════════════════════════════
# 高层 API：一站式权重传递
# ═══════════════════════════════════════════════════════════════

def transfer_weights_tnb(src_verts, src_faces, src_weights,
                         dst_verts, dst_faces, dst_normals=None,
                         max_normal_angle_deg=90.0, k_candidates=8,
                         diffuse_backend="scipy"):
    """
    完整的 TNB 投射 + 扩散权重传递流程。

    1. 三角面法线过滤投射 → 重心坐标插值
    2. 投射失败的点 → Laplacian 扩散补全

    Parameters
    ----------
    src_verts : (Vs, 3)
    src_faces : (Fs, 3) 三角面
    src_weights : (Vs, J)
    dst_verts : (Vd, 3)
    dst_faces : (Fd, 3) 三角面
    dst_normals : (Vd, 3) optional, 如果为 None 则自动计算
    max_normal_angle_deg : float
    k_candidates : int
    diffuse_backend : "scipy" | "heat" | "jacobi"

    Returns
    -------
    weights : (Vd, J)
    stats : dict with 'n_projected', 'n_diffused'
    """
    from core.laplacian_diffuse import (
        laplacian_diffuse_scipy, laplacian_diffuse_heat
    )

    src_verts = np.asarray(src_verts, dtype=np.float64)
    src_faces = np.asarray(src_faces, dtype=np.intp)
    src_weights = np.asarray(src_weights, dtype=np.float64)
    dst_verts = np.asarray(dst_verts, dtype=np.float64)
    dst_faces = np.asarray(dst_faces, dtype=np.intp)

    N_dst = dst_verts.shape[0]
    J = src_weights.shape[1]

    if dst_normals is None:
        dst_normals = compute_vertex_normals(dst_verts, dst_faces)
    else:
        dst_normals = np.asarray(dst_normals, dtype=np.float64)

    src_face_normals = compute_face_normals(src_verts, src_faces)

    # Step 1: TNB 投射
    matched_face_idx, bary_coords, dists = find_closest_triangle_with_normal(
        dst_verts, dst_normals,
        src_verts, src_faces, src_face_normals,
        max_normal_angle_deg=max_normal_angle_deg,
        k_candidates=k_candidates
    )

    new_weights, valid_mask = barycentric_weight_transfer(
        matched_face_idx, bary_coords, src_faces, src_weights
    )

    n_projected = int(valid_mask.sum())
    n_failed = N_dst - n_projected

    # Step 2: 扩散补全 / fallback
    if n_failed > 0 and n_projected > 0:
        anchor_indices = np.where(valid_mask)[0]
        anchor_weights_arr = new_weights[anchor_indices]

        if diffuse_backend == "scipy":
            new_weights = laplacian_diffuse_scipy(
                dst_verts, dst_faces, anchor_indices, anchor_weights_arr, J
            )
        elif diffuse_backend == "heat":
            new_weights = laplacian_diffuse_heat(
                dst_verts, dst_faces, anchor_indices, anchor_weights_arr, J
            )
        else:
            adjacency = _build_adjacency_from_faces(N_dst, dst_faces)
            from core.laplacian_diffuse import laplacian_diffuse
            new_weights = laplacian_diffuse(
                adjacency, anchor_indices, anchor_weights_arr, N_dst, J
            )
    elif n_projected == 0:
        # 全部投射失败 → fallback 到 KDTree 最近点插值
        logger.warning("TNB projection failed for all points, falling back to KDTree K=3.")
        new_weights = _kdtree_fallback(src_verts, src_weights, dst_verts)

    stats = {"n_projected": n_projected, "n_diffused": n_failed,
             "fallback_kdtree": n_projected == 0}
    logger.info(f"transfer_weights_tnb: {n_projected}/{N_dst} projected, "
                f"{n_failed} diffused ({diffuse_backend})")
    return new_weights, stats


def _kdtree_fallback(src_verts, src_weights, dst_verts, k=3, sigma=0.05):
    """KDTree K 近邻 + 高斯加权 fallback（旧方案）。k / sigma 可由 profile 覆盖。"""
    from scipy.spatial import cKDTree

    tree = cKDTree(src_verts)
    k = min(k, len(src_verts))
    dists, indices = tree.query(dst_verts, k=k)
    if k == 1:
        dists = dists[:, None]
        indices = indices[:, None]

    w_gauss = np.exp(-(dists ** 2) / (2 * sigma ** 2))
    row_sums = w_gauss.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    w_gauss /= row_sums

    src_w = src_weights[indices]  # (Q, k, J)
    new_weights = np.sum(src_w * w_gauss[:, :, np.newaxis], axis=1)

    # normalize
    rs = new_weights.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    new_weights /= rs
    return new_weights


def _build_adjacency_from_faces(num_verts, faces):
    """从三角面构建邻接表。"""
    adjacency = [set() for _ in range(num_verts)]
    for f in faces:
        v0, v1, v2 = f
        adjacency[v0].add(v1)
        adjacency[v0].add(v2)
        adjacency[v1].add(v0)
        adjacency[v1].add(v2)
        adjacency[v2].add(v0)
        adjacency[v2].add(v1)
    return [list(s) for s in adjacency]


# ═══════════════════════════════════════════════════════════════
# Profile 版本入口：从 rig_sync_profile 读取算法参数
# ═══════════════════════════════════════════════════════════════

def transfer_weights_with_profile(src_verts, src_faces, src_weights,
                                  dst_verts, dst_faces, dst_normals=None,
                                  profile=None):
    """
    transfer_weights_tnb 的 profile 感知包装器。

    从 profile 的 tnb_projection / diffuse 段读取算法参数，
    便于在项目级调参。profile=None 时自动加载默认值。

    Parameters
    ----------
    profile : dict or None
        rig_sync_profile 字典。包含 tnb_projection.max_normal_angle_deg /
        k_candidates，以及 diffuse.backend。

    Returns
    -------
    weights : (Vd, J)
    stats : dict
    """
    if profile is None:
        try:
            from core.config_loader import get_default_rig_sync_profile
            profile = get_default_rig_sync_profile()
        except Exception:
            profile = {}

    tnb_cfg = profile.get("tnb_projection", {})
    diff_cfg = profile.get("diffuse", {})

    max_angle = tnb_cfg.get("max_normal_angle_deg", 90.0)
    k_cand = tnb_cfg.get("k_candidates", 8)
    backend = diff_cfg.get("backend", "scipy")

    return transfer_weights_tnb(
        src_verts, src_faces, src_weights,
        dst_verts, dst_faces, dst_normals=dst_normals,
        max_normal_angle_deg=max_angle,
        k_candidates=k_cand,
        diffuse_backend=backend,
    )

