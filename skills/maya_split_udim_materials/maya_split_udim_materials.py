# skills/udim_material_split.py
# ── UDIM 面级材质拆分与贴图重连 ──
#
# 用途：根据 geo 组内 mesh 的 UV UDIM 分布，自动拆分为多个 Lambert 材质球，
#       并从指定贴图目录自动发现并连接对应 UDIM 的 color 贴图。
#
# 调用方式：通过 MCP exec_code 或 chain 执行
#   from skills.udim_material_split import execute
#   result = execute({"tex_root": "L:/Project/F3CGT/publish/asset/chr/XiaoTianQuan/rig/.../textures"})
#
# 也支持自动发现贴图路径（从场景中已有 file 节点推断）

import maya.cmds as cmds
import maya.api.OpenMaya as om
import os
import sys
import math
import time
from collections import defaultdict

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item, _ms_to_min

# ─── 常量 ───

TEX_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.tx', '.tga', '.hdr'}
COLOR_KW = ('color', 'col', 'base', 'diffuse', 'diff', 'albedo', 'basecolor')

PLACE2D_ATTRS = [
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV",
    "stagger", "wrapU", "wrapV", "repeatUV", "offset", "rotateUV",
    "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
]


# ═══════════════════════════════════════
# 1. Geo 组定位
# ═══════════════════════════════════════

def find_geo_group(custom_name=None):
    """自动定位 geo 组（支持 geo/Group/cache 等常见名称）"""
    candidates = ['geo', 'Group', 'cache', 'Geo', 'GEO']
    if custom_name:
        candidates = [custom_name] + candidates
    for name in candidates:
        for pattern in [f'*|{name}', f'*|*|{name}', name]:
            matches = cmds.ls(pattern, long=True, type='transform') or []
            if matches:
                return matches[0]
    return None


def get_geo_meshes(geo_grp):
    """获取 geo 组下所有非中间物体的 mesh shape（fullPath）"""
    if not geo_grp:
        return []
    meshes = cmds.listRelatives(geo_grp, allDescendents=True, type='mesh', fullPath=True) or []
    return [m for m in meshes if not cmds.getAttr(m + '.intermediateObject')]


# ═══════════════════════════════════════
# 2. UDIM 分析引擎（来自用户验证通过的 MItMeshPolygon 算法）
# ═══════════════════════════════════════

def resolve_uvset(shape, warnings=None):
    """获取可用 UV 集名称。优先 map1。"""
    try:
        all_sets = cmds.polyUVSet(shape, q=True, allUVSets=True) or []
    except Exception as e:
        if warnings is not None:
            warnings.append(make_item(name=shape, detail=f'读取 UV Set 失败: {e}'))
        return None
    if not all_sets:
        return None
    if 'map1' in all_sets:
        return 'map1'
    return all_sets[0]


def analyze_udim_distribution(shapes):
    """
    核心 UV 分析：使用 MItMeshPolygon.getUVs() 逐面计算 UV 重心，归入 UDIM 象限。
    
    Returns:
        udim_map: {udim: {"by_mesh": {mesh_path: [face_idx, ...]}, "mesh_set": set}}
        failed: [shape_path, ...]
    """
    raw = defaultdict(lambda: {"by_mesh": defaultdict(list), "mesh_set": set()})
    failed = []
    warnings = []

    for shape in shapes:
        uvset = resolve_uvset(shape, warnings)
        if not uvset:
            failed.append(shape)
            continue

        try:
            msel = om.MSelectionList()
            msel.add(shape)
            dag = msel.getDagPath(0)
        except Exception as e:
            failed.append(shape)
            warnings.append(make_item(name=shape, detail=f'OpenMaya 选择失败: {e}'))
            continue

        full_path = dag.fullPathName()

        try:
            it = om.MItMeshPolygon(dag)
        except Exception as e:
            failed.append(shape)
            warnings.append(make_item(name=shape, detail=f'多边形迭代器初始化失败: {e}'))
            continue

        shape_contributed = False
        while not it.isDone():
            fid = it.index()
            try:
                if it.hasUVs(uvset):
                    us, vs = it.getUVs(uvset)
                    ul, vl = list(us), list(vs)
                    if ul:
                        uc = sum(ul) / len(ul)
                        vc = sum(vl) / len(vl)
                        ut = int(math.floor(uc))
                        vt = int(math.floor(vc))
                        if 0 <= ut <= 9 and vt >= 0:
                            udim = 1001 + ut + vt * 10
                            raw[udim]["by_mesh"][full_path].append(fid)
                            raw[udim]["mesh_set"].add(full_path)
                            shape_contributed = True
            except Exception as e:
                warnings.append(make_item(name=shape, detail=f'面 {fid} UV 分析失败: {e}'))
            it.next()

        if not shape_contributed:
            failed.append(shape)

    return dict(raw), failed, warnings


def compress_face_components(by_mesh):
    """将 {mesh: [face_idx, ...]} 压缩为 ["mesh.f[0:99]", ...] 格式"""
    result = []
    for mesh, idxs in by_mesh.items():
        if not idxs:
            continue
        idxs = sorted(set(idxs))
        ranges = []
        s = e = idxs[0]
        for i in idxs[1:]:
            if i == e + 1:
                e = i
            else:
                ranges.append(f"{mesh}.f[{s}]" if s == e else f"{mesh}.f[{s}:{e}]")
                s = e = i
        ranges.append(f"{mesh}.f[{s}]" if s == e else f"{mesh}.f[{s}:{e}]")
        result.extend(ranges)
    return result


# ═══════════════════════════════════════
# 3. 贴图发现
# ═══════════════════════════════════════

def discover_tex_root_from_scene():
    """从场景中已有 file 节点推断贴图目录"""
    file_nodes = cmds.ls(type='file') or []
    for fn in file_nodes:
        fpath = cmds.getAttr(fn + '.fileTextureName') or ''
        if fpath and os.path.isfile(fpath):
            return os.path.dirname(fpath).replace('\\', '/')
    return None


def _resolve_tex_root_from_payload(payload: dict):
    """从 payload 的 project/asset_name + source_path 推导服务器贴图目录"""
    project = payload.get('project', '')
    asset_name = payload.get('asset_name', '')
    source_path = payload.get('source_path', '')
    if not project or not asset_name or not source_path:
        return None

    try:
        from core.config_loader import load_project_config
        cfg = load_project_config(project)
    except Exception:
        return None

    server_root = cfg.get('server_root', '')
    tex_rel = cfg.get('path_roots', {}).get('sourceimages', '')
    if not server_root or not tex_rel:
        return None

    # 从文件名解析 category/stage/task
    import re
    fname = os.path.basename(source_path)
    m = re.match(
        r'^[^_]+_(?P<category>[^_]+)_(?P<asset>[^_]+)_'
        r'(?P<stage>[^_]+)_(?P<task>[^_]+)_v\d{3,4}\.\w+$',
        fname
    )
    if not m:
        return None

    category = m.group('category')
    stage = m.group('stage')
    task = m.group('task')

    tex_base = os.path.join(server_root, tex_rel).replace('\\', '/')

    tex_dir = f'{tex_base}/{category}/{asset_name}/{stage}/{task}'
    if os.path.isdir(tex_dir):
        return tex_dir

    tex_dir2 = f'{tex_base}/{category}/{asset_name}/{stage}'
    if os.path.isdir(tex_dir2):
        return tex_dir2

    return None


def find_color_texture(folder, udim):
    """
    在贴图目录中查找匹配指定 UDIM 的 color 贴图。
    
    匹配策略（按优先级）：
    1. 文件名同时包含 UDIM 数字 + color 关键词
    2. 文件名包含 UDIM 数字（无 color 关键词时降级匹配）
    """
    if not folder or not os.path.isdir(folder):
        return None

    udim_str = str(udim)
    best = None
    fallback = None

    for f in os.listdir(folder):
        ext = os.path.splitext(f)[1].lower()
        if ext not in TEX_EXTS:
            continue
        if udim_str not in f:
            continue

        low = f.lower()
        if any(k in low for k in COLOR_KW):
            # 优先返回 .tif/.exr 高质量格式
            if ext in ('.tif', '.tiff', '.exr'):
                return os.path.join(folder, f).replace('\\', '/')
            if not best:
                best = os.path.join(folder, f).replace('\\', '/')
        elif not fallback:
            fallback = os.path.join(folder, f).replace('\\', '/')

    return best or fallback


# ═══════════════════════════════════════
# 4. 材质创建与赋予
# ═══════════════════════════════════════

def create_udim_material(udim, color_attr='baseColor', tex_path=None, prefix='', warnings=None):
    """
    创建单个 UDIM 材质球（StandardSurface + ShadingGroup + file node）
    
    Returns: (material_name, sg_name)
    """
    mat_name = f"{prefix}MAT_UDIM_{udim}"
    sg_name = f"{prefix}SG_UDIM_{udim}"

    # 幂等：存在则复用
    if not cmds.objExists(mat_name):
        mat_name = cmds.shadingNode('lambert', asShader=True, name=mat_name)
    if not cmds.objExists(sg_name):
        sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)

    # 连接 shader → SG
    src = f"{mat_name}.outColor"
    dst = f"{sg_name}.surfaceShader"
    if not cmds.isConnected(src, dst):
        cmds.connectAttr(src, dst, force=True)

    # 贴图节点
    if tex_path and os.path.isfile(tex_path):
        fnode = f"{prefix}FILE_color_{udim}"
        if not cmds.objExists(fnode):
            fnode = cmds.shadingNode('file', asTexture=True, isColorManaged=True, name=fnode)
            p2d = cmds.shadingNode('place2dTexture', asUtility=True, name=f"{prefix}p2d_{udim}")
            for a in PLACE2D_ATTRS:
                try:
                    cmds.connectAttr(f"{p2d}.{a}", f"{fnode}.{a}", force=True)
                except Exception as e:
                    if warnings is not None:
                        warnings.append(make_item(name=fnode, detail=f'place2dTexture 属性 {a} 连接失败: {e}'))
            try:
                cmds.connectAttr(f"{p2d}.outUV", f"{fnode}.uvCoord", force=True)
            except Exception as e:
                if warnings is not None:
                    warnings.append(make_item(name=fnode, detail=f'outUV 连接失败: {e}'))
            try:
                cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{fnode}.uvFilterSize", force=True)
            except Exception as e:
                if warnings is not None:
                    warnings.append(make_item(name=fnode, detail=f'outUvFilterSize 连接失败: {e}'))

        cmds.setAttr(f"{fnode}.fileTextureName", tex_path, type="string")
        try:
            cmds.setAttr(f"{fnode}.colorSpace", "sRGB", type="string")
        except Exception as e:
            if warnings is not None:
                warnings.append(make_item(name=fnode, detail=f'设置 colorSpace 失败: {e}'))

        # 连接 file → material.baseColor
        try:
            cmds.connectAttr(f"{fnode}.outColor", f"{mat_name}.{color_attr}", force=True)
        except Exception as e:
            if warnings is not None:
                warnings.append(make_item(name=mat_name, detail=f'贴图连接材质颜色失败: {e}'))

    return mat_name, sg_name


# ═══════════════════════════════════════
# 核心入口
# ═══════════════════════════════════════

def execute(payload: dict) -> dict:
    start_time = time.time()

    params = payload.get('parameters', payload) if 'parameters' in payload else payload
    tex_root = params.get('tex_root', '')
    prefix = params.get('prefix', '')  # 材质名前缀（可选，用于区分不同角色）

    report = {
        'task_id': 'udim-material-split',
        'status': 'SUCCESS',
        'message': '',
        'execution_time_ms': 0,
        'steps': []
    }
    warnings = []

    # 1. 定位 geo 组
    geo_grp = find_geo_group(params.get('geo_group'))
    if not geo_grp:
        report['status'] = 'ERROR'
        report['message'] = '未找到 geo 组'
        return report

    # 2. 获取 mesh 列表
    meshes = get_geo_meshes(geo_grp)
    if not meshes:
        report['status'] = 'ERROR'
        report['message'] = f'{geo_grp} 下无有效 mesh'
        return report

    report['steps'].append({'name': 'Geo 定位', 'geo': geo_grp, 'mesh_count': len(meshes)})

    # 3. UDIM 分析
    udim_data, failed, analyze_warnings = analyze_udim_distribution(meshes)
    warnings.extend(analyze_warnings[:20])
    if not udim_data:
        report['status'] = 'ERROR'
        report['message'] = '所有 mesh 均无有效 UDIM UV 数据'
        report['failed_meshes'] = [f.split('|')[-1] for f in failed[:20]]
        return report

    udim_summary = {}
    for udim, data in sorted(udim_data.items()):
        face_count = sum(len(v) for v in data["by_mesh"].values())
        udim_summary[str(udim)] = {
            'mesh_count': len(data["mesh_set"]),
            'face_count': face_count,
            'meshes': [m.split('|')[-1] for m in sorted(data["mesh_set"])][:10]
        }

    report['steps'].append({
        'name': 'UDIM 分析',
        'udim_count': len(udim_data),
        'tiles': udim_summary,
        'failed_meshes': len(failed)
    })

    # 4. 贴图发现
    if not tex_root:
        tex_root = discover_tex_root_from_scene()
    if not tex_root:
        tex_root = _resolve_tex_root_from_payload(payload)

    tex_matches = {}
    if tex_root:
        for udim in sorted(udim_data.keys()):
            found = find_color_texture(tex_root, udim)
            if found:
                tex_matches[udim] = found

    report['steps'].append({
        'name': '贴图发现',
        'tex_root': tex_root or '(未指定)',
        'matched': {str(k): os.path.basename(v) for k, v in tex_matches.items()},
        'unmatched_udim': [str(u) for u in sorted(udim_data.keys()) if u not in tex_matches]
    })

    # 5. 创建材质并按面赋予（在 undoChunk 内）
    cmds.undoInfo(openChunk=True, chunkName="UDIM_MaterialSplit")
    try:
        assigned = []
        for udim in sorted(udim_data.keys()):
            t_udim = time.time()
            data = udim_data[udim]
            faces = compress_face_components(dict(data["by_mesh"]))
            tex_path = tex_matches.get(udim)

            mat_name, sg_name = create_udim_material(
                udim, color_attr='color',
                tex_path=tex_path, prefix=prefix, warnings=warnings
            )

            # 面级别赋予
            if faces:
                cmds.sets(faces, forceElement=sg_name)

            assigned.append({
                'udim': udim,
                'material': mat_name,
                'texture': os.path.basename(tex_path) if tex_path else None,
                'face_components': len(faces),
                'elapsed_sec': time.time() - t_udim,
            })

        report['steps'].append({
            'name': '材质赋予',
            'count': len(assigned),
            'details': assigned
        })

    finally:
        cmds.undoInfo(closeChunk=True)

    report['message'] = f"完成！{len(udim_data)} 个 UDIM 象限材质已拆分创建并按面赋予。"
    report['execution_time_ms'] = round((time.time() - start_time) * 1000, 2)

    source_path = payload.get('source_path', '')
    scene_name = os.path.basename(cmds.file(query=True, sceneName=True) or source_path or 'untitled')
    items = []
    for a in assigned:
        tex_info = a['texture'] or '无贴图'
        items.append(make_item(
            name=a['material'],
            detail=f'UDIM {a["udim"]} | {tex_info} | {a["face_components"]} 面',
            elapsed_min=round(a['elapsed_sec'] / 60, 4),
        ))
    items.extend(warnings[:max(0, 20 - len(items))])

    return make_receipt(
        skill_id='maya_split_udim_materials',
        status='SUCCESS',
        start_time=start_time,
        summary_input=scene_name,
        summary_action=f'UDIM 材质拆分 — {len(assigned)} 个象限',
        summary_count=len(assigned),
        summary_label='UDIM 材质',
        items=items,
    )
