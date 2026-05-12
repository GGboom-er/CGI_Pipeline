import sys, time
import numpy as np
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping
from fast_fm_solve import fit_fm_direct
from pyFM.refine.zoomout import mesh_zoomout_refine
import pyFM.spectral as spectral

d1 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_data.npz')
d2 = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_hh_tris.npz')
aa_pos, hh_pos = d1['aa_pos'], d1['hh_pos']
aa_tris, hh_tris = d2['aa_tris'], d2['hh_tris']

mesh1 = TriMesh(aa_pos, aa_tris)
mesh2 = TriMesh(hh_pos, hh_tris)
mesh1.process(k=100, intrinsic=True)
mesh2.process(k=100, intrinsic=True)

# Landmarks
pairs = []
for axis in range(3):
    pairs.append([int(np.argmax(aa_pos[:, axis])), int(np.argmax(hh_pos[:, axis]))])
    pairs.append([int(np.argmin(aa_pos[:, axis])), int(np.argmin(hh_pos[:, axis]))])
landmarks = np.array(pairs)

# --- pyFM standard (subsample_step=5 for speed) ---
print("=== pyFM L-BFGS-B (subsample_step=5) ===")
fm = FunctionalMapping(mesh1, mesh2)
fm.preprocess(n_ev=(80, 80), descr_type='HKS', n_descr=50, landmarks=landmarks, subsample_step=5)
t0 = time.time()
fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0, optinit='zeros')
print(f'  fit time: {time.time()-t0:.2f}s')
fm.zoomout_refine(step=5, nit=10)
p2p_lbfgs = fm.get_p2p(use_adj=True)
print(f'  unique: {len(np.unique(p2p_lbfgs))}/400')

# --- Direct solve (full descriptors, no subsample) ---
print("\n=== Direct least-squares (full descriptors) ===")
import pyFM.signatures as sg
k1, k2 = 80, 80
descr1 = sg.mesh_HKS(mesh1, 50, k=k1)
descr2 = sg.mesh_HKS(mesh2, 50, k=k2)
lmks1, lmks2 = landmarks[:, 0], landmarks[:, 1]
lm_descr1 = sg.mesh_HKS(mesh1, 50, landmarks=lmks1, k=k1)
lm_descr2 = sg.mesh_HKS(mesh2, 50, landmarks=lmks2, k=k2)
descr1_full = np.hstack([descr1, lm_descr1])
descr2_full = np.hstack([descr2, lm_descr2])
# Normalize
no1 = np.sqrt(mesh1.l2_sqnorm(descr1_full))
no2 = np.sqrt(mesh2.l2_sqnorm(descr2_full))
descr1_full /= no1[None, :]
descr2_full /= no2[None, :]

C_direct, elapsed = fit_fm_direct(mesh1, mesh2, descr1_full, descr2_full, k1, k2,
                                   w_descr=1.0, w_lap=1e-2, w_dcomm=1.0)
print(f'  fit time: {elapsed:.2f}s')

# Zoomout
C_zo = mesh_zoomout_refine(C_direct, mesh1, mesh2, nit=10, step=5)
p2p_direct = spectral.mesh_FM_to_p2p(C_zo, mesh1, mesh2, use_adj=True)
print(f'  unique: {len(np.unique(p2p_direct))}/400')

# Compare
print(f'\n=== Comparison ===')
print(f'  FM diff (before zoomout): {np.linalg.norm(fm._FM_base - C_direct):.4f}')
print(f'  p2p agreement: {np.mean(p2p_lbfgs == p2p_direct)*100:.1f}%')
