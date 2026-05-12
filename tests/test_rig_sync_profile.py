"""
P0-A 单元测试：rig_sync_profile 配置化 + pairing_report 置信度报告

运行方式：
    cd Y:/GGbommer/scripts/CGI_Pipeline
    python tests/test_rig_sync_profile.py

不依赖 Maya / 真实资产，纯 Python。
"""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _check(name, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    marker = '✓' if condition else '✗'
    print(f'  [{status}] {marker} {name}' + (f' — {detail}' if detail else ''))
    if not condition:
        global _failures
        _failures += 1


_failures = 0


# ═══════════════════════════════════════════════════════════════
# Test Group 1: config_loader.get_rig_sync_profile
# ═══════════════════════════════════════════════════════════════

def test_config_loader():
    print('\n[1] config_loader profile')
    from core.config_loader import (
        get_rig_sync_profile,
        get_default_rig_sync_profile,
        _deep_merge,
    )

    d = get_default_rig_sync_profile()
    _check('default has classification.precision_exact',
           d['classification']['precision_exact'] == 0.0001,
           f"got {d['classification']['precision_exact']}")
    _check('default has classification.precision_loose',
           d['classification']['precision_loose'] == 0.005)
    _check('default has tnb_projection.max_normal_angle_deg',
           d['tnb_projection']['max_normal_angle_deg'] == 90.0)
    _check('default has pairing.auto_approve_threshold',
           d['pairing']['auto_approve_threshold'] == 0.95)

    # YSJ project override check
    ysj = get_rig_sync_profile('ysj')
    _check('ysj has rig_sync_profile loaded',
           ysj['classification']['precision_exact'] == 0.0001)

    # Missing project returns default (not raise)
    p = get_rig_sync_profile('nonexistent_xyz')
    _check('missing project returns default',
           p['classification']['precision_exact'] == 0.0001)

    # Deep merge: partial override
    base = {'a': 1, 'b': {'x': 10, 'y': 20}, 'c': [1, 2]}
    over = {'b': {'y': 99}, 'c': [3]}
    merged = _deep_merge(base, over)
    _check('deep_merge preserves sibling fields',
           merged == {'a': 1, 'b': {'x': 10, 'y': 99}, 'c': [3]},
           repr(merged))

    # Deep copy isolation
    d['classification']['precision_exact'] = 999
    d2 = get_default_rig_sync_profile()
    _check('default is deep-copied (no side effect)',
           d2['classification']['precision_exact'] == 0.0001)


# ═══════════════════════════════════════════════════════════════
# Test Group 2: asset_info_schema profile 透传
# ═══════════════════════════════════════════════════════════════

def test_asset_info_profile():
    print('\n[2] asset_info_schema _judge_action（新三步漏斗）')
    from core.asset_info_schema import _judge_action, _resolve_profile

    p = _resolve_profile(None)
    _check('_resolve_profile(None) returns dict', isinstance(p, dict))
    _check('_resolve_profile(None) has classification',
           'classification' in p)

    # IDENTICAL: pct_exact=100% 且 max<exact_thr
    d_id = {'vtx_a': 100, 'vtx_b': 100, 'max_offset': 0.00005,
            'match_pct_exact': '100.0000%', 'match_pct_loose': '100.0000%'}
    _check('pct_exact=100% 且 max<0.0001 → IDENTICAL',
           _judge_action(d_id) == 'IDENTICAL')

    # ORIG_INJECT: pct_loose=100% 且 max<loose_thr，但 max>=exact
    d_oi = {'vtx_a': 100, 'vtx_b': 100, 'max_offset': 0.003,
            'match_pct_exact': '50.0000%', 'match_pct_loose': '100.0000%'}
    _check('pct_loose=100% 且 max<0.005 → ORIG_INJECT',
           _judge_action(d_oi) == 'ORIG_INJECT')

    # 未达标：loose 未满 → None
    d_fail = {'vtx_a': 100, 'vtx_b': 100, 'max_offset': 0.003,
              'match_pct_exact': '50.0000%', 'match_pct_loose': '95.0000%'}
    _check('pct_loose<100% → None（滑下一步）',
           _judge_action(d_fail) is None)

    # max_offset 超 loose 阈值 → None
    d_maxfail = {'vtx_a': 100, 'vtx_b': 100, 'max_offset': 0.01,
                 'match_pct_exact': '100.0000%', 'match_pct_loose': '100.0000%'}
    _check('max>loose → None',
           _judge_action(d_maxfail) is None)

    # 点数不一致 → None
    d_vtx = {'vtx_a': 100, 'vtx_b': 101, 'max_offset': 0.0,
             'match_pct_exact': '99.0000%', 'match_pct_loose': '99.0000%'}
    _check('vtx_a != vtx_b → None',
           _judge_action(d_vtx) is None)

    # strict profile: loose_thr=0.001
    d_strict = {'vtx_a': 100, 'vtx_b': 100, 'max_offset': 0.002,
                'match_pct_exact': '50.0000%', 'match_pct_loose': '100.0000%'}
    strict_act = _judge_action(d_strict, profile={
        'classification': {'precision_exact': 0.0001, 'precision_loose': 0.001}
    })
    _check('strict profile: 0.002cm > 0.001 → None',
           strict_act is None, f'got {strict_act}')


# ═══════════════════════════════════════════════════════════════
# Test Group 3: pairing_report 分桶报告
# ═══════════════════════════════════════════════════════════════

def test_pairing_report():
    print('\n[3] pairing_report 分桶统计')
    from core.pairing_report import generate
    from core.config_loader import get_default_rig_sync_profile

    prof = get_default_rig_sync_profile()

    fake_compare = {
        'label_a': 'tex',
        'label_b': 'rig',
        'paired': [
            {'dag_a': 'A|body', 'dag_b': 'B|body', 'name_a': 'body', 'name_b': 'body',
             'vtx_a': 27601, 'vtx_b': 27601, 'max_offset': 0.0, 'step': 1,
             'match_pct_exact': '100%', 'match_pct_loose': '100%',
             'actionability': 'IDENTICAL'},
            {'dag_a': 'A|face', 'dag_b': 'B|face', 'name_a': 'face', 'name_b': 'face',
             'vtx_a': 5000, 'vtx_b': 5000, 'max_offset': 0.003, 'step': 1,
             'match_pct_exact': '50%', 'match_pct_loose': '100%',
             'actionability': 'ORIG_INJECT'},
            {'dag_a': 'A|eyebrow', 'dag_b': 'B|eyebrow_rig', 'name_a': 'eyebrow', 'name_b': 'eyebrow_rig',
             'vtx_a': 5569, 'vtx_b': 5590, 'max_offset': 0.152, 'step': 3,
             'match_pct_loose': '99.6%',
             'actionability': 'MODIFIED'},
        ],
        'only_a': [{'dag': 'A|beard', 'name': 'beard', 'vtx': 5712}],
        'only_b': [{'dag': 'B|oldhair', 'name': 'oldhair', 'vtx': 800}],
        'merge_groups': [],
        'split_groups': [],
        'total_issues': 3,
        # 新契约：sync 消费的 4 类组 + target_only
        'pairing_groups': [
            {'group_id': 'g0001', 'action': 'IDENTICAL', 'abc_dags': ['A|body'],
             'rig_dags': ['B|body'], 'layer_name': 'body', 'reason': 'Step 1 IDENTICAL'},
            {'group_id': 'g0002', 'action': 'ORIG_INJECT', 'abc_dags': ['A|face'],
             'rig_dags': ['B|face'], 'layer_name': 'face', 'reason': 'Step 1 ORIG_INJECT'},
            {'group_id': 'g0003', 'action': 'PAIRED', 'abc_dags': ['A|eyebrow'],
             'rig_dags': ['B|eyebrow_rig'], 'layer_name': 'eyebrow',
             'reason': 'Step 3 空间配对：MODIFIED'},
            {'group_id': 'g0004', 'action': 'UNPAIRED', 'abc_dags': ['A|beard'],
             'rig_dags': [], 'layer_name': '', 'reason': 'abc 独有'},
        ],
        'target_only_dags': ['B|oldhair'],
    }
    with tempfile.TemporaryDirectory() as td:
        report = generate(fake_compare, prof, output_dir=td, tag='test')
        _check('JSON file written', os.path.isfile(report['json_path']))
        _check('MD file written', os.path.isfile(report['md_path']))
        s = report['summary']
        _check('summary.IDENTICAL == 1', s['IDENTICAL'] == 1, str(s))
        _check('summary.ORIG_INJECT == 1', s['ORIG_INJECT'] == 1, str(s))
        _check('summary.MODIFIED == 1', s['MODIFIED'] == 1, str(s))
        _check('summary.NEW == 1', s['NEW'] == 1, str(s))
        _check('summary.DELETE == 1', s['DELETE'] == 1, str(s))

        # step_distribution
        sd = report['step_distribution']
        _check('step_distribution.step_1 == 2', sd.get('step_1', 0) == 2)
        _check('step_distribution.step_3 == 1', sd.get('step_3', 0) == 1)

        # MD 内容抽检
        with open(report['md_path'], encoding='utf-8') as f:
            md = f.read()
        _check('MD contains precision thresholds',
               '0.0001' in md and '0.005' in md)
        # 新 MD 主视角：sync 执行组展开 + outcome 作为附录
        _check('MD contains PAIRED section with eyebrow (M→N 组明细)',
               '### PAIRED' in md and 'eyebrow' in md)
        _check('MD contains UNPAIRED section with beard (abc 独有)',
               '### UNPAIRED' in md and 'beard' in md)
        _check('MD contains target_only section with oldhair',
               'target_only' in md and 'oldhair' in md)


# ═══════════════════════════════════════════════════════════════
# Test Group 5: 三步漏斗单元测试
# ═══════════════════════════════════════════════════════════════

def test_phased_matching():
    print('\n[5] 三步漏斗配对逻辑')
    from core.asset_info_schema import (
        _normalize_dag, _step1_path_match,
        _step2_vertex_count_match, _step3_spatial_analysis,
        compare,
    )
    from core.config_loader import get_default_rig_sync_profile

    prof = get_default_rig_sync_profile()

    # _normalize_dag: RIG_ 前缀 + namespace
    _check('normalize 去 RIG_ 前缀',
           _normalize_dag('|cache|RIG_body_Grp|RIG_body') == 'cache|body_Grp|body')
    _check('normalize 去 namespace',
           _normalize_dag('|ns:cache|ns:body') == 'cache|body')

    # 构造两组 mesh 数据
    # cube_a: tex 侧 [4 cubes, 1 body(8), 1 hair(4)]
    # cube_b: rig 侧 [4 cubes 同位置 + 3 异]
    def _cube(origin):
        x, y, z = origin
        return [x, y, z,  x+1, y, z,  x, y+1, z,  x+1, y+1, z,
                x, y, z+1, x+1, y, z+1, x, y+1, z+1, x+1, y+1, z+1]

    meshes_a = {
        '|cache|body|body':          {'vertices': 8, 'vert_positions': _cube((0, 0, 0))},
        '|cache|face|face':          {'vertices': 8, 'vert_positions': _cube((10, 0, 0))},
        '|cache|hair|hair':          {'vertices': 8, 'vert_positions': _cube((20, 0, 0))},
        '|cache|newprop|newprop':    {'vertices': 8, 'vert_positions': _cube((100, 100, 100))},
    }
    meshes_b = {
        '|cache|body|body':            {'vertices': 8, 'vert_positions': _cube((0, 0, 0))},       # IDENTICAL
        '|cache|face|face':            {'vertices': 8, 'vert_positions': [v + 0.002 if i % 3 != 2 else v
                                                                          for i, v in enumerate(_cube((10, 0, 0)))]},  # ORIG_INJECT
        '|cache|hair_renamed|hair_x':  {'vertices': 8, 'vert_positions': _cube((20, 0, 0))},       # S2: 改名 + 几何一致
        '|cache|oldhair|oldhair':      {'vertices': 12, 'vert_positions': _cube((200, 200, 200)) + [0,0,0,1,1,1]},  # DELETE
    }

    # Step 1 路径匹配
    s1_pairs, rem_a, rem_b, skip_set = _step1_path_match(meshes_a, meshes_b, profile=prof)
    s1_actions = {p['dag_a']: p['actionability'] for p in s1_pairs}
    _check('S1 body → IDENTICAL',
           s1_actions.get('|cache|body|body') == 'IDENTICAL')
    _check('S1 face → ORIG_INJECT',
           s1_actions.get('|cache|face|face') == 'ORIG_INJECT',
           f'got {s1_actions.get("|cache|face|face")}')
    _check('S1 hair 未配（改名）',
           '|cache|hair|hair' in rem_a)
    _check('S1 newprop 未配（不存在）',
           '|cache|newprop|newprop' in rem_a)
    _check('S1 skip_set 空（没出现路径同点数不同）',
           len(skip_set) == 0)

    # Step 2 点数匹配
    s2_pairs, rem_a2, rem_b2 = _step2_vertex_count_match(
        rem_a, rem_b, meshes_a, meshes_b, profile=prof, skip_set=skip_set)
    s2_actions = {p['dag_a']: p['actionability'] for p in s2_pairs}
    _check('S2 hair 找到 hair_x 改名配对',
           s2_actions.get('|cache|hair|hair') == 'IDENTICAL',
           f'got {s2_actions.get("|cache|hair|hair")}')
    _check('S2 newprop 仍未配（点数候选均不达标）',
           '|cache|newprop|newprop' in rem_a2)

    # Step 3 空间分析
    s3 = _step3_spatial_analysis(rem_a2, rem_b2, meshes_a, meshes_b, profile=prof)
    _check('S3 newprop → NEW',
           '|cache|newprop|newprop' in s3['new_list'])
    _check('S3 oldhair → DELETE',
           '|cache|oldhair|oldhair' in s3['delete_list'])

    # 端到端 compare
    info_a = {'source_file': 'a.ma', 'meshes': meshes_a, 'textures': {}}
    info_b = {'source_file': 'b.ma', 'meshes': meshes_b, 'textures': {}}
    r = compare(info_a, info_b, label_a='A', label_b='B', profile=prof, enable_cpd=False)
    all_actions = {p['dag_a']: p['actionability'] for p in r['paired']}
    _check('E2E body → IDENTICAL',
           all_actions.get('|cache|body|body') == 'IDENTICAL')
    _check('E2E face → ORIG_INJECT',
           all_actions.get('|cache|face|face') == 'ORIG_INJECT',
           f'got {all_actions.get("|cache|face|face")}')
    _check('E2E hair → IDENTICAL（S2 改名配对）',
           all_actions.get('|cache|hair|hair') == 'IDENTICAL')
    only_a_dags = {e['dag'] for e in r['only_a']}
    only_b_dags = {e['dag'] for e in r['only_b']}
    _check('E2E newprop in only_a (NEW)',
           '|cache|newprop|newprop' in only_a_dags)
    _check('E2E oldhair in only_b (DELETE)',
           '|cache|oldhair|oldhair' in only_b_dags)


# ═══════════════════════════════════════════════════════════════
# Test Group 4: spatial_transfer profile wrapper
# ═══════════════════════════════════════════════════════════════

def test_spatial_transfer_profile():
    print('\n[4] spatial_transfer profile wrapper')
    import numpy as np
    from core.spatial_transfer import transfer_weights_with_profile

    src_verts = np.array([[0,0,0],[1,0,0],[0,1,0],[1,1,0]], dtype=np.float64)
    src_faces = np.array([[0,1,2],[1,3,2]], dtype=np.intp)
    src_weights = np.array([[1,0],[0.5,0.5],[0.5,0.5],[0,1]], dtype=np.float64)
    dst_verts = np.array([[0.25,0.25,0],[0.75,0.25,0],[0.25,0.75,0],[0.75,0.75,0]], dtype=np.float64)
    dst_faces = np.array([[0,1,2],[1,3,2]], dtype=np.intp)

    w, s = transfer_weights_with_profile(src_verts, src_faces, src_weights, dst_verts, dst_faces)
    _check('default profile: 100% projected', s['n_projected'] == 4, str(s))
    _check('weights normalize to 1',
           bool(np.allclose(w.sum(axis=1), 1.0)),
           str(w.sum(axis=1)))

    strict = {'tnb_projection': {'max_normal_angle_deg': 60.0, 'k_candidates': 4},
              'diffuse': {'backend': 'scipy'}}
    w2, s2 = transfer_weights_with_profile(src_verts, src_faces, src_weights,
                                           dst_verts, dst_faces, profile=strict)
    _check('strict profile still works', s2['n_projected'] == 4, str(s2))


if __name__ == '__main__':
    test_config_loader()
    test_asset_info_profile()
    test_pairing_report()
    test_spatial_transfer_profile()
    test_phased_matching()

    print(f'\n{"="*60}')
    if _failures == 0:
        print('  ALL TESTS PASSED')
    else:
        print(f'  {_failures} test(s) FAILED')
        sys.exit(1)
