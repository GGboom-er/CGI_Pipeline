"""
计算 pyFM AA->KKK 对应关系并保存可视化数据
"""
import sys
import time
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\pyFM')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\robust_laplacian')
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline\research\potpourri3d')

import numpy as np
from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping
from scipy.spatial import cKDTree

t0 = time.time()

data = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_data.npz')
aa_pos = data['aa_pos']
aa_tris = data['aa_tris']
kkk_pos = data['kkk_pos']
kkk_tris = data['kkk_tris']

print(f'AA: {aa_pos.shape[0]} vtx, KKK: {kkk_pos.shape[0]} vtx')

mesh1 = TriMesh(aa_pos, aa_tris)
mesh2 = TriMesh(kkk_pos, kkk_tris)

print("Processing meshes (k=150)...")
mesh1.process(k=150, intrinsic=True)
print(f"  mesh1 done ({time.time()-t0:.1f}s)")
mesh2.process(k=150, intrinsic=True)
print(f"  mesh2 done ({time.time()-t0:.1f}s)")

def auto_landmarks(pos1, pos2):
    pairs = []
    for axis in range(3):
        pairs.append([int(np.argmax(pos1[:, axis])), int(np.argmax(pos2[:, axis]))])
        pairs.append([int(np.argmin(pos1[:, axis])), int(np.argmin(pos2[:, axis]))])
    return np.array(pairs)

landmarks = auto_landmarks(aa_pos, kkk_pos)
print(f'Landmarks: {landmarks.shape[0]} pairs')

fm = FunctionalMapping(mesh1, mesh2)
fm.preprocess(n_ev=(80, 80), descr_type='WKS', n_descr=100, landmarks=landmarks)
print(f"  preprocess done ({time.time()-t0:.1f}s)")

fm.fit(w_descr=1.0, w_lap=1e-2, w_dcomm=1.0, w_orient=0, optinit='zeros')
print(f"  fit done ({time.time()-t0:.1f}s)")

fm.zoomout_refine(step=5, nit=10)
print(f"  zoomout done ({time.time()-t0:.1f}s), FM shape: {fm.FM.shape}")

p2p = fm.get_p2p(use_adj=True)
print(f'p2p: {p2p.shape}, unique AA: {len(np.unique(p2p))}/382')

# Spectral KNN for soft weights
k2, k1 = fm.FM.shape
emb1 = mesh1.eigenvectors[:, :k1]
emb2 = mesh2.eigenvectors[:, :k2] @ fm.FM

tree = cKDTree(emb1)
dists, indices = tree.query(emb2, k=3)
print(f'KNN done ({time.time()-t0:.1f}s)')

eps = 1e-10
inv_dists = 1.0 / (dists + eps)
weights = inv_dists / inv_dists.sum(axis=1, keepdims=True)

# Confidence: normalized by median distance (scale-invariant)
spec_dists = dists[:, 0]
median_dist = np.median(spec_dists)
confidence = np.exp(-spec_dists / (median_dist + eps))

# Max weight (sharpness) - ratio of nearest to second nearest
sharpness = dists[:, 1] / (dists[:, 0] + eps)  # >1 means nearest is clearly closer
sharpness = np.clip(sharpness, 1.0, 10.0)
sharpness_norm = (sharpness - 1.0) / 9.0  # normalize to [0, 1]

max_weights = weights.max(axis=1)

np.savez(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_viz.npz',
    p2p=p2p,
    confidence=confidence,
    spec_dists=spec_dists,
    knn_indices=indices,
    knn_weights=weights,
    max_weights=max_weights,
    sharpness_norm=sharpness_norm,
    aa_pos=aa_pos,
    kkk_pos=kkk_pos
)

print(f'\nDone in {time.time()-t0:.1f}s')
print(f'Confidence: min={confidence.min():.4f}, max={confidence.max():.4f}, mean={confidence.mean():.4f}')
print(f'Sharpness: min={sharpness_norm.min():.4f}, max={sharpness_norm.max():.4f}, mean={sharpness_norm.mean():.4f}')
print(f'Max weights: min={max_weights.min():.4f}, max={max_weights.max():.4f}, mean={max_weights.mean():.4f}')
print(f'Saved test_fm_viz.npz')
