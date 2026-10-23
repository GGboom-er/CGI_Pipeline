# ── Blender 材质信息采集（独立API）──
#
# 从 Blender 场景 cache 组采集 per-face Color/Alpha 分配。只有 UDIM 输入按
# 象限拆分；普通贴图直接复用现有文件，不烘焙或创建任何资产贴图。

import os
import json
import time
import math
import hashlib
import re

from cgi_pipeline.core.receipt import make_item, make_receipt
from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.run_archive import create_run_dir
from cgi_pipeline.core.material_semantics import should_use_as_color_texture


_SKIP_INPUT_NAMES = {'Factor', 'Fac'}
_COLOR_INPUT_NAMES = ['Base Color', 'Color', 'Emission Color', 'Emission', 'Base']

# UDIM 象限有 UV 但缺贴图时的降级色。品红，在视口里极显眼，
# 便于贴图师定位哪些面缺图；不与任何正常贴图内容混淆。
MISSING_TILE_COLOR = (1.0, 0.0, 1.0)

# mesh 完全没有材质 slot 时的降级色。中性灰，与缺贴图的品红区分：
# 无材质件多为 hiddenMesh 一类不参与渲染的附件，不需要醒目告警。
NO_MATERIAL_COLOR = (0.5, 0.5, 0.5)


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


def _socket_list(sockets):
    if hasattr(sockets, "values"):
        return list(sockets.values())
    return list(sockets or [])


def _socket_index(sockets, target):
    for index, socket in enumerate(_socket_list(sockets)):
        if socket is target or socket == target:
            return index
    return -1


def _trace_image_sources(socket, depth=0, group_stack=(), follow_factors=False):
    """Return existing images influencing a socket, preserving graph priority.

    GROUP_INPUT needs the outer group instance to map an inner interface socket
    back to the actual linked input. This is what makes SP(main) discoverable
    without baking or inventing a texture.
    """
    if not socket or not getattr(socket, "links", None) or depth > 16:
        return []
    link = socket.links[0]
    linked_node = link.from_node
    from_socket = link.from_socket

    if linked_node.type == 'TEX_IMAGE' and linked_node.image:
        channel = "alpha" if from_socket.name.casefold() == "alpha" else "color"
        return [(linked_node.image, channel)]

    if linked_node.type == 'GROUP' and linked_node.node_tree:
        output_index = _socket_index(linked_node.outputs, from_socket)
        group_output = next(
            (node for node in linked_node.node_tree.nodes if node.type == 'GROUP_OUTPUT'),
            None,
        )
        inner_inputs = _socket_list(group_output.inputs) if group_output else []
        if 0 <= output_index < len(inner_inputs):
            return _trace_image_sources(
                inner_inputs[output_index], depth + 1,
                group_stack + (linked_node,), follow_factors
            )
        return []

    if linked_node.type == 'GROUP_INPUT' and group_stack:
        input_index = _socket_index(linked_node.outputs, from_socket)
        outer_group = group_stack[-1]
        outer_inputs = _socket_list(outer_group.inputs)
        if 0 <= input_index < len(outer_inputs):
            return _trace_image_sources(
                outer_inputs[input_index], depth + 1,
                group_stack[:-1], follow_factors
            )
        return []

    sources = []
    for node_input in _socket_list(getattr(linked_node, "inputs", [])):
        is_factor = getattr(node_input, "name", "") in _SKIP_INPUT_NAMES
        if getattr(node_input, "links", None) and (follow_factors or not is_factor):
            sources.extend(
                _trace_image_sources(
                    node_input, depth + 1, group_stack, follow_factors
                )
            )
    return sources


def _trace_image_from_input(socket, depth=0):
    """Compatibility wrapper returning the graph's primary existing image."""
    sources = _trace_image_sources(socket, depth)
    return sources[0][0] if sources else None


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


def _find_origin_dir():
    import bpy
    blend_path = bpy.data.filepath
    if not blend_path:
        return None
    blend_dir = os.path.dirname(blend_path)
    blend_name = os.path.basename(blend_path)

    # 1. 优先从 .info/staged_origins.json
    origins_path = os.path.join(blend_dir, ".info", "staged_origins.json")
    if os.path.isfile(origins_path):
        try:
            with open(origins_path, "r", encoding="utf-8") as f:
                origins = json.load(f)
            if origins.get(blend_name):
                return origins[blend_name]
        except Exception:
            pass

    # 2. 从 .info/run_state.json -> audit/{wf_id}.json 读取 FILE_STAGED
    state_path = os.path.join(blend_dir, ".info", "run_state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                wf_id = json.load(f).get("wf_id")
            if wf_id:
                from cgi_pipeline.core.bootstrap import cfg as _pipeline_cfg
                audit_path = os.path.join(str(_pipeline_cfg.PROJECT_ROOT), "audit", f"{wf_id}.json")
                if os.path.isfile(audit_path):
                    with open(audit_path, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                if entry.get("status") == "FILE_STAGED":
                                    det = json.loads(entry.get("detail", "{}"))
                                    origin = det.get("origin", "")
                                    if origin and os.path.basename(origin) == blend_name:
                                        return os.path.dirname(origin).replace("\\", "/")
                            except Exception:
                                pass
        except Exception:
            pass

    return None


def _x_path_for_s_texture(path, details=None):
    """S textures use only their exact X counterpart, never another search root."""
    path = _normalize_color_path(path)
    if path[:3].casefold() != 's:/':
        return None
    candidate = 'X:' + path[2:]
    if details is not None:
        details.update(path_policy='s_to_x', remapped_from=path)
    return candidate


def _get_image_path(image, details=None):
    """从 Blender Image 对象获取绝对路径，支持沙盒隔离下穿透回原始工程贴图路径。"""
    import bpy
    fp = image.filepath_from_user()
    path = os.path.normpath(os.path.abspath(bpy.path.abspath(fp))).replace("\\", "/")
    x_path = _x_path_for_s_texture(path, details)
    if x_path is not None:
        return x_path
    if os.path.isfile(path):
        return path

    origin_dir = _find_origin_dir()
    if origin_dir:
        raw_fp = image.filepath.replace("\\", "/")
        if raw_fp.startswith("//"):
            raw_fp = raw_fp[2:]
        candidate = os.path.normpath(os.path.join(origin_dir, raw_fp)).replace("\\", "/")
        x_path = _x_path_for_s_texture(candidate, details)
        if x_path is not None:
            return x_path
        if os.path.isfile(candidate):
            return candidate
        tex_sub = os.path.normpath(os.path.join(origin_dir, "output", "textures", os.path.basename(raw_fp))).replace("\\", "/")
        if os.path.isfile(tex_sub):
            return tex_sub

    return path


def _derive_tile_tex_path(template_path, tile, real_tiles=None, *, allow_origin_fallback=True):
    """把 UDIM 模板路径解析成目标 tile 的准确文件路径。"""
    template_path = _normalize_color_path(template_path)
    directory = os.path.dirname(template_path)
    filename = os.path.basename(template_path)
    tile_str = str(tile)

    origin_dir = _find_origin_dir() if allow_origin_fallback else None

    def _pick_existing(candidate):
        if os.path.isfile(candidate):
            return candidate
        if origin_dir:
            cand_name = os.path.basename(candidate)
            fallback = os.path.normpath(os.path.join(origin_dir, "output", "textures", cand_name)).replace("\\", "/")
            if os.path.isfile(fallback):
                return fallback
        return None

    if '<UDIM>' in filename:
        candidate = _normalize_color_path(
            os.path.join(directory, filename.replace('<UDIM>', tile_str))
        )
        existing = _pick_existing(candidate)
        if existing:
            return existing
        raise ValueError(f"UDIM {tile} 对应的 Color 贴图不存在: {candidate}")

    anchors = [str(t) for t in (real_tiles or [])]
    for anchor in anchors:
        pattern = rf'(?<!\d){re.escape(anchor)}(?!\d)'
        if re.search(pattern, filename):
            candidate = _normalize_color_path(
                os.path.join(directory, re.sub(pattern, tile_str, filename, count=1))
            )
            existing = _pick_existing(candidate)
            if existing:
                return existing
            raise ValueError(f"UDIM {tile} 对应的 Color 贴图不存在: {candidate}")
    raise ValueError(
        f"无法从 Color 路径定位 UDIM {tile}: {template_path}；"
        f"Blender 声明 tiles={sorted(int(t) for t in (real_tiles or []))}"
    )


def _get_face_udim_tiles(mesh, face_indices):
    """按 UV 质心将面分组到 UDIM 象限。"""
    uv_layer = mesh.uv_layers.active
    if not uv_layer:
        raise ValueError("使用 Color 贴图的 mesh 没有活动 UV set")

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
        if count == 0:
            raise ValueError(f"face {fi} 没有 UV loop 数据")
        uc = u_sum / count
        vc = v_sum / count
        if not math.isfinite(uc) or not math.isfinite(vc):
            raise ValueError(f"face {fi} 的 UV 不是有限数值: ({uc}, {vc})")
        ut = int(math.floor(uc))
        vt = int(math.floor(vc))
        if ut < 0 or ut > 9 or vt < 0:
            raise ValueError(f"face {fi} 位于非法 UDIM 象限: U={ut}, V={vt}")
        tile = 1001 + ut + vt * 10
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


def _texture_info_from_input(socket, for_color):
    sources = []
    seen = set()
    for image, channel in _trace_image_sources(socket, follow_factors=not for_color):
        path_details = {}
        path = _get_image_path(image, path_details)
        if for_color and not should_use_as_color_texture(path):
            continue
        identity = path.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        sources.append((image, path, channel, path_details))
    if not sources:
        return None

    image, path, channel, path_details = sources[0]
    is_udim = getattr(image, 'source', '') == 'TILED'
    info = {
        "type": "texture",
        "path": path,
        "is_udim": is_udim,
        "udim_tiles": [tile.number for tile in image.tiles] if is_udim else [],
        "approximation": "direct" if len(sources) == 1 else "primary_image",
        **path_details,
    }
    if len(sources) > 1:
        info["source_image_count"] = len(sources)
    if not for_color:
        info["channel"] = "alpha" if channel == "alpha" else "luminance"
    return info


def _shader_inputs(mat):
    for node in mat.node_tree.nodes:
        if node.type != 'OUTPUT_MATERIAL':
            continue
        surface_input = node.inputs.get('Surface')
        if not surface_input or not surface_input.links:
            continue
        root_shader = surface_input.links[0].from_node
        yield surface_input, _resolve_bsdf(root_shader)


def _solid_color(socket):
    value = getattr(socket, "default_value", [0.8, 0.8, 0.8, 1.0])
    values = list(value) if hasattr(value, '__iter__') else [float(value)] * 3
    if len(values) == 3:
        values.append(1.0)
    return {
        "type": "solid",
        "value": [round(float(component), 4) for component in values[:4]],
    }


def _extract_material_color(mat):
    """Extract the best existing Color representation without baking."""
    if not mat:
        return {"type": "unresolved", "reason": "material 为空"}
    if not mat.use_nodes or not mat.node_tree:
        values = list(getattr(mat, "diffuse_color", [0.8, 0.8, 0.8, 1.0]))
        return {
            "type": "solid",
            "value": [round(float(value), 4) for value in values[:4]],
        }

    for surface_input, bsdf_nodes in _shader_inputs(mat):
        # A connected Emission Color is more informative than an unconnected
        # Base Color default. Check every linked candidate before any default.
        for shader_node in bsdf_nodes:
            for input_name in _COLOR_INPUT_NAMES:
                color_input = shader_node.inputs.get(input_name)
                if not color_input or not color_input.links:
                    continue
                texture = _texture_info_from_input(color_input, for_color=True)
                if texture:
                    return texture
                approx = _eval_color_from_input(color_input)
                if approx:
                    return {
                        "type": "solid",
                        "value": approx,
                        "approximation": "node_value",
                    }

        for shader_node in bsdf_nodes:
            for input_name in _COLOR_INPUT_NAMES:
                color_input = shader_node.inputs.get(input_name)
                if color_input and not color_input.links:
                    return _solid_color(color_input)

        surface_color = _eval_color_from_input(surface_input)
        if surface_color:
            return {"type": "solid", "value": surface_color}

    return {
        "type": "unresolved",
        "reason": "Material Output 的 Surface 未解析到 Color 输入",
    }


def _extract_material_alpha(mat):
    """Extract final shader Alpha only; Transmission is intentionally ignored."""
    if not mat:
        return {"type": "value", "value": 1.0}
    if not mat.use_nodes or not mat.node_tree:
        values = list(getattr(mat, "diffuse_color", [0.8, 0.8, 0.8, 1.0]))
        return {
            "type": "value",
            "value": round(float(values[3] if len(values) > 3 else 1.0), 4),
        }

    for _surface_input, bsdf_nodes in _shader_inputs(mat):
        for shader_node in bsdf_nodes:
            alpha_input = shader_node.inputs.get('Alpha')
            if not alpha_input:
                continue
            if alpha_input.links:
                texture = _texture_info_from_input(alpha_input, for_color=False)
                if texture:
                    return texture
                value = getattr(alpha_input, "default_value", 1.0)
                if hasattr(value, '__iter__'):
                    value = list(value)[0]
                return {
                    "type": "value",
                    "value": round(float(value), 4),
                    "approximation": "socket_default",
                }
            value = getattr(alpha_input, "default_value", 1.0)
            if hasattr(value, '__iter__'):
                value = list(value)[0]
            return {"type": "value", "value": round(float(value), 4)}
    return {"type": "value", "value": 1.0}


def _collect_scene_color_sets():
    """Collect globally declared UDIM Color images from the Blender scene."""
    import bpy

    color_sets = []
    for image in bpy.data.images:
        if getattr(image, "source", "") != "TILED":
            continue
        path_details = {}
        path = _get_image_path(image, path_details)
        if not should_use_as_color_texture(path):
            continue
        tiles = sorted({int(tile.number) for tile in image.tiles})
        if not tiles:
            continue
        color_sets.append({
            "type": "texture",
            "path": path,
            "is_udim": True,
            "udim_tiles": tiles,
            **path_details,
        })
    return color_sets


def _select_scene_color_set(color_sets, required_tiles, context="mesh"):
    """Resolve an unlinked material by best tile coverage among global Color sets.

    不要求某一套贴图覆盖全部 UV 象限：只有一个面漏进没人画的象限时，仍按覆盖度
    最高的那套解析，缺的象限交由 _output_color 降级成纯色。歧义（多套并列最高）
    和完全无覆盖仍然报错——那是选错贴图或材质根本没有 Color 源，不是缺一格图。
    """
    required = {int(tile) for tile in required_tiles}
    scored = []
    for color_info in color_sets:
        declared = {int(tile) for tile in color_info.get("udim_tiles", [])}
        covered = len(required & declared)
        if covered:
            scored.append((covered, color_info))

    if not scored:
        return {
            "type": "solid",
            "value": [0.8, 0.8, 0.8, 1.0],
            "approximation": "unresolved_solid_fallback",
        }

    best = max(score for score, _ in scored)
    candidates = [info for score, info in scored if score == best]
    if len(candidates) == 1:
        return dict(candidates[0])

    paths = sorted(_normalize_color_path(item.get("path")) for item in candidates)
    raise ValueError(
        f"{context} 的材质无法解析 Color，UV tiles={sorted(required)} 同时匹配多套贴图: "
        f"{paths}"
    )


def _normalize_color_path(path):
    return os.path.normpath(str(path or "")).replace("\\", "/")

def _material_requires_tiles(color_info, alpha_info):
    return any(
        info.get("type") == "texture" and info.get("is_udim")
        for info in (color_info, alpha_info)
    )


def _output_texture(info, tile, label, missing=None):
    """解析该 tile 的贴图路径；UDIM 缺图时返回 None 交调用方降级。

    UDIM 象限有 UV 但没有对应贴图文件（未声明该 tile，或声明了但文件不在磁盘）
    不再中止整链：登记到 missing 后返回 None，由 _output_color / _output_alpha
    降级成 solid / 不透明，让链跑完并在 receipt 里列出降级项。
    非 UDIM 贴图缺文件仍然报错——那是路径写错，不是象限缺图。
    """
    path = _normalize_color_path(info.get("path", ""))
    if info.get("is_udim"):
        try:
            path = _derive_tile_tex_path(
                path, tile, info.get("udim_tiles"),
                allow_origin_fallback=info.get('path_policy') != 's_to_x',
            )
        except ValueError as exc:
            if missing is not None:
                missing.append({
                    "label": label,
                    "tile": tile,
                    "declared_tiles": sorted(
                        int(t) for t in (info.get("udim_tiles") or [])
                    ),
                    "template_path": path,
                    "reason": str(exc),
                })
            return None
    elif not os.path.isfile(path):
        if info.get('path_policy') == 's_to_x':
            if missing is not None:
                missing.append({'label': label, 'tile': None, 'declared_tiles': [],
                                'template_path': path,
                                'reason': 'X counterpart missing; texture not packaged',
                                'source_path': info.get('remapped_from', '')})
            return None
        raise ValueError(f"{label} 贴图不存在: {path}")
    output = {"type": "texture", "path": _normalize_color_path(path)}
    for key in ("channel", "approximation", "source_image_count"):
        if key in info:
            output[key] = info[key]
    return output


def _output_color(color_info, tile=None, missing=None):
    if color_info.get("type") == "texture":
        output = _output_texture(color_info, tile, "Color", missing)
        if output is not None:
            return output
        # UDIM 缺图降级：醒目报错色，便于在视口里定位缺贴图的面
        return {
            "type": "solid",
            "value": list(MISSING_TILE_COLOR),
            "approximation": "missing_x_texture" if color_info.get('path_policy') == 's_to_x' else "missing_udim_tile",
        }
    values = list(color_info.get("value", [0.8, 0.8, 0.8, 1.0]))
    output = {
        "type": "solid",
        "value": [round(float(value), 4) for value in values[:3]],
    }
    if color_info.get("approximation"):
        output["approximation"] = color_info["approximation"]
    return output


def _output_alpha(alpha_info, tile=None, missing=None):
    if alpha_info.get("type") == "texture":
        output = _output_texture(alpha_info, tile, "Alpha", missing)
        if output is not None:
            return output
        # UDIM 缺图降级：不透明，不猜测遮挡关系
        return {
            "type": "value",
            "value": 1.0,
            "approximation": "missing_x_texture" if alpha_info.get('path_policy') == 's_to_x' else "missing_udim_tile",
        }
    output = {
        "type": "value",
        "value": round(float(alpha_info.get("value", 1.0)), 4),
    }
    if alpha_info.get("approximation"):
        output["approximation"] = alpha_info["approximation"]
    return output


def _entry_identity(color, alpha):
    return (
        json.dumps(color, sort_keys=True, ensure_ascii=True),
        json.dumps(alpha, sort_keys=True, ensure_ascii=True),
    )


def _material_base_name(color_info, alpha_info, tile=None, degraded=False):
    if degraded:
        # 降级条目单独命名，便于在 Maya outliner / hypershade 里一眼认出缺图象限
        return f"missingTex_{tile}" if tile is not None else "missingTex"

    texture_info = color_info if color_info.get("type") == "texture" else alpha_info
    if texture_info.get("type") == "texture":
        stem = os.path.splitext(os.path.basename(texture_info.get("path", "")))[0] or "texture"
        stem = stem.replace("<UDIM>", "UDIM")
        for declared_tile in texture_info.get("udim_tiles") or []:
            stem = re.sub(
                rf'(?<!\d){int(declared_tile)}(?!\d)', 'UDIM', stem, count=1
            )
        return f"{stem}_{tile}" if tile is not None else stem

    rgb = list(color_info.get("value", [0.8, 0.8, 0.8]))[:3]
    encoded = "_".join(
        f"{max(0, min(255, round(float(value) * 255))):02x}" for value in rgb
    )
    return f"solid_{encoded}"


def _no_material_reason_text(item):
    """把无材质成因写成人读的一句话，供 receipt items 与 warnings 共用。"""
    slot_count = item.get("slot_count", 0)
    if item.get("reason") == "empty_slot" or slot_count:
        return f"材质 slot 为空（slot 数={slot_count}，槽内无材质）"
    return "无材质 slot（slot 数=0）"


def _unique_entry_name(base_name, identity, entries):
    if base_name not in entries:
        return base_name
    suffix = hashlib.sha1(repr(identity).encode("utf-8")).hexdigest()[:8]
    return f"{base_name}_{suffix}"


def extract_materials(export_objects):
    """Collect final Color/Alpha entries and per-face ownership as schema v3.

    这是核心公共函数，供 blender_export_abc 内联调用或本API独立调用。
    """
    import bpy
    from collections import Counter, defaultdict

    materials = {}
    ids_by_identity = {}
    material_cache = {}
    missing_tile_items = []
    no_material_items = []
    empty_mesh_items = []
    path_remaps = {}
    scene_color_sets = _collect_scene_color_sets()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_count = 0

    for obj in export_objects:
        if obj.type != 'MESH':
            continue
        mesh_count += 1

        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        if not mesh:
            raise ValueError(f"{_build_dag_path(obj)} 无法取得求值后的 mesh")

        try:
            dag_path = _build_dag_path(obj)
            if not mesh.polygons:
                # 无面 mesh（空壳/定位件）没有逐面材质可采，跳过而非中止整链；
                # 几何本身仍由 blender_export_abc 照常导出。
                empty_mesh_items.append(dag_path)
                continue
            mat_face_map = defaultdict(list)
            unassigned_faces = []
            for poly in mesh.polygons:
                mi = poly.material_index
                if mi < len(obj.material_slots) and obj.material_slots[mi].material:
                    mat_face_map[mi].append(poly.index)
                else:
                    # 没有材质 slot 或 slot 为空（如 hiddenMesh 一类无材质件）：
                    # 不丢面、不中止，降级成纯色条目，保证逐面覆盖完整。
                    unassigned_faces.append(poly.index)

            assigned_faces = []
            if unassigned_faces:
                color = {
                    "type": "solid",
                    "value": list(NO_MATERIAL_COLOR),
                    "approximation": "no_material_slot",
                }
                alpha = {"type": "value", "value": 1.0}
                identity = _entry_identity(color, alpha)
                entry_id = ids_by_identity.get(identity)
                if entry_id is None:
                    entry_id = _unique_entry_name("noMaterial", identity, materials)
                    ids_by_identity[identity] = entry_id
                    materials[entry_id] = {
                        "color": color,
                        "alpha": alpha,
                        "faces_by_mesh": {},
                        "source": {
                            "mode": "no_material",
                            "tile": None,
                            "materials": [],
                        },
                    }
                materials[entry_id]["faces_by_mesh"].setdefault(dag_path, []).extend(
                    unassigned_faces
                )
                assigned_faces.extend(unassigned_faces)
                slot_count = len(obj.material_slots)
                no_material_items.append({
                    "mesh": dag_path,
                    "face_count": len(unassigned_faces),
                    "slot_count": slot_count,
                    # 两种成因分开记：完全没有 slot，或 slot 存在但没装材质。
                    # 前者是建模时就没建材质，后者多为材质被删后留下空槽。
                    "reason": "no_slot" if slot_count == 0 else "empty_slot",
                })

            for mi, face_list in mat_face_map.items():
                material = obj.material_slots[mi].material
                if material.name not in material_cache:
                    material_cache[material.name] = {
                        "color": _extract_material_color(material),
                        "alpha": _extract_material_alpha(material),
                    }
                color_info = material_cache[material.name]["color"]
                alpha_info = material_cache[material.name]["alpha"]
                tile_groups = None

                if color_info.get("type") == "unresolved":
                    tile_groups = _get_face_udim_tiles(mesh, face_list)
                    color_info = _select_scene_color_set(
                        scene_color_sets,
                        tile_groups,
                        context=f"{dag_path} / {material.name}",
                    )

                if _material_requires_tiles(color_info, alpha_info):
                    tile_groups = tile_groups or _get_face_udim_tiles(mesh, face_list)
                else:
                    tile_groups = {None: face_list}

                for info in (color_info, alpha_info):
                    if info.get('path_policy') == 's_to_x':
                        path_remaps[info['remapped_from']] = info['path']

                for tile, entry_faces in tile_groups.items():
                    tile_missing = []
                    color = _output_color(color_info, tile, tile_missing)
                    alpha = _output_alpha(alpha_info, tile, tile_missing)
                    for record in tile_missing:
                        missing_tile_items.append({
                            "mesh": dag_path,
                            "material": material.name,
                            "face_count": len(entry_faces),
                            **record,
                        })
                    identity = _entry_identity(color, alpha)
                    entry_id = ids_by_identity.get(identity)
                    if entry_id is None:
                        entry_id = _unique_entry_name(
                            _material_base_name(
                                color_info, alpha_info, tile, bool(tile_missing)
                            ),
                            identity,
                            materials,
                        )
                        ids_by_identity[identity] = entry_id
                        materials[entry_id] = {
                            "color": color,
                            "alpha": alpha,
                            "faces_by_mesh": {},
                            "source": {
                                "mode": "udim" if tile is not None else "direct",
                                "tile": tile,
                                "materials": [material.name],
                            },
                        }
                    elif material.name not in materials[entry_id]["source"]["materials"]:
                        materials[entry_id]["source"]["materials"].append(material.name)
                    materials[entry_id]["faces_by_mesh"].setdefault(
                        dag_path, []
                    ).extend(entry_faces)
                    assigned_faces.extend(entry_faces)

            expected_faces = set(range(len(mesh.polygons)))
            assigned_counts = Counter(assigned_faces)
            assigned_set = set(assigned_counts)
            duplicate_faces = sorted(face for face, count in assigned_counts.items() if count > 1)
            missing_faces = sorted(expected_faces - assigned_set)
            if missing_faces or duplicate_faces:
                raise ValueError(
                    f"{dag_path} 材质逐面覆盖无效: "
                    f"missing={missing_faces[:20]}, duplicate={duplicate_faces[:20]}"
                )
        finally:
            eval_obj.to_mesh_clear()

    if mesh_count == 0:
        raise ValueError("cache 组下没有可采集的 mesh")

    return {
        "schema_version": 3,
        "materials": materials,
        "missing_tiles": missing_tile_items,
        "no_material_meshes": no_material_items,
        "empty_meshes": empty_mesh_items,
        "texture_path_remaps": path_remaps,
    }


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
    cache_grp = bpy.data.objects.get(blender_obj_name) or bpy.data.objects.get('cache') or bpy.data.objects.get('geo')
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

    try:
        mat_data = extract_materials(export_objects)
    except (OSError, ValueError) as exc:
        return make_receipt(
            'blender_extract_materials', 'ERROR', t0,
            summary_input=os.path.basename(output_path),
            error=str(exc),
        )

    materials = mat_data.get("materials") or {}
    missing_tiles = mat_data.get("missing_tiles") or []
    no_material_meshes = mat_data.get("no_material_meshes") or []
    empty_meshes = mat_data.get("empty_meshes") or []
    mat_count = len(materials)
    material_names = sorted(materials)
    mesh_names = set()
    approximation_items = []
    for material_name, material_data in materials.items():
        mesh_names.update((material_data.get("faces_by_mesh") or {}).keys())
        for channel in ("color", "alpha"):
            info = material_data.get(channel) or {}
            mode = info.get("approximation")
            if mode and mode != "direct":
                approximation_items.append({
                    "material": material_name,
                    "channel": channel,
                    "mode": mode,
                    "source_image_count": info.get("source_image_count", 0),
                })
    mesh_count = len(mesh_names)

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # 落盘只写 schema v3 契约字段；missing_tiles 是诊断信息，只进 receipt
    with open(output_path, 'w', encoding='utf-8') as fp:
        json.dump(
            {
                "schema_version": mat_data.get("schema_version", 3),
                "materials": materials,
            },
            fp, ensure_ascii=False, indent=2,
        )

    missing_items = [
        make_item(
            f"{item.get('mesh', '?')} / UDIM {item.get('tile')}",
            f"WARN: {item.get('label')} 贴图缺失，已降级为纯色 lambert "
            f"({item.get('face_count', 0)} 面); 声明值={item.get('declared_tiles')}",
        )
        for item in missing_tiles
    ]
    missing_items.extend(
        make_item(
            item.get('mesh', '?'),
            f"WARN: {_no_material_reason_text(item)}，"
            f"已降级为中性灰 lambert ({item.get('face_count', 0)} 面)",
        )
        for item in no_material_meshes
    )
    missing_items.extend(
        make_item(path, "WARN: 无 polygon，已跳过材质采集（几何仍照常导出）")
        for path in empty_meshes
    )

    return make_receipt(
        api_id='blender_extract_materials',
        status='SUCCESS',
        start_time=t0,
        summary_input=os.path.basename(output_path),
        summary_action='材质信息采集',
        summary_count=mat_count,
        summary_label='材质条目',
        items=missing_items,
        output={
            'output_path': output_path,
            'result': {
                'materials_path': output_path,
                'schema_version': 3,
                'material_count': mat_count,
                'material_names': material_names,
                'mesh_count': mesh_count,
                'cache_group': cache_group_name,
                'udim_material_count': sum(
                    1 for data in materials.values()
                    if (data.get("source") or {}).get("mode") == "udim"
                ),
                'color_texture_count': sum(
                    1 for data in materials.values()
                    if (data.get("color") or {}).get("type") == "texture"
                ),
                'alpha_texture_count': sum(
                    1 for data in materials.values()
                    if (data.get("alpha") or {}).get("type") == "texture"
                ),
                'approximation_count': len(approximation_items),
                'approximation_items': approximation_items,
                'missing_tile_count': len(missing_tiles),
                'missing_tiles': missing_tiles[:20],
                'texture_path_remaps': mat_data.get('texture_path_remaps', {}),
                'no_material_mesh_count': len(no_material_meshes),
                'no_material_meshes': no_material_meshes[:20],
                'empty_mesh_count': len(empty_meshes),
                'empty_meshes': empty_meshes[:20],
                'warnings': [
                    f"{item.get('mesh')} / {item.get('material')} 的 "
                    f"{item.get('label')} UDIM {item.get('tile')} 缺贴图，已降级为纯色"
                    for item in missing_tiles[:20]
                ] + [
                    f"{item.get('mesh')} {_no_material_reason_text(item)}，已降级为中性灰"
                    for item in no_material_meshes[:20]
                ] + [
                    f"{path} 无 polygon，已跳过材质采集"
                    for path in empty_meshes[:20]
                ],
            },
        },
    )
