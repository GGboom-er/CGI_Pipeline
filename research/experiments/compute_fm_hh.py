import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping
from scipy.spatial import cKDTree
import scipy.sparse as sparse
import scipy.sparse.linalg as splinalg

t0 = time.time()

d1 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
d2 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
aa_pos = d1['aa_pos']
hh_pos = d1['hh_pos']
aa_tris = d2['aa_tris']
hh_tris = d2['hh_tris']
print(f'AA: {aa_pos.shape[0]} vtx, HH: {hh_pos.shape[0]} vtx')

mesh1 = TriMesh(aa_pos, aa_tris)
mesh2 = TriMesh(hh_pos, hh_tris)
mesh1.process(k=100, intrinsic=True)
mesh2.process(k=100, intrinsic=True)
print(f'Mesh processing: {time.time()-t0:.1f}s')

pairs = []
for axis in range(3):
    pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(hh_pos[:, axis]))])
    pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(hh_pos[:, axis]))])
landmarks = np.array(pairs)

fm = FunctionalMapping(mesh1, mesh2)
print(f'preprocess start...', flush=True)
fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=50, landmarks=landmarks, subsample_step=5)
print(f'preprocess done: {time.time()-t0:.1f}s', flush=True)
fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0, optinit='zeros')
print(f'fit done: {time.time()-t0:.1f}s', flush=True)
fm.zoomout_refine(step=5, nit=10)
print(f'zoomout done: {time.time()-t0:.1f}s', flush=True)
FM_12 = fm.FM.copy()
k = FM_12.shape[0]
p2p_21 = fm.get_p2p(use_adj=True)
print(f'FM done: {time.time()-t0:.1f}s, k={k}, unique={len(np.unique(p2p_21))}/{hh_pos.shape[0]}')

aa_y = aa_pos[:, 1]
y_raw = aa_y[p2p_21]

# Spectral transfer k=60
ev1 = mesh1.eigenvectors[:, :60]
ev2 = mesh2.eigenvectors[:, :60]
A1 = mesh1.A
a_60 = ev1.T @ A1 @ aa_y
b_60 = FM_12[:60, :60] @ a_60
y_spectral = ev2 @ b_60

# Cycle consistency + adaptive smooth
FM_21 = FM_12.T
k_use = min(k, mesh1.eigenvectors.shape[1], mesh2.eigenvectors.shape[1])
emb1_in_2 = mesh1.eigenvectors[:, :k_use] @ FM_21[:k_use, :k_use]
tree_2 = cKDTree(mesh2.eigenvectors[:, :k_use])
_, p2p_12 = tree_2.query(emb1_in_2, k=1)
cycle = p2p_12[p2p_21]
cycle_dist = np.linalg.norm(hh_pos[cycle] - hh_pos, axis=1)
median_cd = np.median(cycle_dist[cycle_dist > 0]) if np.any(cycle_dist > 0) else 1.0
confidence_cycle = 1.0 / (1.0 + (cycle_dist / (median_cd + 1e-10)) ** 2)

C = sparse.diags(confidence_cycle)
C_inv = sparse.diags(1.0 - confidence_cycle)
lam = 5.0
lhs = C @ mesh2.A + lam * C_inv @ mesh2.W
rhs = C @ mesh2.A @ y_raw
y_cycle = splinalg.spsolve(lhs, rhs)

# RHM
n1, n2 = aa_pos.shape[0], hh_pos.shape[0]
P21 = sparse.csr_matrix((np.ones(n2), (np.arange(n2), p2p_21)), shape=(n2, n1))
P12 = sparse.csr_matrix((np.ones(n1), (np.arange(n1), p2p_12)), shape=(n1, n2))
B = aa_pos[p2p_21]
lhs_rhm = mesh2.W + 1.0 * mesh2.A + 1.0 * P12.T @ mesh1.A @ P12
rhs_rhm = 1.0 * mesh2.A @ B + 1.0 * P12.T @ mesh1.A @ aa_pos
Y_rhm = splinalg.spsolve(lhs_rhm, rhs_rhm)
y_rhm = Y_rhm[:, 1]

# Edge stats
edges = set()
for f in hh_tris:
    edges.add((min(f[0],f[1]), max(f[0],f[1])))
    edges.add((min(f[1],f[2]), max(f[1],f[2])))
    edges.add((min(f[0],f[2]), max(f[0],f[2])))
edges_arr = np.array(list(edges))

def edge_stats(values):
    diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
    return diffs.mean(), np.percentile(diffs, 95), diffs.max()

print(f'Raw p2p:    mean={edge_stats(y_raw)[0]:.4f}  p95={edge_stats(y_raw)[1]:.4f}')
print(f'Spectral:   mean={edge_stats(y_spectral)[0]:.4f}  p95={edge_stats(y_spectral)[1]:.4f}')
print(f'Cycle:      mean={edge_stats(y_cycle)[0]:.4f}  p95={edge_stats(y_cycle)[1]:.4f}')
print(f'RHM:        mean={edge_stats(y_rhm)[0]:.4f}  p95={edge_stats(y_rhm)[1]:.4f}')

np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_viz.npz',
    y_raw=y_raw, y_spectral=y_spectral, y_cycle=y_cycle, y_rhm=y_rhm,
    aa_pos=aa_pos, hh_pos=hh_pos, aa_y=aa_y)
print(f'Total: {time.time()-t0:.1f}s')
