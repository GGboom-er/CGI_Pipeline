"""
Test HKS NN + ZoomOut on AA->HH and AA->TT
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.signatures.HKS_functions import mesh_HKS
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p
import pyFM.spectral as spectral

def run_fm(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*50}')
    print(f'AA ({aa_pos.shape[0]} vtx) -> {name} ({tgt_pos.shape[0]} vtx)')
    print(f'{"="*50}')
    t0 = time.time()

    mesh1 = TriMesh(aa_pos, aa_tris)
    mesh2 = TriMesh(tgt_pos, tgt_tris)
    mesh1.process(k=150, intrinsic=True)
    mesh2.process(k=150, intrinsic=True)
    print(f'  Mesh processing: {time.time()-t0:.2f}s', flush=True)

    # Landmarks: extremal points
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(tgt_pos[:, axis]))])
        pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(tgt_pos[:, axis]))])
    landmarks = np.array(pairs)
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    # HKS descriptors + landmark HKS
    t1 = time.time()
    k_ev = 100
    hks1 = mesh_HKS(mesh1, 100, k=k_ev)
    hks2 = mesh_HKS(mesh2, 100, k=k_ev)
    lm_hks1 = mesh_HKS(mesh1, 100, landmarks=lmks1, k=k_ev)
    lm_hks2 = mesh_HKS(mesh2, 100, landmarks=lmks2, k=k_ev)
    desc1 = np.hstack([hks1, lm_hks1])
    desc2 = np.hstack([hks2, lm_hks2])
    # Normalize
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
    desc1 /= no1[None, :]
    desc2 /= no2[None, :]

    # NN init
    p2p_21_init = knn_query(desc1, desc2, k=1)
    print(f'  HKS NN init: {time.time()-t1:.2f}s, unique={len(np.unique(p2p_21_init))}/{tgt_pos.shape[0]}', flush=True)

    # ZoomOut
    t2 = time.time()
    FM_12, p2p_21 = mesh_zoomout_refine_p2p(
        p2p_21=p2p_21_init, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1, verbose=False
    )
    k = FM_12.shape[0]
    print(f'  ZoomOut: {time.time()-t2:.2f}s, k={k}, unique={len(np.unique(p2p_21))}/{tgt_pos.shape[0]}', flush=True)

    # Transfer Y coordinate
    aa_y = aa_pos[:, 1]
    y_raw = aa_y[p2p_21]

    # Spectral transfer
    k_use = min(k, 100)
    ev1 = mesh1.eigenvectors[:, :k_use]
    ev2 = mesh2.eigenvectors[:, :k_use]
    a_spec = ev1.T @ mesh1.A @ aa_y
    b_spec = FM_12[:k_use, :k_use] @ a_spec
    y_spectral = ev2 @ b_spec

    # Edge smoothness
    edges = set()
    for f in tgt_tris:
        edges.add((min(f[0],f[1]), max(f[0],f[1])))
        edges.add((min(f[1],f[2]), max(f[1],f[2])))
        edges.add((min(f[0],f[2]), max(f[0],f[2])))
    edges_arr = np.array(list(edges))

    def edge_stats(values):
        diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
        return diffs.mean(), np.percentile(diffs, 95)

    print(f'  Raw p2p:    edge_mean={edge_stats(y_raw)[0]:.4f}  edge_p95={edge_stats(y_raw)[1]:.4f}')
    print(f'  Spectral:   edge_mean={edge_stats(y_spectral)[0]:.4f}  edge_p95={edge_stats(y_spectral)[1]:.4f}')
    print(f'  Total: {time.time()-t0:.2f}s')

    return mesh1, mesh2, FM_12, p2p_21, y_raw, y_spectral

# Load data
d_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
t_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
d_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_data.npz')
t_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_tris.npz')

aa_pos = d_hh['aa_pos']
aa_tris = t_hh['aa_tris']
hh_pos, hh_tris = d_hh['hh_pos'], t_hh['hh_tris']
tt_pos, tt_tris = d_tt['tt_pos'], t_tt['tt_tris']

# Run both
_, _, FM_hh, p2p_hh, y_raw_hh, y_spec_hh = run_fm('HH', aa_pos, aa_tris, hh_pos, hh_tris)
_, _, FM_tt, p2p_tt, y_raw_tt, y_spec_tt = run_fm('TT', aa_pos, aa_tris, tt_pos, tt_tris)

# Save for Maya visualization
np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_results.npz',
         p2p_hh=p2p_hh, y_raw_hh=y_raw_hh, y_spec_hh=y_spec_hh,
         p2p_tt=p2p_tt, y_raw_tt=y_raw_tt, y_spec_tt=y_spec_tt,
         aa_y=aa_pos[:, 1])
