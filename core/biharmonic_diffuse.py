"""
双调和扩散 (Biharmonic / BBW) — Δ²W = 0

相比一阶调和 (ΔW = 0)，双调和给出 C1 连续的权重过渡，
关节处不会出现硬拐点。代价是矩阵更大（L² 的非零元素更多）。

用法：
    from core.biharmonic_diffuse import biharmonic_diffuse
    W = biharmonic_diffuse(verts, faces, anchor_indices, anchor_weights)
"""

import numpy as np
import time
import logging

logger = logging.getLogger(__name__)


def biharmonic_diffuse(verts, faces, anchor_indices, anchor_weights, num_joints=None):
    """
    双调和扩散：解 L² @ W_free = -(L²)[free,anchor] @ W_anchor

    Parameters
    ----------
    verts : (N, 3)
    faces : (F, 3)
    anchor_indices : (A,)
    anchor_weights : (A, J)
    num_joints : int, optional

    Returns
    -------
    weights : (N, J) clamp ≥ 0, 归一化
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
        from core.laplacian_diffuse import _cotan_laplacian
        L = _cotan_laplacian(verts, faces)

    # L² = L @ L (双调和算子)
    L2 = (L @ L).tocsc()

    # 分离 free / anchor
    all_idx = np.arange(N)
    is_anchor = np.zeros(N, dtype=bool)
    is_anchor[anchor_indices] = True
    free_idx = all_idx[~is_anchor]

    L2_ff = L2[np.ix_(free_idx, free_idx)]
    L2_fa = L2[np.ix_(free_idx, anchor_indices)]

    W = np.zeros((N, J))
    W[anchor_indices] = anchor_weights

    rhs = -L2_fa @ anchor_weights  # (n_free, J)

    try:
        LU = spla.splu(L2_ff.tocsc())
        for j in range(J):
            W[free_idx, j] = LU.solve(rhs[:, j])
    except RuntimeError:
        logger.warning("SpLU failed on L², falling back to lsqr.")
        for j in range(J):
            result = spla.lsqr(L2_ff, rhs[:, j])
            W[free_idx, j] = result[0]

    # Clamp & normalize
    W = np.maximum(W, 0)
    row_sums = W.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    W /= row_sums

    logger.info(f"biharmonic_diffuse: {A} anchors, {N-A} free, {J} joints, "
                f"{time.time()-t0:.3f}s")
    return W
