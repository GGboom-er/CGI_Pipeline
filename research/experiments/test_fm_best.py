"""
Best approach: HKS+WKS+XYZ descriptors -> NN init -> pure ZoomOut -> bijective cleanup.
No ICP (orthogonality hurts non-isometric pairs).
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.signatures.HKS_functions import mesh_HKS
from pyFM.signatures.WKS_functions import mesh_WKS
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p
import pyFM.spectral as spectral
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment


def compute_rich_descriptors(mesh1, mesh2, landmarks, k_ev=100):
    """HKS + WKS + landmark HKS + normalized XYZ"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    hks1 = mesh_HKS(mesh1, 50, k=k_ev)
    hks2 = mesh_HKS(mesh2, 50, k=k_ev)
    wks1 = mesh_WKS(mesh1, 50, k=k_ev)
    wks2 = mesh_WKS(mesh2, 50, k=k_ev)
    lm_hks1 = mesh_HKS(mesh1, 50, landmarks=lmks1, k=k_ev)
    lm_hks2 = mesh_HKS(mesh2, 50, landmarks=lmks2, k=k_ev)

    # Normalized XYZ (breaks symmetry)
    xyz1 = mesh1.vertices.copy()
    xyz2 = mesh2.vertices.copy()
    xyz1 = (xyz1 - xyz1.min(0)) / (xyz1.max(0) - xyz1.min(0) + 1e-10)
    xyz2 = (xyz2 - xyz2.min(0)) / (xyz2.max(0) - xyz2.min(0) + 1e-10)

    desc1 = np.hstack([hks1, wks1, lm_hks1, xyz1])
    desc2 = np.hstack([hks2, wks2, lm_hks2, xyz2])

    # Normalize
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
    no1[no1 < 1e-10] = 1.0
    no2[no2 < 1e-10] = 1.0
    desc1 /= no1[None, :]
    desc2 /= no2[None, :]

    return desc1, desc2


def bijective_refine(p2p_21, mesh1, mesh2, FM, k_use=None):
    """
    Post-process p2p to reduce many-to-one mappings.
    For vertices on mesh2 that map to the same vertex on mesh1,
    use spectral embedding distance to reassign.
    """
    n2 = len(p2p_21)
    n1 = mesh1.vertices.shape[0]
    if k_use is None:
        k_use = FM.shape[0]

    # Spectral embeddings
    emb1 = mesh1.eigenvectors[:, :k_use]  # (n1, k)
    emb2 = mesh2.eigenvectors[:, :k_use] @ FM.T  # (n2, k) - mapped to mesh1's basis

    # Find duplicates
    from collections import defaultdict
    target_to_sources = defaultdict(list)
    for i2, i1 in enumerate(p2p_21):
        target_to_sources[i1].append(i2)

    p2p_refined = p2p_21.copy()
    # For each target vertex with multiple sources, keep the best match
    # and reassign others to nearest unmatched
    used = set(p2p_21)
    unused = set(range(n1)) - used

    if not unused:
        return p2p_refined

    unused_arr = np.array(sorted(unused))
    unused_emb = emb1[unused_arr]  # (n_unused, k)
    tree = cKDTree(unused_emb)

    for i1, sources in target_to_sources.items():
        if len(sources) <= 1:
            continue
        # Keep the closest one in spectral space
        dists = np.linalg.norm(emb2[sources] - emb1[i1], axis=1)
        best_idx = np.argmin(dists)
        # Reassign others
        for idx, i2 in enumerate(sources):
            if idx == best_idx:
                continue
            # Find nearest unused vertex
            _, nn_idx = tree.query(emb2[i2])
            p2p_refined[i2] = unused_arr[nn_idx]

    return p2p_refined


def run_best_fm(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*50}')
    print(f'AA ({aa_pos.shape[0]} vtx) -> {name} ({tgt_pos.shape[0]} vtx)')
    print(f'{"="*50}')
    t0 = time.time()

    mesh1 = TriMesh(aa_pos, aa_tris)
    mesh2 = TriMesh(tgt_pos, tgt_tris)
    mesh1.process(k=150, intrinsic=True)
    mesh2.process(k=150, intrinsic=True)
    print(f'  Mesh processing: {time.time()-t0:.2f}s', flush=True)

    # Landmarks
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(tgt_pos[:, axis]))])
        pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(tgt_pos[:, axis]))])
    landmarks = np.array(pairs)

    # Rich descriptors
    t1 = time.time()
    desc1, desc2 = compute_rich_descriptors(mesh1, mesh2, landmarks, k_ev=100)
    print(f'  Descriptors ({desc1.shape[1]} dims): {time.time()-t1:.2f}s', flush=True)

    # NN init with landmark constraint
    t2 = time.time()
    p2p_init = knn_query(desc1, desc2, k=1)
    # Force landmarks
    for l1, l2 in zip(landmarks[:, 0], landmarks[:, 1]):
        p2p_init[l2] = l1
    print(f'  NN init: unique={len(np.unique(p2p_init))}/{tgt_pos.shape[0]}', flush=True)

    # ZoomOut (no ICP)
    t3 = time.time()
    FM, p2p_zo = mesh_zoomout_refine_p2p(
        p2p_21=p2p_init, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1, verbose=False
    )
    print(f'  ZoomOut: {time.time()-t3:.2f}s, k={FM.shape[0]}, unique={len(np.unique(p2p_zo))}/{tgt_pos.shape[0]}', flush=True)

    # Bijective refinement
    t4 = time.time()
    p2p_final = bijective_refine(p2p_zo, mesh1, mesh2, FM)
    print(f'  Bijective: {time.time()-t4:.2f}s, unique={len(np.unique(p2p_final))}/{tgt_pos.shape[0]}', flush=True)

    # Transfer Y coordinate
    aa_y = aa_pos[:, 1]
    y_p2p = aa_y[p2p_final]
    y_zo = aa_y[p2p_zo]

    # Spectral transfer
    k_use = FM.shape[0]
    ev1 = mesh1.eigenvectors[:, :k_use]
    ev2 = mesh2.eigenvectors[:, :k_use]
    a_spec = ev1.T @ mesh1.A @ aa_y
    b_spec = FM @ a_spec
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

    print(f'  ZoomOut p2p: edge_mean={edge_stats(y_zo)[0]:.4f}  edge_p95={edge_stats(y_zo)[1]:.4f}')
    print(f'  Bijective:   edge_mean={edge_stats(y_p2p)[0]:.4f}  edge_p95={edge_stats(y_p2p)[1]:.4f}')
    print(f'  Spectral:    edge_mean={edge_stats(y_spectral)[0]:.4f}  edge_p95={edge_stats(y_spectral)[1]:.4f}')
    print(f'  Total: {time.time()-t0:.2f}s')

    return mesh1, mesh2, FM, p2p_final, y_p2p, y_spectral


# Load data
d_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
t_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
d_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_data.npz')
t_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_tris.npz')

aa_pos = d_hh['aa_pos']
aa_tris = t_hh['aa_tris']
hh_pos, hh_tris = d_hh['hh_pos'], t_hh['hh_tris']
tt_pos, tt_tris = d_tt['tt_pos'], t_tt['tt_tris']

_, _, FM_hh, p2p_hh, y_hh, y_spec_hh = run_best_fm('HH', aa_pos, aa_tris, hh_pos, hh_tris)
_, _, FM_tt, p2p_tt, y_tt, y_spec_tt = run_best_fm('TT', aa_pos, aa_tris, tt_pos, tt_tris)

np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_best.npz',
         p2p_hh=p2p_hh, y_hh=y_hh, y_spec_hh=y_spec_hh,
         p2p_tt=p2p_tt, y_tt=y_tt, y_spec_tt=y_spec_tt,
         aa_y=aa_pos[:, 1])
