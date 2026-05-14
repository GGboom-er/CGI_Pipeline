# -*- coding: utf-8 -*-
# tests/test_write_task_report.py
# 造假 audit JSONL，验证 write_task_report 渲染产出结构正确。

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skills.write_task_report.write_task_report import execute


def _make_audit(task_id: str, entries: list) -> Path:
    p = ROOT / 'audit' / f'{task_id}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in entries),
                 encoding='utf-8')
    return p


def _make_sandbox(name: str) -> Path:
    d = ROOT / 'projects' / '_test_report' / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_happy_path():
    """正常完整 chain：fetch + open + compare + success。"""
    tid = 'test-happy-001'
    sbx = _make_sandbox(tid)

    step_receipt_fetch = {
        'skill_id': 'copy_files', 'status': 'SUCCESS', 'elapsed_min': 0.05,
        'summary': {'input': 'X:/prod/asset.ma', 'action': '→ sandbox',
                    'output_count': 1, 'output_label': '文件'},
        'items': [{'name': 'asset.ma', 'detail': '188.3 MB'}],
        'outputs': {
            'output_path': str(sbx / 'asset.ma'),
            'result': {'copied_count': 1, 'total_bytes': 197_000_000},
        },
    }
    step_receipt_compare = {
        'skill_id': 'pipeline_compare_asset', 'status': 'SUCCESS', 'elapsed_min': 0.8,
        'summary': {'input': '2 jsons', 'action': '三步漏斗: 配对 78, 差异 6',
                    'output_count': 6, 'output_label': '差异'},
        'items': [],
        'outputs': {'output_path': str(sbx / '.info' / 'asset_compare_result.json')},
        'report_content': '# 对比报告\n- 75 identical\n- 3 modified',
    }
    entries = [
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_STARTED', 'ts': 100, 'step': -1, 'total_steps': 2, 'detail': '2 skills'},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_OPEN_FILE', 'ts': 101, 'step': -1, 'detail': str(sbx / 'asset.ma')},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_FILE_OPENED', 'ts': 105, 'step': -1, 'detail': str(sbx / 'asset.ma')},
        {'task_id': tid, 'skill_id': 'copy_files', 'status': 'STEP_SUCCESS', 'ts': 110,
         'step': 0, 'total_steps': 2, 'detail': json.dumps(step_receipt_fetch)},
        {'task_id': tid, 'skill_id': 'pipeline_compare_asset', 'status': 'STEP_SUCCESS', 'ts': 150,
         'step': 1, 'total_steps': 2, 'detail': json.dumps(step_receipt_compare)},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_SUCCESS', 'ts': 155,
         'step': -1, 'detail': '2 steps completed'},
    ]
    _make_audit(tid, entries)

    r = execute({
        'task_id': tid,
        'asset_name': 'mihouwang',
        'project': 'ysj',
        'source_path': str(sbx / 'asset.ma'),
        'parameters': {'run_dir': str(sbx), 'mode': 'normal'},
    })

    assert r['status'] == 'SUCCESS', r
    out = Path(r['outputs']['output_path'])
    assert out.exists(), f'md not written: {out}'
    content = out.read_text(encoding='utf-8')

    # 关键内容必须出现
    assert '# mihouwang 任务报告' in content
    assert tid in content
    assert 'SUCCESS' in content
    assert '📥 取文件' in content, '缺 fetch label'
    assert '📂 开场景' in content, '缺 open file 段'
    assert '⚖️ 对比' in content, '缺 compare label'
    assert 'asset.ma' in content
    assert '188.3 MB' in content, '缺 items 详情'
    assert '完整对比报告' in content, '缺 compare report_content'
    assert '<details>' not in content
    assert '<summary>' not in content
    assert r['outputs']['result']['units_rendered'] == 2
    print(f'✓ test_happy_path  →  {out}')


def test_hold_mode():
    """hold 场景：chain 跑到一半触发 NEEDS_ATTENTION，报告应含暂停警告块。"""
    tid = 'test-hold-001'
    sbx = _make_sandbox(tid)

    step_collect = {
        'skill_id': 'maya_build_asset_info', 'status': 'SUCCESS', 'elapsed_min': 0.3,
        'summary': {'input': 'scene', 'action': '采集',
                    'output_count': 84, 'output_label': 'mesh'},
        'items': [],
        'outputs': {'output_path': str(sbx / 'asset_info.json')},
    }
    step_held = {
        'skill_id': 'maya_sync_rig_incremental', 'status': 'NEEDS_ATTENTION', 'elapsed_min': 0.1,
        'summary': {'action': '需要人工决策'},
        'items': [{'name': 'hair17Shape', 'detail': '拓扑差异无法自动合并'}],
        'outputs': {},
        'error': '存在 3 个 NEEDS_ATTENTION 的 mesh',
        'recovery_hint': '检查 rig/tex 对应关系或手动指定映射',
    }
    entries = [
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_STARTED', 'ts': 0, 'detail': '3 skills', 'step': -1},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_FILE_OPENED', 'ts': 5, 'detail': 'scene.ma', 'step': -1},
        {'task_id': tid, 'skill_id': 'maya_build_asset_info', 'status': 'STEP_SUCCESS', 'ts': 20,
         'step': 0, 'detail': json.dumps(step_collect)},
        {'task_id': tid, 'skill_id': 'maya_sync_rig_incremental', 'status': 'STEP_NEEDS_ATTENTION',
         'ts': 25, 'step': 1, 'detail': json.dumps(step_held)},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_HELD', 'ts': 26,
         'step': 1, 'detail': 'step 1 (maya_sync_rig_incremental) 需要用户决策'},
    ]
    _make_audit(tid, entries)

    remaining = json.dumps([{'skill_id': 'save_scene', 'parameters': {}}])
    r = execute({
        'task_id': tid,
        'asset_name': 'mihouwang',
        'project': 'ysj',
        'source_path': str(sbx / 'scene.ma'),
        'parameters': {
            'run_dir': str(sbx),
            'mode': 'hold',
            'hold_skill_id': 'maya_sync_rig_incremental',
            'hold_step_idx': 1,
            'hold_detail': '存在 3 个 NEEDS_ATTENTION 的 mesh',
            'hold_remaining': remaining,
        },
    })

    assert r['status'] == 'SUCCESS', r
    out = Path(r['outputs']['output_path'])
    content = out.read_text(encoding='utf-8')
    assert '⚠️' in content, '缺 hold banner'
    assert '链式执行暂停' in content
    assert 'maya_sync_rig_incremental' in content
    assert '未执行的剩余步骤' in content
    assert 'save_scene' in content, '剩余步骤应该展示 save_scene'
    assert 'NEEDS_ATTENTION' in content
    assert 'hair17Shape' in content, 'items 应该展示'
    assert '恢复建议' in content
    assert '<details>' not in content
    assert '<summary>' not in content
    print(f'✓ test_hold_mode  →  {out}')


def test_broken_detail_fallback():
    """审计 detail 不是合法 JSON（模拟底层崩溃字符串），应该降级包装。"""
    tid = 'test-broken-001'
    sbx = _make_sandbox(tid)
    entries = [
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_STARTED', 'ts': 0, 'detail': '1 skill', 'step': -1},
        {'task_id': tid, 'skill_id': 'weird_skill', 'status': 'STEP_ERROR', 'ts': 10,
         'step': 0, 'detail': 'Traceback (most recent call last):\n  RuntimeError: segfault\n'},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_ABORTED', 'ts': 11,
         'step': 0, 'detail': 'step 0 failed: weird_skill'},
    ]
    _make_audit(tid, entries)

    r = execute({
        'task_id': tid,
        'asset_name': 'test',
        'project': 'ysj',
        'parameters': {'run_dir': str(sbx), 'mode': 'normal'},
    })
    assert r['status'] == 'SUCCESS'
    content = Path(r['outputs']['output_path']).read_text(encoding='utf-8')
    assert 'weird_skill' in content
    assert 'segfault' in content, 'raw detail 应该在 fallback 里展示'
    assert 'ERROR' in content
    print(f'✓ test_broken_detail_fallback  →  {r["outputs"]["output_path"]}')


def test_empty_audit():
    """audit 空（task_id 不存在），应该报错而非崩溃。"""
    tid = 'test-empty-001'
    sbx = _make_sandbox(tid)
    # 不创建 audit
    r = execute({
        'task_id': tid,
        'asset_name': 'test',
        'project': 'ysj',
        'parameters': {'run_dir': str(sbx), 'mode': 'normal'},
    })
    # 空 audit 应当仍能产出 md（最小头 + 提示）
    assert r['status'] == 'SUCCESS', r
    content = Path(r['outputs']['output_path']).read_text(encoding='utf-8')
    assert 'test-empty-001' in content
    print(f'✓ test_empty_audit  →  {r["outputs"]["output_path"]}')


def test_missing_task_id():
    """不传 task_id 应该 ERROR。"""
    r = execute({'asset_name': 'test', 'parameters': {}})
    assert r['status'] == 'ERROR'
    assert 'task_id' in r.get('error', '')
    print('✓ test_missing_task_id')


def test_workflow_mode():
    """workflow 模式：读 SEGMENT_* + STEP_* entries，渲染多段概览与段内步骤明细。"""
    tid = 'test-workflow-001'
    sbx = _make_sandbox(tid)

    seg0 = {
        'dcc': 'blender', 'step_count': 1, 'elapsed_min': 0.5,
        'summary': {'action': 'Blender 导 ABC'},
    }
    seg1 = {
        'dcc': 'maya', 'step_count': 3, 'elapsed_min': 2.1,
        'summary': {'action': 'Maya 采集+清理+存盘'},
    }
    # 模拟段内 STEP_*（task_id 用子段 id `{wf}_seg{N}`，workflow_id=master tid）
    step_seg0 = {
        'skill_id': 'blender_export_abc', 'status': 'SUCCESS',
        'elapsed_min': 0.4,
        'summary': {'action': 'Blender ABC 导出', 'output_count': 85, 'output_label': 'mesh'},
    }
    step_seg1a = {
        'skill_id': 'maya_build_asset_info', 'status': 'SUCCESS',
        'elapsed_min': 0.3, 'summary': {'action': '采集资产信息'},
    }
    step_seg1b = {
        'skill_id': 'maya_clean_skinweights', 'status': 'SUCCESS',
        'elapsed_min': 1.2, 'summary': {'action': '清理 skinweights'},
    }

    entries = [
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'WORKFLOW_STARTED', 'ts': 1000, 'detail': 'workflow=demo'},
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'SEGMENT_START', 'ts': 1001, 'detail': 'seg 0'},
        {'task_id': f'{tid}_seg0', 'workflow_id': tid, 'status': 'STEP_SUCCESS', 'ts': 1020, 'step': 0, 'detail': json.dumps(step_seg0)},
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'SEGMENT_SUCCESS', 'ts': 1030, 'detail': json.dumps(seg0)},
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'SEGMENT_START', 'ts': 1031, 'detail': 'seg 1'},
        {'task_id': f'{tid}_seg1', 'workflow_id': tid, 'status': 'STEP_SUCCESS', 'ts': 1050, 'step': 0, 'detail': json.dumps(step_seg1a)},
        {'task_id': f'{tid}_seg1', 'workflow_id': tid, 'status': 'STEP_SUCCESS', 'ts': 1120, 'step': 1, 'detail': json.dumps(step_seg1b)},
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'SEGMENT_SUCCESS', 'ts': 1160, 'detail': json.dumps(seg1)},
        {'task_id': tid, 'workflow_id': 'wf_demo', 'status': 'SUCCESS', 'ts': 1161, 'detail': 'all done'},
    ]
    _make_audit(tid, entries)

    r = execute({
        'task_id': tid,
        'asset_name': 'demo_asset',
        'project': 'ysj',
        'parameters': {'run_dir': str(sbx), 'mode': 'workflow'},
    })

    assert r['status'] == 'SUCCESS', r
    assert r['outputs']['result']['mode'] == 'workflow'
    assert r['outputs']['result']['units_rendered'] == 2, r['outputs']
    content = Path(r['outputs']['output_path']).read_text(encoding='utf-8')
    assert 'Segment 0' in content, content
    assert 'Segment 1' in content, content
    assert 'blender' in content and 'maya' in content
    assert 'Blender 导 ABC' in content
    assert 'Maya 采集' in content
    # 段内步骤明细已展开（用 action 文本作为稳定断言，summary 行不再显式含 skill_id）
    assert 'Blender 导 ABC' in content and 'Step 1' in content, content
    assert 'Maya 采集' in content and 'Step 2' in content, content
    assert '清理 skinweights' in content or 'maya_clean_skinweights' in content, content
    assert '<details>' not in content
    assert '<summary>' not in content
    # 新设计：不再有子链报告链接
    assert 'blender_seg0.md' not in content
    print(f'✓ test_workflow_mode  →  {r["outputs"]["output_path"]}')


def test_broken_json_fallback_marker():
    """detail 是半截 JSON（例如 chain 截断残留）时，渲染应标明'损坏'而非把 JSON 塞进 action。"""
    tid = 'test-broken-json-001'
    sbx = _make_sandbox(tid)

    # 模拟一段被截断的 sync_rig receipt JSON
    truncated = '{"status": "SUCCESS", "skill_id": "sync", "summary": {"input":'
    entries = [
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_STARTED', 'ts': 100, 'detail': '1 skills'},
        {'task_id': tid, 'skill_id': 'sync', 'status': 'STEP_SUCCESS', 'ts': 200,
         'step': 0, 'detail': truncated + ' [mem=1.00GB]'},
        {'task_id': tid, 'skill_id': 'chain', 'status': 'CHAIN_SUCCESS', 'ts': 205, 'detail': '1 step'},
    ]
    _make_audit(tid, entries)

    r = execute({
        'task_id': tid, 'asset_name': 'test', 'project': 'ysj',
        'parameters': {'run_dir': str(sbx), 'mode': 'normal'},
    })
    content = Path(r['outputs']['output_path']).read_text(encoding='utf-8')
    assert 'detail 损坏或截断' in content, content
    # 不应该把半截 JSON 作为 action 塞进去
    summary_line = next(line for line in content.splitlines() if line.startswith('### Step '))
    assert '{"status":' not in summary_line
    assert '<details>' not in content
    assert '<summary>' not in content
    print(f'✓ test_broken_json_fallback_marker  →  {r["outputs"]["output_path"]}')


if __name__ == '__main__':
    print('=== write_task_report unit tests ===\n')
    test_happy_path()
    test_hold_mode()
    test_broken_detail_fallback()
    test_empty_audit()
    test_missing_task_id()
    test_workflow_mode()
    test_broken_json_fallback_marker()
    print('\n✅ all pass')
