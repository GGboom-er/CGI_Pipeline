"""
Comprehensive FM benchmark: all pyFM methods + all quality metrics.

Methods tested:
1. fit() L-BFGS-B with different weight combos
2. fit() + orientation preservation
3. HKS NN + ZoomOut (baseline fast)
4. Geodesic+Spatial NN + ZoomOut (our best so far)
5. ZoomOut with different k_init / step
6. ZoomOut + project_FM (orthogonal projection)
7. FMN network (3-shape joint optimization)
8. use_adj variations

Quality metrics:
- Edge smoothness (mean, p95, max): transfer Y-coord, measure discontinuity
- Bijectivity: unique mappings / total vertices
- Cycle consistency: forward-backward composition error
- Conformal distortion: angle preservation
- Area distortion: triangle area ratio
- Geodesic distortion: geodesic distance preservation
- Dirichlet energy: smoothness of the map itself
"""
import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping
from pyFM.signatures.HKS_functions import mesh_HKS
from pyFM.signatures.WKS_functions import mesh_WKS
from pyFM.spectral.nn_utils import knn_query
from pyFM.refine.zoomout import mesh_zoomout_refine_p2p, mesh_zoomout_refine
from pyFM.refine.icp import mesh_icp_refine
import pyFM.spectral as spectral
import potpourri3d as pp3d
from scipy.spatial import cKDTree
import scipy.linalg


# ============================================================
# METRICS
# ============================================================

def compute_all_metrics(p2p_21, mesh1, mesh2, FM, aa_pos, tgt_pos, tgt_tris):
    """Compute all quality metrics for a given mapping."""
    n2 = tgt_pos.shape[0]
    n1 = aa_pos.shape[0]
    metrics = {}

    # --- 1. Bijectivity ---
    unique = len(np.unique(p2p_21))
    metrics['bijectivity'] = unique / n2

    # --- 2. Edge smoothness (Y-coord transfer) ---
    edges = set()
    for f in tgt_tris:
        edges.add((min(f[0],f[1]), max(f[0],f[1])))
        edges.add((min(f[1],f[2]), max(f[1],f[2])))
        edges.add((min(f[0],f[2]), max(f[0],f[2])))
    edges_arr = np.array(list(edges))

    aa_y = aa_pos[:, 1]
    y_transferred = aa_y[p2p_21]
    diffs = np.abs(y_transferred[edges_arr[:, 0]] - y_transferred[edges_arr[:, 1]])
    metrics['edge_mean'] = float(diffs.mean())
    metrics['edge_p95'] = float(np.percentile(diffs, 95))
    metrics['edge_max'] = float(diffs.max())

    # --- 3. Cycle consistency ---
    # Forward: p2p_21 (mesh2 -> mesh1)
    # Backward: use FM^T to get p2p_12 (mesh1 -> mesh2)
    k = FM.shape[0]
    ev1 = mesh1.eigenvectors[:, :k]
    ev2 = mesh2.eigenvectors[:, :k]
    p2p_12 = spectral.FM_to_p2p(FM.T, ev2, ev1, use_adj=True)
    cycle = p2p_12[p2p_21]  # mesh2 -> mesh1 -> mesh2, should be identity
    cycle_dist = np.linalg.norm(tgt_pos[cycle] - tgt_pos, axis=1)
    metrics['cycle_mean'] = float(cycle_dist.mean())
    metrics['cycle_p95'] = float(np.percentile(cycle_dist, 95))

    # --- 4. Conformal distortion (angle preservation) ---
    # For each triangle on mesh2, compare angles with mapped triangle on mesh1
    angle_errors = []
    for f2 in tgt_tris:
        # Triangle on mesh2
        p2 = tgt_pos[f2]
        # Mapped triangle on mesh1
        p1 = aa_pos[p2p_21[f2]]
        # Compute angles
        for i in range(3):
            v1_2 = p2[(i+1)%3] - p2[i]
            v2_2 = p2[(i+2)%3] - p2[i]
            v1_1 = p1[(i+1)%3] - p1[i]
            v2_1 = p1[(i+2)%3] - p1[i]
            cos2 = np.dot(v1_2, v2_2) / (np.linalg.norm(v1_2) * np.linalg.norm(v2_2) + 1e-10)
            cos1 = np.dot(v1_1, v2_1) / (np.linalg.norm(v1_1) * np.linalg.norm(v2_1) + 1e-10)
            angle_errors.append(abs(np.arccos(np.clip(cos2, -1, 1)) - np.arccos(np.clip(cos1, -1, 1))))
    angle_errors = np.array(angle_errors)
    metrics['conformal_mean'] = float(np.degrees(angle_errors.mean()))
    metrics['conformal_p95'] = float(np.degrees(np.percentile(angle_errors, 95)))

    # --- 5. Area distortion ---
    # Ratio of mapped triangle area to original triangle area
    area_ratios = []
    for f2 in tgt_tris:
        p2 = tgt_pos[f2]
        p1 = aa_pos[p2p_21[f2]]
        area2 = 0.5 * np.linalg.norm(np.cross(p2[1]-p2[0], p2[2]-p2[0]))
        area1 = 0.5 * np.linalg.norm(np.cross(p1[1]-p1[0], p1[2]-p1[0]))
        if area2 > 1e-10:
            area_ratios.append(area1 / area2)
    area_ratios = np.array(area_ratios)
    # Log ratio (0 = perfect, positive = expansion, negative = compression)
    log_ratios = np.log(area_ratios + 1e-10)
    metrics['area_distortion_mean'] = float(np.abs(log_ratios).mean())
    metrics['area_distortion_p95'] = float(np.percentile(np.abs(log_ratios), 95))

    # --- 6. Geodesic distortion (sample-based) ---
    # Sample pairs on mesh2, compare geodesic distances
    np.random.seed(42)
    n_samples = min(50, n2)
    sample_idx = np.random.choice(n2, n_samples, replace=False)
    solver1 = pp3d.MeshHeatMethodDistanceSolver(aa_pos, mesh1.facelist)
    solver2 = pp3d.MeshHeatMethodDistanceSolver(tgt_pos, tgt_tris)

    geo_errors = []
    for i in range(min(10, n_samples)):
        d2 = solver2.compute_distance(sample_idx[i])
        d1 = solver1.compute_distance(p2p_21[sample_idx[i]])
        # Compare distances to other sample points
        for j in range(i+1, n_samples):
            dist2 = d2[sample_idx[j]]
            dist1 = d1[p2p_21[sample_idx[j]]]
            if dist2 > 1e-10:
                geo_errors.append(abs(dist1 - dist2) / dist2)
    geo_errors = np.array(geo_errors) if geo_errors else np.array([0.0])
    metrics['geodesic_mean'] = float(geo_errors.mean())
    metrics['geodesic_p95'] = float(np.percentile(geo_errors, 95))

    # --- 7. FM orthogonality (how close to isometric) ---
    # ||C^T C - I|| measures deviation from isometry
    CtC = FM.T @ FM
    ortho_err = np.linalg.norm(CtC - np.eye(CtC.shape[0]), 'fro') / CtC.shape[0]
    metrics['ortho_error'] = float(ortho_err)

    # --- 8. Dirichlet energy of the map (smoothness) ---
    # Sum of squared differences of p2p across edges
    p2p_pos = aa_pos[p2p_21]  # (n2, 3) mapped positions
    dirichlet = 0
    for e in edges_arr:
        dirichlet += np.sum((p2p_pos[e[0]] - p2p_pos[e[1]])**2)
    dirichlet /= len(edges_arr)
    metrics['dirichlet'] = float(dirichlet)

    return metrics


# ============================================================
# METHODS
# ============================================================

def method_fit_standard(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """Standard pyFM fit with default weights + ZoomOut"""
    fm = FunctionalMapping(mesh1, mesh2)
    fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=50,
                  landmarks=landmarks, subsample_step=5)
    fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0)
    fm.zoomout_refine(nit=10, step=5)
    p2p = fm.get_p2p(use_adj=True)
    return p2p, fm.FM, 'fit_standard'


def method_fit_orient(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """fit() with orientation preservation"""
    fm = FunctionalMapping(mesh1, mesh2)
    fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=50,
                  landmarks=landmarks, subsample_step=5)
    fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0.5)
    fm.zoomout_refine(nit=10, step=5)
    p2p = fm.get_p2p(use_adj=True)
    return p2p, fm.FM, 'fit_orient'


def method_fit_strong_lap(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """fit() with stronger Laplacian commutativity"""
    fm = FunctionalMapping(mesh1, mesh2)
    fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=50,
                  landmarks=landmarks, subsample_step=5)
    fm.fit(w_descr=1.0, w_lap=1e-1, w_dcomm=1.0, w_orient=0)
    fm.zoomout_refine(nit=10, step=5)
    p2p = fm.get_p2p(use_adj=True)
    return p2p, fm.FM, 'fit_strong_lap'


def method_hks_zoomout(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """HKS NN + ZoomOut (fast baseline)"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    hks1 = mesh_HKS(mesh1, 100, k=100)
    hks2 = mesh_HKS(mesh2, 100, k=100)
    lm_hks1 = mesh_HKS(mesh1, 100, landmarks=lmks1, k=100)
    lm_hks2 = mesh_HKS(mesh2, 100, landmarks=lmks2, k=100)
    desc1 = np.hstack([hks1, lm_hks1])
    desc2 = np.hstack([hks2, lm_hks2])
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1)); no1[no1<1e-10]=1
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2)); no2[no2<1e-10]=1
    desc1 /= no1[None, :]; desc2 /= no2[None, :]
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, p2p = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True)
    return p2p, FM, 'hks_zoomout'


def method_geo_spatial_zoomout(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """Geodesic + Spatial descriptors + ZoomOut (our best)"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    # Geodesic from landmarks
    solver1 = pp3d.MeshHeatMethodDistanceSolver(aa_pos, mesh1.facelist)
    solver2 = pp3d.MeshHeatMethodDistanceSolver(tgt_pos, mesh2.facelist)
    geo1 = np.column_stack([solver1.compute_distance(l) for l in lmks1])
    geo2 = np.column_stack([solver2.compute_distance(l) for l in lmks2])
    for i in range(geo1.shape[1]):
        geo1[:, i] /= (geo1[:, i].max() + 1e-10)
        geo2[:, i] /= (geo2[:, i].max() + 1e-10)
    # Spatial
    def spatial(v):
        y = (v[:,1]-v[:,1].min())/(v[:,1].max()-v[:,1].min()+1e-10)
        cx, cz = v[:,0].mean(), v[:,2].mean()
        angle = (np.arctan2(v[:,0]-cx, v[:,2]-cz)+np.pi)/(2*np.pi)
        r = np.sqrt((v[:,0]-cx)**2+(v[:,2]-cz)**2)
        r = (r-r.min())/(r.max()-r.min()+1e-10)
        return np.column_stack([y, angle, r])
    sp1, sp2 = spatial(aa_pos), spatial(tgt_pos)
    desc1 = np.hstack([geo1, 2.0*sp1])
    desc2 = np.hstack([geo2, 2.0*sp2])
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, p2p = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True)
    return p2p, FM, 'geo_spatial_zo'


def method_geo_spatial_highk(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """Geodesic+Spatial + ZoomOut with higher k_init=40, more iterations"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    solver1 = pp3d.MeshHeatMethodDistanceSolver(aa_pos, mesh1.facelist)
    solver2 = pp3d.MeshHeatMethodDistanceSolver(tgt_pos, mesh2.facelist)
    geo1 = np.column_stack([solver1.compute_distance(l) for l in lmks1])
    geo2 = np.column_stack([solver2.compute_distance(l) for l in lmks2])
    for i in range(geo1.shape[1]):
        geo1[:, i] /= (geo1[:, i].max() + 1e-10)
        geo2[:, i] /= (geo2[:, i].max() + 1e-10)
    def spatial(v):
        y = (v[:,1]-v[:,1].min())/(v[:,1].max()-v[:,1].min()+1e-10)
        cx, cz = v[:,0].mean(), v[:,2].mean()
        angle = (np.arctan2(v[:,0]-cx, v[:,2]-cz)+np.pi)/(2*np.pi)
        r = np.sqrt((v[:,0]-cx)**2+(v[:,2]-cz)**2)
        r = (r-r.min())/(r.max()-r.min()+1e-10)
        return np.column_stack([y, angle, r])
    sp1, sp2 = spatial(aa_pos), spatial(tgt_pos)
    desc1 = np.hstack([geo1, 2.0*sp1])
    desc2 = np.hstack([geo2, 2.0*sp2])
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, p2p = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=40, nit=22, step=5, return_p2p=True)
    return p2p, FM, 'geo_spatial_highk'


def method_zoomout_then_icp(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """Geo+Spatial + ZoomOut + ICP post-refinement"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    solver1 = pp3d.MeshHeatMethodDistanceSolver(aa_pos, mesh1.facelist)
    solver2 = pp3d.MeshHeatMethodDistanceSolver(tgt_pos, mesh2.facelist)
    geo1 = np.column_stack([solver1.compute_distance(l) for l in lmks1])
    geo2 = np.column_stack([solver2.compute_distance(l) for l in lmks2])
    for i in range(geo1.shape[1]):
        geo1[:, i] /= (geo1[:, i].max() + 1e-10)
        geo2[:, i] /= (geo2[:, i].max() + 1e-10)
    def spatial(v):
        y = (v[:,1]-v[:,1].min())/(v[:,1].max()-v[:,1].min()+1e-10)
        cx, cz = v[:,0].mean(), v[:,2].mean()
        angle = (np.arctan2(v[:,0]-cx, v[:,2]-cz)+np.pi)/(2*np.pi)
        r = np.sqrt((v[:,0]-cx)**2+(v[:,2]-cz)**2)
        r = (r-r.min())/(r.max()-r.min()+1e-10)
        return np.column_stack([y, angle, r])
    sp1, sp2 = spatial(aa_pos), spatial(tgt_pos)
    desc1 = np.hstack([geo1, 2.0*sp1])
    desc2 = np.hstack([geo2, 2.0*sp2])
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, _ = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True)
    # ICP post-refinement
    FM_icp = mesh_icp_refine(FM, mesh1, mesh2, nit=10, use_adj=True)
    p2p = spectral.mesh_FM_to_p2p(FM_icp, mesh1, mesh2, use_adj=True)
    return p2p, FM_icp, 'zo_then_icp'


def method_project_fm(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """Geo+Spatial + ZoomOut + project FM to orthogonal"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    solver1 = pp3d.MeshHeatMethodDistanceSolver(aa_pos, mesh1.facelist)
    solver2 = pp3d.MeshHeatMethodDistanceSolver(tgt_pos, mesh2.facelist)
    geo1 = np.column_stack([solver1.compute_distance(l) for l in lmks1])
    geo2 = np.column_stack([solver2.compute_distance(l) for l in lmks2])
    for i in range(geo1.shape[1]):
        geo1[:, i] /= (geo1[:, i].max() + 1e-10)
        geo2[:, i] /= (geo2[:, i].max() + 1e-10)
    def spatial(v):
        y = (v[:,1]-v[:,1].min())/(v[:,1].max()-v[:,1].min()+1e-10)
        cx, cz = v[:,0].mean(), v[:,2].mean()
        angle = (np.arctan2(v[:,0]-cx, v[:,2]-cz)+np.pi)/(2*np.pi)
        r = np.sqrt((v[:,0]-cx)**2+(v[:,2]-cz)**2)
        r = (r-r.min())/(r.max()-r.min()+1e-10)
        return np.column_stack([y, angle, r])
    sp1, sp2 = spatial(aa_pos), spatial(tgt_pos)
    desc1 = np.hstack([geo1, 2.0*sp1])
    desc2 = np.hstack([geo2, 2.0*sp2])
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, _ = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True)
    # Project to nearest orthogonal matrix
    U, S, Vt = scipy.linalg.svd(FM)
    FM_proj = U @ np.eye(FM.shape[0], FM.shape[1]) @ Vt
    p2p = spectral.mesh_FM_to_p2p(FM_proj, mesh1, mesh2, use_adj=True)
    return p2p, FM_proj, 'project_ortho'


def method_wks_zoomout(mesh1, mesh2, aa_pos, tgt_pos, landmarks):
    """WKS NN + ZoomOut"""
    lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
    wks1 = mesh_WKS(mesh1, 100, k=100)
    wks2 = mesh_WKS(mesh2, 100, k=100)
    lm_wks1 = mesh_WKS(mesh1, 100, landmarks=lmks1, k=100)
    lm_wks2 = mesh_WKS(mesh2, 100, landmarks=lmks2, k=100)
    desc1 = np.hstack([wks1, lm_wks1])
    desc2 = np.hstack([wks2, lm_wks2])
    no1 = np.sqrt(mesh1.l2_sqnorm(desc1)); no1[no1<1e-10]=1
    no2 = np.sqrt(mesh2.l2_sqnorm(desc2)); no2[no2<1e-10]=1
    desc1 /= no1[None, :]; desc2 /= no2[None, :]
    p2p_init = knn_query(desc1, desc2, k=1)
    for l1, l2 in zip(lmks1, lmks2): p2p_init[l2] = l1
    FM, p2p = mesh_zoomout_refine_p2p(p2p_init, mesh1, mesh2, k_init=20, nit=20, step=5, return_p2p=True)
    return p2p, FM, 'wks_zoomout'


# ============================================================
# MAIN
# ============================================================

def run_benchmark(name, aa_pos, aa_tris, tgt_pos, tgt_tris):
    print(f'\n{"="*60}')
    print(f'  BENCHMARK: AA -> {name}')
    print(f'{"="*60}')
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

    methods = [
        method_hks_zoomout,
        method_wks_zoomout,
        method_geo_spatial_zoomout,
        method_geo_spatial_highk,
        method_zoomout_then_icp,
        method_project_fm,
        method_fit_standard,
        method_fit_orient,
        method_fit_strong_lap,
    ]

    results = {}
    for method_fn in methods:
        try:
            t1 = time.time()
            p2p, FM, label = method_fn(mesh1, mesh2, aa_pos, tgt_pos, landmarks)
            elapsed = time.time() - t1
            metrics = compute_all_metrics(p2p, mesh1, mesh2, FM, aa_pos, tgt_pos, tgt_tris)
            metrics['time'] = elapsed
            results[label] = metrics
            print(f'  {label:20s} ({elapsed:5.1f}s) | '
                  f'bij={metrics["bijectivity"]:.2f} '
                  f'edge_p95={metrics["edge_p95"]:.3f} '
                  f'cycle={metrics["cycle_mean"]:.3f} '
                  f'conf={metrics["conformal_mean"]:.1f}° '
                  f'geo={metrics["geodesic_mean"]:.3f} '
                  f'ortho={metrics["ortho_error"]:.3f}', flush=True)
        except Exception as e:
            print(f'  {method_fn.__name__:20s} FAILED: {e}', flush=True)

    print(f'\n  Total benchmark time: {time.time()-t0:.1f}s')
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

r_hh = run_benchmark('HH', aa_pos, aa_tris, hh_pos, hh_tris)
r_tt = run_benchmark('TT', aa_pos, aa_tris, tt_pos, tt_tris)

# Print summary table
print('\n\n' + '='*80)
print('SUMMARY TABLE')
print('='*80)
header = f'{"Method":<20} {"Bij":>5} {"Edge95":>7} {"Cycle":>6} {"Conf°":>6} {"Area":>6} {"Geo":>6} {"Ortho":>6} {"Dir":>6} {"Time":>5}'
for target, results in [('HH', r_hh), ('TT', r_tt)]:
    print(f'\n--- AA -> {target} ---')
    print(header)
    for label, m in sorted(results.items(), key=lambda x: x[1]['edge_p95']):
        print(f'{label:<20} {m["bijectivity"]:>5.2f} {m["edge_p95"]:>7.3f} '
              f'{m["cycle_mean"]:>6.3f} {m["conformal_mean"]:>6.1f} '
              f'{m["area_distortion_mean"]:>6.3f} {m["geodesic_mean"]:>6.3f} '
              f'{m["ortho_error"]:>6.3f} {m["dirichlet"]:>6.2f} {m["time"]:>5.1f}')
