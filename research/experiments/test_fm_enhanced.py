"""
Enhanced FM matching: break symmetry + ICP+ZoomOut combined refinement.

Key improvements over basic HKS NN + ZoomOut:
1. Add spatial coordinates as descriptors to break rotational symmetry
2. Use ICP refinement between ZoomOut steps
3. Landmark-constrained NN initialization
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
from pyFM.refine.zoomout import zoomout_iteration
from pyFM.refine.icp import icp_iteration
import pyFM.spectral as spectral
import scipy.linalg


def compute_descriptors(mesh1, mesh2, landmarks, k_ev=100, use_xyz=True):
    """
    Compute rich descriptors combining:
    - HKS (intrinsic, scale-invariant)
    - WKS (intrinsic, frequency-selective)
    - Normalized XYZ coordinates (extrinsic, breaks symmetry)
    - Landmark-based HKS
    """
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    # HKS
    hks1 = mesh_HKS(mesh1, 50, k=k_ev)
    hks2 = mesh_HKS(mesh2, 50, k=k_ev)

    # WKS
    wks1 = mesh_WKS(mesh1, 50, k=k_ev)
    wks2 = mesh_WKS(mesh2, 50, k=k_ev)

    # Landmark HKS
    lm_hks1 = mesh_HKS(mesh1, 50, landmarks=lmks1, k=k_ev)
    lm_hks2 = mesh_HKS(mesh2, 50, landmarks=lmks2, k=k_ev)

    desc1 = np.hstack([hks1, wks1, lm_hks1])
    desc2 = np.hstack([hks2, wks2, lm_hks2])

    # Extrinsic: normalized spatial coordinates (breaks rotational symmetry)
    if use_xyz:
        xyz1 = mesh1.vertices.copy()
        xyz2 = mesh2.vertices.copy()
        # Normalize to unit bounding box
        xyz1 = (xyz1 - xyz1.min(0)) / (xyz1.max(0) - xyz1.min(0) + 1e-10)
        xyz2 = (xyz2 - xyz2.min(0)) / (xyz2.max(0) - xyz2.min(0) + 1e-10)
        # Weight XYZ less than spectral descriptors
        xyz_weight = 0.3
        desc1 = np.hstack([desc1, xyz_weight * xyz1])
        desc2 = np.hstack([desc2, xyz_weight * xyz2])

    # Normalize each descriptor column by L2 norm on mesh
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
    no1[no1 < 1e-10] = 1.0
    no2[no2 < 1e-10] = 1.0
    desc1 /= no1[None, :]
    desc2 /= no2[None, :]

    return desc1, desc2


def landmark_constrained_nn(desc1, desc2, landmarks):
    """
    NN matching in descriptor space, but force landmark pairs to match.
    Then propagate landmark constraints to nearby vertices.
    """
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    # Standard NN
    p2p_21 = knn_query(desc1, desc2, k=1)

    # Force landmark matches
    for l1, l2 in zip(lmks1, lmks2):
        p2p_21[l2] = l1

    return p2p_21


def icp_zoomout_refine(p2p_21_init, mesh1, mesh2, k_start=20, k_end=120, step=5, icp_nit=3):
    """
    Combined ICP + ZoomOut refinement.
    At each ZoomOut step, also run a few ICP iterations to enforce orthogonality.
    """
    ev1 = mesh1.eigenvectors
    ev2 = mesh2.eigenvectors

    # Convert initial p2p to FM
    k = k_start
    FM = spectral.p2p_to_FM(p2p_21_init, ev1[:, :k], ev2[:, :k], A2=mesh2.A)

    while k < k_end:
        # ICP iterations at current scale
        for _ in range(icp_nit):
            FM = icp_iteration(FM, ev1, ev2, use_adj=True)

        # ZoomOut step
        new_k = min(k + step, k_end)
        p2p = spectral.FM_to_p2p(FM, ev1, ev2, use_adj=True)
        FM = spectral.p2p_to_FM(p2p, ev1[:, :new_k], ev2[:, :new_k], A2=mesh2.A)
        k = new_k

    # Final ICP at full resolution
    for _ in range(icp_nit):
        FM = icp_iteration(FM, ev1, ev2, use_adj=True)

    p2p_final = spectral.FM_to_p2p(FM, ev1, ev2, use_adj=True)
    return FM, p2p_final


def run_enhanced_fm(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
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

    # Rich descriptors with XYZ
    t1 = time.time()
    desc1, desc2 = compute_descriptors(mesh1, mesh2, landmarks, k_ev=100, use_xyz=True)
    print(f'  Descriptors ({desc1.shape[1]} dims): {time.time()-t1:.2f}s', flush=True)

    # Landmark-constrained NN init
    t2 = time.time()
    p2p_init = landmark_constrained_nn(desc1, desc2, landmarks)
    n_unique_init = len(np.unique(p2p_init))
    print(f'  NN init: {time.time()-t2:.2f}s, unique={n_unique_init}/{tgt_pos.shape[0]}', flush=True)

    # ICP + ZoomOut combined refinement
    t3 = time.time()
    FM, p2p_final = icp_zoomout_refine(p2p_init, mesh1, mesh2,
                                        k_start=20, k_end=120, step=5, icp_nit=3)
    n_unique = len(np.unique(p2p_final))
    print(f'  ICP+ZoomOut: {time.time()-t3:.2f}s, k={FM.shape[0]}, unique={n_unique}/{tgt_pos.shape[0]}', flush=True)

    # Transfer Y coordinate
    aa_y = aa_pos[:, 1]
    y_p2p = aa_y[p2p_final]

    # Spectral transfer
    k_use = FM.shape[0]
    ev1 = mesh1.eigenvectors[:, :k_use]
    ev2 = mesh2.eigenvectors[:, :k_use]
    a_spec = ev1.T @ mesh1.A @ aa_y
    b_spec = FM[:k_use, :k_use] @ a_spec
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

    print(f'  P2P:      edge_mean={edge_stats(y_p2p)[0]:.4f}  edge_p95={edge_stats(y_p2p)[1]:.4f}')
    print(f'  Spectral: edge_mean={edge_stats(y_spectral)[0]:.4f}  edge_p95={edge_stats(y_spectral)[1]:.4f}')
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

# Run enhanced matching
_, _, FM_hh, p2p_hh, y_p2p_hh, y_spec_hh = run_enhanced_fm('HH', aa_pos, aa_tris, hh_pos, hh_tris)
_, _, FM_tt, p2p_tt, y_p2p_tt, y_spec_tt = run_enhanced_fm('TT', aa_pos, aa_tris, tt_pos, tt_tris)

# Save results
np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_enhanced.npz',
         p2p_hh=p2p_hh, y_p2p_hh=y_p2p_hh, y_spec_hh=y_spec_hh,
         p2p_tt=p2p_tt, y_p2p_tt=y_p2p_tt, y_spec_tt=y_spec_tt,
         aa_y=aa_pos[:, 1])
