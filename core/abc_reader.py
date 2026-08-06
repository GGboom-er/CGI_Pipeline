# core/abc_reader.py
# ── ABC 文件直读 → asset_info 格式 ──
#
# 使用 PyAlembic (cgohlke/pyalembic-wheels) 原生读取 ABC 文件。
# 纯 Python，无需 Maya/Blender，无子进程。
# 返回与 maya_build_asset_info / blender_build_asset_info 完全一致的 dict。
#
# Maya AbcImport 会把 Alembic 的每个 polygon 顶点顺序逐面逆序后再创建
# MFnMesh，并用同一映射写入 UV 与 face-varying normals。本 reader 在数据层
# 做相同转换，所有下游 Maya 构建路径直接消费转换后的数组。

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


def _reverse_per_face(face_counts, values):
    """按 polygon 边界逐面逆序，不修改输入数组。"""
    reversed_values = list(values)
    idx = 0
    for count in face_counts:
        reversed_values[idx:idx + count] = reversed(
            reversed_values[idx:idx + count]
        )
        idx += count
    return reversed_values


def _convert_face_order_for_maya(
        face_counts, face_indices, uv_indices=None, normals_fv=None):
    """复刻 Maya AbcImport 的逐面索引映射。

    normals_fv 是展平的 xyz 三元组。缺失或长度不符合 face-varying
    契约时返回空数组，由 Maya 根据转换后的拓扑自动生成法线。
    """
    maya_face_indices = _reverse_per_face(face_counts, face_indices)

    maya_uv_indices = list(uv_indices or [])
    if maya_uv_indices:
        maya_uv_indices = _reverse_per_face(face_counts, maya_uv_indices)

    maya_normals_fv = list(normals_fv or [])
    expected_normal_values = len(maya_face_indices) * 3
    if maya_normals_fv and len(maya_normals_fv) == expected_normal_values:
        normal_triples = [
            maya_normals_fv[i:i + 3]
            for i in range(0, len(maya_normals_fv), 3)
        ]
        normal_triples = _reverse_per_face(face_counts, normal_triples)
        maya_normals_fv = [
            component
            for normal in normal_triples
            for component in normal
        ]
    else:
        maya_normals_fv = []

    return maya_face_indices, maya_uv_indices, maya_normals_fv

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

        # 法线数据提取（facevarying / per-face-vertex，顺序同 face_indices）
        # ABC 存的是资产真法线（含硬边）；注入时优先用它，setFaceVertexNormals 直接赋。
        # normals_fv：每面顶点一个 [x,y,z]，展平为 [x,y,z, x,y,z, ...]，长度 = len(face_indices)*3。
        normals_fv = []
        normal_param = schema.getNormalsParam()
        try:
            if normal_param.valid():
                n_samp = normal_param.getExpandedValue()
                n_vals = n_samp.getVals()
                for n in n_vals:
                    normals_fv.extend([float(n.x), float(n.y), float(n.z)])
        except Exception:
            # 法线不是构建拓扑的前置条件；不可读时交给 Maya 自动生成。
            normals_fv = []

        # 拓扑数据
        face_counts = list(sample.getFaceCounts())
        face_indices = list(sample.getFaceIndices())

        # Maya AbcImport 对 Alembic polygon 固定逐面逆序；UV 和显式法线必须
        # 使用同一 face-vertex 映射。这里不判断封闭性，也不猜测“外侧”。
        face_indices, uv_indices, normals_fv = _convert_face_order_for_maya(
            face_counts, face_indices, uv_indices, normals_fv
        )

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
            'normals_fv': normals_fv,
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

