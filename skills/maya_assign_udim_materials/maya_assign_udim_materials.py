# skills/assign_udim_materials.py
# ── UDIM 材质分配 ──
#
# 为场景中的 mesh 按 UDIM 象限创建材质球、连接贴图并按面赋予。
#
# 数据来源优先级：
# 1. ABC FaceSet → 面→材质名映射（导入时自动创建 objectSet）
# 2. _texmap.json → 材质名→贴图路径（精简清单，从 Blender 节点树提取）
# 3. Maya UV 分析 → 自行分析 UDIM 象限（回退模式）
# 4. 贴图目录搜索 → 命名规范推导（最终兜底）

import os
import sys
import json
import math
import shutil
import time
from collections import defaultdict

import maya.cmds as cmds
import maya.api.OpenMaya as om

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item
from core.path_guard import is_protected_path

# ── 常量 ──

TEX_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.tx', '.tga', '.hdr', '.rat'}
COLOR_KW = ('color', 'col', 'base', 'diffuse', 'diff', 'albedo')

PLACE2D_ATTRS = [
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV",
    "stagger", "wrapU", "wrapV", "repeatUV", "offset", "rotateUV",
    "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
]


# ═════════════════════════════════════════════════════════════
# FaceSet 读取（从 ABC 导入后的 Maya 场景）
# ═════════════════════════════════════════════════════════════

def _read_facesets_from_scene(meshes):
    """
    读取 ABC 导入后的 FaceSet 信息。

    ABC FaceSet 在 Maya 中表现为 objectSet 或 shadingGroup。
    返回: {材质名: {mesh_path: [face_indices]}}
    """
    faceset_map = defaultdict(lambda: defaultdict(list))

    for mesh in meshes:
        # 获取此 mesh 所属的 sets（排除 default 组）
        transform = cmds.listRelatives(mesh, parent=True, fullPath=True)
        node = transform[0] if transform else mesh

        sets = cmds.listSets(object=node, type=1) or []  # type=1 = rendering sets
        for sg in sets:
            if sg in ('initialShadingGroup', 'initialParticleSE'):
                continue
            # 获取此 SG 中属于这个 mesh 的面
            members = cmds.sets(sg, q=True) or []
            my_faces = [m for m in members if node in m or mesh in m]
            if my_faces:
                # SG 名称通常是 Blender 材质名 + "SG" 后缀
                mat_name = sg.replace('SG', '').rstrip('_')
                if not mat_name:
                    mat_name = sg
                faceset_map[mat_name][node].extend(my_faces)

    return dict(faceset_map) if faceset_map else None


# ═════════════════════════════════════════════════════════════
# UDIM 分析
# ═════════════════════════════════════════════════════════════

def _resolve_uvset(shape, warnings=None):
    """解析 mesh 的 UV Set，优先 map1"""
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
    try:
        cur = cmds.polyUVSet(shape, q=True, currentUVSet=True)
        return cur[0] if cur else all_sets[0]
    except Exception as e:
        if warnings is not None:
            warnings.append(make_item(name=shape, detail=f'读取当前 UV Set 失败，回退到 {all_sets[0]}: {e}'))
        return all_sets[0]


def _compress_face_components(mesh_face_dict):
    """将面索引列表压缩为 Maya 面组件字符串"""
    result = []
    for mesh, idxs in mesh_face_dict.items():
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


def _analyze_udim_distribution(shapes):
    """使用 OpenMaya 遍历 mesh 面的 UV 质心，按 UDIM 象限分组"""
    raw = defaultdict(lambda: {"by_mesh": defaultdict(list)})
    failed = []
    warnings = []

    for shape in shapes:
        uvset = _resolve_uvset(shape, warnings)
        if not uvset:
            failed.append(shape)
            continue

        try:
            msel = om.MSelectionList()
            msel.add(shape)
            dag = msel.getDagPath(0)
            full_path = dag.fullPathName()
            it = om.MItMeshPolygon(dag)
        except Exception as e:
            failed.append(shape)
            warnings.append(make_item(name=shape, detail=f'OpenMaya 初始化失败: {e}'))
            continue

        shape_contributed = False
        while not it.isDone():
            fid = it.index()
            try:
                if it.hasUVs(uvset):
                    us, vs = it.getUVs(uvset)
                    ul = list(us)
                    vl = list(vs)
                    if ul:
                        uc = sum(ul) / len(ul)
                        vc = sum(vl) / len(vl)
                        ut = int(math.floor(uc))
                        vt = int(math.floor(vc))
                        if 0 <= ut <= 9 and vt >= 0:
                            udim = 1001 + ut + vt * 10
                            raw[udim]["by_mesh"][full_path].append(fid)
                            shape_contributed = True
            except Exception as e:
                warnings.append(make_item(name=shape, detail=f'面 {fid} UV 分析失败: {e}'))
            it.next()

        if not shape_contributed:
            failed.append(shape)

    result = {}
    for udim, data in raw.items():
        compressed = _compress_face_components(dict(data["by_mesh"]))
        result[udim] = {"faces": compressed}
    return result, failed, warnings


# ═════════════════════════════════════════════════════════════
# 材质创建与赋予
# ═════════════════════════════════════════════════════════════

def _create_and_assign_material(base_name, udim, faces, tex_path=None, warnings=None):
    """创建 lambert 材质球 + file 节点，按面赋予"""
    safe_name = base_name.replace(".", "_").replace(" ", "_")
    mat_name = f"MAT_{safe_name}_{udim}"
    sg_name = f"SG_{safe_name}_{udim}"

    if not cmds.objExists(mat_name):
        mat_name = cmds.shadingNode('lambert', asShader=True, name=mat_name)
    if not cmds.objExists(sg_name):
        sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)

    try:
        cmds.connectAttr(f"{mat_name}.outColor", f"{sg_name}.surfaceShader", force=True)
    except Exception as e:
        if warnings is not None:
            warnings.append(make_item(name=sg_name, detail=f'材质连接 SG 失败: {e}'))

    if tex_path:
        fnode = f"FILE_{safe_name}_{udim}"
        if not cmds.objExists(fnode):
            fnode = cmds.shadingNode('file', asTexture=True, isColorManaged=True, name=fnode)
            p2d = cmds.shadingNode('place2dTexture', asUtility=True, name=f"p2d_{safe_name}_{udim}")
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
        cmds.setAttr(f"{fnode}.fileTextureName", tex_path, type="string")
        try:
            cmds.connectAttr(f"{fnode}.outColor", f"{mat_name}.color", force=True)
        except Exception as e:
            if warnings is not None:
                warnings.append(make_item(name=mat_name, detail=f'贴图连接颜色失败: {e}'))

    if faces:
        cmds.sets(faces, forceElement=sg_name)


def _derive_udim_tex_path(template_path, udim):
    """
    从模板贴图路径推导指定 UDIM 的实际贴图路径。

    支持：
    - abc_body_BaseColor.1001.exr → abc_body_BaseColor.{udim}.exr
    - abc_body_BaseColor_1001.exr → abc_body_BaseColor_{udim}.exr
    - 无 UDIM 编号的单张贴图 → 直接返回
    """
    if not template_path:
        return None

    udim_str = str(udim)
    path = template_path.replace('\\', '/')

    # 尝试替换模板中的 1001
    if '1001' in path:
        candidate = path.replace('1001', udim_str)
        if os.path.isfile(candidate):
            return candidate

    # 尝试 <UDIM> 占位符
    if '<UDIM>' in path:
        candidate = path.replace('<UDIM>', udim_str)
        if os.path.isfile(candidate):
            return candidate

    # 单张贴图（无 UDIM 编号），直接返回原路径
    if os.path.isfile(path):
        return path

    return None


# ═════════════════════════════════════════════════════════════
# 技能入口
# ═════════════════════════════════════════════════════════════

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    params = payload.get('parameters', {})
    texmap_path = params.get('texmap_path', '')  # 兼容旧参数名
    info_path = params.get('info_path', '') or texmap_path  # 新参数名优先
    manifest_path = params.get('manifest_path', '')
    srcimg_root = params.get('srcimg_root', '')
    asset_name = params.get('asset_name', '') or payload.get('asset_name', '')
    category = params.get('category', 'chr')
    stage = params.get('stage', 'tex')
    tex_output_dir = params.get('tex_output_dir', '')
    target_group = params.get('target_group', '')

    reports = []
    warnings = []

    # ── 获取 mesh 列表 ──
    if target_group and cmds.objExists(target_group):
        meshes = cmds.ls(target_group, dag=True, type='mesh', noIntermediate=True, long=True) or []
    else:
        meshes = cmds.ls(type='mesh', noIntermediate=True, long=True) or []

    if not meshes:
        return make_receipt(
            skill_id='maya_assign_udim_materials',
            status='SUCCESS',
            start_time=t0,
            summary_action='场景中没有 mesh',
            summary_count=0,
            summary_label='材质球',
            items=[],
        )

    # 贴图输出目录
    if tex_output_dir:
        if is_protected_path(tex_output_dir):
            return make_receipt('maya_assign_udim_materials', 'BLOCKED', t0,
                                error=f'贴图输出路径 "{tex_output_dir}" 位于只读/受保护区域，禁止写入。')
        tex_out_dir = os.path.join(tex_output_dir, "sourceimages").replace('\\', '/')
        if not os.path.exists(tex_out_dir):
            os.makedirs(tex_out_dir)
    else:
        tex_out_dir = None

    # ── 读取贴图映射（_info.json > _texmap.json > 旧版 manifest）──
    texmap = {}
    if info_path and os.path.isfile(info_path):
        try:
            with open(info_path, 'r', encoding='utf-8') as fp:
                raw = json.load(fp)
            # 支持两种格式：
            #   新版 _info.json: {"meshes": {...}, "textures": {...}}
            #   旧版 _texmap.json: {"mat_name": "tex_path", ...}
            if "textures" in raw and isinstance(raw["textures"], dict):
                raw_tex = raw["textures"]
                # _info.json 格式: {目录路径: [文件名列表]}
                # 需要展开为 {文件名(不含扩展): 完整路径}
                for dir_path, files_or_path in raw_tex.items():
                    if isinstance(files_or_path, list):
                        for fname in files_or_path:
                            full_path = os.path.join(dir_path, fname).replace('\\', '/')
                            base = os.path.splitext(fname)[0]
                            texmap[base] = full_path
                    elif isinstance(files_or_path, str):
                        # 旧格式: {材质名: 贴图路径}
                        texmap[dir_path] = files_or_path
            else:
                texmap = raw  # 直接当 flat texmap 用
            reports.append(f"已加载资产信息: {os.path.basename(info_path)} ({len(texmap)} 个贴图)")
        except Exception as e:
            reports.append(f"资产信息解析失败: {e}")
    elif manifest_path and os.path.isfile(manifest_path):
        # 兼容旧版完整 manifest
        try:
            with open(manifest_path, 'r', encoding='utf-8') as fp:
                manifest = json.load(fp)
            if manifest.get("materials"):
                for mat_name, mat_data in manifest["materials"].items():
                    template_tex = mat_data.get("template_texture")
                    if template_tex:
                        texmap[mat_name] = template_tex
            reports.append(f"已加载旧版清单: {os.path.basename(manifest_path)}")
        except Exception as e:
            reports.append(f"旧版清单解析失败: {e}")

    materials_created = 0
    textures_linked = 0

    # ── Maya UDIM 分析（所有 mesh 按 UDIM 象限分面）──
    udim_data, failed, analyze_warnings = _analyze_udim_distribution(meshes)
    warnings.extend(analyze_warnings[:20])
    if failed:
        reports.append(f"注意: {len(failed)} 个 mesh 无法分析 UV")

    if not udim_data:
        return make_receipt(
            skill_id='maya_assign_udim_materials',
            status='SUCCESS',
            start_time=t0,
            summary_action='场景中未检测到 UDIM 分布',
            summary_count=0,
            summary_label='材质球',
            items=[make_item(name='材质操作', detail=r) for r in reports],
        )

    # ── 尝试从 FaceSet 读取面→材质映射 ──
    faceset_map = _read_facesets_from_scene(meshes)
    if faceset_map:
        reports.append(f"从 FaceSet 读取到 {len(faceset_map)} 个材质分组")

    cmds.undoInfo(openChunk=True, chunkName='maya_assign_udim_materials')
    try:
        # ── 为每个 UDIM 创建材质并赋予 ──
        for udim, info in sorted(udim_data.items()):
            udim_str = str(udim)
            faces = info['faces']

            # 确定材质名（优先从 FaceSet，否则用通用名）
            mat_name = None
            if faceset_map:
                # 看这些面属于哪个 FaceSet 材质
                for fs_mat, fs_mesh_dict in faceset_map.items():
                    # 简单匹配：如果这个 UDIM 的面和某个 FaceSet 有交集
                    # TODO: 精确匹配可在后续迭代中优化
                    mat_name = fs_mat
                    break  # 对于单材质场景直接取第一个

            if not mat_name:
                mat_name = asset_name if asset_name else "Material"

            # 查找贴图路径
            tex_path = None

            # 优先级 1：texmap 精确匹配
            if mat_name in texmap:
                tex_path = _derive_udim_tex_path(texmap[mat_name], udim)
            # 优先级 2：texmap 模糊匹配（材质名可能有 _shader 后缀）
            if not tex_path and texmap:
                for tm_name, tm_path in texmap.items():
                    core1 = mat_name.replace('_shader', '').replace('MAT_', '').lower()
                    core2 = tm_name.replace('_shader', '').replace('MAT_', '').lower()
                    if core1 == core2 or core1 in core2 or core2 in core1:
                        tex_path = _derive_udim_tex_path(tm_path, udim)
                        if tex_path:
                            break
            # 优先级 2.5：按 UDIM 编号 + Color 关键字直接匹配 texmap
            if not tex_path and texmap:
                for tm_name, tm_path in texmap.items():
                    tl = tm_name.lower()
                    if udim_str in tm_name and any(k in tl for k in COLOR_KW):
                        if os.path.isfile(tm_path):
                            tex_path = tm_path
                            break
            # 优先级 3：目录搜索（命名规范推导）
            if not tex_path and srcimg_root and asset_name:
                task_name = payload.get('_primary_task', f"{stage}Master")
                tex_dir = os.path.join(
                    srcimg_root, category, asset_name, stage,
                    task_name
                ).replace('\\', '/')
                if os.path.exists(tex_dir):
                    for f in sorted(os.listdir(tex_dir)):
                        if os.path.splitext(f)[1].lower() not in TEX_EXTS:
                            continue
                        fl = f.lower()
                        if any(k in fl for k in COLOR_KW):
                            if udim_str in f:
                                tex_path = os.path.join(tex_dir, f)
                                break

            # 贴图拷贝
            final_tex = None
            if tex_path and os.path.isfile(tex_path):
                if tex_out_dir:
                    tex_basename = os.path.basename(tex_path)
                    try:
                        shutil.copy2(tex_path, os.path.join(tex_out_dir, tex_basename))
                        final_tex = f"sourceimages/{tex_basename}"
                        textures_linked += 1
                    except Exception as e:
                        reports.append(f"  [{mat_name}] UDIM {udim}: 贴图拷贝失败: {e}")
                else:
                    final_tex = tex_path
                    textures_linked += 1

            # 创建材质 + 赋予
            try:
                _create_and_assign_material(mat_name, udim, faces, final_tex, warnings)
                materials_created += 1
                if not final_tex:
                    reports.append(f"  [{mat_name}] UDIM {udim}: 空白材质（无匹配贴图）")
            except Exception as e:
                reports.append(f"  [{mat_name}] UDIM {udim}: 赋予失败: {e}")
    finally:
        cmds.undoInfo(closeChunk=True)

    reports.insert(0, f"成功建立 {materials_created} 个 UDIM 材质球，{textures_linked} 张贴图已连接")

    source_path = payload.get('source_path', '')
    scene_name = os.path.basename(cmds.file(query=True, sceneName=True) or source_path or 'untitled')
    items = []
    for r in reports[1:]:
        items.append(make_item(name='材质操作', detail=r))
    items.extend(warnings[:max(0, 20 - len(items))])
    status = 'PARTIAL' if any('赋予失败' in r for r in reports) else 'SUCCESS'

    return make_receipt(
        skill_id='maya_assign_udim_materials',
        status=status,
        start_time=t0,
        summary_input=scene_name,
        summary_action=f'UDIM 材质分配 — {materials_created} 个材质球, {textures_linked} 张贴图',
        summary_count=materials_created,
        summary_label='材质球',
        items=items,
    )
