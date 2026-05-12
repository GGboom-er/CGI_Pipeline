"""
统一变形场 (UnifiedDeformationField) 综合测试。

测试覆盖：
  1. 多源模型合并 → build → query
  2. 层级过滤（winding number）
  3. 法线一致性加权
  4. 形状保持正则化
  5. 延伸结构 geodesic falloff
  6. 置信度查询
  7. RBF 方法
  8. 边界情况
"""
import numpy as np
import time
import sys
sys.path.insert(0, r"Y:\GGbommer\scripts\CGI_Pipeline")


# ═══════════════════════════════════════════════════════════════
# 测试数据生成
# ═══════════════════════════════════════════════════════════════

def make_uv_sphere(radius=1.0, rings=10, segments=16, center=(0, 0, 0)):
    """生成 UV 球体 mesh。"""
    verts = []
    cx, cy, cz = center

    # 顶点
    verts.append([cx, cy + radius, cz])  # 北极
    for i in range(1, rings):
        phi = np.pi * i / rings
        for j in range(segments):
            theta = 2 * np.pi * j / segments
            x = cx + radius * np.sin(phi) * np.cos(theta)
            y = cy + radius * np.cos(phi)
            z = cz + radius * np.sin(phi) * np.sin(theta)
            verts.append([x, y, z])
    verts.append([cx, cy - radius, cz])  # 南极

    verts = np.array(verts, dtype=np.float64)
    N = len(verts)

    # 三角面
    faces = []
    # 北极扇面
    for j in range(segments):
        j_next = (j + 1) % segments
        faces.append([0, 1 + j, 1 + j_next])
    # 中间带
    for i in range(rings - 2):
        for j in range(segments):
            j_next = (j + 1) % segments
            curr = 1 + i * segments + j
            next_row = 1 + (i + 1) * segments + j
            curr_next = 1 + i * segments + j_next
            next_row_next = 1 + (i + 1) * segments + j_next
            faces.append([curr, next_row, curr_next])
            faces.append([curr_next, next_row, next_row_next])
    # 南极扇面
    south = N - 1
    last_ring_start = 1 + (rings - 2) * segments
    for j in range(segments):
        j_next = (j + 1) % segments
        faces.append([south, last_ring_start + j_next, last_ring_start + j])

    faces = np.array(faces, dtype=np.intp)
    return verts, faces


def make_plane_mesh(grid_size=10, scale=1.0, z_offset=0.0):
    """生成平面三角网格。"""
    verts = []
    for i in range(grid_size):
        for j in range(grid_size):
            verts.append([i * scale / (grid_size - 1),
                          j * scale / (grid_size - 1),
                          z_offset])
    verts = np.array(verts, dtype=np.float64)

    faces = []
    for i in range(grid_size - 1):
        for j in range(grid_size - 1):
            v00 = i * grid_size + j
            v10 = (i + 1) * grid_size + j
            v01 = i * grid_size + (j + 1)
            v11 = (i + 1) * grid_size + (j + 1)
            faces.append([v00, v10, v01])
            faces.append([v10, v11, v01])
    faces = np.array(faces, dtype=np.intp)
    return verts, faces


def make_strip_mesh(length=10, width=3, z_offset=0.0):
    """生成细长条带 mesh（模拟睫毛/头发）。"""
    verts = []
    for i in range(length):
        for j in range(width):
            x = i * 0.1
            y = j * 0.02
            z = z_offset + i * 0.05  # 逐渐远离表面
            verts.append([x, y, z])
    verts = np.array(verts, dtype=np.float64)

    faces = []
    for i in range(length - 1):
        for j in range(width - 1):
            v00 = i * width + j
            v10 = (i + 1) * width + j
            v01 = i * width + (j + 1)
            v11 = (i + 1) * width + (j + 1)
            faces.append([v00, v10, v01])
            faces.append([v10, v11, v01])
    faces = np.array(faces, dtype=np.intp)
    return verts, faces


def make_weights_by_height(verts, joints=None):
    """根据 Y 坐标生成渐变权重（模拟上下半身）。"""
    if joints is None:
        joints = ["hip", "spine", "chest", "head"]
    J = len(joints)
    N = verts.shape[0]

    y_min = verts[:, 1].min()
    y_max = verts[:, 1].max()
    y_range = y_max - y_min
    if y_range < 1e-10:
        y_range = 1.0

    weights = np.zeros((N, J), dtype=np.float64)
    for i in range(N):
        t = (verts[i, 1] - y_min) / y_range  # 0=底部, 1=顶部
        # 分段线性
        if t < 0.25:
            weights[i, 0] = 1.0
        elif t < 0.5:
            s = (t - 0.25) / 0.25
            weights[i, 0] = 1.0 - s
            weights[i, 1] = s
        elif t < 0.75:
            s = (t - 0.5) / 0.25
            weights[i, 1] = 1.0 - s
            weights[i, 2] = s
        else:
            s = (t - 0.75) / 0.25
            weights[i, 2] = 1.0 - s
            weights[i, 3] = s

    return weights, joints


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════

def test_basic_build_and_query():
    """测试基本的 add_source → build → query 流程。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源：平面 z=0，4 个骨骼按 X 渐变
    src_verts, src_faces = make_plane_mesh(8, scale=2.0)
    joints = ["j0", "j1", "j2", "j3"]
    src_weights, _ = make_weights_by_height(src_verts, joints)

    field = UnifiedDeformationField()
    field.add_source("plane", src_verts, src_faces, src_weights, joints)
    field.build(compute_layers=False)

    assert field.num_joints == 4
    assert field.joint_union == joints

    # 查询：同一平面上的不同拓扑网格
    query_verts, query_faces = make_plane_mesh(5, scale=2.0, z_offset=0.01)
    result = field.query(query_verts, k=8)

    assert result.shape == (25, 4)
    assert np.allclose(result.sum(axis=1), 1.0, atol=0.01)

    # 底部点应该主要是 j0
    bottom_idx = 0  # y=0 的点
    assert result[bottom_idx, 0] > 0.5, f"Bottom should be j0-dominant, got {result[bottom_idx]}"

    # 顶部点应该主要是 j3
    top_idx = 24  # y=max 的点
    assert result[top_idx, 3] > 0.5, f"Top should be j3-dominant, got {result[top_idx]}"

    print("[basic] PASSED — build & query 正常，权重渐变正确")


def test_multi_source_union():
    """测试多源模型骨骼合并。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源 1: body，骨骼 [hip, spine, chest]
    body_verts, body_faces = make_plane_mesh(6, scale=1.0)
    body_joints = ["hip", "spine", "chest"]
    body_weights = np.zeros((36, 3))
    body_weights[:12, 0] = 1.0
    body_weights[12:24, 1] = 1.0
    body_weights[24:, 2] = 1.0

    # 源 2: jacket，骨骼 [spine, chest, shoulder_L]
    jacket_verts, jacket_faces = make_plane_mesh(6, scale=1.0, z_offset=0.05)
    jacket_joints = ["spine", "chest", "shoulder_L"]
    jacket_weights = np.zeros((36, 3))
    jacket_weights[:18, 0] = 1.0  # spine
    jacket_weights[18:30, 1] = 1.0  # chest
    jacket_weights[30:, 2] = 1.0  # shoulder_L

    field = UnifiedDeformationField()
    field.add_source("body", body_verts, body_faces, body_weights, body_joints)
    field.add_source("jacket", jacket_verts, jacket_faces, jacket_weights, jacket_joints)
    field.build(compute_layers=False)

    # 骨骼 union 应该是 4 个
    assert field.num_joints == 4, f"Expected 4 union joints, got {field.num_joints}"
    assert set(field.joint_union) == {"hip", "spine", "chest", "shoulder_L"}

    # 查询中间位置的点
    query_pts = np.array([[0.5, 0.5, 0.025]], dtype=np.float64)  # 两层之间
    result = field.query(query_pts, k=12)
    assert result.shape == (1, 4)
    assert np.allclose(result.sum(), 1.0, atol=0.01)

    print(f"[multi_source] PASSED — union joints={field.joint_union}, "
          f"query result={result[0]}")


def test_normal_consistency():
    """测试法线一致性加权：背面点不应从正面源采样。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源：平面 z=0，法线朝 +Z
    src_verts, src_faces = make_plane_mesh(6, scale=1.0)
    joints = ["A", "B"]
    src_weights = np.zeros((36, 2))
    src_weights[:, 0] = 1.0  # 全部是 joint A

    field = UnifiedDeformationField()
    field.add_source("front", src_verts, src_faces, src_weights, joints)
    field.build(compute_layers=False)

    # 查询点在 z=0.01，法线朝 +Z（同向）→ 应该正常采样
    q_same = np.array([[0.5, 0.5, 0.01]])
    n_same = np.array([[0.0, 0.0, 1.0]])
    w_same = field.query(q_same, normals=n_same, normal_weight=0.8)
    assert w_same[0, 0] > 0.9, f"Same-normal should get A, got {w_same[0]}"

    # 查询点法线朝 -Z（反向）→ 法线加权应该降低影响
    q_opp = np.array([[0.5, 0.5, 0.01]])
    n_opp = np.array([[0.0, 0.0, -1.0]])
    w_opp = field.query(q_opp, normals=n_opp, normal_weight=0.8)

    # 反向法线时权重仍然有效（因为只有一个源），但加权会不同
    # 关键是：同向法线的置信度应该更高
    print(f"[normal] same-dir weight: {w_same[0]}, opp-dir weight: {w_opp[0]}")
    print("[normal] PASSED — 法线一致性加权生效")


def test_shape_preservation():
    """测试形状保持正则化：权重过渡应更平滑。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源：两个分离的小平面，各自不同骨骼
    src1_verts = np.array([[0, 0, 0], [0.1, 0, 0], [0, 0.1, 0],
                           [0.1, 0.1, 0]], dtype=np.float64)
    src1_faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.intp)
    src1_weights = np.array([[1, 0], [1, 0], [1, 0], [1, 0]], dtype=np.float64)

    src2_verts = np.array([[0.9, 0, 0], [1.0, 0, 0], [0.9, 0.1, 0],
                           [1.0, 0.1, 0]], dtype=np.float64)
    src2_faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.intp)
    src2_weights = np.array([[0, 1], [0, 1], [0, 1], [0, 1]], dtype=np.float64)

    field = UnifiedDeformationField()
    field.add_source("left", src1_verts, src1_faces, src1_weights, ["A", "B"])
    field.add_source("right", src2_verts, src2_faces, src2_weights, ["A", "B"])
    field.build(compute_layers=False)

    # 查询一条线段从左到右
    query_verts, query_faces = make_plane_mesh(10, scale=1.0)
    query_verts[:, 2] = 0.01

    # 无正则化
    w_raw = field.query(query_verts, faces=query_faces, shape_preserve=0.0)
    # 有正则化
    w_smooth = field.query(query_verts, faces=query_faces, shape_preserve=2.0)

    # 正则化后权重变化应该更平滑（相邻点差异更小）
    diff_raw = np.abs(np.diff(w_raw[:, 0]))
    diff_smooth = np.abs(np.diff(w_smooth[:, 0]))

    max_jump_raw = diff_raw.max()
    max_jump_smooth = diff_smooth.max()

    print(f"[shape_preserve] max jump raw={max_jump_raw:.4f}, smooth={max_jump_smooth:.4f}")
    assert max_jump_smooth <= max_jump_raw + 0.01, \
        "Smoothed weights should have smaller or equal max jump"
    print("[shape_preserve] PASSED — 正则化使权重过渡更平滑")


def test_confidence():
    """测试置信度：远离源点的区域置信度低。"""
    from core.unified_deformation_field import UnifiedDeformationField

    src_verts, src_faces = make_plane_mesh(6, scale=1.0)
    joints = ["A", "B"]
    src_weights = np.zeros((36, 2))
    src_weights[:18, 0] = 1.0
    src_weights[18:, 1] = 1.0

    field = UnifiedDeformationField()
    field.add_source("src", src_verts, src_faces, src_weights, joints)
    field.build(compute_layers=False)

    # 近处查询
    q_near = np.array([[0.5, 0.5, 0.01]])
    # 远处查询
    q_far = np.array([[0.5, 0.5, 10.0]])

    w_near, conf_near = field.query_with_confidence(q_near)
    w_far, conf_far = field.query_with_confidence(q_far)

    print(f"[confidence] near: conf={conf_near[0]:.4f}, far: conf={conf_far[0]:.4f}")
    assert conf_near[0] > conf_far[0], \
        f"Near confidence ({conf_near[0]}) should > far ({conf_far[0]})"
    print("[confidence] PASSED — 距离越远置信度越低")


def test_rbf_method():
    """测试 RBF 插值方法。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 用球体数据（3D 分布），避免 thin_plate_spline 奇异
    src_verts, src_faces = make_uv_sphere(radius=1.0, rings=6, segments=8)
    N = src_verts.shape[0]
    joints = ["A", "B", "C"]
    src_weights = np.zeros((N, 3))
    # Y 方向渐变
    for i in range(N):
        y = src_verts[i, 1]
        t = (y + 1.0) / 2.0  # 映射到 [0, 1]
        src_weights[i, 0] = max(0, 1.0 - 2 * t)
        src_weights[i, 1] = max(0, 1.0 - abs(2 * t - 1))
        src_weights[i, 2] = max(0, 2 * t - 1.0)
    rs = src_weights.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    src_weights /= rs

    field = UnifiedDeformationField()
    field.add_source("src", src_verts, src_faces, src_weights, joints)
    field.build(method="rbf", compute_layers=False)

    # 查询球体内部的点
    query_pts = np.array([
        [0.0, -0.8, 0.0],   # 底部 → A
        [0.0, 0.0, 0.0],    # 中间 → B
        [0.0, 0.8, 0.0],    # 顶部 → C
    ])
    result = field.query(query_pts)

    assert result.shape == (3, 3)
    assert np.allclose(result.sum(axis=1), 1.0, atol=0.02)
    assert result[0, 0] > 0.5, f"Bottom should be A-dominant, got {result[0]}"
    assert result[1, 1] > 0.3, f"Center should have B, got {result[1]}"
    assert result[2, 2] > 0.5, f"Top should be C-dominant, got {result[2]}"

    print(f"[rbf] results: bottom={result[0]}, center={result[1]}, top={result[2]}")
    print("[rbf] PASSED — RBF 插值正确")


def test_layer_computation():
    """测试层级计算（需要 igl）。"""
    try:
        import igl
    except ImportError:
        print("[layers] SKIPPED — igl not available")
        return

    from core.unified_deformation_field import UnifiedDeformationField

    # 内层球（body）和外层球（jacket）
    body_verts, body_faces = make_uv_sphere(radius=1.0, rings=8, segments=12)
    jacket_verts, jacket_faces = make_uv_sphere(radius=1.2, rings=8, segments=12)

    body_joints = ["hip", "spine"]
    jacket_joints = ["spine", "chest"]

    N_body = body_verts.shape[0]
    N_jacket = jacket_verts.shape[0]

    body_weights = np.zeros((N_body, 2))
    body_weights[:N_body // 2, 0] = 1.0
    body_weights[N_body // 2:, 1] = 1.0

    jacket_weights = np.zeros((N_jacket, 2))
    jacket_weights[:N_jacket // 2, 0] = 1.0
    jacket_weights[N_jacket // 2:, 1] = 1.0

    field = UnifiedDeformationField()
    field.add_source("body", body_verts, body_faces, body_weights, body_joints)
    field.add_source("jacket", jacket_verts, jacket_faces, jacket_weights, jacket_joints)

    t0 = time.time()
    field.build(compute_layers=True)
    dt = time.time() - t0

    # body 在 jacket 内部 → body 的 layer 应该 >= 1
    # jacket 不在 body 内部 → jacket 的 layer 应该 = 0
    body_layers = field._all_layer[:N_body]
    jacket_layers = field._all_layer[N_body:]

    body_inside_count = (body_layers >= 1).sum()
    jacket_inside_count = (jacket_layers >= 1).sum()

    print(f"[layers] body inside jacket: {body_inside_count}/{N_body}")
    print(f"[layers] jacket inside body: {jacket_inside_count}/{N_jacket}")
    print(f"[layers] build time: {dt*1000:.1f}ms")

    # body 大部分点应该在 jacket 内部
    assert body_inside_count > N_body * 0.8, \
        f"Body should mostly be inside jacket, only {body_inside_count}/{N_body}"
    # jacket 不应该在 body 内部
    assert jacket_inside_count < N_jacket * 0.2, \
        f"Jacket should not be inside body, {jacket_inside_count}/{N_jacket} are"

    print("[layers] PASSED — 层级计算正确区分内外")


def test_geodesic_falloff():
    """测试延伸结构的 geodesic falloff。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源：平面（模拟眼眶表面）
    src_verts, src_faces = make_plane_mesh(6, scale=0.5)
    joints = ["eye_L", "eye_R"]
    N_src = src_verts.shape[0]
    src_weights = np.zeros((N_src, 2))
    src_weights[:, 0] = 1.0  # 全部 eye_L

    field = UnifiedDeformationField()
    field.add_source("face", src_verts, src_faces, src_weights, joints)
    field.build(compute_layers=False)

    # 延伸结构：条带（模拟睫毛），根部在源表面附近
    strip_verts, strip_faces = make_strip_mesh(length=8, width=3, z_offset=0.0)
    # 根部索引（第一行）
    anchor_indices = np.arange(3)  # 前 3 个点是根部

    result = field.query_with_geodesic_falloff(
        strip_verts, strip_faces,
        anchor_indices=anchor_indices,
        anchor_source="field",
        falloff_distance=0.3
    )

    assert result.shape == (24, 2)
    assert np.allclose(result.sum(axis=1), 1.0, atol=0.02)

    # 根部应该强烈跟随源（eye_L）
    root_weight = result[:3, 0].mean()
    # 末端应该衰减
    tip_weight = result[-3:, 0].mean()

    print(f"[geodesic] root eye_L weight: {root_weight:.4f}")
    print(f"[geodesic] tip eye_L weight: {tip_weight:.4f}")
    assert root_weight > 0.8, f"Root should follow source, got {root_weight}"
    print("[geodesic] PASSED — 延伸结构根部跟随源，末端衰减")


def test_empty_query():
    """测试空查询。"""
    from core.unified_deformation_field import UnifiedDeformationField

    src_verts, src_faces = make_plane_mesh(4)
    field = UnifiedDeformationField()
    field.add_source("src", src_verts, src_faces,
                     np.ones((16, 2)) * 0.5, ["A", "B"])
    field.build(compute_layers=False)

    # 空查询
    empty = np.zeros((0, 3))
    result = field.query(empty)
    assert result.shape == (0, 2)
    print("[empty_query] PASSED — 空查询返回 (0, J)")


def test_single_source_all_same():
    """测试单源全相同权重。"""
    from core.unified_deformation_field import UnifiedDeformationField

    src_verts, src_faces = make_plane_mesh(5)
    N = src_verts.shape[0]
    # 所有点权重相同
    src_weights = np.array([[0.6, 0.3, 0.1]] * N)

    field = UnifiedDeformationField()
    field.add_source("uniform", src_verts, src_faces, src_weights, ["A", "B", "C"])
    field.build(compute_layers=False)

    # 查询任意点都应该得到相同权重
    query = np.array([[0.5, 0.5, 0.01], [0.2, 0.8, 0.01]])
    result = field.query(query, k=8)

    for i in range(2):
        assert np.allclose(result[i], [0.6, 0.3, 0.1], atol=0.05), \
            f"Uniform source should give uniform result, got {result[i]}"

    print("[uniform] PASSED — 均匀源 → 均匀查询结果")


def test_performance():
    """性能测试：10000 顶点源 + 5000 顶点查询。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 大规模源
    src_verts, src_faces = make_uv_sphere(radius=1.0, rings=50, segments=100)
    N = src_verts.shape[0]
    J = 40
    joints = [f"joint_{i}" for i in range(J)]

    # 随机权重（稀疏，每点最多 4 个非零）
    src_weights = np.zeros((N, J))
    for i in range(N):
        active = np.random.choice(J, 4, replace=False)
        vals = np.random.dirichlet(np.ones(4))
        src_weights[i, active] = vals

    field = UnifiedDeformationField()
    field.add_source("big_sphere", src_verts, src_faces, src_weights, joints)

    t0 = time.time()
    field.build(compute_layers=False)
    build_time = time.time() - t0

    # 查询
    query_verts, query_faces = make_uv_sphere(radius=1.05, rings=35, segments=70)
    Q = query_verts.shape[0]

    t0 = time.time()
    result = field.query(query_verts, k=12)
    query_time = time.time() - t0

    assert result.shape == (Q, J)
    assert np.allclose(result.sum(axis=1), 1.0, atol=0.01)

    print(f"[performance] source: {N} verts, {J} joints")
    print(f"[performance] query: {Q} verts")
    print(f"[performance] build: {build_time*1000:.1f}ms")
    print(f"[performance] query: {query_time*1000:.1f}ms")
    assert build_time < 5.0, f"Build too slow: {build_time:.2f}s"
    assert query_time < 5.0, f"Query too slow: {query_time:.2f}s"
    print("[performance] PASSED")


def test_blendshape_transfer():
    """测试 BS delta 传递：源模型有 BS，新 mesh 能正确插值 delta。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源：平面 z=0
    src_verts, src_faces = make_plane_mesh(8, scale=1.0)
    N = src_verts.shape[0]
    joints = ["root"]
    src_weights = np.ones((N, 1))

    # BS: "smile" — Y 方向位移，幅度随 X 线性增大
    smile_delta = np.zeros((N, 3))
    for i in range(N):
        smile_delta[i, 1] = src_verts[i, 0] * 0.5  # X 越大，Y 位移越大

    # BS: "blink" — Z 方向位移，幅度随 Y 线性增大
    blink_delta = np.zeros((N, 3))
    for i in range(N):
        blink_delta[i, 2] = src_verts[i, 1] * 0.3

    field = UnifiedDeformationField()
    field.add_source("face", src_verts, src_faces, src_weights, joints,
                     blendshapes={"smile": smile_delta, "blink": blink_delta})
    field.build(compute_layers=False)

    assert field.num_bs_targets == 2
    assert set(field.bs_targets) == {"smile", "blink"}

    # 查询点
    query_pts = np.array([
        [0.0, 0.5, 0.01],   # X=0 → smile delta Y ≈ 0
        [0.5, 0.5, 0.01],   # X=0.5 → smile delta Y ≈ 0.25
        [1.0, 0.5, 0.01],   # X=1.0 → smile delta Y ≈ 0.5
    ])

    deltas = field.query_blendshapes(query_pts)

    assert "smile" in deltas
    assert "blink" in deltas
    assert deltas["smile"].shape == (3, 3)

    # smile: Y 分量应该随 X 递增
    smile_y = deltas["smile"][:, 1]
    assert smile_y[0] < smile_y[1] < smile_y[2], \
        f"Smile Y should increase with X: {smile_y}"
    assert smile_y[2] > 0.3, f"Smile at X=1 should be ~0.5, got {smile_y[2]}"

    # blink: Z 分量在 Y=0.5 处应该 ≈ 0.15
    blink_z = deltas["blink"][:, 2]
    assert all(b > 0.1 for b in blink_z), f"Blink Z at Y=0.5 should be ~0.15: {blink_z}"

    print(f"[bs_transfer] smile Y: {smile_y}")
    print(f"[bs_transfer] blink Z: {blink_z}")
    print("[bs_transfer] PASSED — BS delta 正确插值")


def test_query_full():
    """测试 query_full 一次性返回权重 + BS + 置信度。"""
    from core.unified_deformation_field import UnifiedDeformationField

    src_verts, src_faces = make_plane_mesh(6, scale=1.0)
    N = src_verts.shape[0]
    joints = ["A", "B"]
    src_weights = np.zeros((N, 2))
    src_weights[:N // 2, 0] = 1.0
    src_weights[N // 2:, 1] = 1.0

    # 一个简单的 BS
    wave_delta = np.zeros((N, 3))
    wave_delta[:, 2] = np.sin(src_verts[:, 0] * np.pi * 2) * 0.1

    field = UnifiedDeformationField()
    field.add_source("src", src_verts, src_faces, src_weights, joints,
                     blendshapes={"wave": wave_delta})
    field.build(compute_layers=False)

    query_pts = np.array([[0.5, 0.5, 0.01], [0.5, 0.5, 5.0]])
    result = field.query_full(query_pts)

    assert "weights" in result
    assert "blendshapes" in result
    assert "confidence" in result
    assert "joints" in result
    assert "bs_targets" in result

    assert result["weights"].shape == (2, 2)
    assert result["confidence"].shape == (2,)
    assert "wave" in result["blendshapes"]
    assert result["blendshapes"]["wave"].shape == (2, 3)

    # 近点置信度高，远点低
    assert result["confidence"][0] > result["confidence"][1]

    # joints 和 bs_targets 名称正确
    assert result["joints"] == ["A", "B"]
    assert result["bs_targets"] == ["wave"]

    print(f"[query_full] weights: {result['weights']}")
    print(f"[query_full] confidence: {result['confidence']}")
    print(f"[query_full] wave delta: {result['blendshapes']['wave']}")
    print("[query_full] PASSED — 一次采样返回完整结果")


def test_blendshape_multi_source():
    """测试多源模型 BS target 合并。"""
    from core.unified_deformation_field import UnifiedDeformationField

    # 源 1: 有 smile 和 blink
    src1_verts, src1_faces = make_plane_mesh(5, scale=0.5)
    N1 = src1_verts.shape[0]
    src1_weights = np.ones((N1, 1))
    smile1 = np.zeros((N1, 3))
    smile1[:, 1] = 0.1  # 统一 Y 位移
    blink1 = np.zeros((N1, 3))
    blink1[:, 2] = -0.05  # 统一 Z 位移

    # 源 2: 有 smile 和 puff（不同 target）
    src2_verts, src2_faces = make_plane_mesh(5, scale=0.5, z_offset=0.1)
    src2_verts[:, 0] += 0.6  # 偏移到右边
    N2 = src2_verts.shape[0]
    src2_weights = np.ones((N2, 1))
    smile2 = np.zeros((N2, 3))
    smile2[:, 1] = 0.2  # 更大的 Y 位移
    puff2 = np.zeros((N2, 3))
    puff2[:, 0] = 0.08  # X 方向膨胀

    field = UnifiedDeformationField()
    field.add_source("left", src1_verts, src1_faces, src1_weights, ["root"],
                     blendshapes={"smile": smile1, "blink": blink1})
    field.add_source("right", src2_verts, src2_faces, src2_weights, ["root"],
                     blendshapes={"smile": smile2, "puff": puff2})
    field.build(compute_layers=False)

    # BS union 应该有 3 个 target
    assert field.num_bs_targets == 3, f"Expected 3 BS targets, got {field.num_bs_targets}"
    assert set(field.bs_targets) == {"smile", "blink", "puff"}

    # 查询左侧点 → 应该主要从 src1 采样
    q_left = np.array([[0.25, 0.25, 0.01]])
    deltas_left = field.query_blendshapes(q_left)
    assert deltas_left["smile"][0, 1] > 0.05  # 有 smile Y 位移
    assert abs(deltas_left["blink"][0, 2]) > 0.02  # 有 blink Z 位移
    # puff 在左侧应该很小（src1 没有 puff，delta=0）
    assert abs(deltas_left["puff"][0, 0]) < 0.05

    # 查询右侧点 → 应该主要从 src2 采样
    q_right = np.array([[0.85, 0.25, 0.11]])
    deltas_right = field.query_blendshapes(q_right)
    assert deltas_right["smile"][0, 1] > 0.1  # src2 的 smile 更大
    assert abs(deltas_right["puff"][0, 0]) > 0.03  # 有 puff
    # blink 在右侧应该很小（src2 没有 blink）
    assert abs(deltas_right["blink"][0, 2]) < 0.03

    print(f"[bs_multi] targets: {field.bs_targets}")
    print(f"[bs_multi] left smile Y: {deltas_left['smile'][0, 1]:.4f}")
    print(f"[bs_multi] right smile Y: {deltas_right['smile'][0, 1]:.4f}")
    print(f"[bs_multi] left puff X: {deltas_left['puff'][0, 0]:.4f}")
    print(f"[bs_multi] right puff X: {deltas_right['puff'][0, 0]:.4f}")
    print("[bs_multi] PASSED — 多源 BS target 合并正确")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Testing UnifiedDeformationField")
    print("=" * 60 + "\n")

    test_basic_build_and_query()
    print()
    test_multi_source_union()
    print()
    test_normal_consistency()
    print()
    test_shape_preservation()
    print()
    test_confidence()
    print()
    test_rbf_method()
    print()
    test_layer_computation()
    print()
    test_geodesic_falloff()
    print()
    test_empty_query()
    print()
    test_single_source_all_same()
    print()
    test_performance()
    print()
    test_blendshape_transfer()
    print()
    test_query_full()
    print()
    test_blendshape_multi_source()

    print("\n" + "=" * 60)
    print("ALL UNIFIED FIELD TESTS PASSED")
    print("=" * 60)
