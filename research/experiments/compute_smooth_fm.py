"""
修复所有失败方法的对比实验

1. 谱域直接传递（不经过 p2p）
2. TV 正则化（L1 代替 L2）
3. Soft Correspondence 降维版（只用前 20 个特征向量）
4. Cycle Consistency 置信度 + 自适应平滑
5. RHM (Reversible Harmonic Maps)
"""
import sys
import time
import numpy as np
import scipy.sparse as sparse
import scipy.sparse.linalg as splinalg
from scipy.spatial import cKDTree

sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping

t0 = time.time()

data = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_data.npz')
aa_pos = data['aa_pos']
aa_tris = data['aa_tris']
kkk_pos = data['kkk_pos']
kkk_tris = data['kkk_tris']
print(f"AA: {aa_pos.shape[0]} vtx, KKK: {kkk_pos.shape[0]} vtx")

mesh1 = TriMesh(aa_pos, aa_tris)
mesh2 = TriMesh(kkk_pos, kkk_tris)
mesh1.process(k=150, intrinsic=True)
mesh2.process(k=150, intrinsic=True)
print(f"Mesh processing: {time.time()-t0:.1f}s")

def auto_landmarks(pos1, pos2):
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(pos1[:, axis])), int(np.argmax(pos2[:, axis]))])
        pairs.append([int(np.argmin(pos1[:, axis])), int(np.argmin(pos2[:, axis]))])
    return np.array(pairs)

landmarks = auto_landmarks(aa_pos, kkk_pos)

fm = FunctionalMapping(mesh1, mesh2)
fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=100, landmarks=landmarks)
fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0, optinit='zeros')
fm.zoomout_refine(step=5, nit=10)
FM_12 = fm.FM.copy()
k = FM_12.shape[0]
p2p_21 = fm.get_p2p(use_adj=True)
print(f"FM done: {time.time()-t0:.1f}s, k={k}, unique={len(np.unique(p2p_21))}/382")

aa_y = aa_pos[:, 1]
y_raw = aa_y[p2p_21]

# Edge 计算工具
edges = set()
for f in kkk_tris:
    edges.add((min(f[0],f[1]), max(f[0],f[1])))
    edges.add((min(f[1],f[2]), max(f[1],f[2])))
    edges.add((min(f[0],f[2]), max(f[0],f[2])))
edges_arr = np.array(list(edges))

def edge_stats(values):
    diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
    return diffs.mean(), np.percentile(diffs, 95), diffs.max()

def print_result(name, values):
    m, p, mx = edge_stats(values)
    print(f"  {name:<35} mean={m:.4f}  p95={p:.4f}  max={mx:.4f}  range=[{values.min():.2f}, {values.max():.2f}]")


print(f"\n{'='*80}")
print("RESULTS:")
print(f"{'='*80}")
print_result("0. Raw p2p", y_raw)


# ============ 方法 1: 谱域直接传递 ============
print(f"\n--- Method 1: Spectral Transfer (no p2p) ---")
# weights_aa 投影到 mesh1 的谱基，通过 FM 传递到 mesh2
ev1 = mesh1.eigenvectors[:, :k]  # (n1, k)
ev2 = mesh2.eigenvectors[:, :k]  # (n2, k)
A1 = mesh1.A  # (n1, n1) area matrix

# 投影 aa_y 到谱基
a = ev1.T @ A1 @ aa_y  # (k,)
# 通过 FM 传递
b = FM_12 @ a  # (k,)
# 在 KKK 上重建
y_spectral_full = ev2 @ b  # (n2,)

print_result("1a. Spectral k=130", y_spectral_full)

# 不同带宽
for k_use in [20, 40, 60, 80, 100, 130]:
    a_k = ev1[:, :k_use].T @ A1 @ aa_y
    b_k = FM_12[:k_use, :k_use] @ a_k
    y_k = ev2[:, :k_use] @ b_k
    print_result(f"1b. Spectral k={k_use}", y_k)


# ============ 方法 2: TV 正则化 ============
print(f"\n--- Method 2: Total Variation (L1) ---")
# TV: min ||y - y_raw||² + λ * Σ_edges |y_i - y_j|
# 用 ADMM 求解
def solve_tv(y_raw, edges_arr, n_vtx, lam=1.0, rho=1.0, nit=50):
    """ADMM for TV denoising on graph"""
    n_edges = edges_arr.shape[0]

    # 差分矩阵 D: (n_edges, n_vtx)
    I = np.repeat(np.arange(n_edges), 2)
    J = edges_arr.flatten()
    V = np.tile([-1, 1], n_edges)
    D = sparse.csr_matrix((V, (I, J)), shape=(n_edges, n_vtx))

    # ADMM: min ||y - y_raw||² + λ||z||_1  s.t. Dy = z
    # y-update: (I + rho * D^T D) y = y_raw + rho * D^T (z - u)
    # z-update: shrinkage(Dy + u, λ/rho)
    # u-update: u += Dy - z

    lhs = sparse.eye(n_vtx) + rho * D.T @ D
    lhs_factored = splinalg.factorized(lhs.tocsc())

    y = y_raw.copy()
    z = np.zeros(n_edges)
    u = np.zeros(n_edges)

    for it in range(nit):
        # y-update
        rhs = y_raw + rho * D.T @ (z - u)
        y = lhs_factored(rhs)

        # z-update (soft thresholding)
        Dy = D @ y
        z_new = Dy + u
        threshold = lam / rho
        z = np.sign(z_new) * np.maximum(np.abs(z_new) - threshold, 0)

        # u-update
        u += Dy - z

    return y

for lam_tv in [0.1, 0.5, 1.0, 2.0, 5.0]:
    y_tv = solve_tv(y_raw, edges_arr, len(kkk_pos), lam=lam_tv, rho=1.0, nit=100)
    print_result(f"2. TV λ={lam_tv}", y_tv)


# ============ 方法 3: Soft Correspondence 降维 ============
print(f"\n--- Method 3: Soft Correspondence (low-dim) ---")
for k_soft in [10, 20, 30, 50]:
    ev1_k = ev1[:, :k_soft]
    ev2_k = ev2[:, :k_soft]
    FM_k = FM_12[:k_soft, :k_soft]

    # KKK 在 AA 谱空间中的 embedding
    emb2 = ev2_k @ FM_k  # (n2, k_soft)
    emb1 = ev1_k  # (n1, k_soft)

    # 对每个 KKK 点，找 AA 上 k=5 最近邻，Gaussian 加权
    tree = cKDTree(emb1)
    dists, indices = tree.query(emb2, k=5)

    # 自适应 sigma
    sigma = np.median(dists[:, 0]) * 2
    weights = np.exp(-dists**2 / (2 * sigma**2))
    weights /= weights.sum(axis=1, keepdims=True)

    y_soft = np.sum(weights * aa_y[indices], axis=1)
    print_result(f"3. Soft k={k_soft} knn=5", y_soft)


# ============ 方法 4: Cycle Consistency 置信度 ============
print(f"\n--- Method 4: Cycle Consistency + Adaptive ---")
# 正向: KKK -> AA (p2p_21)
# 反向: AA -> KKK
FM_21 = FM_12.T
emb1_in_2 = ev1 @ FM_21  # (n1, k) in mesh2 spectral space
tree_2 = cKDTree(ev2)
_, p2p_12 = tree_2.query(emb1_in_2, k=1)  # AA -> KKK

# Cycle consistency: p2p_12[p2p_21[i]] == i ?
cycle = p2p_12[p2p_21]  # (n2,) - 应该等于 range(n2)
cycle_dist = np.linalg.norm(kkk_pos[cycle] - kkk_pos, axis=1)
# 归一化
median_cd = np.median(cycle_dist[cycle_dist > 0]) if np.any(cycle_dist > 0) else 1.0
confidence_cycle = 1.0 / (1.0 + (cycle_dist / (median_cd + 1e-10)) ** 2)

print(f"  Cycle confidence: mean={confidence_cycle.mean():.3f}, >0.8: {(confidence_cycle > 0.8).sum()}/{len(confidence_cycle)}")

# 自适应平滑
def adaptive_smooth(W, A, values, confidence, lam=3.0):
    C = sparse.diags(confidence)
    C_inv = sparse.diags(1.0 - confidence)
    lhs = C @ A + lam * C_inv @ W
    rhs = C @ A @ values
    return splinalg.spsolve(lhs, rhs)

for lam_as in [1.0, 3.0, 5.0, 10.0]:
    y_cycle = adaptive_smooth(mesh2.W, mesh2.A, y_raw, confidence_cycle, lam=lam_as)
    print_result(f"4. Cycle+Adaptive λ={lam_as}", y_cycle)


# ============ 方法 5: RHM (Reversible Harmonic Maps) ============
print(f"\n--- Method 5: RHM (Bijective Dirichlet) ---")
# RHM: min ||∇Y||²_W + w_couple * ||Y - B||²_A + w_bij * ||P21 * Y - X2||²_A2
# P12: (n1, n2) - AA -> KKK
# P21: (n2, n1) - KKK -> AA

P21 = sparse.csr_matrix((np.ones(len(p2p_21)), (np.arange(len(p2p_21)), p2p_21)),
                         shape=(len(kkk_pos), len(aa_pos)))
P12 = sparse.csr_matrix((np.ones(len(p2p_12)), (np.arange(len(p2p_12)), p2p_12)),
                         shape=(len(aa_pos), len(kkk_pos)))

for w_couple, w_bij in [(1.0, 0.1), (1.0, 1.0), (1.0, 10.0), (10.0, 1.0)]:
    # (W2 + w_couple * A2 + w_bij * P12^T A1 P12) Y = w_couple * A2 * B + w_bij * P12^T A1 X1
    B = aa_pos[p2p_21]  # (n2, 3) target positions
    lhs = mesh2.W + w_couple * mesh2.A + w_bij * P12.T @ mesh1.A @ P12
    rhs = w_couple * mesh2.A @ B + w_bij * P12.T @ mesh1.A @ aa_pos

    Y_rhm = splinalg.spsolve(lhs, rhs)  # (n2, 3)
    y_rhm = Y_rhm[:, 1] if Y_rhm.ndim == 2 else Y_rhm

    # 如果是 3D，取 Y 坐标
    if Y_rhm.ndim == 2:
        y_rhm_val = Y_rhm[:, 1]
    else:
        y_rhm_val = Y_rhm

    print_result(f"5. RHM couple={w_couple} bij={w_bij}", y_rhm_val)


# ============ 最终对比 ============
print(f"\n{'='*80}")
print("BEST OF EACH METHOD:")
print(f"{'='*80}")
print_result("Raw p2p", y_raw)

# 保存所有结果用于 Maya 可视化
# 选择每个方法的最佳参数版本
a_60 = ev1[:, :60].T @ A1 @ aa_y
b_60 = FM_12[:60, :60] @ a_60
y_spectral_best = ev2[:, :60] @ b_60

y_tv_best = solve_tv(y_raw, edges_arr, len(kkk_pos), lam=1.0, rho=1.0, nit=100)

# Soft k=20
ev1_20 = ev1[:, :20]
ev2_20 = ev2[:, :20]
FM_20 = FM_12[:20, :20]
emb2_20 = ev2_20 @ FM_20
tree_20 = cKDTree(ev1_20)
dists_20, indices_20 = tree_20.query(emb2_20, k=5)
sigma_20 = np.median(dists_20[:, 0]) * 2
weights_20 = np.exp(-dists_20**2 / (2 * sigma_20**2))
weights_20 /= weights_20.sum(axis=1, keepdims=True)
y_soft_best = np.sum(weights_20 * aa_y[indices_20], axis=1)

y_cycle_best = adaptive_smooth(mesh2.W, mesh2.A, y_raw, confidence_cycle, lam=5.0)

# RHM best
B = aa_pos[p2p_21]
lhs_rhm = mesh2.W + 1.0 * mesh2.A + 1.0 * P12.T @ mesh1.A @ P12
rhs_rhm = 1.0 * mesh2.A @ B + 1.0 * P12.T @ mesh1.A @ aa_pos
Y_rhm_best = splinalg.spsolve(lhs_rhm, rhs_rhm)
y_rhm_best = Y_rhm_best[:, 1]

print_result("1. Spectral k=60", y_spectral_best)
print_result("2. TV λ=1.0", y_tv_best)
print_result("3. Soft k=20 knn=5", y_soft_best)
print_result("4. Cycle+Adaptive λ=5", y_cycle_best)
print_result("5. RHM couple=1 bij=1", y_rhm_best)

np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_smooth_viz.npz',
    y_raw=y_raw,
    y_spectral=y_spectral_best,
    y_tv=y_tv_best,
    y_soft=y_soft_best,
    y_cycle=y_cycle_best,
    y_rhm=y_rhm_best,
    confidence_cycle=confidence_cycle,
    aa_pos=aa_pos,
    kkk_pos=kkk_pos,
)
print(f"\nTotal: {time.time()-t0:.1f}s")
