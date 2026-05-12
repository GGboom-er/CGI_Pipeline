# tests/test_compare_asset.py
# ── compare_asset 固定测试 ──
#
# 用 xtbyao 已有数据跑全流程验证。
#
# 术语约定：
#   tex       = Blender 贴图源文件 (.blend)
#   tex_json  = 从 tex 源文件采集的 JSON
#   rig       = Maya 绑定源文件 (.ma)
#   rig_json  = 从 rig 源文件采集的 JSON
#
# 运行方式：conda run -n cgi_pipeline python tests/test_compare_asset.py

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ═══════════════════════════════════════════
# 已知测试数据路径
# ═══════════════════════════════════════════

# tex 源文件 → tex_json
TEX_SOURCE = r'y:\runs\assets\chr\xtbyao\tex\texMaster\ysj_chr_xtbyao_tex_texMaster_v002.blend'
TEX_JSON   = r'y:\runs\assets\chr\xtbyao\tex\texMaster\.info\ysj_chr_xtbyao_tex_texMaster_v002.json'

# rig 源文件 → rig_json
RIG_SOURCE = r'y:\runs\assets\chr\xtbyao\rig\rigMaster\ysj_chr_xtbyao_rig_rigMaster_v002.ma'
RIG_JSON   = r'y:\runs\assets\chr\xtbyao\rig\rigMaster\.info\ysj_chr_xtbyao_rig_rigMaster_v002.json'


def _header(title):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print(f'{"=" * 60}')


def _check(name, condition, detail=''):
    status = '✅ PASS' if condition else '❌ FAIL'
    msg = f'  {status}  {name}'
    if detail:
        msg += f'  ({detail})'
    print(msg)
    return condition


# ═══════════════════════════════════════════
# Test 1: 数据文件存在性
# ═══════════════════════════════════════════

def test_files_exist():
    _header('Test 1: 数据文件存在性')
    ok = True
    ok &= _check('tex_json 存在', os.path.isfile(TEX_JSON), TEX_JSON)
    ok &= _check('rig_json 存在', os.path.isfile(RIG_JSON), RIG_JSON)
    print(f'\n  tex 源文件: {TEX_SOURCE}')
    print(f'  rig 源文件: {RIG_SOURCE}')
    print(f'  tex_json:   {TEX_JSON}')
    print(f'  rig_json:   {RIG_JSON}')
    return ok


# ═══════════════════════════════════════════
# Test 2: tex_json vs rig_json — KDTree 引擎验证
# ═══════════════════════════════════════════

def test_tex_json_vs_rig_json():
    _header('Test 2: tex_json vs rig_json (全局 KDTree)')

    from core.asset_info_schema import compare

    print(f'  输入 A: tex_json (来源: tex 源文件)')
    print(f'    {TEX_JSON}')
    print(f'  输入 B: rig_json (来源: rig 源文件)')
    print(f'    {RIG_JSON}')
    print()

    with open(TEX_JSON, 'r', encoding='utf-8') as f:
        tex_info = json.load(f)
    with open(RIG_JSON, 'r', encoding='utf-8') as f:
        rig_info = json.load(f)

    t0 = time.time()
    result = compare(tex_info, rig_info, label_a='tex', label_b='rig')
    elapsed = time.time() - t0

    paired = result['paired']
    only_a = result['only_a']
    only_b = result['only_b']

    n_identical = sum(1 for p in paired if p.get('actionability') == 'IDENTICAL')
    n_orig_inject = sum(1 for p in paired if p.get('actionability') == 'ORIG_INJECT')
    n_modified = sum(1 for p in paired if p.get('actionability') == 'MODIFIED')
    n_merge = sum(1 for p in paired if p.get('actionability') == 'MERGE')
    n_split = sum(1 for p in paired if p.get('actionability') == 'SPLIT')

    print(f'  结果:')
    print(f'    耗时: {elapsed:.3f}s')
    print(f'    配对: {len(paired)} '
          f'(IDENTICAL={n_identical}, ORIG_INJECT={n_orig_inject}, '
          f'MODIFIED={n_modified}, MERGE={n_merge}, SPLIT={n_split})')
    print(f'    仅tex: {len(only_a)}, 仅rig: {len(only_b)}')
    print()

    ok = True
    ok &= _check('耗时 < 5s', elapsed < 5, f'{elapsed:.3f}s')
    ok &= _check('配对数 > 0', len(paired) > 0, str(len(paired)))
    ok &= _check('paired 结构完整',
                 all('dag_a' in p and 'dag_b' in p and 'actionability' in p for p in paired))
    ok &= _check('actionability 取值合法',
                 all(p['actionability'] in ('IDENTICAL', 'ORIG_INJECT', 'MODIFIED', 'MERGE', 'SPLIT')
                     for p in paired))

    return ok


# ═══════════════════════════════════════════
# Test 3: compare_asset 技能入口 (tex_json vs rig_json)
# ═══════════════════════════════════════════

def test_compare_asset_skill():
    _header('Test 3: compare_asset 技能 (tex_json vs rig_json)')

    from skills.compare_asset import execute

    print(f'  输入 A (input_source): tex_json')
    print(f'    {TEX_JSON}')
    print(f'  输入 B (input_target): rig_json')
    print(f'    {RIG_JSON}')
    print()

    t0 = time.time()
    receipt = execute({
        'parameters': {
            'input_source': TEX_JSON,
            'input_target': RIG_JSON,
        }
    })
    elapsed = time.time() - t0

    report_path = receipt['outputs'].get('report_path', '')

    print(f'  结果:')
    print(f'    status: {receipt["status"]}')
    print(f'    耗时: {elapsed:.3f}s')
    print(f'    报告输出: {os.path.abspath(report_path) if report_path else "无"}')
    print()

    ok = True
    ok &= _check('status = SUCCESS', receipt['status'] == 'SUCCESS')
    ok &= _check('无 error', not receipt.get('error'))
    ok &= _check('报告文件存在', os.path.isfile(report_path))

    if os.path.isfile(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
        ok &= _check('报告含对比来源', '对比来源' in content)
        ok &= _check('报告含完全一致', '完全一致' in content)

    return ok


# ═══════════════════════════════════════════
# Test 4: tex_json vs tex_json（同源自比，验证全匹配）
# ═══════════════════════════════════════════

def test_tex_json_vs_self():
    _header('Test 4: tex_json vs tex_json (同源自比)')

    from core.asset_info_schema import compare

    print(f'  输入 A: tex_json')
    print(f'    {TEX_JSON}')
    print(f'  输入 B: tex_json (同一份)')
    print(f'    {TEX_JSON}')
    print()

    with open(TEX_JSON, 'r', encoding='utf-8') as f:
        tex_info = json.load(f)

    result = compare(tex_info, tex_info, label_a='tex_v1', label_b='tex_v2')

    paired = result['paired']
    n_identical = sum(1 for p in paired if p.get('actionability') == 'IDENTICAL')

    print(f'  结果:')
    print(f'    配对: {len(paired)} / {len(tex_info["meshes"])} mesh')
    print(f'    全部 IDENTICAL: {n_identical == len(paired)}')
    print()

    ok = True
    ok &= _check('全部配对', len(paired) == len(tex_info['meshes']),
                  f'{len(paired)} / {len(tex_info["meshes"])}')
    ok &= _check('全部 IDENTICAL', n_identical == len(paired),
                  f'{n_identical} / {len(paired)}')
    ok &= _check('无遗漏 (only_a=0)', len(result['only_a']) == 0)
    ok &= _check('无遗漏 (only_b=0)', len(result['only_b']) == 0)
    ok &= _check('total_issues = 0', result['total_issues'] == 0)

    return ok


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

if __name__ == '__main__':
    results = []
    results.append(('文件存在性', test_files_exist()))
    results.append(('tex_json vs rig_json', test_tex_json_vs_rig_json()))
    results.append(('compare_asset 技能', test_compare_asset_skill()))
    results.append(('tex_json vs tex_json (同源自比)', test_tex_json_vs_self()))

    _header('汇总')
    all_pass = True
    for name, passed in results:
        status = '✅' if passed else '❌'
        print(f'  {status}  {name}')
        all_pass &= passed

    print()
    if all_pass:
        print('  🎉 全部通过')
    else:
        print('  ⚠️  存在失败项')

    sys.exit(0 if all_pass else 1)
