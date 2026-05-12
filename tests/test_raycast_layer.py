# test_raycast_layer.py
# 验证 Ray-Cast 层分类在穿插场景下的正确性
# 不依赖 Maya，纯 trimesh + numpy

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vendor'))

import numpy as np
import trimesh
from scipy.spatial import cKDTree

RAY_LAYER_OFFSET = 0.001

def _classify_layer_depth(points, normals, mesh_ray, face_source_ids, num_sources,
                          point_source_ids=None):
    """
    通过射线穿透计数判断每个点的层深度。
    用固定方向射线 + 奇偶规则判断点在多少个"其他"源 mesh 内部。
    层深度 = 点被多少个其他源 mesh 包裹（排除自身 mesh）。

    point_source_ids: 每个点属于哪个源 mesh（用于排除自身）。
                      如果为 None，则不排除任何 mesh。
    """
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=np.int32)

    direction = np.array([0.4395064455, 0.617598629942, 0.652231566745])
    directions = np.tile(direction, (n, 1))

    index_tri, index_ray = mesh_ray.intersects_id(
        ray_origins=points,
        ray_directions=directions,
        multiple_hits=True,
    )
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
            # 排除自身 mesh 的贡献
            self_mask = (point_source_ids == src_id)
            inside[self_mask] = 0

        layer_depth += inside

    return layer_depth


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


def make_sphere(radius, subdivisions=3):
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)


# ═══════════════════════════════════════════
# 构造场景
# ═══════════════════════════════════════════
body = make_sphere(1.0, subdivisions=3)
cloth = make_sphere(1.3, subdivisions=3)
fat_body = make_sphere(1.5, subdivisions=3)

num_body_faces = len(body.faces)
num_cloth_faces = len(cloth.faces)

# Super Mesh = body + cloth
combined_verts = np.vstack([body.vertices, cloth.vertices])
combined_faces = np.vstack([body.faces, cloth.faces + len(body.vertices)])
super_mesh = trimesh.Trimesh(vertices=combined_verts, faces=combined_faces, process=False)

# face_source_ids: 0=body, 1=cloth
face_source_ids = np.zeros(len(combined_faces), dtype=np.int32)
face_source_ids[num_body_faces:] = 1
NUM_SOURCES = 2


# ═══════════════════════════════════════════
# Test 1: 基本层分类 — 双层球体
# ═══════════════════════════════════════════
print("=" * 60)
print("Test 1: 双层球体的层深度分类")
print("=" * 60)

face_centroids = super_mesh.triangles.mean(axis=1)
face_norms = super_mesh.face_normals.copy()
fn_lens = np.linalg.norm(face_norms, axis=1, keepdims=True)
fn_lens[fn_lens == 0] = 1.0
face_norms /= fn_lens

source_layers = _classify_layer_depth(face_centroids, face_norms, super_mesh.ray,
                                       face_source_ids, NUM_SOURCES,
                                       point_source_ids=face_source_ids)

body_layers = source_layers[:num_body_faces]
cloth_layers = source_layers[num_body_faces:]

# body 面在 cloth 内部 → layer_depth 应该 = 1（被 cloth 包裹）
# cloth 面不在 body 内部 → layer_depth 应该 = 0
body_inner_ratio = np.mean(body_layers > 0)
cloth_outer_ratio = np.mean(cloth_layers == 0)

print(f"  body 面层深度分布: {np.bincount(body_layers)}")
print(f"  cloth 面层深度分布: {np.bincount(cloth_layers)}")
print(f"  body 面被识别为内层的比例: {body_inner_ratio:.1%}")
print(f"  cloth 面被识别为外层的比例: {cloth_outer_ratio:.1%}")

check("body 面大部分被识别为内层 (>80%)", body_inner_ratio > 0.8,
      f"实际: {body_inner_ratio:.1%}")
check("cloth 面大部分被识别为外层 (>80%)", cloth_outer_ratio > 0.8,
      f"实际: {cloth_outer_ratio:.1%}")


# ═══════════════════════════════════════════
# Test 2: 穿插场景 — 无层分类 vs 有层分类
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("Test 2: 穿插场景下的投射对比")
print("=" * 60)
print("  场景: body(r=1.0) + cloth(r=1.3) → fat_body(r=1.5) 穿过 cloth")

# fat_body 的顶点在 cloth 外面（r=1.5 > 1.3），空间上离 cloth 外表面最近
# 但 fat_body 是 body 的膨胀版，应该从 body 采样

new_verts = fat_body.vertices.copy()
new_normals = fat_body.vertex_normals.copy()
nn_lens = np.linalg.norm(new_normals, axis=1, keepdims=True)
nn_lens[nn_lens == 0] = 1.0
new_normals /= nn_lens

# --- 无层分类：直接 closest point ---
_, _, face_ids_naive = super_mesh.nearest.on_surface(new_verts)
naive_source = face_source_ids[face_ids_naive]
naive_body_ratio = np.mean(naive_source == 0)
print(f"  无层分类: fat_body 从 body 采样: {naive_body_ratio:.1%}")
print(f"  无层分类: fat_body 从 cloth 采样: {1-naive_body_ratio:.1%}")

# --- 有层分类 ---
# fat_body 在 cloth 外面，所以它不在任何 mesh 内部 → layer = 0
# cloth 面也是 layer = 0（最外层）
# body 面是 layer = 1（在 cloth 内部）
# 所以 fat_body(layer=0) 应该匹配 cloth(layer=0)...

# 等等，这里有个概念问题：
# fat_body 是 body 膨胀后穿过 cloth 的结果。
# 从纯几何角度看，fat_body 的顶点确实在 cloth 外面。
# 如果我们只看几何，fat_body 确实应该从 cloth 采样（因为它现在在外层）。
#
# 但在实际 pipeline 中，fat_body 和 body 是同一个 mesh 的不同版本。
# 这个对应关系是通过 pairing_groups 中的 mesh 配对来确定的。
#
# 所以 Ray-Cast 层分类解决的是另一个问题：
# 当 Super Mesh 中有多层 mesh，新顶点应该从哪一层采样。
# 如果新 mesh 本身就是外层的（穿出来了），它确实应该从外层采样。
#
# 真正的穿插问题是：body 膨胀后，body 的某些顶点穿过了 cloth，
# 这些顶点在空间上离 cloth 内表面很近，可能错误地从 cloth 采样。
# 但这些顶点仍然在 cloth 内部（只是贴近内壁），layer_depth 仍然 > 0。

# 重新构造正确的测试场景：
# body 膨胀到 r=1.25（穿入 cloth 但没穿出来）
print("\n  --- 修正场景: body 膨胀到 r=1.25（穿入 cloth 内壁但未穿出）---")
fat_body_inside = make_sphere(1.25, subdivisions=3)
new_verts2 = fat_body_inside.vertices.copy()
new_normals2 = fat_body_inside.vertex_normals.copy()
nn_lens2 = np.linalg.norm(new_normals2, axis=1, keepdims=True)
nn_lens2[nn_lens2 == 0] = 1.0
new_normals2 /= nn_lens2

# 无层分类
_, _, face_ids_naive2 = super_mesh.nearest.on_surface(new_verts2)
naive_source2 = face_source_ids[face_ids_naive2]
naive_body_ratio2 = np.mean(naive_source2 == 0)
print(f"  无层分类: fat_body(1.25) 从 body 采样: {naive_body_ratio2:.1%}")

# 有层分类
target_layers2 = _classify_layer_depth(new_verts2, new_normals2, super_mesh.ray,
                                        face_source_ids, NUM_SOURCES)
print(f"  fat_body(1.25) 层深度分布: {np.bincount(target_layers2)}")

# fat_body(1.25) 在 cloth(1.3) 内部 → layer = 1
# body 面也是 layer = 1 → 应该从 body 采样
unique_layers = np.unique(source_layers)
layer_trees = {}
for layer in unique_layers:
    mask = source_layers == layer
    indices = np.where(mask)[0]
    layer_trees[int(layer)] = (cKDTree(face_centroids[mask]), indices)

face_ids_layered2 = face_ids_naive2.copy()
for v_idx in range(len(new_verts2)):
    t_layer = int(target_layers2[v_idx])
    if t_layer in layer_trees:
        tree_l, indices_l = layer_trees[t_layer]
        _, local_idx = tree_l.query(new_verts2[v_idx])
        face_ids_layered2[v_idx] = indices_l[local_idx]

layered_source2 = face_source_ids[face_ids_layered2]
layered_body_ratio2 = np.mean(layered_source2 == 0)
print(f"  有层分类: fat_body(1.25) 从 body 采样: {layered_body_ratio2:.1%}")

check("穿插场景: 层分类提升 body 采样率",
      layered_body_ratio2 > naive_body_ratio2 + 0.1,
      f"naive={naive_body_ratio2:.1%}, layered={layered_body_ratio2:.1%}")


# ═══════════════════════════════════════════
# Test 3: 非穿插场景不退化
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("Test 3: 非穿插场景（body 微调 r=1.05）不退化")
print("=" * 60)

body_tweaked = make_sphere(1.05, subdivisions=3)
new_verts_tw = body_tweaked.vertices.copy()
new_normals_tw = body_tweaked.vertex_normals.copy()
nn_lens_tw = np.linalg.norm(new_normals_tw, axis=1, keepdims=True)
nn_lens_tw[nn_lens_tw == 0] = 1.0
new_normals_tw /= nn_lens_tw

# 无层分类
_, _, face_ids_tw_naive = super_mesh.nearest.on_surface(new_verts_tw)
tw_naive_correct = np.mean(face_source_ids[face_ids_tw_naive] == 0)

# 有层分类
target_layers_tw = _classify_layer_depth(new_verts_tw, new_normals_tw, super_mesh.ray,
                                          face_source_ids, NUM_SOURCES)
face_ids_tw_layered = face_ids_tw_naive.copy()
for v_idx in range(len(new_verts_tw)):
    t_layer = int(target_layers_tw[v_idx])
    if t_layer in layer_trees:
        tree_l, indices_l = layer_trees[t_layer]
        _, local_idx = tree_l.query(new_verts_tw[v_idx])
        face_ids_tw_layered[v_idx] = indices_l[local_idx]

tw_layered_correct = np.mean(face_source_ids[face_ids_tw_layered] == 0)

print(f"  无层分类: tweaked body 从 body 采样: {tw_naive_correct:.1%}")
print(f"  有层分类: tweaked body 从 body 采样: {tw_layered_correct:.1%}")

check("非穿插场景层分类不退化",
      tw_layered_correct >= tw_naive_correct - 0.05,
      f"naive={tw_naive_correct:.1%}, layered={tw_layered_correct:.1%}")


# ═══════════════════════════════════════════
# Test 4: cloth 变形应从 cloth 采样
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("Test 4: cloth 外层变形(r=1.4)应从 cloth 采样")
print("=" * 60)

cloth_tweaked = make_sphere(1.4, subdivisions=3)
new_verts_cl = cloth_tweaked.vertices.copy()
new_normals_cl = cloth_tweaked.vertex_normals.copy()
nn_lens_cl = np.linalg.norm(new_normals_cl, axis=1, keepdims=True)
nn_lens_cl[nn_lens_cl == 0] = 1.0
new_normals_cl /= nn_lens_cl

# 无层分类
_, _, face_ids_cl_naive = super_mesh.nearest.on_surface(new_verts_cl)
cl_naive_correct = np.mean(face_source_ids[face_ids_cl_naive] == 1)

# 有层分类
target_layers_cl = _classify_layer_depth(new_verts_cl, new_normals_cl, super_mesh.ray,
                                          face_source_ids, NUM_SOURCES)
face_ids_cl_layered = face_ids_cl_naive.copy()
for v_idx in range(len(new_verts_cl)):
    t_layer = int(target_layers_cl[v_idx])
    if t_layer in layer_trees:
        tree_l, indices_l = layer_trees[t_layer]
        _, local_idx = tree_l.query(new_verts_cl[v_idx])
        face_ids_cl_layered[v_idx] = indices_l[local_idx]

cl_layered_correct = np.mean(face_source_ids[face_ids_cl_layered] == 1)

print(f"  无层分类: cloth 变形从 cloth 采样: {cl_naive_correct:.1%}")
print(f"  有层分类: cloth 变形从 cloth 采样: {cl_layered_correct:.1%}")

check("cloth 变形正确从 cloth 采样 (>90%)",
      cl_layered_correct > 0.9,
      f"实际: {cl_layered_correct:.1%}")


# ═══════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
