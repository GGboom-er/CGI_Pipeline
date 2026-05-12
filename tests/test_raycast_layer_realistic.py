# test_raycast_layer_realistic.py
# 模拟真实穿插场景：body + cloth 多层 mesh，body 膨胀后穿入 cloth
# 验证层分类在 Super Mesh 投射中的实际效果

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vendor'))

import numpy as np
import trimesh
from scipy.spatial import cKDTree

RAY_LAYER_OFFSET = 0.001

def _classify_layer_depth(points, normals, mesh_ray, face_source_ids, num_sources,
                          point_source_ids=None):
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    direction = np.array([0.4395064455, 0.617598629942, 0.652231566745])
    directions = np.tile(direction, (n, 1))
    index_tri, index_ray = mesh_ray.intersects_id(
        ray_origins=points, ray_directions=directions, multiple_hits=True)
    if len(index_ray) == 0:
        return np.zeros(n, dtype=np.int32)
    layer_depth = np.zeros(n, dtype=np.int32)
    hit_sources = face_source_ids[index_tri]
    for src_id in range(num_sources):
        src_mask = hit_sources == src_id
        if not np.any(src_mask):
            continue
        src_ray_ids = index_ray[src_mask]
        counts = np.bincount(src_ray_ids, minlength=n)
        inside = (counts % 2).astype(np.int32)
        if point_source_ids is not None:
            inside[point_source_ids == src_id] = 0
        layer_depth += inside
    return layer_depth


def make_sphere(radius, subdivisions=3):
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)


def run_scenario(name, body_r, cloth_r, target_r, target_is_body=True, subdivisions=3):
    """
    构造场景并测试层分类效果。
    body_r: body 半径（内层）
    cloth_r: cloth 半径（外层）
    target_r: 目标 mesh 半径（膨胀后的 body 或 cloth）
    target_is_body: True=目标应从 body 采样, False=应从 cloth 采样
    """
    body = make_sphere(body_r, subdivisions=subdivisions)
    cloth = make_sphere(cloth_r, subdivisions=subdivisions)
    target = make_sphere(target_r, subdivisions=subdivisions)

    num_body_faces = len(body.faces)
    combined_verts = np.vstack([body.vertices, cloth.vertices])
    combined_faces = np.vstack([body.faces, cloth.faces + len(body.vertices)])
    super_mesh = trimesh.Trimesh(vertices=combined_verts, faces=combined_faces, process=False)

    face_source_ids = np.zeros(len(combined_faces), dtype=np.int32)
    face_source_ids[num_body_faces:] = 1
    NUM_SOURCES = 2

    # 源面层分类
    face_centroids = super_mesh.triangles.mean(axis=1)
    face_norms = super_mesh.face_normals.copy()
    fn_lens = np.linalg.norm(face_norms, axis=1, keepdims=True)
    fn_lens[fn_lens == 0] = 1.0
    face_norms /= fn_lens
    source_layers = _classify_layer_depth(face_centroids, face_norms, super_mesh.ray,
                                           face_source_ids, NUM_SOURCES,
                                           point_source_ids=face_source_ids)

    # 为每层建 KDTree
    unique_layers = np.unique(source_layers)
    layer_trees = {}
    for layer in unique_layers:
        mask = source_layers == layer
        indices = np.where(mask)[0]
        layer_trees[int(layer)] = (cKDTree(face_centroids[mask]), indices)

    # 目标顶点
    new_verts = target.vertices.copy()
    new_normals = target.vertex_normals.copy()
    nn_lens = np.linalg.norm(new_normals, axis=1, keepdims=True)
    nn_lens[nn_lens == 0] = 1.0
    new_normals /= nn_lens

    # 无层分类
    _, _, face_ids_naive = super_mesh.nearest.on_surface(new_verts)
    expected_src = 0 if target_is_body else 1
    naive_correct = np.mean(face_source_ids[face_ids_naive] == expected_src)

    # 有层分类
    target_layers = _classify_layer_depth(new_verts, new_normals, super_mesh.ray,
                                           face_source_ids, NUM_SOURCES)
    face_ids_layered = face_ids_naive.copy()
    for v_idx in range(len(new_verts)):
        t_layer = int(target_layers[v_idx])
        if t_layer in layer_trees:
            tree_l, indices_l = layer_trees[t_layer]
            _, local_idx = tree_l.query(new_verts[v_idx])
            face_ids_layered[v_idx] = indices_l[local_idx]

    layered_correct = np.mean(face_source_ids[face_ids_layered] == expected_src)

    src_name = "body" if target_is_body else "cloth"
    penetration = "穿插" if (target_is_body and target_r > cloth_r * 0.95) else "未穿插"
    if not target_is_body and target_r < body_r * 1.05:
        penetration = "穿插"

    print(f"\n{'─'*60}")
    print(f"  场景: {name}")
    print(f"  body={body_r}, cloth={cloth_r}, target={target_r} ({penetration})")
    print(f"  期望从 {src_name} 采样")
    print(f"  无层分类正确率: {naive_correct:6.1%}")
    print(f"  有层分类正确率: {layered_correct:6.1%}")
    improvement = layered_correct - naive_correct
    if improvement > 0.01:
        print(f"  提升: +{improvement:.1%} ✓")
    elif improvement < -0.01:
        print(f"  退化: {improvement:.1%} ✗")
    else:
        print(f"  持平 ✓")

    return naive_correct, layered_correct


print("=" * 60)
print("Ray-Cast 层分类效果测试 — 多场景对比")
print("=" * 60)

results = []

# 场景 1: body 轻微膨胀（不穿插）
results.append(("body 轻微膨胀 (不穿插)",
    *run_scenario("body 轻微膨胀", body_r=1.0, cloth_r=1.3, target_r=1.05, target_is_body=True)))

# 场景 2: body 膨胀接近 cloth 内壁（不穿插但很近）
results.append(("body 接近 cloth 内壁",
    *run_scenario("body 接近 cloth 内壁", body_r=1.0, cloth_r=1.3, target_r=1.25, target_is_body=True)))

# 场景 3: body 膨胀穿入 cloth（穿插核心场景）
results.append(("body 穿入 cloth 内壁",
    *run_scenario("body 穿入 cloth 内壁", body_r=1.0, cloth_r=1.3, target_r=1.28, target_is_body=True)))

# 场景 4: body 大幅膨胀穿出 cloth（完全穿出）
results.append(("body 穿出 cloth",
    *run_scenario("body 穿出 cloth", body_r=1.0, cloth_r=1.3, target_r=1.5, target_is_body=True)))

# 场景 5: cloth 正常变形（不穿插）
results.append(("cloth 正常变形",
    *run_scenario("cloth 正常变形", body_r=1.0, cloth_r=1.3, target_r=1.4, target_is_body=False)))

# 场景 6: cloth 收缩接近 body（穿插）
results.append(("cloth 收缩接近 body",
    *run_scenario("cloth 收缩接近 body", body_r=1.0, cloth_r=1.3, target_r=1.05, target_is_body=False)))

# 场景 7: 三层场景 — body + undershirt + jacket
print(f"\n\n{'='*60}")
print("三层场景: body(0.9) + undershirt(1.0) + jacket(1.2)")
print("=" * 60)

body3 = make_sphere(0.9, subdivisions=3)
undershirt = make_sphere(1.0, subdivisions=3)
jacket = make_sphere(1.2, subdivisions=3)

n_body3 = len(body3.faces)
n_under = len(undershirt.faces)
n_jacket = len(jacket.faces)

combined_v3 = np.vstack([body3.vertices, undershirt.vertices, jacket.vertices])
combined_f3 = np.vstack([
    body3.faces,
    undershirt.faces + len(body3.vertices),
    jacket.faces + len(body3.vertices) + len(undershirt.vertices)
])
super3 = trimesh.Trimesh(vertices=combined_v3, faces=combined_f3, process=False)

face_src3 = np.zeros(len(combined_f3), dtype=np.int32)
face_src3[n_body3:n_body3+n_under] = 1
face_src3[n_body3+n_under:] = 2
NUM_SRC3 = 3

fc3 = super3.triangles.mean(axis=1)
fn3 = super3.face_normals.copy()
fn3_lens = np.linalg.norm(fn3, axis=1, keepdims=True)
fn3_lens[fn3_lens == 0] = 1.0
fn3 /= fn3_lens
src_layers3 = _classify_layer_depth(fc3, fn3, super3.ray, face_src3, NUM_SRC3,
                                     point_source_ids=face_src3)

print(f"  body 层深度: {np.unique(src_layers3[:n_body3], return_counts=True)}")
print(f"  undershirt 层深度: {np.unique(src_layers3[n_body3:n_body3+n_under], return_counts=True)}")
print(f"  jacket 层深度: {np.unique(src_layers3[n_body3+n_under:], return_counts=True)}")

layer_trees3 = {}
for layer in np.unique(src_layers3):
    mask = src_layers3 == layer
    indices = np.where(mask)[0]
    layer_trees3[int(layer)] = (cKDTree(fc3[mask]), indices)

# 测试: undershirt 膨胀到 1.1（穿入 jacket 但不穿出）
target3 = make_sphere(1.1, subdivisions=3)
tv3 = target3.vertices.copy()
tn3 = target3.vertex_normals.copy()
tn3_lens = np.linalg.norm(tn3, axis=1, keepdims=True)
tn3_lens[tn3_lens == 0] = 1.0
tn3 /= tn3_lens

_, _, fids_naive3 = super3.nearest.on_surface(tv3)
naive3_under = np.mean(face_src3[fids_naive3] == 1)

tl3 = _classify_layer_depth(tv3, tn3, super3.ray, face_src3, NUM_SRC3)
fids_layered3 = fids_naive3.copy()
for v_idx in range(len(tv3)):
    t_layer = int(tl3[v_idx])
    if t_layer in layer_trees3:
        tree_l, indices_l = layer_trees3[t_layer]
        _, local_idx = tree_l.query(tv3[v_idx])
        fids_layered3[v_idx] = indices_l[local_idx]

layered3_under = np.mean(face_src3[fids_layered3] == 1)

print(f"\n  undershirt 膨胀到 r=1.1（穿入 jacket 内壁）:")
print(f"  无层分类从 undershirt 采样: {naive3_under:.1%}")
print(f"  有层分类从 undershirt 采样: {layered3_under:.1%}")

# 汇总
print(f"\n\n{'='*60}")
print("汇总")
print("=" * 60)
print(f"{'场景':<25} {'无层分类':>10} {'有层分类':>10} {'变化':>8}")
print("-" * 60)
for name, naive, layered in results:
    diff = layered - naive
    sign = "+" if diff > 0 else ""
    print(f"{name:<25} {naive:>9.1%} {layered:>9.1%} {sign}{diff:>6.1%}")
print("-" * 60)
print(f"{'三层 undershirt':<25} {naive3_under:>9.1%} {layered3_under:>9.1%} {'+' if layered3_under > naive3_under else ''}{layered3_under-naive3_under:>6.1%}")
