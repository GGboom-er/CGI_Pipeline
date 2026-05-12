"""
Use pyFM's fit() core properly:
- Inject our geodesic+spatial descriptors manually
- Use proper weights
- Compare with pure ZoomOut

The key insight: fit() optimizes BOTH descriptor preservation AND structural constraints
(Laplacian commutativity, orientation). ZoomOut only does p2p<->FM iteration.
If we give fit() good descriptors, it should outperform ZoomOut.
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping
from pyFM.signatures.HKS_functions import mesh_HKS
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p
import pyFM.spectral as spectral
import potpourri3d as pp3d


def compute_geo_spatial_desc(mesh, pos, lmks, k_ev=100):
    """Our best descriptors: geodesic from landmarks + spatial"""
    # Geodesic from landmarks
    solver = pp3d.MeshHeatMethodDistanceSolver(pos, mesh.facelist)
    geo = np.column_stack([solver.compute_distance(l) for l in lmks])
    for i in range(geo.shape[1]):
        geo[:, i] /= (geo[:, i].max() + 1e-10)

    # Spatial parameterization
    y = (pos[:, 1] - pos[:, 1].min()) / (pos[:, 1].max() - pos[:, 1].min() + 1e-10)
    cx, cz = pos[:, 0].mean(), pos[:, 2].mean()
    angle = (np.arctan2(pos[:, 0] - cx, pos[:, 2] - cz) + np.pi) / (2 * np.pi)
    r = np.sqrt((pos[:, 0] - cx)**2 + (pos[:, 2] - cz)**2)
    r = (r - r.min()) / (r.max() - r.min() + 1e-10)
    spatial = np.column_stack([y, angle, r])

    # HKS for intrinsic info
    hks = mesh_HKS(mesh, 50, k=k_ev)

    return np.hstack([geo, 2.0 * spatial, hks])


def run_fit_with_custom_desc(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*60}')
    print(f'AA -> {name}: fit() with custom descriptors')
    print(f'{"="*60}')

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

    # Compute our custom descriptors
    desc1 = compute_geo_spatial_desc(mesh1, aa_pos, lmks1)
    desc2 = compute_geo_spatial_desc(mesh2, tgt_pos, lmks2)

    # Normalize using mesh area
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1))
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2))
    no1[no1 < 1e-10] = 1.0
    no2[no2 < 1e-10] = 1.0
    desc1 /= no1[None, :]
    desc2 /= no2[None, :]

    # --- Method A: Inject into FunctionalMapping and use fit() ---
    print('\n  [A] fit() with custom descriptors:', flush=True)
    fm = FunctionalMapping(mesh1, mesh2)
    fm.k1, fm.k2 = 80, 80
    # Manually inject descriptors (bypass preprocess)
    fm.descr1 = desc1
    fm.descr2 = desc2
    fm._preprocessed = True

    t1 = time.time()
    fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0.5, verbose=True)
    print(f'  fit() time: {time.time()-t1:.2f}s', flush=True)

    # ZoomOut refinement on top of fit
    fm.zoomout_refine(nit=10, step=5)
    p2p_fit = fm.get_p2p(use_adj=True)
    FM_fit = fm.FM
    print(f'  fit+ZO: k={FM_fit.shape[0]}, unique={len(np.unique(p2p_fit))}/{tgt_pos.shape[0]}', flush=True)

    # --- Method B: Same descriptors, pure NN + ZoomOut (no fit) ---
    print('\n  [B] NN + ZoomOut (same descriptors):', flush=True)
    t2 = time.time()
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2):
        p2p_init[l2] = l1
    FM_zo, p2p_zo = mesh_zoomout_refine_p2p(
        p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True
    )
    print(f'  ZoomOut: {time.time()-t2:.2f}s, k={FM_zo.shape[0]}, unique={len(np.unique(p2p_zo))}/{tgt_pos.shape[0]}', flush=True)

    # --- Method C: fit() initialized from NN (optinit from our p2p) ---
    print('\n  [C] fit() initialized from NN p2p:', flush=True)
    fm2 = FunctionalMapping(mesh1, mesh2)
    fm2.k1, fm2.k2 = 80, 80
    fm2.descr1 = desc1
    fm2.descr2 = desc2
    fm2._preprocessed = True
    # Convert our NN p2p to FM as initialization
    FM_init = spectral.p2p_to_FM(p2p_init, mesh1.eigenvectors[:, :80], mesh2.eigenvectors[:, :80], A2=mesh2.A)
    # Inject as initial FM
    fm2.FM = FM_init
    # Now refine with ICP + ZoomOut
    fm2.icp_refine(nit=5, use_adj=True)
    fm2.zoomout_refine(nit=10, step=5)
    p2p_c = fm2.get_p2p(use_adj=True)
    FM_c = fm2.FM
    print(f'  NN->ICP->ZO: k={FM_c.shape[0]}, unique={len(np.unique(p2p_c))}/{tgt_pos.shape[0]}', flush=True)

    # --- Evaluate all ---
    aa_y = aa_pos[:, 1]
    edges = set()
    for f in tgt_tris:
        edges.add((min(f[0], f[1]), max(f[0], f[1])))
        edges.add((min(f[1], f[2]), max(f[1], f[2])))
        edges.add((min(f[0], f[2]), max(f[0], f[2])))
    edges_arr = np.array(list(edges))

    def edge_stats(values):
        diffs = np.abs(values[edges_arr[:, 0]] - values[edges_arr[:, 1]])
        return diffs.mean(), np.percentile(diffs, 95), diffs.max()

    def cycle_err(p2p_21, FM):
        k = FM.shape[0]
        ev1 = mesh1.eigenvectors[:, :k]
        ev2 = mesh2.eigenvectors[:, :k]
        p2p_12 = spectral.FM_to_p2p(FM.T, ev2, ev1, use_adj=True)
        cycle = p2p_12[p2p_21]
        return np.linalg.norm(tgt_pos[cycle] - tgt_pos, axis=1).mean()

    print(f'\n  {"Method":<20} {"Edge95":>7} {"EdgeMax":>8} {"Cycle":>6} {"Unique":>6}')
    print(f'  {"-"*50}')
    for label, p2p, FM in [('[A] fit+ZO', p2p_fit, FM_fit),
                            ('[B] NN+ZO', p2p_zo, FM_zo),
                            ('[C] NN->ICP->ZO', p2p_c, FM_c)]:
        y = aa_y[p2p]
        em, ep, emax = edge_stats(y)
        cyc = cycle_err(p2p, FM)
        uniq = len(np.unique(p2p))
        print(f'  {label:<20} {ep:>7.3f} {emax:>8.3f} {cyc:>6.3f} {uniq:>6}')


# Load data
d_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
t_hh = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
d_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_data.npz')
t_tt = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_tt_tris.npz')

aa_pos = d_hh['aa_pos']
aa_tris = t_hh['aa_tris']
hh_pos, hh_tris = d_hh['hh_pos'], t_hh['hh_tris']
tt_pos, tt_tris = d_tt['tt_pos'], t_tt['tt_tris']

run_fit_with_custom_desc('HH', aa_pos, aa_tris, hh_pos, hh_tris)
run_fit_with_custom_desc('TT', aa_pos, aa_tris, tt_pos, tt_tris)
