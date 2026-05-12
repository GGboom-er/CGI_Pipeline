# skills/blender_extract_materials/blender_extract_materials.py
# ── Blender 材质信息采集（独立技能）──
#
# 从 Blender 场景 cache 组采集 per-face 材质分配，UDIM 按象限拆分，
# 输出 _materials.json。与 maya_apply_materials 配对。

import os
import json
import time
import math

from core.receipt import make_receipt
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir


_SKIP_INPUT_NAMES = {'Factor', 'Fac'}
_COLOR_INPUT_NAMES = ['Base Color', 'Color', 'Emission Color', 'Emission', 'Base']
_ALPHA_INPUT_NAMES = ['Alpha', 'Transmission Weight', 'Transmission']


def _build_dag_path(obj):
    """构建从 cache 开始的 DAG 路径（含 cache）。"""
    path = []
    current = obj
    while current:
        path.append(current.name)
        current = current.parent
    path.reverse()
    try:
        ci = path.index("cache")
        return "|".join(path[ci:])
    except ValueError:
        return "|".join(path)


def _resolve_output_path(payload, params):
    """推导 `_materials.json` 输出路径，优先使用沙盒 `.info`。"""
    output_path = (params.get('output_path') or '').strip()
    if output_path:
        return output_path

    source_path = payload.get('source_path', '') or ''
    source_stem = os.path.splitext(os.path.basename(source_path or 'asset'))[0] or 'asset'

    info_dir = (
        params.get('info_dir')
        or payload.get('info_dir')
        or (payload.get('extra_params') or {}).get('info_dir')
    )
    if not info_dir:
        run_dir = payload.get('run_dir') or (payload.get('extra_params') or {}).get('run_dir')
        if not run_dir and payload.get('task_id'):
            run_dir = create_run_dir(
                payload.get('task_id'),
                payload.get('project', 'default'),
                payload.get('asset_name', 'untitled'),
                payload.get('submitted_at'),
            )
        if run_dir:
            info_dir = os.path.join(str(run_dir), '.info')

    if not info_dir:
        return ''

    return os.path.join(str(info_dir), f'{source_stem}_materials.json')


def _trace_image_from_input(socket, depth=0):
    """递归追踪节点输入端口连线，穿透中间节点找到 TEX_IMAGE 节点。"""
    if not socket or not socket.links or depth > 10:
        return None
    linked_node = socket.links[0].from_node
    from_socket = socket.links[0].from_socket

    if linked_node.type == 'TEX_IMAGE' and linked_node.image:
        return linked_node.image

    if linked_node.type == 'GROUP' and linked_node.node_tree:
        inner_tree = linked_node.node_tree
        for inner_node in inner_tree.nodes:
            if inner_node.type == 'GROUP_OUTPUT':
                out_idx = 0
                for i, out in enumerate(linked_node.outputs):
                    if out == from_socket:
                        out_idx = i
                        break
                if out_idx < len(inner_node.inputs):
                    result = _trace_image_from_input(inner_node.inputs[out_idx], depth + 1)
                    if result:
                        return result
                break

    for inp in linked_node.inputs:
        if inp.links and inp.name not in _SKIP_INPUT_NAMES:
            result = _trace_image_from_input(inp, depth + 1)
            if result:
                return result
    return None


def _eval_color_from_input(socket, depth=0):
    """递归追踪节点链，尝试提取一个近似 RGBA 颜色值。"""
    if depth > 10:
        return None
    if not socket:
        return None

    if not socket.links:
        dv = getattr(socket, 'default_value', None)
        if dv is None:
            return None
        if hasattr(dv, '__iter__'):
            vals = list(dv)
            if len(vals) == 3:
                vals.append(1.0)
            return [round(v, 4) for v in vals[:4]]
        return [round(float(dv), 4)] * 3 + [1.0]

    linked_node = socket.links[0].from_node
    from_socket = socket.links[0].from_socket

    if linked_node.type == 'REROUTE':
        return _eval_color_from_input(linked_node.inputs[0], depth + 1)

    if linked_node.type == 'HUE_SAT':
        import colorsys
        base = _eval_color_from_input(linked_node.inputs.get('Color'), depth + 1)
        if not base:
            base = [0.5, 0.5, 0.5, 1.0]
        h_off = float(linked_node.inputs['Hue'].default_value) - 0.5
        s_fac = float(linked_node.inputs['Saturation'].default_value)
        v_fac = float(linked_node.inputs['Value'].default_value)
        r, g, b = base[0], base[1], base[2]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        h = (h + h_off) % 1.0
        s = min(max(s * s_fac, 0.0), 1.0)
        v = min(max(v * v_fac, 0.0), 1.0)
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return [round(r2, 4), round(g2, 4), round(b2, 4), base[3]]

    if linked_node.type in ('MIX', 'MIX_RGB'):
        a_inp = linked_node.inputs.get('A') or linked_node.inputs.get('Color1')
        b_inp = linked_node.inputs.get('B') or linked_node.inputs.get('Color2')
        fac_inp = linked_node.inputs.get('Factor') or linked_node.inputs.get('Fac')
        fac = 0.5
        if fac_inp and not fac_inp.links:
            fac = float(fac_inp.default_value)
        a_col = _eval_color_from_input(a_inp, depth + 1) if a_inp else None
        b_col = _eval_color_from_input(b_inp, depth + 1) if b_inp else None
        if a_col and b_col:
            return [round(a_col[i] * (1 - fac) + b_col[i] * fac, 4) for i in range(4)]
        return a_col or b_col

    if linked_node.type == 'VALTORGB':
        cr = linked_node.color_ramp
        if cr.elements:
            mid = cr.elements[len(cr.elements) // 2]
            return [round(c, 4) for c in mid.color]
        return None

    if linked_node.type == 'GROUP' and linked_node.node_tree:
        inner_tree = linked_node.node_tree
        for inner_node in inner_tree.nodes:
            if inner_node.type == 'GROUP_OUTPUT':
                out_idx = 0
                for i, out in enumerate(linked_node.outputs):
                    if out == from_socket:
                        out_idx = i
                        break
                if out_idx < len(inner_node.inputs):
                    return _eval_color_from_input(inner_node.inputs[out_idx], depth + 1)
                break
        return None

    if linked_node.type == 'RGB':
        out = from_socket
        if hasattr(out, 'default_value') and hasattr(out.default_value, '__iter__'):
            return [round(v, 4) for v in out.default_value[:4]]

    for inp_name in ('Color', 'Image', 'Base Color', 'Color1', 'A', 'Input'):
        inp = linked_node.inputs.get(inp_name)
        if inp:
            result = _eval_color_from_input(inp, depth + 1)
            if result:
                return result

    return None


def _get_image_path(image):
    """从 Blender Image 对象获取绝对路径。"""
    import bpy
    fp = image.filepath_from_user()
    return os.path.normpath(os.path.abspath(bpy.path.abspath(fp))).replace("\\", "/")


def _derive_tile_tex_path(template_path, tile):
    """将 UDIM 模板路径中的 1001 替换为目标 tile 编号。"""
    tile_str = str(tile)
    if '1001' in template_path:
        candidate = template_path.replace('1001', tile_str)
        if os.path.isfile(candidate):
            return candidate
    return template_path


def _get_face_udim_tiles(mesh, face_indices):
    """按 UV 质心将面分组到 UDIM 象限。"""
    uv_layer = mesh.uv_layers.active
    if not uv_layer:
        return {1001: face_indices}

    tiles = {}
    for fi in face_indices:
        poly = mesh.polygons[fi]
        u_sum = v_sum = 0.0
        count = 0
        for li in poly.loop_indices:
            uv = uv_layer.data[li].uv
            u_sum += uv[0]
            v_sum += uv[1]
            count += 1
        if count > 0:
            uc = u_sum / count
            vc = v_sum / count
            ut = int(math.floor(uc))
            vt = int(math.floor(vc))
            tile = 1001 + max(0, min(ut, 9)) + max(0, vt) * 10
        else:
            tile = 1001
        tiles.setdefault(tile, []).append(fi)
    return tiles


def _resolve_bsdf(shader_node):
    """穿透 ADD_SHADER / MIX_SHADER，找到实际的 BSDF 节点。"""
    if shader_node.type not in ('ADD_SHADER', 'MIX_SHADER'):
        return [shader_node]
    candidates = []
    for inp in shader_node.inputs:
        if inp.name == 'Fac' or not inp.links:
            continue
        child = inp.links[0].from_node
        candidates.extend(_resolve_bsdf(child))
    principled = [n for n in candidates if n.type == 'BSDF_PRINCIPLED']
    return principled if principled else candidates


def _extract_material_color(mat):
    """提取材质的颜色信息。返回 dict: {type, path/value, is_udim}"""
    if not mat or not mat.use_nodes or not mat.node_tree:
        return {"type": "solid", "value": [0.8, 0.8, 0.8, 1.0]}

    for node in mat.node_tree.nodes:
        if node.type != 'OUTPUT_MATERIAL':
            continue
        surface_input = node.inputs.get('Surface')
        if not surface_input or not surface_input.links:
            continue
        root_shader = surface_input.links[0].from_node
        bsdf_nodes = _resolve_bsdf(root_shader)

        for shader_node in bsdf_nodes:
            for input_name in _COLOR_INPUT_NAMES:
                color_input = shader_node.inputs.get(input_name)
                if not color_input:
                    continue
                if color_input.links:
                    image = _trace_image_from_input(color_input)
                    if image:
                        is_udim = getattr(image, 'source', '') == 'TILED'
                        return {
                            "type": "texture",
                            "path": _get_image_path(image),
                            "is_udim": is_udim,
                        }
                    approx = _eval_color_from_input(color_input)
                    if approx:
                        return {"type": "solid", "value": approx}
                dv = color_input.default_value
                return {"type": "solid", "value": [round(dv[0], 4), round(dv[1], 4),
                                                   round(dv[2], 4), round(dv[3], 4) if len(dv) > 3 else 1.0]}

    return {"type": "solid", "value": [0.8, 0.8, 0.8, 1.0]}


def _extract_material_alpha(mat):
    """提取材质的透明度信息。返回 dict: {type, path/value, semantic}"""
    if not mat or not mat.use_nodes or not mat.node_tree:
        return {"type": "value", "value": 1.0, "semantic": "alpha"}

    for node in mat.node_tree.nodes:
        if node.type != 'OUTPUT_MATERIAL':
            continue
        surface_input = node.inputs.get('Surface')
        if not surface_input or not surface_input.links:
            continue
        root_shader = surface_input.links[0].from_node
        bsdf_nodes = _resolve_bsdf(root_shader)

        for shader_node in bsdf_nodes:
            for input_name in _ALPHA_INPUT_NAMES:
                alpha_input = shader_node.inputs.get(input_name)
                if not alpha_input:
                    continue
                semantic = "alpha" if input_name == "Alpha" else "transmission"
                if alpha_input.links:
                    image = _trace_image_from_input(alpha_input)
                    if image:
                        return {"type": "texture", "path": _get_image_path(image), "semantic": semantic}
                    approx = _eval_color_from_input(alpha_input)
                    if approx:
                        return {"type": "value", "value": round(approx[0], 4), "semantic": semantic}
                dv = alpha_input.default_value
                if hasattr(dv, '__iter__'):
                    return {"type": "value", "value": round(float(dv[0]), 4), "semantic": semantic}
                return {"type": "value", "value": round(float(dv), 4), "semantic": semantic}

    return {"type": "value", "value": 1.0, "semantic": "alpha"}


def extract_materials(export_objects):
    """采集 mesh 列表的材质信息，返回 {"materials": {...}} 字典。

    这是核心公共函数，供 blender_export_abc 内联调用或本技能独立调用。
    """
    import bpy
    from collections import defaultdict

    materials_data = {}
    _mat_cache = {}

    for obj in export_objects:
        if obj.type != 'MESH':
            continue

        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        if not mesh:
            continue

        dag_path = _build_dag_path(obj)

        mat_face_map = defaultdict(list)
        for poly in mesh.polygons:
            mi = poly.material_index
            if mi < len(obj.material_slots) and obj.material_slots[mi].material:
                mat_face_map[mi].append(poly.index)

        for mi, face_list in mat_face_map.items():
            slot = obj.material_slots[mi]
            mat_name = slot.material.name

            if mat_name not in _mat_cache:
                _mat_cache[mat_name] = {
                    "color": _extract_material_color(slot.material),
                    "alpha": _extract_material_alpha(slot.material),
                }
            color_info = _mat_cache[mat_name]["color"]
            alpha_info = _mat_cache[mat_name]["alpha"]

            if color_info.get("type") == "texture" and color_info.get("is_udim"):
                tile_groups = _get_face_udim_tiles(mesh, face_list)
                for tile, tile_faces in tile_groups.items():
                    entry_name = f"{mat_name}_{tile}"
                    if entry_name not in materials_data:
                        materials_data[entry_name] = {
                            "color": {
                                "type": "texture",
                                "path": _derive_tile_tex_path(color_info["path"], tile),
                                "is_udim": False,
                            },
                            "alpha": alpha_info,
                            "faces_by_mesh": {},
                        }
                    materials_data[entry_name]["faces_by_mesh"].setdefault(dag_path, []).extend(tile_faces)
            else:
                if mat_name not in materials_data:
                    materials_data[mat_name] = {
                        "color": color_info,
                        "alpha": alpha_info,
                        "faces_by_mesh": {},
                    }
                materials_data[mat_name]["faces_by_mesh"].setdefault(dag_path, []).extend(face_list)

        eval_obj.to_mesh_clear()

    return {"materials": materials_data}


def execute(payload: dict) -> dict:
    t0 = time.time()
    import bpy

    params = payload.get('parameters', {})
    output_path = _resolve_output_path(payload, params)
    cache_group_name = (params.get('cache_group') or '').strip()

    if not output_path:
        return make_receipt('blender_extract_materials', 'ERROR', t0,
                           error='无法确定输出路径: output_path 为空，且无法从任务沙盒推导 .info 目录')

    if not cache_group_name:
        return make_receipt('blender_extract_materials', 'ERROR', t0,
                           summary_input=os.path.basename(output_path),
                           error='缺少必填参数 cache_group。请由 workflow 从项目配置传入。')

    if is_protected_path(output_path):
        return make_receipt('blender_extract_materials', 'BLOCKED', t0,
                           error=f'输出路径 "{output_path}" 位于只读/受保护区域，禁止写入。')

    blender_obj_name = cache_group_name.split('|')[-1]
    cache_grp = bpy.data.objects.get(blender_obj_name)
    if not cache_grp:
        objs = [o.name for o in bpy.data.objects][:20]
        return make_receipt('blender_extract_materials', 'ERROR', t0,
                           error=f"场景中未找到 '{cache_group_name}' 组。当前对象: {objs}")

    export_objects = []

    def collect_hierarchy(parent):
        export_objects.append(parent)
        for child in parent.children:
            collect_hierarchy(child)

    collect_hierarchy(cache_grp)

    mat_data = extract_materials(export_objects)
    mat_count = len(mat_data.get("materials", {}))

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    with open(output_path, 'w', encoding='utf-8') as fp:
        json.dump(mat_data, fp, ensure_ascii=False, indent=2)

    return make_receipt(
        skill_id='blender_extract_materials',
        status='SUCCESS',
        start_time=t0,
        summary_input=os.path.basename(output_path),
        summary_action='材质信息采集',
        summary_count=mat_count,
        summary_label='材质条目',
        outputs={'output_path': output_path},
    )
