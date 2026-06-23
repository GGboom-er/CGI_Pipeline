# core/abc_reader.py
# ── ABC 文件直读 → asset_info 格式 ──
#
# 使用 PyAlembic (cgohlke/pyalembic-wheels) 原生读取 ABC 文件。
# 纯 Python，无需 Maya/Blender，无子进程。
# 返回与 maya_build_asset_info / blender_build_asset_info 完全一致的 dict。
#
# 法线修正：通过 Signed Volume（带符号体积，散度定理 Divergence Theorem）
# 检测面绕序朝向。Volume < 0 表示上游 DCC 的负缩放烘焙导致了物理镜像，
# 此时在数据级反转面索引绕序，确保 MFnMesh.create 后法线自然朝外。
# 该方案不依赖任何 Metadata 或引擎插件，只信任微积分数学。

import os
from alembic.Abc import IArchive
from alembic.AbcGeom import IPolyMesh, IXform


def _open_archive(abc_path: str):
    """打开 ABC 文件（自动检测 Ogawa/HDF5 格式）。"""
    return IArchive(abc_path)


def _traverse(obj, parent_path="", parent_matrix=None):
    """
    递归遍历 ABC 层级，收集所有 IPolyMesh 节点及世界空间矩阵。

    返回:
        list of (dag_path, mesh_obj, world_matrix)
    """
    import imath
    if parent_matrix is None:
        parent_matrix = imath.M44d()

    world_matrix = parent_matrix
    header = obj.getHeader()
    if IXform.matches(header):
        xform = IXform(obj.getParent(), obj.getName())
        schema = xform.getSchema()
        sample = schema.getValue()
        local_matrix = sample.getMatrix()
        world_matrix = local_matrix * parent_matrix

    meshes = []
    name = obj.getName()
    current_path = f"{parent_path}|{name}" if parent_path else name

    if IPolyMesh.matches(header):
        meshes.append((current_path, obj, world_matrix))

    for i in range(obj.getNumChildren()):
        child = obj.getChild(i)
        meshes.extend(_traverse(child, current_path, world_matrix))

    return meshes


def _normalize_abc_display_path(dag_path: str) -> str:
    """把 Alembic archive 顶层 `ABC` 从业务 DAG 路径中剥离。"""
    display_path = (dag_path or "").strip("|")
    if display_path.startswith("ABC|"):
        display_path = display_path[4:]
    if not display_path.startswith("|"):
        display_path = "|" + display_path
    return display_path


def _extract_faceset_materials(obj):
    """
    从 IPolyMesh 的子对象中提取 FaceSet 名称作为材质名。
    Blender 导出 ABC 时，FaceSet 名称通常就是材质名。
    """
    materials = []
    for i in range(obj.getNumChildren()):
        child = obj.getChild(i)
        child_name = child.getName()
        # FaceSet 子对象的 metadata 或名称包含材质信息
        if child_name.startswith('.') or child_name == 'ArbGeomParams':
            continue
        # 典型的 FaceSet 命名即材质名
        materials.append(child_name)
    return materials


def _compute_signed_volume(positions, face_counts, face_indices):
    """计算网格的带符号体积（Signed Volume）。

    基于散度定理（Divergence Theorem）的体积积分公式：
    V = Σ dot(p0, cross(p1, p2)) / 6.0

    对封闭几何体：
    - Volume > 0 → 面法线朝外（正常绕序）
    - Volume < 0 → 面法线朝内（被烘焙镜像，需反转绕序）

    参数:
        positions: ABC 顶点数组（Imath V3f 列表）
        face_counts: 每个面的顶点数列表
        face_indices: 面顶点索引展平列表

    返回:
        float: 带符号体积值
    """
    volume = 0.0
    idx = 0
    for count in face_counts:
        if count >= 3:
            i0 = face_indices[idx]
            p0 = positions[i0]
            for j in range(1, count - 1):
                i1 = face_indices[idx + j]
                i2 = face_indices[idx + j + 1]
                p1 = positions[i1]
                p2 = positions[i2]
                # cross(p1, p2)
                cx = p1.y * p2.z - p1.z * p2.y
                cy = p1.z * p2.x - p1.x * p2.z
                cz = p1.x * p2.y - p1.y * p2.x
                # dot(p0, cross) / 6
                volume += (p0.x * cx + p0.y * cy + p0.z * cz) / 6.0
        idx += count
    return volume


def _fix_winding_order(face_counts, indices):
    """按面反转索引绕序，保持拓扑结构不变。

    对每个面的顶点索引进行 reverse，使法线翻转到正确方向。
    同时适用于面索引（face_indices）和 UV 索引（uv_indices）。

    参数:
        face_counts: 每个面的顶点数列表
        indices: 待反转的索引列表（会被拷贝，不修改原数组）

    返回:
        list: 反转后的索引列表
    """
    fixed = list(indices)
    idx = 0
    for count in face_counts:
        fixed[idx:idx + count] = reversed(fixed[idx:idx + count])
        idx += count
    return fixed

def read_abc_as_info(abc_path: str, lightweight: bool = False) -> dict:
    """
    直接读取 ABC 文件，返回 asset_info dict。

    参数:
        abc_path: ABC 文件绝对路径
        lightweight: 轻量模式（仅提取顶点数+坐标，跳过 UV/拓扑/FaceSet/winding）。
                     用于 pipeline_compare_asset 对比场景，速度约快 2x。

    返回:
        asset_info dict（与 JSON 格式一致）:
        {
            'source_file': str,
            'meshes': {
                dag_path: {
                    'vertices': int,
                    'vert_positions': [x, y, z, ...],
                    'materials': [str, ...],       # lightweight 时省略
                    'u_array': [float, ...],       # lightweight 时省略
                    'v_array': [float, ...],       # lightweight 时省略
                    'uv_indices': [int, ...],      # lightweight 时省略
            },
            'textures': {},
        }

    异常:
        FileNotFoundError: ABC 文件不存在
        RuntimeError: 读取失败
    """
    if not os.path.isfile(abc_path):
        raise FileNotFoundError(f"ABC 文件不存在: {abc_path}")

    try:
        archive = _open_archive(abc_path)
    except Exception as e:
        raise RuntimeError(f"无法打开 ABC 文件: {e}")

    top = archive.getTop()
    mesh_nodes = _traverse(top)

    meshes = {}
    for dag_path, obj, world_matrix in mesh_nodes:
        # 清理路径前缀，使其与 JSON 对齐 (Alembic 导出可能以 ABC 开头)
        display_path = _normalize_abc_display_path(dag_path)

        # 补齐 Shape 后缀以匹配 Maya 原生 JSON 导出
        if not display_path.endswith("Shape"):
            display_path += "Shape"

        mesh = IPolyMesh(obj.getParent(), obj.getName())
        schema = mesh.getSchema()

        # 读取第一帧的几何数据
        sample = schema.getValue()
        positions = sample.getPositions()

        # 顶点坐标转换到世界空间并展平
        vert_positions = []
        for pt in positions:
            world_pt = pt * world_matrix
            vert_positions.extend([
                round(float(world_pt[0]), 6),
                round(float(world_pt[1]), 6),
                round(float(world_pt[2]), 6),
            ])

        # ── 轻量模式：仅需顶点数据，跳过 UV/拓扑/FaceSet/winding ──
        if lightweight:
            meshes[display_path] = {
                'vertices': len(positions),
                'vert_positions': vert_positions,
            }
            continue

        # ── 完整模式：提取全部几何信息 ──

        # UV 数据提取
        u_array = []
        v_array = []
        uv_indices = []
        uv_param = schema.getUVsParam()
        if uv_param.valid():
            uv_samp = uv_param.getIndexedValue()
            vals = uv_samp.getVals()
            u_array = [v.x for v in vals]
            v_array = [v.y for v in vals]
            uv_indices = list(uv_samp.getIndices())

        # 拓扑数据
        face_counts = list(sample.getFaceCounts())
        face_indices = list(sample.getFaceIndices())

        # ── Signed Volume 检测：判断面绕序是否需要修正 ──
        # 基于散度定理，Volume < 0 意味着上游 DCC 烘焙了负缩放镜像
        # 但未修正面索引绕序，导致法线朝内。此时需要反转绕序。
        signed_vol = _compute_signed_volume(positions, face_counts, face_indices)
        winding_flipped = False
        if signed_vol < 0:
            face_indices = _fix_winding_order(face_counts, face_indices)
            # UV 索引也必须同步反转，否则 UV 映射会错位
            if uv_indices:
                uv_indices = _fix_winding_order(face_counts, uv_indices)
            winding_flipped = True

        # 材质名称（从 FaceSet 提取）
        materials = _extract_faceset_materials(obj)

        meshes[display_path] = {
            'vertices': len(positions),
            'vert_positions': vert_positions,
            'face_counts': face_counts,
            'face_indices': face_indices,
            'materials': materials,
            'u_array': u_array,
            'v_array': v_array,
            'uv_indices': uv_indices,
            'winding_flipped': winding_flipped,
        }

    result = {
        'source_file': abc_path,
        'meshes': meshes,
        'textures': {},
        'materials': {},
    }

    # 自动加载伴生 _materials.json
    mat_path = os.path.splitext(abc_path)[0] + '_materials.json'
    if os.path.isfile(mat_path):
        try:
            import json
            with open(mat_path, 'r', encoding='utf-8') as fp:
                mat_data = json.load(fp)
            result['materials'] = mat_data.get('materials', {})
        except Exception:
            pass

    return result

