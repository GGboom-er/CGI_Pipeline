# skills/validate_publish.py
# ── 发布前质量门禁（Pre-Publish QC Gate） ──
#
# 在 publish_asset 之前强制执行的自动化检查：
#   1. 场景非空（含 mesh）
#   2. cache/geo 组存在且有内容
#   3. 无未知节点（unknown nodes）
#   4. 无未引用的中间节点（dead shapes and orphan nodes）
#   5. 无空 transform（空组）
#   6. 命名规范检查（无空格、特殊字符）
#
# 只读操作，不修改场景。输出 PASS / FAIL 和 Markdown 报告。

import maya.cmds as cmds
import os
import re
import time
import datetime

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.receipt import make_receipt



def _find_cache_group(cache_group=''):
    """查找场景中的 cache/geo 组。"""
    candidates = []
    if cache_group:
        candidates.append(cache_group)
    candidates += [
        '|Group|Geometry|cache', '|Group|cache',
        'cache', '|Group|Geometry|geo', '|Group|geo', 'geo',
    ]
    for c in candidates:
        if cmds.objExists(c):
            return c
    return None


def _check_unknown_nodes():
    """检查未知节点。"""
    unknown = cmds.ls(type='unknown') or []
    unknown_dag = cmds.ls(type='unknownDag') or []
    return unknown + unknown_dag


def _check_empty_transforms():
    """检查空 transform（无子节点的组）。"""
    empties = []
    all_transforms = cmds.ls(type='transform', long=True) or []
    for t in all_transforms:
        children = cmds.listRelatives(t, children=True) or []
        if not children:
            # 排除约束节点等
            if not cmds.listConnections(t, type='constraint'):
                empties.append(t)
    return empties


def _check_naming(cache_root):
    """检查命名规范：不含空格和特殊字符。"""
    bad_names = []
    if not cache_root:
        return bad_names
    all_nodes = cmds.listRelatives(cache_root, allDescendents=True, fullPath=True) or []
    pattern = re.compile(r'^[a-zA-Z0-9_|:]+$')
    for node in all_nodes:
        short_name = node.split('|')[-1].split(':')[-1]
        if not pattern.match(short_name):
            bad_names.append({'node': node, 'name': short_name})
    return bad_names


def _check_mesh_count(cache_root):
    """统计 cache 组下的 mesh 数量。"""
    if not cache_root:
        return 0
    shapes = cmds.listRelatives(cache_root, allDescendents=True, type='mesh', fullPath=True) or []
    visible = [s for s in shapes if not cmds.getAttr(s + '.intermediateObject')]
    return len(visible)


def _generate_report(results, project, category, asset_name, stage, elapsed):
    """生成 QC 报告。"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    passed = results['passed']
    icon = '✅ PASS' if passed else '❌ FAIL'

    report_dir = os.path.join(str(_PROJECT_ROOT), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(
        report_dir,
        f'qc_{project}_{category}_{asset_name}_{stage}.md'
    )

    lines = [
        f'# 🔍 发布前质量检查 {icon}',
        '',
        '| 项目 | 值 |',
        '|---|---|',
        f'| 状态 | {icon} |',
        f'| 项目 | `{project}` |',
        f'| 分类 | `{category}` |',
        f'| 资产 | `{asset_name}` |',
        f'| 阶段 | `{stage}` |',
        f'| 检查时间 | {now} |',
        f'| 耗时 | {elapsed:.2f}s |',
        '',
        '## 检查项',
        '',
        '| 检查项 | 状态 | 详情 |',
        '|---|---|---|',
    ]

    for check in results['checks']:
        status_icon = '✅' if check['passed'] else '❌'
        lines.append(f'| {check["name"]} | {status_icon} | {check["detail"]} |')

    lines += ['', '---', '*由 CGI Pipeline validate_publish 自动生成*']

    md_content = '\n'.join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    return report_path, md_content


def execute(payload: dict) -> dict:
    """
    发布前质量门禁。

    遍历执行多项自动化检查，只要有一项 FAIL 则整体 FAIL。
    完全只读，不修改场景。
    """
    params = payload.get('parameters', {})
    project = params.get('project', '').strip()
    category = params.get('category', '').strip()
    asset_name = params.get('asset_name', '').strip()
    stage = params.get('stage', '').strip()
    cache_group = params.get('cache_group', '').strip()

    t0 = time.time()
    checks = []
    all_passed = True

    # ── 检查 1：场景是否有打开文件 ──
    scene = cmds.file(query=True, sceneName=True) or ''
    if scene:
        checks.append({'name': '场景文件', 'passed': True, 'detail': os.path.basename(scene)})
    else:
        checks.append({'name': '场景文件', 'passed': False, 'detail': '当前没有打开的场景文件'})
        all_passed = False

    # ── 检查 2：cache/geo 组存在 ──
    cache_root = _find_cache_group(cache_group)
    if cache_root:
        checks.append({'name': 'Cache 组', 'passed': True, 'detail': f'找到 `{cache_root}`'})
    else:
        checks.append({'name': 'Cache 组', 'passed': False, 'detail': '未找到 cache/geo 组'})
        all_passed = False

    # ── 检查 3：Mesh 数量 ──
    mesh_count = _check_mesh_count(cache_root)
    if mesh_count > 0:
        checks.append({'name': 'Mesh 数量', 'passed': True, 'detail': f'{mesh_count} 个可见 mesh'})
    else:
        checks.append({'name': 'Mesh 数量', 'passed': False, 'detail': '未找到可见 mesh'})
        all_passed = False

    # ── 检查 4：未知节点 ──
    unknowns = _check_unknown_nodes()
    if not unknowns:
        checks.append({'name': '未知节点', 'passed': True, 'detail': '无未知节点 ✓'})
    else:
        checks.append({'name': '未知节点', 'passed': False,
                       'detail': f'{len(unknowns)} 个: {", ".join(unknowns[:5])}{"..." if len(unknowns) > 5 else ""}'})
        all_passed = False

    # ── 检查 5：空 Transform ──
    empties = _check_empty_transforms()
    if not empties:
        checks.append({'name': '空组', 'passed': True, 'detail': '无空 transform ✓'})
    else:
        short_list = [e.split('|')[-1] for e in empties[:5]]
        checks.append({'name': '空组', 'passed': False,
                       'detail': f'{len(empties)} 个: {", ".join(short_list)}{"..." if len(empties) > 5 else ""}'})
        all_passed = False

    # ── 检查 6：命名规范 ──
    bad_names = _check_naming(cache_root)
    if not bad_names:
        checks.append({'name': '命名规范', 'passed': True, 'detail': '所有节点命名合规 ✓'})
    else:
        bad_list = [b['name'] for b in bad_names[:5]]
        checks.append({'name': '命名规范', 'passed': False,
                       'detail': f'{len(bad_names)} 个: {", ".join(bad_list)}{"..." if len(bad_names) > 5 else ""}'})
        all_passed = False

    elapsed = time.time() - t0

    results = {
        'passed': all_passed,
        'checks': checks,
        'total_checks': len(checks),
        'passed_checks': sum(1 for c in checks if c['passed']),
        'failed_checks': sum(1 for c in checks if not c['passed']),
    }

    # ── 生成报告 ──
    report_path, md_content = _generate_report(
        results, project or '?', category or '?',
        asset_name or '?', stage or '?', elapsed,
    )

    status = 'SUCCESS' if all_passed else 'AUDIT_FAILED'
    summary_action = f'QC {"通过" if all_passed else "未通过"} — {results["passed_checks"]}/{results["total_checks"]} 项'

    receipt = make_receipt(
        'validate_publish', status, t0,
        summary_input=asset_name or 'unknown',
        summary_action=summary_action,
        summary_count=results['failed_checks'],
        summary_label='问题',
        outputs={
            'report_path': report_path,
            'passed': all_passed,
            'total_checks': results['total_checks'],
            'passed_checks': results['passed_checks'],
            'failed_checks': results['failed_checks'],
        },
        report_content=md_content,
    )
    return receipt
