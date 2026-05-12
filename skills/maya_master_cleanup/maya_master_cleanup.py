# skills/master_cleanup.py
# ── 管线级质检与优化 (Pipeline QC & Cleanup) ──
#
# 三层架构：
#   Phase 1 — 管线级诊断：上下文锁定、层级校验、绑定偷渡防线
#   Phase 2 — 管线级精准清理：未知节点(安全墙)、插件底噪、野生相机、K帧、UV修复
#   Phase 3 — 原生引擎兜底：optionVar 注入 → cleanUpScene 1（逐类别分组）+ MLdeleteUnused
#
# 双模式：
#   mode="check"  只读扫描，输出结构化 JSON 诊断报告
#   mode="fix"    先诊断，无红线则执行清理，返回逐项详情

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMaya as om
import os
import sys
import time
import datetime
from contextlib import contextmanager

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item, _ms_to_min
from core.receipt import make_receipt, make_item, _ms_to_min


# ─── 常量 ───

MAX_DETAIL_ITEMS = 20  # 每项详情最多记录条目数

SUPPORTED_CAMERAS = ['persp', 'top', 'front', 'side', 'left', 'bottom']
PLUGIN_RESIDUES = ['TurtleBakeLayer', 'ilrBakeLayer*', 'ilr*', 'mentalray*', 'bifrost*']
TRASH_PLUGINS = [
    'Turtle', 'Mayatomr', 'VectorRender', 'bifrostGraph', 'BifrostMain', 'mtoa',
    'stereoCamera',  # Maya 内置立体摄影机插件
    'nodeEditorSavedTabsInfo',  # Maya 节点编辑器布局缓存
    'py_matrix_ribbon.py',  # Custom plugin used in the project
]

# 某些 Maya 内置 unknown 节点没有 plugin 来源，只能通过节点名称模式匹配
SAFE_UNKNOWN_NAMES = [
    'NodeEditorSavedTabsInfo',   # 节点编辑器布局缓存（无 plugin 归属）
    'hyperShadePrimaryNodeEditor',  # Hypershade 编辑器缓存
]

CLEANUP_OPTIONVARS = [
    'cleanUpSceneUnusedSkinInfs',
    'cleanUpSceneUnusedMaterials',
    'cleanUpSceneUnusedCameras',
    'cleanUpSceneUnusedBrushes',
    'cleanUpSceneUnusedNurbsCurves',
    'cleanUpSceneUnusedNurbsSurfaces',
    'cleanUpSceneEmptyGroups',
    'cleanUpSceneEmptySets',
    'cleanUpSceneEmptyDisplayLayers',
    'cleanUpSceneEmptyRenderLayers',
    'cleanUpSceneUnusedLocators',
    'cleanUpSceneUnusedConstraints',
    'cleanUpSceneUnusedPairBlends',
    'cleanUpSceneUnusedDeformers',
    'cleanUpSceneUnusedExpressions',
    'cleanUpSceneUnusedGroupIdNodes',
    'cleanUpSceneUnusedAnimationCurves',
    'cleanUpSceneUnusedSnapshotNodes',
    'cleanUpSceneUnusedUnitConversionNodes'
]


@contextmanager
def _undo_chunk(name):
    """用上下文管理器确保 Maya undo chunk 在异常路径也会关闭。"""
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


# ═══════════════════════════════════════
# Phase 1: 管线级诊断探测器
# ═══════════════════════════════════════

def resolve_geom_scope(ctx):
    """从管线上下文的 geom_roots 数组中寻址第一个命中的合法层级根节点。"""
    if not ctx or not hasattr(ctx, 'geom_roots') or not ctx.geom_roots:
        return None, False
    for root_tpl in ctx.geom_roots:
        matched = cmds.ls(root_tpl, long=True)
        if matched:
            return matched[0], False
    return None, True


def detect_rig_signatures():
    """扫描绑定专属的高危指纹。"""
    sigs = []
    if cmds.ls(type='joint'):       sigs.append('骨骼(joint)')
    if cmds.ls(type='skinCluster'): sigs.append('蒙皮(skinCluster)')
    if cmds.ls(type='constraint'):  sigs.append('约束(constraint)')
    if cmds.ls(type='lattice'):     sigs.append('晶格变形(lattice)')
    return sigs


def detect_wild_cameras():
    """检测所有非默认、非引用的野生摄像机。"""
    cams = cmds.ls(type='camera', long=True) or []
    startup_cams = {c for c in cams if cmds.camera(c, query=True, startupCamera=True)}
    wild = []
    for cam in cams:
        if cam in startup_cams:
            continue
        tr = cmds.listRelatives(cam, parent=True, fullPath=True)[0]
        short_name = tr.split('|')[-1]
        if short_name not in SUPPORTED_CAMERAS and not cmds.referenceQuery(tr, isNodeReferenced=True):
            wild.append(tr)
    return wild


def detect_plugin_residue():
    """检测 Turtle/MentalRay 等已知垃圾插件残留节点。"""
    residue = []
    for pattern in PLUGIN_RESIDUES:
        for n in (cmds.ls(pattern) or []):
            if not cmds.referenceQuery(n, isNodeReferenced=True):
                residue.append(n)
    return residue


def detect_unknown_nodes():
    """收集所有 unknown/unknownDag 节点与残留插件注册，按安全等级分类。"""
    unknowns = (cmds.ls(type='unknown') or []) + (cmds.ls(type='unknownDag') or [])
    unknown_plugins = cmds.unknownPlugin(q=True, list=True) or []

    safe_nodes, unsafe_nodes = [], []
    for n in unknowns:
        if cmds.referenceQuery(n, isNodeReferenced=True):
            continue
        plugin_src = ''
        try:
            plugin_src = cmds.unknownNode(n, q=True, plugin=True) or ''
        except RuntimeError as e:
            plugin_src = f'Unknown ({e})'
        if plugin_src and any(t.lower() in plugin_src.lower() for t in TRASH_PLUGINS):
            safe_nodes.append(n)
        elif any(pat in n for pat in SAFE_UNKNOWN_NAMES):
            safe_nodes.append(n)
        else:
            unsafe_nodes.append(f"{n} (Plugin: {plugin_src or 'Unknown'})")

    safe_plugins = [p for p in unknown_plugins if any(t.lower() in p.lower() for t in TRASH_PLUGINS)]
    unsafe_plugins = [p for p in unknown_plugins if not any(t.lower() in p.lower() for t in TRASH_PLUGINS)]

    return {
        'safe_nodes': safe_nodes, 'unsafe_nodes': unsafe_nodes,
        'safe_plugins': safe_plugins, 'unsafe_plugins': unsafe_plugins,
    }


def detect_timeline_animations():
    """精准识别手工 K 帧：仅标记输入端无连接的动画曲线（不误伤 SDK）。"""
    curves = []
    for c_type in ['animCurveTA', 'animCurveTL', 'animCurveTT', 'animCurveTU']:
        for c in (cmds.ls(type=c_type) or []):
            if cmds.referenceQuery(c, isNodeReferenced=True):
                continue
            if not cmds.listConnections(c + ".input", source=True, destination=False):
                curves.append(c)
    return curves


def detect_uv_anomalies(geom_scope):
    """检测多余/空壳 UV Set 以及唯一有效 Set 名称不是 map1 的情况。"""
    meshes = cmds.ls(geom_scope, type='mesh', long=True) if geom_scope else cmds.ls(type='mesh', long=True)
    issues = []
    for mesh in (meshes or []):
        if cmds.getAttr(mesh + ".intermediateObject"):
            continue
        if cmds.referenceQuery(mesh, isNodeReferenced=True):
            continue
        sets = cmds.polyUVSet(mesh, query=True, allUVSets=True) or []
        if len(sets) <= 1 and sets and sets[0] == 'map1':
            continue
        valid_sets = []
        for s in sets:
            cmds.polyUVSet(mesh, currentUVSet=True, uvSet=s)
            if cmds.polyEvaluate(mesh, uv=True) > 0:
                valid_sets.append(s)
        dead_sets = [s for s in sets if s not in valid_sets]
        rename_target = None
        if len(valid_sets) == 1 and valid_sets[0] != 'map1':
            rename_target = valid_sets[0]
        if len(sets) > 1 or dead_sets or rename_target or not valid_sets:
            issues.append({
                'mesh': mesh,
                'total_sets': len(sets),
                'dead_sets': dead_sets,
                'valid_sets': valid_sets,
                'rename_target': rename_target
            })
    return issues


# ═══════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════

def _short(name):
    """取短名（去掉 DAG 路径前缀）"""
    return name.split('|')[-1]


def _truncate(items):
    """截断列表至 MAX_DETAIL_ITEMS，超出部分用 '...及其他N个' 标记。"""
    if len(items) <= MAX_DETAIL_ITEMS:
        return items
    return items[:MAX_DETAIL_ITEMS] + [f"...及其他 {len(items) - MAX_DETAIL_ITEMS} 个"]


def _unlock_and_delete(nodes):
    """统一的解锁 → 删除，返回实际被删除的短名列表。"""
    deleted = []
    for n in nodes:
        if not cmds.objExists(n):
            continue
        if cmds.lockNode(n, q=True, lock=True)[0]:
            cmds.lockNode(n, lock=False)
        cmds.delete(n)
        deleted.append(_short(n))
    return deleted


def _parse_native_output(raw_lines):
    """
    解析 cleanUpScene / MLdeleteUnused 输出，按 'Removing xxx' 段落标题分组。

    Returns:
        {
            'summary': {category: count},
            'categories': {category: [node_name, ...]},
            'deleted_count': int
        }
    """
    import re
    summary = {}
    categories = {}
    current_cat = None
    total = 0

    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith('---'):
            continue

        # 段落标题: "Removing empty transforms"
        m = re.match(r'Removing\s+(.+)', line)
        if m:
            current_cat = m.group(1).strip()
            if current_cat not in categories:
                categories[current_cat] = []
            continue

        # Summary: "Removed N xxx"
        m = re.match(r'Removed\s+(\d+)\s+(.+)', line)
        if m:
            summary[m.group(2).strip()] = int(m.group(1))
            continue

        # Summary: "Cleaned out N datablocks"
        m = re.match(r'Cleaned out\s+(\d+)\s+(.+)', line)
        if m:
            summary[m.group(2).strip()] = int(m.group(1))
            continue

        # delete 行
        m = re.match(r'delete\s+"?([^";]+)"?\s*;?', line)
        if m:
            node = m.group(1).strip()
            total += 1
            if current_cat:
                categories[current_cat].append(node)

    return {
        'summary': summary,
        'categories': {k: v for k, v in categories.items() if v},
        'deleted_count': total
    }


def _capture_native_cleanup():
    """分别执行 cleanUpScene 3 和 MLdeleteUnused，独立捕获 + 独立计时。"""

    def _run_with_capture(mel_cmd):
        buf = []
        def _on_output(msg, msg_type, data):
            buf.append(msg)
        cb_id = om.MCommandMessage.addCommandOutputCallback(_on_output)
        t0 = time.time()
        mel.eval(mel_cmd)
        elapsed = round((time.time() - t0) * 1000, 2)
        om.MMessage.removeCallback(cb_id)
        return buf, elapsed

    mel.eval('source "cleanUpScene.mel"')
    for opt in CLEANUP_OPTIONVARS:
        cmds.optionVar(intValue=(opt, 1))

    # cleanUpScene 1 = 逐类别执行，每类输出 "Removing xxx" + delete 行，便于分组
    cleanup_lines, cleanup_ms = _run_with_capture('cleanUpScene 1')
    unused_lines, unused_ms = _run_with_capture('MLdeleteUnused()')

    cr = _parse_native_output(cleanup_lines)
    cr['elapsed_ms'] = cleanup_ms
    ur = _parse_native_output(unused_lines)
    ur['elapsed_ms'] = unused_ms
    return cr, ur


# ═══════════════════════════════════════
# 核心入口
# ═══════════════════════════════════════

def execute(payload: dict) -> dict:
    start_time = time.time()

    params = payload.get('parameters', payload) if 'parameters' in payload else payload
    mode = params.get('mode', 'check')

    # 场景来源信息
    source_path = payload.get('source_path', '')
    scene_path = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(scene_path or source_path) if (scene_path or source_path) else 'untitled'

    report = {
        'task_id': 'master-cleanup',
        'status': 'SUCCESS',
        'message': '',
        'scene_info': {
            'source_path': scene_path,
            'source_name': scene_name,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        },
        'execution_time_ms': 0,
        'fatal_blockers': [],
        'audit': {},
        'steps': []
    }

    # ── Phase 1: 管线级诊断 ──

    ctx = None
    if 'PipelineContext' in globals():
        try:
            ctx = globals()['PipelineContext'](cmds.file(query=True, sceneName=True) or "untitled.ma")
        except Exception as e:
            report['fatal_blockers'].append(f"Context Error: {e}")

    geom_scope = None
    if ctx and hasattr(ctx, 'geom_roots'):
        geom_scope, root_missing = resolve_geom_scope(ctx)
        if root_missing:
            report['fatal_blockers'].append(
                f"Hierarchy Error: 未命中任何合法根节点，目标: {ctx.geom_roots}"
            )

    if ctx and hasattr(ctx, 'step') and ctx.step in ['mod', 'uv', 'tex']:
        rig_sigs = detect_rig_signatures()
        if rig_sigs:
            report['fatal_blockers'].append(
                f"Rig Contamination: {ctx.step} 阶段禁止包含 {rig_sigs}"
            )

    # ── 全面扫描 ──

    wild_cams = detect_wild_cameras()
    residues = detect_plugin_residue()
    unknown_info = detect_unknown_nodes()
    timeline_anims = detect_timeline_animations()
    uv_issues = detect_uv_anomalies(geom_scope) if geom_scope else []

    if unknown_info['unsafe_plugins'] or unknown_info['unsafe_nodes']:
        report['fatal_blockers'].append(
            f"Unknown Security: 高危未知依赖 — 插件: {unknown_info['unsafe_plugins']}, "
            f"节点: {unknown_info['unsafe_nodes'][:5]}"
        )

    report['audit'] = {
        'wild_cameras': len(wild_cams),
        'plugin_residue': len(residues),
        'uv_anomalies': len(uv_issues) if geom_scope else -1,
        'timeline_animations': len(timeline_anims),
        'unknown_safe': len(unknown_info['safe_nodes']),
        'unknown_unsafe': len(unknown_info['unsafe_nodes']),
    }

    # ── Check 模式 ──

    if mode == 'check':
        report['details'] = {
            'wild_cameras': _truncate([_short(c) for c in wild_cams]),
            'plugin_residue': _truncate([_short(c) for c in residues]),
            'uv_issues': uv_issues[:MAX_DETAIL_ITEMS],
            'timeline_animations': _truncate([_short(c) for c in timeline_anims]),
            'unknown_safe': _truncate([_short(n) for n in unknown_info['safe_nodes']]),
            'unknown_unsafe': _truncate(unknown_info['unsafe_nodes']),
            'hazardous_plugins': unknown_info['unsafe_plugins']
        }

        if report['fatal_blockers']:
            report['status'] = 'FATAL_ERROR'
            report['message'] = "场景命中原则性红线，不可自动清理！需人为修复。"
        elif any(v > 0 for v in report['audit'].values() if v != -1):
            report['status'] = 'WARNING'
            report['message'] = "存在垃圾与冗余，建议启用 Fix 模式清理。"
        else:
            report['message'] = "场景检测极为纯净！"

        report['execution_time_ms'] = round((time.time() - start_time) * 1000, 2)

        # Receipt 适配
        check_items = []
        audit = report.get('audit', {})
        audit_labels = {
            'wild_cameras': '野生摄像机',
            'plugin_residue': '插件残留',
            'uv_anomalies': 'UV 异常',
            'timeline_animations': '时间轴K帧',
            'unknown_safe': '安全未知节点',
            'unknown_unsafe': '高危未知节点',
        }
        for key, label in audit_labels.items():
            cnt = audit.get(key, 0)
            if cnt > 0:
                check_items.append(make_item(name=label, detail=f'检出 {cnt} 个'))

        if report['fatal_blockers']:
            r_status = 'AUDIT_FAILED'
            action = f'诊断扫描 — 命中 {len(report["fatal_blockers"])} 条红线'
        elif check_items:
            r_status = 'SUCCESS'
            action = f'诊断扫描 — 检出 {sum(audit.get(k, 0) for k in audit_labels if audit.get(k, 0) > 0)} 项问题'
        else:
            r_status = 'SUCCESS'
            action = '诊断扫描 — 场景纯净'

        receipt = make_receipt(
            skill_id='maya_master_cleanup',
            status=r_status,
            start_time=start_time,
            summary_input=scene_name,
            summary_action=action,
            summary_count=len(check_items),
            summary_label='检出类别',
            items=check_items,
        )
        receipt['_legacy'] = report

        return receipt

    # ── Fix 红线卡控 ──

    if report['fatal_blockers']:
        report['status'] = 'ERROR'
        report['message'] = f"Fix 被系统熔断！必须先解决: {report['fatal_blockers']}"
        report['execution_time_ms'] = round((time.time() - start_time) * 1000, 2)
        receipt = make_receipt(
            skill_id='maya_master_cleanup',
            status='AUDIT_FAILED',
            start_time=start_time,
            summary_input=scene_name,
            summary_action=f'Fix 熔断 — {len(report["fatal_blockers"])} 条红线',
            error='; '.join(report['fatal_blockers']),
        )
        receipt['_legacy'] = report
        return receipt

    # ═══════════════════════════════════════
    # Phase 2: 管线级精准清理（每步计时）
    # ═══════════════════════════════════════

    def _timed_step(fn):
        t0 = time.time()
        r = fn()
        return r, round((time.time() - t0) * 1000, 2)

    with _undo_chunk('maya_master_cleanup_fix'):
        # 2-1. 未知节点
        def _do_unknown():
            dn = _unlock_and_delete(unknown_info['safe_nodes'])
            rp = []
            for p in unknown_info['safe_plugins']:
                cmds.unknownPlugin(p, remove=True)
                rp.append(p)
            return dn, rp
        (deleted_names, removed_plugins), ms = _timed_step(_do_unknown)
        if deleted_names or removed_plugins:
            report['steps'].append({
                'name': '未知节点与插件注册表清理',
                'count': len(deleted_names) + len(removed_plugins),
                'deleted_nodes': _truncate(deleted_names),
                'removed_plugins': removed_plugins,
                'elapsed_ms': ms
            })

        # 2-2. 插件底噪
        deleted_names, ms = _timed_step(lambda: _unlock_and_delete(residues))
        if deleted_names:
            report['steps'].append({
                'name': '插件底噪残留',
                'count': len(deleted_names),
                'items': _truncate(deleted_names),
                'elapsed_ms': ms
            })

        # 2-3. 野生相机
        deleted_names, ms = _timed_step(lambda: _unlock_and_delete(wild_cams))
        if deleted_names:
            report['steps'].append({
                'name': '野生摄像机',
                'count': len(deleted_names),
                'items': _truncate(deleted_names),
                'elapsed_ms': ms
            })

        # 2-4. K 帧
        deleted_names, ms = _timed_step(lambda: _unlock_and_delete(timeline_anims))
        if deleted_names:
            report['steps'].append({
                'name': '时间轴测试K帧',
                'count': len(deleted_names),
                'items': _truncate(deleted_names),
                'elapsed_ms': ms
            })

        # 2-5. UV Set 修复
        def _do_uv():
            fixed = []
            for issue in uv_issues:
                mesh = issue['mesh']
                for dset in issue['dead_sets']:
                    cmds.polyUVSet(mesh, delete=True, uvSet=dset)
                if issue.get('rename_target'):
                    cmds.polyUVSet(mesh, rename=True, uvSet=issue['rename_target'], newUVSet='map1')
                fixed.append(_short(mesh))
            return fixed
        uv_fixed, ms = _timed_step(_do_uv)
        if uv_fixed:
            report['steps'].append({
                'name': 'UV Set拓扑修复',
                'count': len(uv_fixed),
                'items': _truncate(uv_fixed),
                'elapsed_ms': ms
            })

        # ═══════════════════════════════════════
        # Phase 3: 原生引擎兜底（内部各自计时）
        # ═══════════════════════════════════════

        cleanup_result, unused_result = _capture_native_cleanup()

        # cleanUpScene — 按类别拆分
        cleanup_active = {k: v for k, v in cleanup_result['summary'].items() if v > 0}
        if cleanup_active or cleanup_result['deleted_count'] > 0:
            report['steps'].append({
                'name': 'cleanUpScene 优化',
                'summary': cleanup_active,
                'categories': {k: _truncate(v) for k, v in cleanup_result['categories'].items()},
                'deleted_count': cleanup_result['deleted_count'],
                'elapsed_ms': cleanup_result['elapsed_ms']
            })

        # MLdeleteUnused
        if unused_result['deleted_count'] > 0:
            report['steps'].append({
                'name': 'MLdeleteUnused 深度材质清理',
                'deleted_count': unused_result['deleted_count'],
                'categories': {k: _truncate(v) for k, v in unused_result['categories'].items()},
                'elapsed_ms': unused_result['elapsed_ms']
            })

    # ── 输出 ──

    total_ms = round((time.time() - start_time) * 1000, 2)
    report['message'] = "Fix 模式已执行完毕！"
    report['execution_time_ms'] = total_ms
    report['execution_time_min'] = round(total_ms / 60000, 2)

    # 生成 MD 报告内容（不落盘，通过 receipt 传回主控）
    report_content_str = ""
    try:
        report_content_str = _generate_report_md(report)
    except Exception as e:
        report['report_export_error'] = str(e)

    # Receipt 适配：将 steps[] 映射为 items[]
    fix_items = []
    for step in report.get('steps', []):
        count = step.get('count', step.get('deleted_count', 0))
        detail = f'清理 {count} 个' if count else '已执行'
        fix_items.append(make_item(
            name=step['name'],
            detail=detail,
            elapsed_min=_ms_to_min(step.get('elapsed_ms', 0)),
        ))

    total_cleaned = sum(
        s.get('count', s.get('deleted_count', 0)) for s in report.get('steps', [])
    )

    receipt = make_receipt(
        skill_id='maya_master_cleanup',
        status='SUCCESS',
        start_time=start_time,
        summary_input=scene_name,
        summary_action=f'场景清理 (fix)',
        summary_count=total_cleaned,
        summary_label='清理项',
        items=fix_items,
        report_content=report_content_str,
    )
    receipt['_legacy'] = report

    return receipt


def _generate_report_md(report):
    """将结构化报告生成为可读 Markdown 字符串（纯内存生成，不落盘）。"""
    L = []  # lines accumulator
    info = report.get('scene_info', {})
    total_ms = report.get('execution_time_ms', 0)
    total_min = report.get('execution_time_min', round(total_ms / 60000, 2))

    L.append("# 场景清理报告")
    L.append("")
    L.append("| 项目 | 值 |")
    L.append("|---|---|")
    L.append(f"| 来源文件 | `{info.get('source_name', '')}` |")
    L.append(f"| 完整路径 | `{info.get('source_path', '')}` |")
    L.append(f"| 执行时间 | {info.get('timestamp', '')} |")
    L.append(f"| 总耗时 | **{total_min} 分钟** ({total_ms} ms) |")
    L.append(f"| 状态 | **{report.get('status', '')}** |")
    L.append("")

    # 红线
    blockers = report.get('fatal_blockers', [])
    if blockers:
        L.append("## ⛔ 红线熔断")
        for b in blockers:
            L.append(f"- {b}")
        L.append("")

    # 审计概览
    audit = report.get('audit', {})
    if audit:
        L.append("## 审计概览")
        L.append("")
        L.append("| 检查项 | 数量 |")
        L.append("|---|---|")
        labels = {
            'wild_cameras': '野生摄像机',
            'plugin_residue': '插件底噪残留',
            'uv_anomalies': 'UV 异常',
            'timeline_animations': '时间轴 K 帧',
            'unknown_safe': '安全未知节点',
            'unknown_unsafe': '⚠️ 高危未知节点',
        }
        for k, v in audit.items():
            label = labels.get(k, k)
            val = '未扫描' if v == -1 else str(v)
            L.append(f"| {label} | {val} |")
        L.append("")

    # 清理步骤
    steps = report.get('steps', [])
    if steps:
        L.append("## 清理详情")
        L.append("")
        for i, step in enumerate(steps, 1):
            name = step.get('name', f'Step {i}')
            elapsed = step.get('elapsed_ms', '')
            time_tag = f" ⏱ {elapsed} ms" if elapsed else ''
            L.append(f"### {i}. {name}{time_tag}")
            L.append("")

            # 插件注册表（Phase 2 未知节点专属）
            plugins = step.get('removed_plugins')
            if plugins:
                L.append(f"**移除插件注册：** {', '.join(f'`{p}`' for p in plugins)}")
                L.append("")

            # Pipeline step: items 列表 / deleted_nodes（Phase 2）
            count = step.get('count', 0)
            items = step.get('items') or step.get('deleted_nodes') or []
            if count:
                L.append(f"**共清理 {count} 个**")
                L.append("")
            if items:
                for item in items[:20]:
                    L.append(f"- `{item}`")
                if len(items) > 20:
                    L.append(f"- *...及其他 {len(items) - 20} 个*")
                L.append("")

            # Phase 3: 按类别分组输出（核心改进）
            categories = step.get('categories', {})
            summary = step.get('summary', {})

            if categories:
                for cat, nodes in categories.items():
                    cat_count = summary.get(cat, len(nodes))
                    L.append(f"#### {cat} ({cat_count})")
                    L.append("")
                    for n in nodes[:20]:
                        L.append(f"- `{n}`")
                    if len(nodes) > 20:
                        L.append(f"- *...及其他 {len(nodes) - 20} 个*")
                    L.append("")
                # summary 有值但 categories 没详情的（delete 行被跳过 / 无对应段落）
                for cat, cnt in summary.items():
                    if cnt > 0 and cat not in categories:
                        L.append(f"#### {cat} ({cnt})")
                        L.append("")
            elif summary:
                # 无 categories 时回退到表格
                L.append("| 类别 | 清理数量 |")
                L.append("|---|---|")
                for cat, cnt in summary.items():
                    if cnt > 0:
                        L.append(f"| {cat} | {cnt} |")
                L.append("")

            dc = step.get('deleted_count', 0)
            if dc and not count:
                L.append(f"**本步骤共清理 {dc} 个节点**")
                L.append("")
    else:
        L.append("## ✅ 场景已处于纯净状态，无需清理")
        L.append("")

    L.append("---")
    L.append("*由 CGI Pipeline master_cleanup 自动生成*")

    return '\n'.join(L)
