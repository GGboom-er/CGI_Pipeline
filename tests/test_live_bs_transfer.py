import os
import sys

import numpy as np


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.live_bs_transfer import compose_live_target_weights


def _check(name, condition):
    if not condition:
        print(f"[FAIL] {name}")
        raise AssertionError(name)
    print(f"[PASS] {name}")


def test_active_rows_replace_body_weights():
    base_joints = ["body", "jaw"]
    base_weights = np.array([
        [1.0, 0.0],
        [0.8, 0.2],
        [0.7, 0.3],
    ])
    live_joints = ["faceA", "faceB"]
    live_weights = np.array([
        [0.4, 0.6],
        [0.1, 0.9],
        [0.5, 0.5],
    ])
    active = np.array([False, True, True])

    joints, weights = compose_live_target_weights(
        base_joints, base_weights, live_joints, live_weights, active
    )
    j = {name: idx for idx, name in enumerate(joints)}

    _check("inactive 保留 body 权重", np.allclose(weights[0, [j["body"], j["jaw"]]], [1.0, 0.0]))
    _check("active 清掉 body 权重", np.allclose(weights[active][:, [j["body"], j["jaw"]]], 0.0))
    _check("active 写入 live 权重", np.allclose(weights[1:, [j["faceA"], j["faceB"]]], live_weights[1:]))
    _check("每行归一", np.allclose(weights.sum(axis=1), 1.0))


if __name__ == "__main__":
    test_active_rows_replace_body_weights()
    print("\nALL LIVE BS TRANSFER TESTS PASSED!")
