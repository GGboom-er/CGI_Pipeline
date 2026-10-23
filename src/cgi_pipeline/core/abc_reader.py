# cgi_pipeline/core/abc_reader.py
# ── ABC 文件直读 → asset_info 格式 ──
#
# 使用 PyAlembic (cgohlke/pyalembic-wheels) 原生读取 ABC 文件。
# 纯 Python，无需 Maya/Blender，无子进程。
# 返回与 maya_build_asset_info / blender_build_asset_info 完全一致的 dict。
#
# Maya AbcImport 会把 Alembic 的每个 polygon 顶点顺序逐面逆序后再创建
# MFnMesh，并用同一映射写入 UV 与 face-varying normals。本 reader 在数据层
# 做相同转换，所有下游 Maya 构建路径直接消费转换后的数组。

import math
import os
from alembic.Abc import IArchive
from alembic.AbcGeom import GeometryScope, IPolyMesh, IXform


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
        world_matrix = (local_matrix * parent_matrix
                        if sample.getInheritsXforms() else local_matrix)

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


def validate_normals_fv(normals_fv, expected_corners=None):
    """Validate explicit corner normals; only None/[] mean absent normals."""
    if normals_fv is None:
        return []
    try:
        values = [float(value) for value in normals_fv]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('normals_fv must contain numeric xyz values') from exc
    if not values:
        return []
    if len(values) % 3 or (expected_corners is not None
                          and len(values) != expected_corners * 3):
        raise ValueError('normals_fv length must match face corner count * 3')
    if not all(math.isfinite(value) for value in values):
        raise ValueError('normals_fv contains non-finite values')
    for index in range(0, len(values), 3):
        if math.hypot(*values[index:index + 3]) == 0:
            raise ValueError(f'normals_fv has zero-length normal at corner {index // 3}')
    return values


def _affine_matrix_rows(matrix):
    """Validate point transforms without requiring an invertible orientation."""
    try:
        if isinstance(matrix, (list, tuple)) and (
                len(matrix) != 4 or any(len(row) != 4 for row in matrix)):
            raise ValueError('matrix must be 4 x 4')
        rows = [[float(matrix[i][j]) for j in range(4)] for i in range(4)]
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ValueError('matrix must be a finite 4 x 4 affine matrix') from exc
    if not all(math.isfinite(value) for row in rows for value in row):
        raise ValueError('matrix contains non-finite values')
    if any(abs(rows[i][3]) > 1e-12 for i in range(3)) or abs(rows[3][3] - 1) > 1e-12:
        raise ValueError('matrix must be affine under the row-vector convention')
    return rows


def _normal_transform_matrix(matrix):
    """Return inverse transpose for row vectors; reject unproven reflection."""
    rows = _affine_matrix_rows(matrix)
    scale = max(abs(rows[i][j]) for i in range(3) for j in range(3))
    if scale == 0:
        raise ValueError('matrix is singular')
    a, b, c, d, e, f, g, h, i = [
        rows[row][col] / scale for row in range(3) for col in range(3)
    ]
    cofactors = [[e*i - f*h, f*g - d*i, d*h - e*g],
                 [c*h - b*i, a*i - c*g, b*g - a*h],
                 [b*f - c*e, c*d - a*f, a*e - b*d]]
    determinant = a*cofactors[0][0] + b*cofactors[0][1] + c*cofactors[0][2]
    if determinant == 0 or not math.isfinite(determinant):
        raise ValueError('matrix is singular or numerically degenerate')
    if determinant < 0:
        raise ValueError('matrix reflection is unsupported; face orientation needs explicit validation')
    # The positive common scale cancels when each output normal is normalized.
    normal_matrix = [[value / determinant for value in row] for row in cofactors]
    if not all(math.isfinite(value) for row in normal_matrix for value in row):
        raise ValueError('matrix inverse transpose is not finite')
    coefficient_scale = max(abs(value) for row in normal_matrix for value in row)
    return [[value / coefficient_scale for value in row] for row in normal_matrix]


def transform_normals_fv(normals_fv, matrix, expected_corners=None):
    """Move corner normals into the same space as points, then normalize."""
    normal_matrix = _normal_transform_matrix(matrix)
    values = validate_normals_fv(normals_fv, expected_corners)
    result = []
    for index in range(0, len(values), 3):
        normal = values[index:index + 3]
        magnitude = max(abs(component) for component in normal)
        normal = [component / magnitude for component in normal]
        transformed = [sum(normal[row] * normal_matrix[row][col] for row in range(3))
                       for col in range(3)]
        magnitude = math.hypot(*transformed)
        if magnitude == 0 or not math.isfinite(magnitude):
            raise ValueError(f'invalid transformed normal at corner {index // 3}')
        result.extend(component / magnitude for component in transformed)
    return result


def validate_mesh_entry(mesh_data):
    """Preflight complete asset_info geometry before any scene mutation."""
    if not isinstance(mesh_data, dict):
        raise ValueError('mesh entry must be a dict')
    try:
        _validate_mesh_entry(mesh_data)
    except (TypeError, OverflowError) as exc:
        raise ValueError(f'invalid mesh array data: {exc}') from exc


def _validate_mesh_entry(mesh_data):
    vertices = mesh_data.get('vertices')
    positions = mesh_data.get('vert_positions', [])
    if not isinstance(vertices, int) or isinstance(vertices, bool) or vertices < 0:
        raise ValueError('vertices must be a non-negative integer')
    if len(positions) != vertices * 3 or not all(math.isfinite(float(v)) for v in positions):
        raise ValueError('vert_positions must contain vertices * 3 finite values')
    counts = mesh_data.get('face_counts', [])
    indices = mesh_data.get('face_indices', [])
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 3 for n in counts):
        raise ValueError('face_counts must contain polygon corner counts >= 3')
    if sum(counts) != len(indices):
        raise ValueError('face_indices length must equal sum(face_counts)')
    if any(not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < vertices for n in indices):
        raise ValueError('face_indices contains an invalid vertex index')
    u_values = mesh_data.get('u_array', [])
    v_values = mesh_data.get('v_array', [])
    uv_indices = mesh_data.get('uv_indices', [])
    if u_values or v_values or uv_indices:
        if len(u_values) != len(v_values) or len(uv_indices) != len(indices):
            raise ValueError('UV arrays and corner indices must be provided together')
        if not all(math.isfinite(float(v)) for v in list(u_values) + list(v_values)):
            raise ValueError('UV arrays contain non-finite values')
        if any(not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(u_values)
               for n in uv_indices):
            raise ValueError('uv_indices contains an invalid UV index')
    validate_normals_fv(mesh_data.get('normals_fv'), len(indices))


def _read_normals_fv(normal_param, corner_count):
    """Read supported authored normals without treating damaged data as absent."""
    if not normal_param.valid():
        return []
    scope = normal_param.getScope()
    if scope != GeometryScope.kFacevaryingScope:
        raise ValueError(f'unsupported authored normal scope: {scope}; expected face-varying')
    sample = normal_param.getIndexedValue()
    values = list(sample.getVals())
    if not values and corner_count:
        raise ValueError('authored normals have no values')
    if normal_param.isIndexed():
        indices = list(sample.getIndices())
        if len(indices) != corner_count or any(not 0 <= n < len(values) for n in indices):
            raise ValueError('authored normal indices do not match face corners or value range')
        values = [values[index] for index in indices]
    return validate_normals_fv([float(c) for normal in values for c in normal], corner_count)


def _convert_face_order_for_maya(
        face_counts, face_indices, uv_indices=None, normals_fv=None):
    """复刻 Maya AbcImport 的逐面索引映射。

    normals_fv 是展平的 xyz 三元组。合法缺失允许自动法线；损坏的
    显式法线必须失败，不能在重排时静默丢失作者数据。
    """
    maya_face_indices = _reverse_per_face(face_counts, face_indices)

    maya_uv_indices = list(uv_indices or [])
    if maya_uv_indices:
        maya_uv_indices = _reverse_per_face(face_counts, maya_uv_indices)

    maya_normals_fv = validate_normals_fv(normals_fv, len(maya_face_indices))
    if maya_normals_fv:
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
    return maya_face_indices, maya_uv_indices, maya_normals_fv

def read_abc_as_info(abc_path: str, lightweight: bool = False) -> dict:
    """
    直接读取 ABC 文件，返回 asset_info dict。

    参数:
        abc_path: ABC 文件绝对路径
        lightweight: 轻量模式（仅提取顶点数+坐标，跳过法线/UV/拓扑/FaceSet/winding）。
                     用于 pipeline_compare_asset 对比场景，速度约快 2x。
                     只验证有限仿射点变换，允许镜像及奇异缩放；完整模式仍拒绝二者。

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
        try:
            if lightweight:
                _affine_matrix_rows(world_matrix)
            else:
                _normal_transform_matrix(world_matrix)
        except ValueError as exc:
            raise ValueError(f'{display_path}: {exc}') from exc

        # 顶点坐标转换到世界空间并展平
        vert_positions = []
        for pt in positions:
            world_pt = pt * world_matrix
            coordinates = [float(world_pt[axis]) for axis in range(3)]
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError(f'{display_path}: transformed positions contain non-finite values')
            vert_positions.extend(round(value, 6) for value in coordinates)

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
        normal_param = schema.getNormalsParam()

        # 拓扑数据
        face_counts = list(sample.getFaceCounts())
        face_indices = list(sample.getFaceIndices())
        try:
            normals_fv = transform_normals_fv(
                _read_normals_fv(normal_param, len(face_indices)), world_matrix,
                len(face_indices)
            )
        except ValueError as exc:
            raise ValueError(f'{display_path}: {exc}') from exc

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
        try:
            validate_mesh_entry(meshes[display_path])
        except ValueError as exc:
            raise ValueError(f'{display_path}: {exc}') from exc

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

