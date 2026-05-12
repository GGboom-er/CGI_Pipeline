# test_sync_vectorize.py
# 纯 numpy 单元测试：验证向量化权重插值和 BS delta 投射的正确性
# 不依赖 Maya，纯数学验证

import numpy as np
import sys

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✓ {name}")
        PASS += 1
    else:
        print(f"  ✗ {name} — {detail}")
        FAIL += 1

print("=" * 60)
print("Test 1: 向量化权重插值 vs 逐顶点循环")
print("=" * 60)

# 模拟数据
np.random.seed(42)
num_new_verts = 5000
num_src_joints = 30
num_src_verts = 3000

# 随机权重矩阵 (已归一化)
src_weights = np.random.rand(num_src_verts, num_src_joints)
src_weights /= src_weights.sum(axis=1, keepdims=True)

# 模拟三角形面顶点索引 (已减去 offset)
fvi = np.random.randint(0, num_src_verts, size=(num_new_verts, 3))

# 重心坐标
raw_bary = np.random.rand(num_new_verts, 3)
bary = raw_bary / raw_bary.sum(axis=1, keepdims=True)

# 模拟距离和有效掩码
dists = np.random.rand(num_new_verts) * 8  # 0-8cm, 部分会超过 5cm
valid_mask = dists <= 5.0

# --- 旧方法: 逐顶点循环 ---
old_weights = np.zeros((num_new_verts, num_src_joints), dtype=np.float64)
for i in range(num_new_verts):
    if dists[i] > 5.0:
        continue
    w0 = src_weights[fvi[i, 0]]
    w1 = src_weights[fvi[i, 1]]
    w2 = src_weights[fvi[i, 2]]
    u, v, w = bary[i]
    interp = u * w0 + v * w1 + w * w2
    old_weights[i] = interp

# --- 新方法: 向量化 ---
new_weights = np.zeros((num_new_verts, num_src_joints), dtype=np.float64)
idx = np.where(valid_mask)[0]
if len(idx) > 0:
    lv0 = fvi[idx, 0]
    lv1 = fvi[idx, 1]
    lv2 = fvi[idx, 2]
    w0 = src_weights[lv0]
    w1 = src_weights[lv1]
    w2 = src_weights[lv2]
    b = bary[idx]
    interp = b[:, 0:1] * w0 + b[:, 1:2] * w1 + b[:, 2:3] * w2
    new_weights[idx] = interp

diff = np.abs(old_weights - new_weights)
max_diff = diff.max()
check("数值一致性", max_diff < 1e-12, f"max diff = {max_diff}")
check("无效顶点为零", np.all(new_weights[~valid_mask] == 0))
check("有效顶点非零", np.any(new_weights[valid_mask] > 0))

# 性能对比
import time
t0 = time.time()
for _ in range(3):
    old_w = np.zeros((num_new_verts, num_src_joints), dtype=np.float64)
    for i in range(num_new_verts):
        if dists[i] > 5.0:
            continue
        w0 = src_weights[fvi[i, 0]]
        w1 = src_weights[fvi[i, 1]]
        w2 = src_weights[fvi[i, 2]]
        u, v, w = bary[i]
        old_w[i] = u * w0 + v * w1 + w * w2
t_old = (time.time() - t0) / 3

t0 = time.time()
for _ in range(3):
    nw = np.zeros((num_new_verts, num_src_joints), dtype=np.float64)
    idx = np.where(valid_mask)[0]
    lv0 = fvi[idx, 0]; lv1 = fvi[idx, 1]; lv2 = fvi[idx, 2]
    w0 = src_weights[lv0]; w1 = src_weights[lv1]; w2 = src_weights[lv2]
    b = bary[idx]
    nw[idx] = b[:, 0:1] * w0 + b[:, 1:2] * w1 + b[:, 2:3] * w2
t_new = (time.time() - t0) / 3

speedup = t_old / t_new if t_new > 0 else float('inf')
print(f"  ⏱  旧方法: {t_old*1000:.1f}ms | 新方法: {t_new*1000:.1f}ms | 加速比: {speedup:.0f}x")
check("加速比 > 10x", speedup > 10, f"仅 {speedup:.1f}x")

print()
print("=" * 60)
print("Test 2: 向量化 BS Delta 投射 vs 逐顶点循环")
print("=" * 60)

num_old_delta = num_src_verts
old_delta = np.random.rand(num_old_delta, 3) * 0.1  # 小位移 delta

# --- 旧方法 ---
old_result = np.zeros((num_new_verts, 3), dtype=np.float64)
for vi in range(num_new_verts):
    if dists[vi] > 5.0:
        continue
    lv0 = fvi[vi, 0]
    lv1 = fvi[vi, 1]
    lv2 = fvi[vi, 2]
    if max(lv0, lv1, lv2) >= num_old_delta:
        continue
    bu, bv, bw = bary[vi]
    old_result[vi] = bu * old_delta[lv0] + bv * old_delta[lv1] + bw * old_delta[lv2]

# --- 新方法 ---
new_result = np.zeros((num_new_verts, 3), dtype=np.float64)
idx = np.where(valid_mask)[0]
if len(idx) > 0:
    lv0 = fvi[idx, 0]; lv1 = fvi[idx, 1]; lv2 = fvi[idx, 2]
    bounds_ok = (lv0 >= 0) & (lv1 >= 0) & (lv2 >= 0) & \
                (lv0 < num_old_delta) & (lv1 < num_old_delta) & (lv2 < num_old_delta)
    idx_ok = idx[bounds_ok]
    if len(idx_ok) > 0:
        d0 = old_delta[lv0[bounds_ok]]
        d1 = old_delta[lv1[bounds_ok]]
        d2 = old_delta[lv2[bounds_ok]]
        b = bary[idx_ok]
        new_result[idx_ok] = b[:, 0:1] * d0 + b[:, 1:2] * d1 + b[:, 2:3] * d2

diff2 = np.abs(old_result - new_result)
max_diff2 = diff2.max()
check("Delta 数值一致性", max_diff2 < 1e-12, f"max diff = {max_diff2}")

# 稀疏收集验证
norms_old = []
for vi in range(num_new_verts):
    d = old_result[vi]
    l = (d[0]**2 + d[1]**2 + d[2]**2) ** 0.5
    if l > 0.00001:
        norms_old.append(vi)

norms_new = np.linalg.norm(new_result, axis=1)
sparse_new = np.where(norms_new > 0.00001)[0]
check("稀疏 ID 一致", set(norms_old) == set(sparse_new.tolist()), f"old={len(norms_old)} new={len(sparse_new)}")

print()
print("=" * 60)
print("Test 3: np.add.at 散射累加正确性")
print("=" * 60)

# 模拟多源 rig 的权重合并
num_union = 50
union_weights_old = np.zeros((100, num_union), dtype=np.float64)
union_weights_new = np.zeros((100, num_union), dtype=np.float64)

src_to_union = np.array([3, 7, 12, 25, 40], dtype=np.int32)
interp = np.random.rand(60, 5)  # 60 个有效顶点, 5 个源骨骼
idx = np.arange(20, 80)  # 有效范围

# 旧方法
for i_idx, vi in enumerate(idx):
    for src_j in range(5):
        union_weights_old[vi, src_to_union[src_j]] += interp[i_idx, src_j]

# 新方法
for src_j in range(5):
    np.add.at(union_weights_new[:, src_to_union[src_j]], idx, interp[:, src_j])

diff3 = np.abs(union_weights_old - union_weights_new)
check("散射累加一致", diff3.max() < 1e-12, f"max diff = {diff3.max()}")

print()
print("=" * 60)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 60)

sys.exit(1 if FAIL > 0 else 0)
