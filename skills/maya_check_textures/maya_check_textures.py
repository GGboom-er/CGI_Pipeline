# skills/check_textures.py
# ── 贴图检查（Maya）──
# 扫描场景中所有 file 节点，检查贴图是否存在于磁盘，汇总报告。

import os
import sys
import re
import time
import glob as globmod
import maya.cmds as cmds

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item

_UDIM_PATTERN = re.compile(r'<udim>|\d{4}', re.IGNORECASE)


def _resolve_udim_exists(tex_path):
    """检查 UDIM 贴图是否至少有一张存在。"""
    if '<udim>' in tex_path.lower() or '<UDIM>' in tex_path:
        pattern = re.sub(r'<[Uu][Dd][Ii][Mm]>', '[0-9][0-9][0-9][0-9]', tex_path)
        return len(globmod.glob(pattern)) > 0
    base, ext = os.path.splitext(tex_path)
    match = re.search(r'\.(\d{4})$', base)
    if match:
        pattern = base[:match.start()] + '.[0-9][0-9][0-9][0-9]' + ext
        return len(globmod.glob(pattern)) > 0
    return os.path.isfile(tex_path)


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    check_exists = params.get('check_exists', True)
    source_path = payload.get('source_path', '')
    scene_name = os.path.basename(
        cmds.file(query=True, sceneName=True) or source_path or 'untitled'
    )

    file_nodes = cmds.ls(type='file') or []
    if not file_nodes:
        return make_receipt('maya_check_textures', 'SUCCESS', t0,
                            summary_input=scene_name,
                            summary_action='场景中无 file 节点',
                            summary_count=0, summary_label='贴图')

    # 收集所有贴图信息
    all_textures = {}  # {dir: [(filename, file_node, exists)]}
    missing = []
    total_count = 0

    for fn in file_nodes:
        tex_path = cmds.getAttr(fn + '.fileTextureName') or ''
        if not tex_path:
            continue
        tex_path = os.path.normpath(tex_path).replace('\\', '/')
        tex_dir = os.path.dirname(tex_path)
        tex_name = os.path.basename(tex_path)
        total_count += 1

        exists = True
        if check_exists:
            exists = _resolve_udim_exists(tex_path)

        if tex_dir not in all_textures:
            all_textures[tex_dir] = []
        all_textures[tex_dir].append((tex_name, fn, exists))

        if not exists:
            missing.append((tex_dir, tex_name, fn))

    # 构建报告
    items = []
    report_lines = []

    report_lines.append(f'== 场景贴图汇总 ({total_count}张) ==')
    report_lines.append('')
    for tex_dir, entries in sorted(all_textures.items()):
        report_lines.append(tex_dir)
        for tex_name, fn, exists in sorted(entries, key=lambda x: x[0]):
            mark = '' if exists else '  [缺失]'
            report_lines.append(f'  {tex_name}{mark}')
        report_lines.append('')

    if missing:
        report_lines.append(f'== 缺失贴图 ({len(missing)}张) ==')
        report_lines.append('')
        missing_by_dir = {}
        for tex_dir, tex_name, fn in missing:
            if tex_dir not in missing_by_dir:
                missing_by_dir[tex_dir] = []
            missing_by_dir[tex_dir].append((tex_name, fn))
        for tex_dir, entries in sorted(missing_by_dir.items()):
            report_lines.append(tex_dir)
            for tex_name, fn in sorted(entries, key=lambda x: x[0]):
                report_lines.append(f'  {tex_name}  ({fn})')
            report_lines.append('')

        for tex_dir, tex_name, fn in missing:
            items.append(make_item(tex_name, f'缺失 — {fn} → {tex_dir}'))

    items.insert(0, make_item('贴图汇总',
                              f'共 {total_count} 张, 缺失 {len(missing)} 张'))

    status_msg = f'{total_count} 张贴图全部存在' if not missing else f'{len(missing)} 张贴图缺失'

    receipt = make_receipt(
        skill_id='maya_check_textures',
        status='SUCCESS',
        start_time=t0,
        summary_input=scene_name,
        summary_action=status_msg,
        summary_count=len(missing),
        summary_label='缺失',
        items=items,
    )
    receipt['_texture_report'] = '\n'.join(report_lines)
    return receipt
