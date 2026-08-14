# ── PyAlembic → Maya Mesh 纯数据构建 ──

import os
import time
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om2

from cgi_pipeline.core.receipt import make_receipt


MAX_DETAIL_ITEMS = 20


def create_mesh(name, mesh_data, parent_path=""):
    """从 mesh_data 字典构建单个 Maya mesh 节点。

    参数:
        name: 目标 transform 名称
        mesh_data: dict，包含 vertices/vert_positions/face_counts/face_indices/u_array/v_array/uv_indices
        parent_path: 父节点 DAG 路径（可选）

    返回:
        str: 构建后的节点 fullPath，失败返回 None
    """
    num_v = mesh_data.get("vertices", 0)
    pos = mesh_data.get("vert_positions", [])
    fc = mesh_data.get("face_counts", [])
    fi = list(mesh_data.get("face_indices", []))
    if not (num_v and pos and fc and fi):
        return None

    pos_arr = om2.MFloatPointArray([(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]) for i in range(num_v)])

    fn = om2.MFnMesh()
    new_obj = fn.create(pos_arr, om2.MIntArray(fc), om2.MIntArray(fi))
    temp_name = cmds.rename(om2.MFnDependencyNode(new_obj).name(), "TEMP_" + name)

    warnings = []
    uv_ids = list(mesh_data.get("uv_indices", []))

    try:
        cmds.sets(temp_name, edit=True, forceElement="initialShadingGroup")
    except Exception as e:
        warnings.append(f"分配 initialShadingGroup 失败: {e}")

    # UV 写入
    u_arr = mesh_data.get("u_array", [])
    v_arr = mesh_data.get("v_array", [])
    if u_arr and v_arr and uv_ids:
        shapes = cmds.listRelatives(temp_name, shapes=True, fullPath=True)
        if shapes:
            sel = om2.MSelectionList()
            sel.add(shapes[0])
            dag = sel.getDagPath(0)
            fn2 = om2.MFnMesh(dag)
            u = om2.MFloatArray(u_arr)
            v = om2.MFloatArray(v_arr)
            fn2.setUVs(u, v, "map1")
            uv_c = om2.MIntArray(fc)
            uv_i = om2.MIntArray(uv_ids)
            fn2.assignUVs(uv_c, uv_i, "map1")

    # 与 Maya AbcImport 一致：只消费 reader 已按面序转换的显式 normals。
    # 法线缺失或不可用不是构建失败条件，Maya 会按现有拓扑自动生成。
    normals_fv = list(mesh_data.get("normals_fv", []))
    if normals_fv and len(normals_fv) == len(fi) * 3:
        try:
            shapes = cmds.listRelatives(
                temp_name, shapes=True, fullPath=True, ni=True
            ) or []
            if shapes:
                sel = om2.MSelectionList()
                sel.add(shapes[0])
                mesh_fn = om2.MFnMesh(sel.getDagPath(0))
                normals = om2.MVectorArray()
                for i in range(0, len(normals_fv), 3):
                    normals.append(om2.MVector(*normals_fv[i:i + 3]))
                face_ids = om2.MIntArray()
                vertex_ids = om2.MIntArray()
                offset = 0
                for face_id, face_count in enumerate(fc):
                    for _unused in range(face_count):
                        face_ids.append(face_id)
                        vertex_ids.append(fi[offset])
                        offset += 1
                mesh_fn.setFaceVertexNormals(normals, face_ids, vertex_ids)
        except Exception as e:
            warnings.append(f"显式法线写入失败，已回退 Maya 自动法线: {e}")

    # 放入父层级
    if parent_path:
        temp_name = cmds.parent(temp_name, parent_path)[0]
    final_name = cmds.rename(temp_name, name)

    # 构建 fullPath
    final = f"{parent_path}|{name}" if parent_path else final_name

    # Shape 规范命名
    shapes = cmds.listRelatives(final, shapes=True, fullPath=True) or []
    for sh in shapes:
        sh_short = sh.split("|")[-1]
        ideal_name = f"{name}Shape"
        if sh_short != ideal_name:
            try:
                cmds.rename(sh, ideal_name)
            except Exception as e:
                warnings.append(f"Shape 重命名失败 {sh_short} -> {ideal_name}: {e}")

    return final, warnings


def _ensure_hierarchy(parts, root_group=""):
    """确保 DAG 层级存在，返回最深层的 fullPath。

    参数:
        parts: 层级名称列表，如 ["cache", "grp", "sub_grp"]
        root_group: 可选的顶层根组（替代 parts[0]）
    """
    if not parts:
        return root_group or ""

    def _as_long_path(node):
        matches = cmds.ls(node, long=True) or []
        return matches[0] if matches else node

    if root_group:
        current = root_group
        if not cmds.objExists(current):
            current = cmds.group(empty=True, name=current)
        current = _as_long_path(current)
    else:
        current = parts[0]
        if not cmds.objExists(current):
            current = cmds.group(empty=True, name=parts[0])
        current = _as_long_path(current)
        parts = parts[1:]

    for part in parts:
        child_path = f"{current}|{part}"
        if not cmds.objExists(child_path):
            child_path = cmds.group(empty=True, name=part, parent=current)
        current = _as_long_path(child_path)

    return current


def _parse_dag_parts(dag_path):
    """从 DAG 路径解析出层级 parts 和 mesh 名称。

    输入如 "|cache|body_grp|body|bodyShape" 或 "cache|body_grp|body|bodyShape"
    返回: (hierarchy_parts, mesh_name)
        hierarchy_parts = ["cache", "body_grp"]
        mesh_name = "body"
    """
    path = dag_path.strip("|")
    segments = path.split("|")

    # 去掉 Shape 后缀的节点（最后一个通常是 Shape）
    if segments and segments[-1].endswith("Shape"):
        segments = segments[:-1]

    if len(segments) <= 1:
        return [], segments[0] if segments else "mesh"

    return segments[:-1], segments[-1]


def execute(payload: dict) -> dict:
    t0 = time.time()
    try:
        params = payload.get('parameters', {})
        abc_path = params.get('abc_path', '')

        if not abc_path:
            return make_receipt('maya_build_mesh_from_abc', 'ERROR', t0,
                               error='abc_path 参数为空')

        if not os.path.isfile(abc_path):
            return make_receipt('maya_build_mesh_from_abc', 'ERROR', t0,
                               error=f'ABC 文件不存在: {abc_path}')

        mesh_filter = params.get('mesh_filter', None)
        parent_group = params.get('parent_group', '')

        # 读取 ABC 数据
        from cgi_pipeline.core.abc_reader import read_abc_as_info
        info = read_abc_as_info(abc_path)
        all_meshes = info.get('meshes', {})

        if not all_meshes:
            return make_receipt('maya_build_mesh_from_abc', 'ERROR', t0,
                               error='ABC 文件中未找到任何 mesh')

        # 过滤
        if mesh_filter:
            filter_set = set(mesh_filter)
            target_meshes = {k: v for k, v in all_meshes.items() if k in filter_set}
        else:
            target_meshes = all_meshes

        # 构建
        cmds.undoInfo(openChunk=True, chunkName="build_mesh_from_abc")
        built = []
        skipped = []
        items = []

        try:
            for dag_path, mesh_data in target_meshes.items():
                hierarchy_parts, mesh_name = _parse_dag_parts(dag_path)

                # 还原层级
                if parent_group:
                    parent_path = _ensure_hierarchy(hierarchy_parts, root_group=parent_group)
                elif hierarchy_parts:
                    parent_path = _ensure_hierarchy(hierarchy_parts)
                else:
                    parent_path = ""

                # 检查同名冲突
                check_path = f"{parent_path}|{mesh_name}" if parent_path else mesh_name
                if cmds.objExists(check_path):
                    skipped.append(mesh_name)
                    continue

                result, build_warnings = create_mesh(mesh_name, mesh_data, parent_path)
                if result:
                    built.append(result)
                    if len(items) < MAX_DETAIL_ITEMS:
                        detail = "BUILT"
                        if build_warnings:
                            detail += "; " + "; ".join(build_warnings)
                        items.append({"name": mesh_name, "detail": detail})
        finally:
            cmds.undoInfo(closeChunk=True)

        # 报告
        if skipped and len(items) < MAX_DETAIL_ITEMS:
            for s in skipped[:MAX_DETAIL_ITEMS - len(items)]:
                items.append({"label": s, "status": "SKIPPED (同名已存在)"})

        summary_parts = [f"构建 {len(built)} mesh"]
        if skipped:
            summary_parts.append(f"跳过 {len(skipped)} 同名")

        return make_receipt(
            api_id='maya_build_mesh_from_abc',
            status='SUCCESS',
            start_time=t0,
            summary_input=os.path.basename(abc_path),
            summary_action=', '.join(summary_parts),
            summary_count=len(built),
            summary_label='mesh',
            items=items,
            output={'result': {'built_meshes': built}},
        )

    except Exception as e:
        return make_receipt(
            api_id='maya_build_mesh_from_abc',
            status='ERROR',
            start_time=t0,
            error=f"执行失败: {str(e)}\n{traceback.format_exc()}"
        )
