"""
Final best approach:
1. HKS + XYZ descriptors -> NN init -> ZoomOut (fast, ~0.5s)
2. Cycle-consistent smoothing of p2p (from previous work)
3. Compare all transfer methods
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
from scipy.spatial import cKDTree


def run_final(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*50}')
    print(f'AA ({aa_pos.shape[0]} vtx) -> {name} ({tgt_pos.shape[0]} vtx)')
    print(f'{"="*50}')
    t0 = time.time()

    mesh1 = TriMesh(aa_pos, aa_tris)
    mesh2 = TriMesh(tgt_pos, tgt_tris)
    mesh1.process(k=150, intrinsic=True)
    mesh2.process(k=150, intrinsic=True)

    # Landmarks
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(tgt_pos[:, axis]))])
        pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(tgt_pos[:, axis]))])
    landmarks = np.array(pairs)
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    # HKS + landmark HKS (intrinsic only, no XYZ for HH; with XYZ for symmetric shapes)
    k_ev = 100
    hks1 = mesh_HKS(mesh1, 100, k=k_ev)
    hks2 = mesh_HKS(mesh2, 100, k=k_ev)
    lm_hks1 = mesh_HKS(mesh1, 100, landmarks=lmks1, k=k_ev)
    lm_hks2 = mesh_HKS(mesh2, 100, landmarks=lmks2, k=k_ev)

    # Add XYZ to break symmetry
    xyz1 = mesh1.vertices.copy()
    xyz2 = mesh2.vertices.copy()
    xyz1 = (xyz1 - xyz1.min(0)) / (xyz1.max(0) - xyz1.min(0) + 1e-10)
    xyz2 = (xyz2 - xyz2.min(0)) / (xyz2.max(0) - xyz2.min(0) + 1e-10)

    desc1 = np.hstack([hks1, lm_hks1, xyz1])
    desc2 = np.hstack([hks2, lm_hks2, xyz2])

    # Normalize
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
    no1[no1 < 1e-10] = 1.0
    no2[no2 < 1e-10] = 1.0
    desc1 /= no1[None, :]
    desc2 /= no2[None, :]

    # NN init
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2):
        p2p_init[l2] = l1

    # ZoomOut
    t1 = time.time()
    FM, p2p_zo = mesh_zoomout_refine_p2p(
        p2p_21=p2p_init, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1, verbose=False
    )
    print(f'  ZoomOut: {time.time()-t1:.2f}s, k={FM.shape[0]}, unique={len(np.unique(p2p_zo))}/{tgt_pos.shape[0]}', flush=True)

    # --- Cycle-consistent smoothing ---
    # Forward: mesh1 -> mesh2 (use adjoint FM)
    # Backward: mesh2 -> mesh1 (use FM)
    k_use = FM.shape[0]
    ev1 = mesh1.eigenvectors[:, :k_use]
    ev2 = mesh2.eigenvectors[:, :k_use]

    # Forward map (2->1): p2p_zo
    # Backward map (1->2): use FM^T (adjoint)
    FM_adj = FM.T  # (k1, k2) - maps functions from mesh2 basis to mesh1 basis
    # Actually for p2p from 1->2, we need to compare ev2 @ FM^T with ev1
    p2p_12 = spectral.FM_to_p2p(FM.T, ev2, ev1, use_adj=False)

    # Cycle consistency: for each v2, check if p2p_12[p2p_zo[v2]] is close to v2
    cycle = p2p_12[p2p_zo]  # should be close to identity on mesh2
    cycle_error = np.linalg.norm(tgt_pos[cycle] - tgt_pos[np.arange(len(tgt_pos))], axis=1)

    # Adaptive smoothing: vertices with high cycle error get more smoothing
    threshold = np.percentile(cycle_error, 75)
    bad_mask = cycle_error > threshold

    # For bad vertices, use spectral transfer instead of p2p
    aa_y = aa_pos[:, 1]
    y_p2p = aa_y[p2p_zo]

    # Spectral transfer
    a_spec = ev1.T @ mesh1.A @ aa_y
    b_spec = FM @ a_spec
    y_spectral = ev2 @ b_spec

    # Hybrid: use p2p where cycle-consistent, spectral where not
    y_hybrid = y_p2p.copy()
    y_hybrid[bad_mask] = y_spectral[bad_mask]

    # Laplacian smoothing on hybrid (1 iteration, only on bad vertices' neighbors)
    from scipy.sparse import lil_matrix
    n2 = tgt_pos.shape[0]
    adj = lil_matrix((n2, n2))
    for f in tgt_tris:
        for i in range(3):
            for j in range(i+1, 3):
                adj[f[i], f[j]] = 1
                adj[f[j], f[i]] = 1
    adj = adj.tocsr()

    y_smooth = y_hybrid.copy()
    for _ in range(2):
        y_new = y_smooth.copy()
        for v in np.where(bad_mask)[0]:
            neighbors = adj[v].nonzero()[1]
            if len(neighbors) > 0:
                y_new[v] = 0.5 * y_smooth[v] + 0.5 * y_smooth[neighbors].mean()
        y_smooth = y_new

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

    print(f'  P2P:      edge_mean={edge_stats(y_p2p)[0]:.4f}  p95={edge_stats(y_p2p)[1]:.4f}')
    print(f'  Spectral: edge_mean={edge_stats(y_spectral)[0]:.4f}  p95={edge_stats(y_spectral)[1]:.4f}')
    print(f'  Hybrid:   edge_mean={edge_stats(y_hybrid)[0]:.4f}  p95={edge_stats(y_hybrid)[1]:.4f}')
    print(f'  Smooth:   edge_mean={edge_stats(y_smooth)[0]:.4f}  p95={edge_stats(y_smooth)[1]:.4f}')
    print(f'  Bad vtx: {bad_mask.sum()}/{n2} ({100*bad_mask.mean():.0f}%)')
    print(f'  Total: {time.time()-t0:.2f}s')

    return mesh1, mesh2, FM, p2p_zo, y_p2p, y_spectral, y_hybrid, y_smooth


# Load data
d_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
t_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
d_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_data.npz')
t_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_tris.npz')

aa_pos = d_hh['aa_pos']
aa_tris = t_hh['aa_tris']
hh_pos, hh_tris = d_hh['hh_pos'], t_hh['hh_tris']
tt_pos, tt_tris = d_tt['tt_pos'], t_tt['tt_tris']

r_hh = run_final('HH', aa_pos, aa_tris, hh_pos, hh_tris)
r_tt = run_final('TT', aa_pos, aa_tris, tt_pos, tt_tris)

# Save for Maya viz
np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_final.npz',
         p2p_hh=r_hh[4], spec_hh=r_hh[5], hybrid_hh=r_hh[6], smooth_hh=r_hh[7],
         p2p_tt=r_tt[4], spec_tt=r_tt[5], hybrid_tt=r_tt[6], smooth_tt=r_tt[7],
         aa_y=aa_pos[:, 1])
