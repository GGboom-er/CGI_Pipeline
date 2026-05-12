"""
Fast FM via WKS/HKS NN init + ZoomOut refinement.
This is the approach recommended in pyFM's official examples:
1. Compute descriptors (WKS or HKS)
2. Get initial p2p via nearest-neighbor in descriptor space
3. Refine with ZoomOut (iterative FM refinement from p2p)

Much faster than L-BFGS-B optimization with dcomm constraints.
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.signatures.WKS_functions import mesh_WKS
from pyFM.signatures.HKS_functions import mesh_HKS
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p
import pyFM.spectral as spectral
from scipy.spatial import cKDTree
import scipy.sparse as sparse
import scipy.sparse.linalg as splinalg

t0 = time.time()

d1 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
d2 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
aa_pos, hh_pos = d1['aa_pos'], d1['hh_pos']
aa_tris, hh_tris = d2['aa_tris'], d2['hh_tris']
print(f'AA: {aa_pos.shape[0]} vtx, HH: {hh_pos.shape[0]} vtx')

mesh1 = TriMesh(aa_pos, aa_tris)
mesh2 = TriMesh(hh_pos, hh_tris)
mesh1.process(k=150, intrinsic=True)
mesh2.process(k=150, intrinsic=True)
print(f'Mesh processing: {time.time()-t0:.1f}s', flush=True)

# Landmarks
pairs = []
for axis in range(3):
    pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(hh_pos[:, axis]))])
    pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(hh_pos[:, axis]))])
landmarks = np.array(pairs)
lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

# --- Method A: WKS NN init + ZoomOut ---
print('\n--- Method A: WKS NN + ZoomOut ---', flush=True)
t1 = time.time()
wks1 = mesh_WKS(mesh1, num_E=100, k=100)
wks2 = mesh_WKS(mesh2, num_E=100, k=100)
# Add landmark-based WKS
lm_wks1 = mesh_WKS(mesh1, num_E=100, landmarks=lmks1, k=100)
lm_wks2 = mesh_WKS(mesh2, num_E=100, landmarks=lmks2, k=100)
desc1 = np.hstack([wks1, lm_wks1])
desc2 = np.hstack([wks2, lm_wks2])
# Normalize
no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
desc1 /= no1[None, :]
desc2 /= no2[None, :]

p2p_21_init = knn_query(desc1, desc2, k=1)
print(f'  NN init: {time.time()-t1:.2f}s, unique={len(np.unique(p2p_21_init))}/400', flush=True)

t2 = time.time()
FM_12, p2p_21 = mesh_zoomout_refine_p2p(
    p2p_21=p2p_21_init, mesh1=mesh1, mesh2=mesh2,
    k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1, verbose=False
)
print(f'  ZoomOut: {time.time()-t2:.2f}s, k={FM_12.shape[0]}, unique={len(np.unique(p2p_21))}/400', flush=True)

# --- Method B: HKS NN init + ZoomOut ---
print('\n--- Method B: HKS NN + ZoomOut ---', flush=True)
t1 = time.time()
hks1 = mesh_HKS(mesh1, 100, k=100)
hks2 = mesh_HKS(mesh2, 100, k=100)
lm_hks1 = mesh_HKS(mesh1, 100, landmarks=lmks1, k=100)
lm_hks2 = mesh_HKS(mesh2, 100, landmarks=lmks2, k=100)
desc1_h = np.hstack([hks1, lm_hks1])
desc2_h = np.hstack([hks2, lm_hks2])
no1 = np.sqrt(mesh1.l2_sqnorm(desc1_h))
no2 = np.sqrt(mesh2.l2_sqnorm(desc2_h))
desc1_h /= no1[None, :]
desc2_h /= no2[None, :]

p2p_21_init_h = knn_query(desc1_h, desc2_h, k=1)
print(f'  NN init: {time.time()-t1:.2f}s, unique={len(np.unique(p2p_21_init_h))}/400', flush=True)

t2 = time.time()
FM_12_h, p2p_21_h = mesh_zoomout_refine_p2p(
    p2p_21=p2p_21_init_h, mesh1=mesh1, mesh2=mesh2,
    k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1, verbose=False
)
print(f'  ZoomOut: {time.time()-t2:.2f}s, k={FM_12_h.shape[0]}, unique={len(np.unique(p2p_21_h))}/400', flush=True)

# --- Edge smoothness comparison ---
edges = set()
for f in hh_tris:
    edges.add((min(f[0],f[1]), max(f[0],f[1])))
    edges.add((min(f[1],f[2]), max(f[1],f[2])))
    edges.add((min(f[0],f[2]), max(f[0],f[2])))
edges_arr = np.array(list(edges))

aa_y = aa_pos[:, 1]

def edge_stats(values):
    diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
    return diffs.mean(), np.percentile(diffs, 95), diffs.max()

y_wks = aa_y[p2p_21]
y_hks = aa_y[p2p_21_h]

print(f'\n=== Edge smoothness (Y-coord transfer) ===')
print(f'WKS+ZO:  mean={edge_stats(y_wks)[0]:.4f}  p95={edge_stats(y_wks)[1]:.4f}')
print(f'HKS+ZO:  mean={edge_stats(y_hks)[0]:.4f}  p95={edge_stats(y_hks)[1]:.4f}')

# Spectral transfer for comparison
k_use = min(FM_12.shape[0], 100)
ev1 = mesh1.eigenvectors[:, :k_use]
ev2 = mesh2.eigenvectors[:, :k_use]
a_spec = ev1.T @ mesh1.A @ aa_y
b_spec = FM_12[:k_use, :k_use] @ a_spec
y_spectral = ev2 @ b_spec
print(f'Spectral: mean={edge_stats(y_spectral)[0]:.4f}  p95={edge_stats(y_spectral)[1]:.4f}')

print(f'\nTotal: {time.time()-t0:.1f}s')
