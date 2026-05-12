"""
测试三种扩散后端 + TNB 投射引擎的正确性。
用一个简单的平面网格做验证。
"""
import numpy as np
import time
import sys
sys.path.insert(0, r"Y:\GGbommer\scripts\CGI_Pipeline")


def make_plane_mesh(grid_size=10, scale=1.0):
    """生成 grid_size x grid_size 的平面三角网格。"""
    verts = []
    for i in range(grid_size):
        for j in range(grid_size):
            verts.append([i * scale / (grid_size - 1),
                          j * scale / (grid_size - 1),
                          0.0])
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


def test_laplacian_scipy():
    """测试余切拉普拉斯精确解。"""
    from core.laplacian_diffuse import laplacian_diffuse_scipy

    verts, faces = make_plane_mesh(10)
    N = verts.shape[0]

    # 四角锚点，2 个 joint
    anchors = np.array([0, 9, 90, 99])
    anchor_w = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
        [1.0, 0.0],
    ])

    t0 = time.time()
    W = laplacian_diffuse_scipy(verts, faces, anchors, anchor_w)
    dt = time.time() - t0

    mid = 45  # 中心点
    print(f"[scipy] center weights: {W[mid]} (expect ~[0.5, 0.5])")
    print(f"[scipy] time: {dt*1000:.1f}ms")
    print(f"[scipy] row sums range: [{W.sum(1).min():.4f}, {W.sum(1).max():.4f}]")
    assert np.allclose(W.sum(1), 1.0, atol=0.01), "Row sums should be ~1"
    assert np.allclose(W[mid], [0.5, 0.5], atol=0.15), f"Center should be ~0.5, got {W[mid]}"
    print("[scipy] PASSED\n")


def test_laplacian_heat():
    """测试 Heat Method 扩散。"""
    from core.laplacian_diffuse import laplacian_diffuse_heat

    verts, faces = make_plane_mesh(10)

    anchors = np.array([0, 9, 90, 99])
    anchor_w = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
        [1.0, 0.0],
    ])

    t0 = time.time()
    W = laplacian_diffuse_heat(verts, faces, anchors, anchor_w)
    dt = time.time() - t0

    mid = 45
    print(f"[heat] center weights: {W[mid]} (expect ~[0.5, 0.5])")
    print(f"[heat] time: {dt*1000:.1f}ms")
    print(f"[heat] row sums range: [{W.sum(1).min():.4f}, {W.sum(1).max():.4f}]")
    assert np.allclose(W.sum(1), 1.0, atol=0.01), "Row sums should be ~1"
    print("[heat] PASSED\n")


def test_laplacian_jacobi():
    """测试旧版 Jacobi 迭代（兼容性）。"""
    from core.laplacian_diffuse import laplacian_diffuse

    grid_size = 10
    N = grid_size * grid_size

    # 构建邻接表
    adjacency = []
    for i in range(grid_size):
        for j in range(grid_size):
            nbrs = []
            if i > 0: nbrs.append((i - 1) * grid_size + j)
            if i < grid_size - 1: nbrs.append((i + 1) * grid_size + j)
            if j > 0: nbrs.append(i * grid_size + (j - 1))
            if j < grid_size - 1: nbrs.append(i * grid_size + (j + 1))
            adjacency.append(nbrs)

    anchors = [0, 9, 90, 99]
    anchor_w = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
        [1.0, 0.0],
    ])

    t0 = time.time()
    W = laplacian_diffuse(adjacency, anchors, anchor_w, N, 2)
    dt = time.time() - t0

    mid = 45
    print(f"[jacobi] center weights: {W[mid]} (expect ~[0.5, 0.5])")
    print(f"[jacobi] time: {dt*1000:.1f}ms")
    assert np.allclose(W[mid], [0.5, 0.5], atol=0.15), f"Center should be ~0.5, got {W[mid]}"
    print("[jacobi] PASSED\n")


def test_biharmonic():
    """测试双调和扩散。"""
    from core.biharmonic_diffuse import biharmonic_diffuse

    verts, faces = make_plane_mesh(10)

    anchors = np.array([0, 9, 90, 99])
    anchor_w = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
        [1.0, 0.0],
    ])

    t0 = time.time()
    W = biharmonic_diffuse(verts, faces, anchors, anchor_w)
    dt = time.time() - t0

    mid = 45
    print(f"[biharmonic] center weights: {W[mid]} (expect ~[0.5, 0.5])")
    print(f"[biharmonic] time: {dt*1000:.1f}ms")
    print(f"[biharmonic] row sums range: [{W.sum(1).min():.4f}, {W.sum(1).max():.4f}]")
    assert np.allclose(W.sum(1), 1.0, atol=0.01), "Row sums should be ~1"
    print("[biharmonic] PASSED\n")


def test_spatial_transfer():
    """测试 TNB 投射引擎。"""
    from core.spatial_transfer import (
        find_closest_triangle_with_normal,
        barycentric_weight_transfer,
        compute_face_normals,
        compute_vertex_normals,
        transfer_weights_tnb,
    )

    # 源 mesh: 平面 z=0
    src_verts, src_faces = make_plane_mesh(5)
    J = 3
    # 源权重：线性渐变
    src_weights = np.zeros((25, J))
    for i in range(25):
        x = src_verts[i, 0]
        src_weights[i, 0] = 1.0 - x
        src_weights[i, 1] = x
        src_weights[i, 2] = 0.0

    # 目标 mesh: 平面 z=0.01（微偏移）
    dst_verts, dst_faces = make_plane_mesh(8)
    dst_verts[:, 2] = 0.01  # 微偏移

    W, stats = transfer_weights_tnb(
        src_verts, src_faces, src_weights,
        dst_verts, dst_faces,
        diffuse_backend="scipy"
    )

    print(f"[spatial] projected: {stats['n_projected']}/{len(dst_verts)}")
    print(f"[spatial] diffused: {stats['n_diffused']}")
    print(f"[spatial] sample weights (x=0.5): {W[28]}")  # 中间点

    # 中间点 x≈0.5 应该有 w0≈0.5, w1≈0.5
    assert W.shape == (64, 3)
    assert np.allclose(W.sum(1), 1.0, atol=0.01)
    print("[spatial] PASSED\n")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing diffusion backends and spatial transfer")
    print("=" * 60 + "\n")

    test_laplacian_scipy()
    test_laplacian_heat()
    test_laplacian_jacobi()
    test_biharmonic()
    test_spatial_transfer()

    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
