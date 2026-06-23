import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _check(name, condition, detail=''):
    if condition:
        print(f'[PASS] {name}')
    else:
        print(f'[FAIL] {name}' + (f' - {detail}' if detail else ''))
        raise SystemExit(1)


def _make_strip(y_value, n=9, width=0.05):
    verts = []
    for i in range(n):
        x = float(i)
        verts.append([x, y_value, -width])
        verts.append([x, y_value, width])
    faces = []
    for i in range(n - 1):
        a = 2 * i
        b = 2 * i + 1
        c = 2 * (i + 1)
        d = 2 * (i + 1) + 1
        faces.append([a, c, b])
        faces.append([b, c, d])
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def _combine_strips(upper_y, lower_y, bridge_ends=False):
    upper_v, upper_f = _make_strip(upper_y)
    lower_v, lower_f = _make_strip(lower_y)
    offset = upper_v.shape[0]
    verts = np.vstack([upper_v, lower_v])
    faces = np.vstack([upper_f, lower_f + offset])

    if bridge_ends:
        n = upper_v.shape[0] // 2
        left = np.asarray([[0, 1, offset + 1], [0, offset + 1, offset]], dtype=np.int64)
        ru0 = 2 * (n - 1)
        ru1 = ru0 + 1
        rl0 = offset + ru0
        rl1 = offset + ru1
        right = np.asarray([[ru0, rl1, ru1], [ru0, rl0, rl1]], dtype=np.int64)
        faces = np.vstack([faces, left, right])

    return verts, faces, np.arange(upper_v.shape[0]), np.arange(offset, verts.shape[0])


def main():
    from scipy.spatial import cKDTree
    from core.topology_support_matcher import TopologySupportMatcher, compute_family_scores

    dag_weights = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float64)
    dag_joints = [
        '|Rig|M_Head_A_zero|M_AllLip_A_grp|L_LoLip2_A_jnt',
        '|Rig|M_Head_A_zero|M_Head_A_jnt',
    ]
    families, dag_scores, _ = compute_family_scores(
        dag_weights,
        dag_joints,
        {'lower_lip': ['lolip'], 'head': ['head']},
    )
    family_index = {family: i for i, family in enumerate(families)}
    _check(
        'family scoring uses influence leaf not DAG ancestors',
        dag_scores[0, family_index['lower_lip']] == 1.0 and dag_scores[0, family_index['head']] == 0.0,
        str(dag_scores.tolist()),
    )

    src_v, src_f, src_upper, src_lower = _combine_strips(0.0, 0.08, bridge_ends=False)
    src_w = np.zeros((src_v.shape[0], 2), dtype=np.float64)
    src_w[src_upper, 0] = 1.0
    src_w[src_lower, 1] = 1.0

    tgt_v, tgt_f, tgt_upper, tgt_lower = _combine_strips(0.0, 0.01, bridge_ends=True)

    tree = cKDTree(src_v)
    _, nearest = tree.query(tgt_v, k=1)
    naive = src_w[nearest]
    naive_lower_ratio = float(np.mean(np.argmax(naive[tgt_lower], axis=1) == 1))
    _check('naive nearest fails on close lower strip', naive_lower_ratio < 0.25, f'ratio={naive_lower_ratio:.3f}')

    matcher = TopologySupportMatcher(
        source_vertices=src_v,
        source_faces=src_f,
        source_weights=src_w,
        joint_names=['UpLip_A_jnt', 'LoLip_A_jnt'],
        family_rules={
            'upper_lip': ['uplip'],
            'lower_lip': ['lolip'],
        },
        support_threshold=0.75,
        min_island_vertices=4,
    )

    center = len(tgt_upper) // 2
    seeds = {
        int(tgt_upper[center]): 'upper_lip',
        int(tgt_lower[center]): 'lower_lip',
    }
    result = matcher.transfer_weights(tgt_v, tgt_f, target_seed_labels=seeds, k=4, auto_seed=False)

    predicted = np.argmax(result.weights, axis=1)
    upper_ratio = float(np.mean(predicted[tgt_upper] == 0))
    lower_ratio = float(np.mean(predicted[tgt_lower] == 1))
    _check('topology matcher preserves upper support', upper_ratio > 0.95, f'ratio={upper_ratio:.3f}')
    _check('topology matcher preserves lower support', lower_ratio > 0.95, f'ratio={lower_ratio:.3f}')
    _check('support islands extracted per lip', result.diagnostics['support_island_count'] == 2, str(result.diagnostics))

    lower_mid_neighbor = int(tgt_lower[center + 1])
    _check('unseeded lower vertex inherits lower label',
           result.labels[lower_mid_neighbor] == 'lower_lip',
           result.labels[lower_mid_neighbor])
    _check('weights normalized', np.allclose(result.weights.sum(axis=1), 1.0))

    src_delta = np.zeros((src_v.shape[0], 3), dtype=np.float64)
    src_delta[src_upper, 1] = 0.25
    src_delta[src_lower, 1] = -5.0
    naive_delta = src_delta[nearest]
    _check('naive delta crosses support on close strip',
           float(np.mean(naive_delta[tgt_lower, 1] < -4.0)) < 0.25)

    field_result = matcher.transfer_values(
        src_delta,
        tgt_v,
        tgt_f,
        target_seed_labels=seeds,
        k=4,
        auto_seed=False,
    )
    _check('topology matcher transfers lower delta by support',
           float(np.mean(field_result.values[tgt_lower, 1] < -4.0)) > 0.95)
    _check('topology matcher keeps upper delta by support',
           float(np.mean(field_result.values[tgt_upper, 1] > 0.20)) > 0.95)

    print('[PASS] topology support matcher contract')


if __name__ == '__main__':
    main()
