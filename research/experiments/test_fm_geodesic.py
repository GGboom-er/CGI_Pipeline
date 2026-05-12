"""
Better approach for non-isometric but spatially-aligned shapes:
1. Geodesic distance from landmarks as descriptors (more discriminative than HKS)
2. Direct spatial parameterization matching
3. Weighted combination
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p
import pyFM.spectral as spectral
import potpourri3d as pp3d
from scipy.spatial import cKDTree


def geodesic_descriptors(vertices, faces, landmarks):
    """
    Compute geodesic distance from each landmark to all vertices.
    This is a powerful descriptor for non-isometric shapes because
    it captures the spatial structure relative to known anchor points.
    """
    solver = pp3d.MeshHeatMethodDistanceSolver(vertices, faces)
    descs = []
    for lm in landmarks:
        dist = solver.compute_distance(lm)
        descs.append(dist)
    return np.column_stack(descs)  # (n_vtx, n_landmarks)


def spatial_parameterization(vertices):
    """
    Parameterize mesh by normalized height (Y) and angle (atan2(X,Z)).
    Works well for shapes that are roughly cylindrically symmetric and aligned.
    """
    # Normalized height
    y = vertices[:, 1]
    h = (y - y.min()) / (y.max() - y.min() + 1e-10)

    # Angle around Y axis
    x, z = vertices[:, 0], vertices[:, 2]
    cx, cz = x.mean(), z.mean()
    angle = np.arctan2(x - cx, z - cz)  # [-pi, pi]
    angle_norm = (angle + np.pi) / (2 * np.pi)  # [0, 1]

    # Radius from center axis
    r = np.sqrt((x - cx)**2 + (z - cz)**2)
    r_norm = (r - r.min()) / (r.max() - r.min() + 1e-10)

    return np.column_stack([h, angle_norm, r_norm])


def run_geodesic_fm(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*50}')
    print(f'AA ({aa_pos.shape[0]} vtx) -> {name} ({tgt_pos.shape[0]} vtx)')
    print(f'{"="*50}')
    t0 = time.time()

    mesh1 = TriMesh(aa_pos, aa_tris)
    mesh2 = TriMesh(tgt_pos, tgt_tris)
    mesh1.process(k=150, intrinsic=True)
    mesh2.process(k=150, intrinsic=True)

    # More landmarks: 6 extremal + center top/bottom
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(tgt_pos[:, axis]))])
        pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(tgt_pos[:, axis]))])
    landmarks = np.array(pairs)
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]

    # --- Method 1: Geodesic distance descriptors ---
    print('  Computing geodesic descriptors...', flush=True)
    t1 = time.time()
    geo_desc1 = geodesic_descriptors(aa_pos, aa_tris, lmks1)
    geo_desc2 = geodesic_descriptors(tgt_pos, tgt_tris, lmks2)
    # Normalize each column
    for i in range(geo_desc1.shape[1]):
        geo_desc1[:, i] /= (geo_desc1[:, i].max() + 1e-10)
        geo_desc2[:, i] /= (geo_desc2[:, i].max() + 1e-10)
    print(f'  Geodesic desc: {time.time()-t1:.2f}s', flush=True)

    # --- Method 2: Spatial parameterization ---
    spatial1 = spatial_parameterization(aa_pos)
    spatial2 = spatial_parameterization(tgt_pos)

    # --- Combined descriptor: geodesic + spatial ---
    # Weight spatial more heavily since shapes are aligned
    desc1 = np.hstack([geo_desc1, 2.0 * spatial1])
    desc2 = np.hstack([geo_desc2, 2.0 * spatial2])

    # NN matching
    p2p_combined = knn_query(desc1, desc2, k=1)
    # Force landmarks
    for l1, l2 in zip(lmks1, lmks2):
        p2p_combined[l2] = l1

    print(f'  Combined NN: unique={len(np.unique(p2p_combined))}/{tgt_pos.shape[0]}', flush=True)

    # ZoomOut refinement
    t2 = time.time()
    FM_c, p2p_c = mesh_zoomout_refine_p2p(
        p2p_21=p2p_combined, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1
    )
    print(f'  ZoomOut: {time.time()-t2:.2f}s, k={FM_c.shape[0]}, unique={len(np.unique(p2p_c))}/{tgt_pos.shape[0]}', flush=True)

    # --- Method 3: Pure spatial NN (no FM, just closest in parameterization) ---
    # Use height + angle matching directly
    tree1 = cKDTree(spatial1)
    _, p2p_spatial = tree1.query(spatial2)
    print(f'  Pure spatial NN: unique={len(np.unique(p2p_spatial))}/{tgt_pos.shape[0]}', flush=True)

    # ZoomOut on spatial init
    FM_s, p2p_s = mesh_zoomout_refine_p2p(
        p2p_21=p2p_spatial, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1
    )
    print(f'  Spatial+ZoomOut: unique={len(np.unique(p2p_s))}/{tgt_pos.shape[0]}', flush=True)

    # --- Method 4: Pure geodesic NN + ZoomOut ---
    p2p_geo = knn_query(geo_desc1, geo_desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2):
        p2p_geo[l2] = l1
    FM_g, p2p_g = mesh_zoomout_refine_p2p(
        p2p_21=p2p_geo, mesh1=mesh1, mesh2=mesh2,
        k_init=20, nit=20, step=5, return_p2p=True, n_jobs=1
    )
    print(f'  Geodesic+ZoomOut: unique={len(np.unique(p2p_g))}/{tgt_pos.shape[0]}', flush=True)

    # Evaluate all methods
    aa_y = aa_pos[:, 1]
    edges = set()
    for f in tgt_tris:
        edges.add((min(f[0],f[1]), max(f[0],f[1])))
        edges.add((min(f[1],f[2]), max(f[1],f[2])))
        edges.add((min(f[0],f[2]), max(f[0],f[2])))
    edges_arr = np.array(list(edges))

    def edge_stats(values):
        diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
        return diffs.mean(), np.percentile(diffs, 95), diffs.max()

    results = {}
    for label, p2p, FM in [('Combined+ZO', p2p_c, FM_c),
                            ('Spatial+ZO', p2p_s, FM_s),
                            ('Geodesic+ZO', p2p_g, FM_g)]:
        y_p2p = aa_y[p2p]
        # Spectral
        k_use = FM.shape[0]
        ev1 = mesh1.eigenvectors[:, :k_use]
        ev2 = mesh2.eigenvectors[:, :k_use]
        a_spec = ev1.T @ mesh1.A @ aa_y
        y_spec = ev2 @ (FM @ a_spec)

        em, ep, emax = edge_stats(y_p2p)
        sm, sp, smax = edge_stats(y_spec)
        print(f'  {label:16s} p2p: mean={em:.4f} p95={ep:.4f} max={emax:.4f}')
        print(f'  {" "*16} spec: mean={sm:.4f} p95={sp:.4f} max={smax:.4f}')
        results[label] = {'p2p': y_p2p, 'spec': y_spec, 'FM': FM, 'p2p_map': p2p}

    print(f'  Total: {time.time()-t0:.2f}s')
    return results


# Load data
d_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
t_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
d_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_data.npz')
t_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_tris.npz')

aa_pos = d_hh['aa_pos']
aa_tris = t_hh['aa_tris']
hh_pos, hh_tris = d_hh['hh_pos'], t_hh['hh_tris']
tt_pos, tt_tris = d_tt['tt_pos'], t_tt['tt_tris']

r_hh = run_geodesic_fm('HH', aa_pos, aa_tris, hh_pos, hh_tris)
r_tt = run_geodesic_fm('TT', aa_pos, aa_tris, tt_pos, tt_tris)

# Save best results for Maya
np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_geo.npz',
         # HH
         hh_combined_p2p=r_hh['Combined+ZO']['p2p'],
         hh_spatial_p2p=r_hh['Spatial+ZO']['p2p'],
         hh_geodesic_p2p=r_hh['Geodesic+ZO']['p2p'],
         hh_combined_spec=r_hh['Combined+ZO']['spec'],
         # TT
         tt_combined_p2p=r_tt['Combined+ZO']['p2p'],
         tt_spatial_p2p=r_tt['Spatial+ZO']['p2p'],
         tt_geodesic_p2p=r_tt['Geodesic+ZO']['p2p'],
         tt_combined_spec=r_tt['Combined+ZO']['spec'],
         aa_y=aa_pos[:, 1])
