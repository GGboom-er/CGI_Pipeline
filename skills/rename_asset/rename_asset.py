# skills/rename_asset.py
# ── 标准化资产重命名（Pipeline 命名规范强制匹配） ──
#
# 将当前打开的 Maya 场景文件按照管线命名规范重命名并另存为。
# 命名格式: {project}_{category}_{asset}_{stage}_{task}_v{version}.ma
#
# 纯重命名 + 另存为操作，不修改场景内容。

import maya.cmds as cmds
import os
import re
import time
import datetime

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.config_loader import load_project_config
from core.asset_resolver import AssetResolver
from core.path_guard import is_protected_path, suggest_ai_publish_path
from core.receipt import make_receipt



def execute(payload: dict) -> dict:
    """
    标准化资产重命名。

    根据 project/category/asset_name/stage 生成符合管线规范的文件名，
    将当前场景另存为到标准路径。

    参数说明：
        project:     项目代号
        category:    资产分类
        asset_name:  资产名称
        stage:       制作阶段
        task:        子任务名（可选，默认取 stage 的 primary_task）
        output_dir:  输出目录（可选，默认与源文件同目录）
    """
    params = payload.get('parameters', {})
    project = params.get('project', '').strip()
    category = params.get('category', '').strip()
    asset_name = params.get('asset_name', '').strip()
    stage = params.get('stage', '').strip()
    task = params.get('task', '').strip() or None
    output_dir = params.get('output_dir', '').strip()

    t0 = time.time()

    # ── 参数校验 ──
    missing = []
    if not project: missing.append('project')
    if not category: missing.append('category')
    if not asset_name: missing.append('asset_name')
    if not stage: missing.append('stage')
    if missing:
        return make_receipt(
            'rename_asset', 'ERROR', t0,
            summary_input=asset_name or 'unknown',
            error=f'缺少必要参数: {", ".join(missing)}',
        )

    # ── 获取当前场景 ──
    current_scene = cmds.file(query=True, sceneName=True) or ''
    if not current_scene:
        return make_receipt(
            'rename_asset', 'ERROR', t0,
            summary_input=asset_name,
            error='当前没有打开的场景文件。',
        )

    try:
        # ── 解析 primary_task ──
        cfg = load_project_config(project)
        resolver = AssetResolver(cfg)
        task_name = task or resolver._get_primary_task(stage)

        # ── 推算版本号：从当前文件名提取，否则默认 v001 ──
        current_basename = os.path.basename(current_scene)
        ver_match = re.search(r'_v(\d{3,4})', current_basename)
        version = int(ver_match.group(1)) if ver_match else 1

        # ── 构建标准文件名 ──
        ext = os.path.splitext(current_basename)[1] or '.ma'
        standard_name = f'{project}_{category}_{asset_name}_{stage}_{task_name}_v{version:03d}{ext}'

        # ── 确定输出目录 ──
        if not output_dir:
            output_dir = os.path.dirname(current_scene)

        output_path = os.path.join(output_dir, standard_name).replace('\\', '/')

        # ── 安全检查 ──
        if is_protected_path(output_path):
            ai_path = suggest_ai_publish_path(current_scene)
            if ai_path:
                output_dir = os.path.dirname(ai_path)
                output_path = os.path.join(output_dir, standard_name).replace('\\', '/')
            else:
                return make_receipt(
                    'rename_asset', 'BLOCKED', t0,
                    summary_input=asset_name,
                    error=f'输出路径受保护: {output_path}',
                )

        # ── 确保目录存在 ──
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # ── 如果当前文件名已符合标准，跳过 ──
        if os.path.normpath(current_scene) == os.path.normpath(output_path):
            return make_receipt(
                'rename_asset', 'SUCCESS', t0,
                summary_input=asset_name,
                summary_action='文件名已符合规范，无需重命名',
                outputs={
                    'output_path': output_path,
                    'result': {
                        'original_name': current_basename,
                        'standard_name': standard_name,
                        'renamed': False,
                    },
                },
            )

        # ── 执行重命名（另存为） ──
        file_type = 'mayaAscii' if ext.lower() == '.ma' else 'mayaBinary'
        cmds.file(rename=output_path)
        cmds.file(save=True, type=file_type)

        elapsed = time.time() - t0

        receipt = make_receipt(
            'rename_asset', 'SUCCESS', t0,
            summary_input=asset_name,
            summary_action=f'已重命名为 {standard_name}',
            outputs={
                'output_path': output_path,
                'result': {
                    'original_name': current_basename,
                    'standard_name': standard_name,
                    'renamed': True,
                },
            },
        )
        return receipt

    except Exception as e:
        import traceback
        return make_receipt(
            'rename_asset', 'ERROR', t0,
            summary_input=asset_name,
            error=f'{e}\n{traceback.format_exc()}',
        )
