"""
权重扩散引擎 — 三种后端可选：
  1. laplacian_diffuse_scipy  — 余切拉普拉斯 + spsolve 精确调和解（推荐）
  2. laplacian_diffuse_heat   — potpourri3d Vector Heat Method 标量扩展
  3. laplacian_diffuse        — 旧版 Jacobi 迭代（fallback，无需 faces）
"""

import numpy as np
import time
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 方案 1：余切拉普拉斯 + spsolve（精确调和解）
# ═══════════════════════════════════════════════════════════════

def laplacian_diffuse_scipy(verts, faces, anchor_indices, anchor_weights, num_joints=None):
    """
    基于余切拉普拉斯 + scipy.sparse.linalg.spsolve 的精确调和扩散。

    解方程: L[free,free] @ W_free = -L[free,anchor] @ W_anchor
    保证结果满足 ΔW = 0（调和函数，极值只出现在边界/锚点上）。

    Parameters
    ----------
    verts : ndarray (N, 3)
        顶点世界坐标
    faces : ndarray (F, 3)
        三角面索引（int）
    anchor_indices : ndarray (A,)
        锚点顶点索引
    anchor_weights : ndarray (A, J)
        锚点处的权重值
    num_joints : int, optional
        骨骼数。如果为 None，从 anchor_weights.shape[1] 推断

    Returns
    -------
    weights : ndarray (N, J)
        全顶点权重矩阵，已 clamp ≥ 0 并归一化
    """
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    t0 = time.time()

    anchor_indices = np.asarray(anchor_indices, dtype=np.intp)
    anchor_weights = np.asarray(anchor_weights, dtype=np.float64)
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.intp)

    N = verts.shape[0]
    J = anchor_weights.shape[1] if num_joints is None else num_joints
    A = len(anchor_indices)

    if A == 0:
        logger.warning("No anchors provided, returning zeros.")
        return np.zeros((N, J))
    if A == N:
        W = np.zeros((N, J))
        W[anchor_indices] = anchor_weights
        return W

    # 构建余切拉普拉斯
    try:
        import robust_laplacian
        L, M = robust_laplacian.mesh_laplacian(verts, faces)
    except ImportError:
        logger.info("robust_laplacian not available, using built-in cotangent.")
        L = _cotan_laplacian(verts, faces)

    # 分离 free / anchor
    all_idx = np.arange(N)
    is_anchor = np.zeros(N, dtype=bool)
    is_anchor[anchor_indices] = True
    free_idx = all_idx[~is_anchor]

    # 建立索引映射
    anchor_order = np.zeros(N, dtype=np.intp)
    anchor_order[anchor_indices] = np.arange(A)

    L_ff = L[np.ix_(free_idx, free_idx)]
    L_fa = L[np.ix_(free_idx, anchor_indices)]

    # 对每个 joint 求解
    W = np.zeros((N, J))
    W[anchor_indices] = anchor_weights

    rhs = -L_fa @ anchor_weights  # (n_free, J)

    # 批量求解：LU 分解一次，解 J 个右端
    try:
        LU = spla.splu(L_ff.tocsc())
        for j in range(J):
            W[free_idx, j] = LU.solve(rhs[:, j])
    except RuntimeError:
        # 如果 LU 失败（矩阵奇异），逐列用 lsqr
        logger.warning("SpLU failed, falling back to lsqr per joint.")
        for j in range(J):
            result = spla.lsqr(L_ff, rhs[:, j])
            W[free_idx, j] = result[0]

    # Clamp & normalize
    W = np.maximum(W, 0)
    row_sums = W.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    W /= row_sums

    logger.info(f"laplacian_diffuse_scipy: {A} anchors, {N-A} free, {J} joints, "
                f"{time.time()-t0:.3f}s")
    return W


# ═══════════════════════════════════════════════════════════════
# 方案 2：Vector Heat Method 标量扩展
# ═══════════════════════════════════════════════════════════════

def laplacian_diffuse_heat(verts, faces, anchor_indices, anchor_weights, num_joints=None):
    """
    基于 potpourri3d Vector Heat Method 的标量扩展。
    对每个 joint 独立调用 extend_scalar，从锚点向全网格扩散。

    优点：自动处理边界、无需手动分离 free/anchor、对非流形鲁棒。
    缺点：每个 joint 独立求解，不保证权重和 = 1（需后处理归一化）。
    """
    import potpourri3d as pp3d

    t0 = time.time()

    anchor_indices = np.asarray(anchor_indices, dtype=np.intp)
    anchor_weights = np.asarray(anchor_weights, dtype=np.float64)
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.intp)

    N = verts.shape[0]
    J = anchor_weights.shape[1] if num_joints is None else num_joints

    if len(anchor_indices) == 0:
        return np.zeros((N, J))

    solver = pp3d.MeshVectorHeatSolver(verts, faces)

    W = np.zeros((N, J))
    for j in range(J):
        col_vals = anchor_weights[:, j]
        if np.all(col_vals == 0):
            continue
        W[:, j] = solver.extend_scalar(anchor_indices, col_vals)

    # Clamp & normalize
    W = np.maximum(W, 0)
    row_sums = W.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    W /= row_sums

    logger.info(f"laplacian_diffuse_heat: {len(anchor_indices)} anchors, {J} joints, "
                f"{time.time()-t0:.3f}s")
    return W


# ═══════════════════════════════════════════════════════════════
# 方案 3：旧版 Jacobi 迭代（无需 faces，纯邻接表）
# ═══════════════════════════════════════════════════════════════

def laplacian_diffuse(adjacency, anchor_indices, anchor_weights, num_verts, num_joints,
                      outer_rounds=3, smooth_rounds=8, smooth_blend=0.5, diffuse_rounds=150):
    """纯 numpy Jacobi + 迭代平滑扩散，返回 (num_verts, num_joints) 权重矩阵。

    当没有三角面信息（只有邻接表）时使用此 fallback。
    """
    t1 = time.time()

    max_deg = max([len(nbrs) for nbrs in adjacency] + [0]) if adjacency else 0
    if max_deg == 0:
        logger.warning("Max degree is 0 in adjacency list. Returning default weights.")
        return np.zeros((num_verts, num_joints))

    adj_pad = np.zeros((num_verts, max_deg), dtype=np.int32)
    deg_arr = np.zeros(num_verts, dtype=np.int32)
    nbr_mask = np.zeros((num_verts, max_deg), dtype=np.float64)

    for vi in range(num_verts):
        nbrs = adjacency[vi]
        d = len(nbrs)
        deg_arr[vi] = d
        for j in range(d):
            adj_pad[vi, j] = nbrs[j]
            nbr_mask[vi, j] = 1.0

    nbr_mask_3d = nbr_mask[:, :, np.newaxis]
    inv_deg = np.zeros(num_verts)
    mask_nonzero = deg_arr > 0
    inv_deg[mask_nonzero] = 1.0 / deg_arr[mask_nonzero]

    is_anchor = np.zeros(num_verts, dtype=bool)
    is_anchor[anchor_indices] = True

    W = np.zeros((num_verts, num_joints))
    W[anchor_indices] = anchor_weights

    n_anchors = len(anchor_indices)
    n_free = num_verts - n_anchors
    logger.info(f"Laplacian diffusion (Jacobi): {n_anchors} anchors, {n_free} free verts.")

    if n_free == 0:
        return W

    for iteration in range(500):
        W_nbr = W[adj_pad] * nbr_mask_3d
        W_sum = W_nbr.sum(axis=1)
        W_new = W_sum * inv_deg[:, np.newaxis]
        W_new[is_anchor] = anchor_weights
        W = W_new

    for outer in range(outer_rounds):
        for _ in range(smooth_rounds):
            for vi in anchor_indices:
                nbrs = adjacency[vi]
                if not nbrs:
                    continue
                nbr_avg = W[nbrs].mean(axis=0)
                W[vi] = (1 - smooth_blend) * W[vi] + smooth_blend * nbr_avg

        anchor_snap = W.copy()
        for it in range(diffuse_rounds):
            W_nbr = W[adj_pad] * nbr_mask_3d
            W_sum = W_nbr.sum(axis=1)
            W_new = W_sum * inv_deg[:, np.newaxis]
            W_new[is_anchor] = anchor_snap[is_anchor]
            W = W_new

    final_weights = np.maximum(W, 0)
    rs = final_weights.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    final_weights = final_weights / rs

    logger.info(f"Laplacian diffusion (Jacobi) done in {time.time() - t1:.2f}s")
    return final_weights


# ═══════════════════════════════════════════════════════════════
# 内部工具：手写余切拉普拉斯（robust_laplacian 不可用时的 fallback）
# ═══════════════════════════════════════════════════════════════

def _cotan_laplacian(verts, faces):
    """
    构建余切权重拉普拉斯矩阵。
    参考 potpourri3d 和 pyFM 的实现。
    """
    import scipy.sparse as sp

    N = verts.shape[0]
    mat_i = []
    mat_j = []
    mat_data = []

    for i in range(3):
        inds_i = faces[:, i]
        inds_j = faces[:, (i + 1) % 3]
        inds_k = faces[:, (i + 2) % 3]

        vec_ki = verts[inds_i] - verts[inds_k]
        vec_kj = verts[inds_j] - verts[inds_k]

        dots = np.sum(vec_ki * vec_kj, axis=1)
        cross_mags = np.linalg.norm(np.cross(vec_ki, vec_kj), axis=1)

        # 防除零
        cross_mags = np.maximum(cross_mags, 1e-10)
        cotans = 0.5 * dots / cross_mags

        # 对角线 +cotan
        mat_i.append(inds_i)
        mat_j.append(inds_i)
        mat_data.append(cotans)

        mat_i.append(inds_j)
        mat_j.append(inds_j)
        mat_data.append(cotans)

        # 非对角线 -cotan
        mat_i.append(inds_i)
        mat_j.append(inds_j)
        mat_data.append(-cotans)

        mat_i.append(inds_j)
        mat_j.append(inds_i)
        mat_data.append(-cotans)

    mat_i = np.concatenate(mat_i)
    mat_j = np.concatenate(mat_j)
    mat_data = np.concatenate(mat_data)

    L = sp.coo_matrix((mat_data, (mat_i, mat_j)), shape=(N, N)).tocsc()
    return L
