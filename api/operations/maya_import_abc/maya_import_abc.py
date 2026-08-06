# api/operations/maya_import_abc/maya_import_abc.py
# ── 导入 ABC 缓存 ──
#
# 将 Alembic (.abc) 文件导入 Maya 场景，执行：
# 1. 可选新建空场景
# 2. 加载 AbcImport 插件
# 3. 导入 ABC 文件
# 4. 坐标系缩放对齐（默认 100x，Blender→Maya 适配）
# 5. 冻结变换归零
#
# 设计为链式执行的第一步，后续可接 assign_udim_materials → save_scene。

import os
import sys
import time
import traceback
import maya.cmds as cmds

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    abc_path = params.get('abc_path', '') or payload.get('source_path', '')
    scale_factor = float(params.get('scale_factor', 100.0))
    new_scene = params.get('new_scene', True)
    abc_name = os.path.basename(abc_path) if abc_path else 'unknown'

    if not abc_path:
        return make_receipt('maya_import_abc', 'ERROR', t0, error='缺少必填参数: abc_path')
    if not os.path.isfile(abc_path):
        return make_receipt('maya_import_abc', 'ERROR', t0,
                            summary_input=abc_name,
                            error=f'ABC 文件不存在: {abc_path}')

    warnings = []

    cmds.undoInfo(openChunk=True, chunkName='maya_import_abc')
    try:
        if new_scene:
            cmds.file(new=True, force=True)

        try:
            cmds.loadPlugin('AbcImport', quiet=True)
        except Exception as e:
            return make_receipt('maya_import_abc', 'ERROR', t0,
                                summary_input=abc_name,
                                error=f'加载 AbcImport 插件失败: {e}\n{traceback.format_exc()}')

        pre_nodes = set(cmds.ls(assemblies=True, long=True))
        t_import = time.time()
        try:
            cmds.file(abc_path, i=True, type='Alembic', namespace=":")
        except Exception as e:
            return make_receipt('maya_import_abc', 'ERROR', t0,
                                summary_input=abc_name,
                                error=f'ABC 导入失败: {e}')
        import_sec = time.time() - t_import

        post_nodes = set(cmds.ls(assemblies=True, long=True))
        top_nodes = sorted(post_nodes - pre_nodes)
        if not top_nodes:
            return make_receipt('maya_import_abc', 'ERROR', t0,
                                summary_input=abc_name,
                                error='ABC 导入后未检测到任何新增的顶层节点')

        root = top_nodes[0]

        if scale_factor and scale_factor != 1.0:
            t_scale = time.time()
            transforms = cmds.ls(root, dag=True, type='transform', long=True) or []
            for t in transforms:
                name = t.split('|')[-1]
                for attr, expected in [
                    ('tx', 0), ('ty', 0), ('tz', 0),
                    ('rx', 0), ('ry', 0), ('rz', 0),
                    ('sx', 1), ('sy', 1), ('sz', 1),
                ]:
                    try:
                        val = cmds.getAttr(f"{t}.{attr}")
                        if abs(val - expected) > 1e-4:
                            warnings.append(f"[{name}] {attr} 异常: {val:.3f}")
                    except Exception as e:
                        warnings.append(f"[{name}] 读取 {attr} 失败: {e}")

            cmds.setAttr(f"{root}.sx", scale_factor)
            cmds.setAttr(f"{root}.sy", scale_factor)
            cmds.setAttr(f"{root}.sz", scale_factor)
            cmds.makeIdentity(root, apply=True, scale=True, normal=False, preserveNormals=True)
            scale_sec = time.time() - t_scale

        meshes = cmds.ls(root, dag=True, type='mesh', long=True) or []

        items = [
            make_item(name='ABC 导入', detail=f'{len(meshes)} 个 mesh', elapsed_min=round(import_sec / 60, 4)),
        ]
        if scale_factor and scale_factor != 1.0:
            items.append(make_item(name=f'缩放 {scale_factor}x + Freeze', detail=root.split("|")[-1], elapsed_min=round(scale_sec / 60, 4)))
        for n in top_nodes:
            items.append(make_item(name=n.split('|')[-1], detail='顶层节点'))
        if warnings:
            for w in warnings[:10]:
                items.append(make_item(name='变换警告', detail=w))

        return make_receipt(
            api_id='maya_import_abc',
            status='SUCCESS',
            start_time=t0,
            summary_input=abc_name,
            summary_action=f'导入 ABC (缩放 {scale_factor}x)',
            summary_count=len(meshes),
            summary_label='mesh',
            items=items,
            outputs={'output_path': abc_path, 'result': {'top_nodes': top_nodes}},
        )
    finally:
        cmds.undoInfo(closeChunk=True)
