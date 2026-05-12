"""测试 pyFM Functional Maps 在 AA→TT 权重传递上的效果。"""
import sys
import json
import numpy as np
import time

sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline/research/pyFM')
sys.path.insert(0, 'Y:/GGbommer/scripts/CGI_Pipeline')

from pyFM.mesh import TriMesh
from pyFM.functional import FunctionalMapping

# 加载数据
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/scene_data.json') as f:
    src_data = json.load(f)
with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/target_meshes.json') as f:
    tgt_data = json.load(f)

# 用 AA 作为源（最大的绑定 mesh）
aa = src_data['AA']
aa_verts = np.array(aa['positions'], dtype=np.float64)
aa_faces = np.array(aa['faces'], dtype=np.intp)
aa_weights = np.array(aa['weights'], dtype=np.float64)
joints = aa['joints']

# TT 作为目标
tt = tgt_data['TT']
tt_verts = np.array(tt['positions'], dtype=np.float64)
tt_faces = np.array(tt['faces'], dtype=np.intp)

print(f"Source AA: {aa_verts.shape[0]}v, {aa_faces.shape[0]}f")
print(f"Target TT: {tt_verts.shape[0]}v, {tt_faces.shape[0]}f")

# 构建 pyFM TriMesh
t0 = time.time()
mesh1 = TriMesh(aa_verts, aa_faces)
mesh2 = TriMesh(tt_verts, tt_faces)
print(f"TriMesh created: {time.time()-t0:.2f}s")

# 构建 Functional Map
fm = FunctionalMapping(mesh1, mesh2)

# 预处理：计算 Laplacian 特征基 + WKS 描述子
t0 = time.time()
fm.preprocess(n_ev=(30, 30), n_descr=100, descr_type='WKS', verbose=True)
print(f"Preprocess done: {time.time()-t0:.2f}s")

# 拟合 FM（无 landmark，纯描述子匹配）
t0 = time.time()
fm.fit(w_descr=1.0, w_lap=0.1, w_dcomm=0.01, w_orient=0.0, verbose=True)
print(f"Fit done: {time.time()-t0:.2f}s")

# 获取点对点映射 (target → source)
p2p_21 = fm.get_p2p()
print(f"p2p map shape: {p2p_21.shape}")
print(f"p2p range: [{p2p_21.min()}, {p2p_21.max()}] (source has {aa_verts.shape[0]} verts)")

# 用 p2p 直接传递权重
tt_weights_fm = aa_weights[p2p_21]  # (n_tt, J)
print(f"\nFM transferred weights shape: {tt_weights_fm.shape}")

# 也试试 ZoomOut 精修
t0 = time.time()
fm.icp_refine(verbose=True)
print(f"ICP refine done: {time.time()-t0:.2f}s")

fm.change_FM_type('icp')
p2p_21_icp = fm.get_p2p()
tt_weights_icp = aa_weights[p2p_21_icp]

# ZoomOut
t0 = time.time()
fm.zoomout_refine(nit=10, step=5, verbose=True)
print(f"ZoomOut done: {time.time()-t0:.2f}s")

fm.change_FM_type('zoomout')
p2p_21_zo = fm.get_p2p()
tt_weights_zo = aa_weights[p2p_21_zo]

# 输出结果对比
print("\n=== Results ===")
for name, w in [('FM_basic', tt_weights_fm), ('FM_icp', tt_weights_icp), ('FM_zoomout', tt_weights_zo)]:
    print(f"\n--- {name} ---")
    for ji, jname in enumerate(joints):
        col = w[:, ji]
        if col.max() > 0.01:
            affected = int((col > 0.01).sum())
            print(f"  {jname}: max={col.max():.4f}, affected={affected}/{tt_verts.shape[0]}")

# 保存最佳结果
best_weights = tt_weights_zo if tt_weights_zo is not None else tt_weights_icp

# 归一化
row_sums = best_weights.sum(axis=1, keepdims=True)
row_sums[row_sums == 0] = 1.0
best_weights = best_weights / row_sums

result = {
    'TT': {
        'weights': best_weights.tolist(),
        'joints': joints,
        'method': 'pyFM_zoomout',
    }
}

with open('Y:/GGbommer/scripts/CGI_Pipeline/_maya_export/fm_result.json', 'w') as f:
    json.dump(result, f)

print("\nSaved to _maya_export/fm_result.json")
