"""
创建一个用于 Maya 注入测试的 ABC 文件。
包含: 立方体 + 球体，带 UV 和法线。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.Abc import OArchive, OObject
from alembic.AbcGeom import (
    OPolyMesh, OPolyMeshSchemaSample,
    OXform, OV2fGeomParamSample, ON3fGeomParamSample,
    GeometryScope,
)
import imath
import math

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'tests', 'data')
os.makedirs(OUT_DIR, exist_ok=True)
OUT_PATH = os.path.join(OUT_DIR, 'test_inject.abc')


def _make_cube():
    """创建一个有 UV 的立方体（24 顶点, 6 面, 每面 4 顶点展开）。"""
    # 8 个唯一顶点位置，但 UV 展开需要 24 个
    raw = [
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),    # 前
        (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.5, -0.5, -0.5),  # 后
        (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5),      # 上
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5),  # 下
        (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), (0.5, -0.5, 0.5),      # 右
        (-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (-0.5, 0.5, -0.5),  # 左
    ]
    positions = imath.V3fArray(24)
    for i, (x, y, z) in enumerate(raw):
        positions[i] = imath.V3f(x, y, z)

    # 6 面, 每面 4 顶点
    face_counts = imath.IntArray(6)
    for i in range(6):
        face_counts[i] = 4

    face_indices = imath.IntArray(24)
    for i in range(24):
        face_indices[i] = i

    # 法线（每面一个方向）
    normals_raw = [
        (0, 0, 1),   # 前
        (0, 0, -1),  # 后
        (0, 1, 0),   # 上
        (0, -1, 0),  # 下
        (1, 0, 0),   # 右
        (-1, 0, 0),  # 左
    ]
    normals = imath.V3fArray(24)
    for face_i in range(6):
        nx, ny, nz = normals_raw[face_i]
        for v in range(4):
            normals[face_i * 4 + v] = imath.V3f(nx, ny, nz)

    # UV（每面展开一个 [0,1] 正方形）
    uvs = imath.V2fArray(24)
    uv_pattern = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for face_i in range(6):
        for v in range(4):
            u, vv = uv_pattern[v]
            uvs[face_i * 4 + v] = imath.V2f(u, vv)

    return positions, face_counts, face_indices, normals, uvs


def _make_sphere(subdivisions=8):
    """创建一个 UV 球体。"""
    verts = []
    faces_counts = []
    faces_indices = []
    norms = []
    uv_list = []

    rings = subdivisions
    segments = subdivisions * 2
    radius = 0.5

    # 生成顶点
    for i in range(rings + 1):
        phi = math.pi * i / rings
        for j in range(segments):
            theta = 2 * math.pi * j / segments
            x = radius * math.sin(phi) * math.cos(theta)
            y = radius * math.cos(phi)
            z = radius * math.sin(phi) * math.sin(theta)
            verts.append((x, y, z))
            # 法线就是归一化的位置
            length = math.sqrt(x*x + y*y + z*z) or 1.0
            norms.append((x/length, y/length, z/length))
            uv_list.append((j / segments, 1.0 - i / rings))

    # 生成面
    for i in range(rings):
        for j in range(segments):
            next_j = (j + 1) % segments
            v0 = i * segments + j
            v1 = i * segments + next_j
            v2 = (i + 1) * segments + next_j
            v3 = (i + 1) * segments + j
            faces_counts.append(4)
            faces_indices.extend([v0, v1, v2, v3])

    positions = imath.V3fArray(len(verts))
    for i, (x, y, z) in enumerate(verts):
        positions[i] = imath.V3f(x, y, z)

    fc = imath.IntArray(len(faces_counts))
    for i, c in enumerate(faces_counts):
        fc[i] = c

    fi = imath.IntArray(len(faces_indices))
    for i, idx in enumerate(faces_indices):
        fi[i] = idx

    normals = imath.V3fArray(len(norms))
    for i, (nx, ny, nz) in enumerate(norms):
        normals[i] = imath.V3f(nx, ny, nz)

    uvs = imath.V2fArray(len(uv_list))
    for i, (u, v) in enumerate(uv_list):
        uvs[i] = imath.V2f(u, v)

    return positions, fc, fi, normals, uvs


def main():
    archive = OArchive(OUT_PATH, True)  # Ogawa
    top = archive.getTop()

    # 根组
    grp = OXform(top, 'cache')

    # --- 立方体 ---
    cube_mesh = OPolyMesh(grp, 'test_cube')
    cs = cube_mesh.getSchema()
    c_pos, c_fc, c_fi, c_norms, c_uvs = _make_cube()

    # UV 参数
    uv_sample = OV2fGeomParamSample(c_uvs, GeometryScope.kFacevaryingScope)
    # 法线参数
    norm_sample = ON3fGeomParamSample(c_norms, GeometryScope.kFacevaryingScope)

    c_sample = OPolyMeshSchemaSample(c_pos, c_fi, c_fc, uv_sample, norm_sample)
    cs.set(c_sample)

    # --- 球体 ---
    sphere_mesh = OPolyMesh(grp, 'test_sphere')
    ss = sphere_mesh.getSchema()
    s_pos, s_fc, s_fi, s_norms, s_uvs = _make_sphere(8)

    s_uv_sample = OV2fGeomParamSample(s_uvs, GeometryScope.kVertexScope)
    s_norm_sample = ON3fGeomParamSample(s_norms, GeometryScope.kVertexScope)

    s_sample = OPolyMeshSchemaSample(s_pos, s_fi, s_fc, s_uv_sample, s_norm_sample)
    ss.set(s_sample)

    # 释放
    del c_sample, s_sample, cs, ss, cube_mesh, sphere_mesh, grp, top, archive

    size = os.path.getsize(OUT_PATH)
    print(f'Created: {OUT_PATH}')
    print(f'Size: {size} bytes')

    # 验证可读
    from core.abc_reader import read_abc_as_info
    info = read_abc_as_info(OUT_PATH)
    print(f'Meshes: {len(info["meshes"])}')
    for dag, data in info['meshes'].items():
        print(f'  {dag}: {data["vertices"]} verts, uvsets={data["uvsets"]}')
    print('DONE')


if __name__ == '__main__':
    main()
