"""Numerical contracts for the independent adaptation; host acceptance is separate."""
import importlib.util
import importlib
import json
from pathlib import Path
import sys
import types
import uuid

import numpy as np
import pytest

OWNER = Path(__file__).parents[1] / "src/cgi_pipeline/capabilities/maya/rig/maya_clean_skinweights"
PACKAGE = "cgi_dair_numeric_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(OWNER)]
sys.modules[PACKAGE] = package
SPEC = importlib.util.spec_from_file_location(PACKAGE + ".dair_kernel", OWNER / "dair_kernel.py")
kernel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel)


def test_roundoff_negative_is_zero_but_real_negative_is_rejected():
    original, baseline, _, active = kernel.proportional_baseline([[.4, .6, -6.6e-18]], .001, 12)
    np.testing.assert_array_equal(original, [[.4, .6, 0]])
    np.testing.assert_array_equal(baseline, original)
    assert len(active) == 0
    with pytest.raises(ValueError):
        kernel.proportional_baseline([[.4, .601, -.001]], .001, 12)
    original, baseline, _, _ = kernel.proportional_baseline([[.4, .6000000285, -2.85e-8]], .001, 12)
    assert original.min() == 0
    np.testing.assert_allclose(original.sum(axis=1), 1)
    np.testing.assert_allclose(original[0, 0] / original[0, 1], .4 / .6000000285)
    with pytest.raises(ValueError, match="negative mass"):
        kernel.proportional_baseline([[1.00000012, -6e-8, -6e-8]], .001, 12)


def explicit_features(points, influence_paths, pivots):
    """Direct Rodrigues matrices, one ancestor/axis/angle at a time."""
    poses = [np.zeros((len(points), len(influence_paths), 3))]
    for path, pivot in sorted(pivots.items()):
        mask = np.asarray([name == path or name.startswith(path + "|") for name in influence_paths])
        relative = points - pivot
        for axis in np.eye(3):
            for angle in np.deg2rad(kernel.ANGLES_DEGREES):
                cross = np.cross(np.broadcast_to(axis, relative.shape), relative)
                rotated = (relative * np.cos(angle) + cross * np.sin(angle) +
                           np.outer(relative @ axis, axis) * (1 - np.cos(angle)))
                displacement = rotated - relative
                poses.append(displacement[:, None, :] * mask[None, :, None])
    return np.asarray(poses)


def metric_fixture():
    points = np.array([[1., 2, 3], [2, -.5, .7], [-1, 1, .2]])
    paths = ["|root|A", "|root|B", "|root|A|C"]
    pivots = {"|root": np.array([0., 0, 0]), "|root|A": np.array([1., 0, .2]),
              "|root|B": np.array([-.2, 1, 0]), "|root|A|C": np.array([1., 2, 0])}
    masks, moments, count = kernel.compress_poses(paths, pivots)
    return points, paths, pivots, kernel.DeformationMetric(points, masks, moments, count)


def test_analytic_pose_gram_matches_direct_rodrigues_for_sum_zero_weight_deltas():
    points, paths, pivots, metric = metric_fixture()
    direct = explicit_features(points, paths, pivots)
    deltas = np.array([[.1, -.3, .2], [-.2, .1, .1], [.4, -.1, -.3]])
    errors = np.einsum("pvjc,vj->pvc", direct, deltas)
    coefficients = deltas @ metric.masks.T
    for vertex in range(len(points)):
        compressed = np.sum(metric.pair(vertex, np.arange(len(points))) * coefficients[vertex] * coefficients, axis=1)
        expected = np.einsum("pc,pvc->v", errors[:, vertex], errors) / len(direct)
        assert compressed == pytest.approx(expected, abs=1e-14)


def test_full_laplacian_columns_include_unedited_neighbor_and_two_ring_coupling():
    points, paths, pivots, metric = metric_fixture()
    edges = np.array([[0, 1], [1, 2]])
    coupling, _ = kernel.build_coupling(points, edges)
    assert coupling[0, 2] != 0  # Shared unedited middle vertex contributes its L row.
    original = np.array([[.6, .3, .1], [1, 0, 0], [.2, .5, .3]])
    output = original.copy()
    output[[0, 2]] = [[1, 0, 0], [0, 1, 0]]
    direct = explicit_features(points, paths, pivots)
    errors = np.einsum("pvjc,vj->pvc", direct, output - original)
    energy = sum(float(error[:, axis] @ coupling @ error[:, axis])
                 for error in errors for axis in range(3)) / len(direct)
    energy += kernel.REGULARIZATION * np.square(output - original).sum()
    compressed = kernel.evaluate_energy(original, output, np.array([0, 2]), coupling, metric)
    assert compressed == pytest.approx(energy, rel=1e-12, abs=1e-13)


def test_pose_compression_combines_identical_masks_without_dropping_their_pivots():
    paths = ["|r|a|middle|leaf", "|r|b"]
    pivots = {"|r|a": [0, 1, 0], "|r|a|middle": [1, 0, 0], "|r|b": [0, 0, 2]}
    masks, moments, count = kernel.compress_poses(paths, pivots)
    assert len(masks) == 2
    assert sorted(moments[:, 0]) == [1, 2]
    assert count == 37
    points = np.array([[1., .2, .5], [.3, 1., 2.]])
    metric = kernel.DeformationMetric(points, masks, moments, count)
    direct = explicit_features(points, paths, pivots)
    delta = np.array([[.2, -.2], [-.1, .1]])
    errors = np.einsum("pvjc,vj->pvc", direct, delta)
    coeff = delta @ masks.T
    expected = np.sum(errors[:, 0] * errors[:, 1]) / count
    assert np.sum(metric.pair(0, [1])[0] * coeff[0] * coeff[1]) == pytest.approx(expected)


def solve_fixture():
    points = np.array([[1., 0, 0], [1, 1, 0], [.2, 1.5, .2], [2, .7, 1.]])
    paths = ["|a", "|b", "|c", "|d"]
    pivots = {"|a": [0, 0, 0], "|b": [1, 0, 0], "|c": [0, 2, 0], "|d": [8, 5, 2]}
    masks, moments, count = kernel.compress_poses(paths, pivots)
    weights = np.array([[.4, .3, .2, .1], [.45, .3, .15, .1],
                        [.5, .25, .15, .1], [.35, .3, .25, .1]])
    edges = np.array([[0, 1], [1, 2], [2, 3]])
    return points, edges, weights, masks, moments, count


def test_scipy_solver_constraints_determinism_and_baseline_nonregression():
    values = solve_fixture()
    first, metrics = kernel.optimize_weights(*values, threshold=.11, maximum=2)
    second, repeated = kernel.optimize_weights(*values, threshold=.11, maximum=2)
    assert first == pytest.approx(second, abs=1e-14)
    assert np.all(first >= 0)
    assert np.allclose(first.sum(axis=1), 1)
    assert np.max(np.count_nonzero(first, axis=1)) <= 2
    assert np.all(first[:, 3] == 0)  # Original threshold cannot re-enter via refitting.
    assert metrics["optimized_error"] <= metrics["baseline_error"] + 1e-12
    assert metrics["optimized_error"] < metrics["baseline_error"] * .99
    assert metrics["optimized_error"] == pytest.approx(repeated["optimized_error"])


def test_support_exchange_can_retain_a_smaller_but_geometrically_important_weight():
    points, edges, weights, masks, moments, count = solve_fixture()
    # Isolate support selection. A coupled many-vertex local search can be
    # trapped behind a coordinated support change, which is not a global solver.
    points, weights, edges = points[:1], weights[:1], np.empty((0, 2), dtype=np.int64)
    _, baseline, _, _ = kernel.proportional_baseline(weights, 0, 2)
    result, metrics = kernel.optimize_weights(points, edges, weights, masks, moments, count, 0, 2)
    assert np.all(baseline[:, 3] == 0)
    assert np.any(result[:, 3] > 0), (result, metrics)
    assert metrics["optimized_error"] < metrics["baseline_error"]


def test_noop_does_not_add_solver_changes_to_acceptable_vertices():
    points, edges, weights, masks, moments, count = solve_fixture()
    weights[:] = [1, 0, 0, 0]
    result, metrics = kernel.optimize_weights(points, edges, weights, masks, moments, count, 0, 2)
    assert np.array_equal(result, weights)
    assert metrics["changed_vertices"] == 0


def neighbor_fixture():
    points, _, weights, masks, moments, count = solve_fixture()
    points[2:] = points[:2] + .001  # Spatially close, but a disconnected shell.
    weights[1:] = [[.55, .45, 0, 0], [.65, .35, 0, 0], [.3, .7, 0, 0]]
    return points, np.array([[0, 1], [2, 3]]), weights, masks, moments, count


def test_one_ring_neighbor_can_improve_continuity_with_fixed_support_and_box_bound():
    args = neighbor_fixture()
    original = args[2]
    fixed, fixed_metrics = kernel.optimize_weights(*args, threshold=.001, maximum=2, neighbor_rings=0)
    output, metrics = kernel.optimize_weights(*args, threshold=.001, maximum=2,
                                               neighbor_rings=1, neighbor_max_delta=.02)
    assert metrics["primary_count"] == 1
    assert metrics["neighbor_candidates"] == 1
    assert metrics["neighbor_changed_count"] == 1
    assert metrics["optimized_error"] < fixed_metrics["optimized_error"]
    assert metrics["optimized_error"] <= metrics["baseline_error"]
    assert np.array_equal(output[1] > 0, original[1] > 0)
    assert np.max(np.abs(output[1] - original[1])) <= .02 + 1e-10
    assert metrics["max_neighbor_delta"] == pytest.approx(np.max(np.abs(output[1] - original[1])))
    assert np.allclose(output.sum(axis=1), 1)
    assert np.max(np.count_nonzero(output, axis=1)) <= 2
    assert np.array_equal(fixed[1], original[1])


def test_neighbors_do_not_cross_disconnected_shells_and_zero_rings_disables_extra_variables():
    args = neighbor_fixture()
    output, metrics = kernel.optimize_weights(*args, threshold=.001, maximum=2)
    assert np.array_equal(output[2:], args[2][2:])
    output, metrics = kernel.optimize_weights(*args, threshold=.001, maximum=2, neighbor_rings=0)
    assert metrics["neighbor_candidates"] == metrics["neighbor_changed_count"] == 0
    assert np.array_equal(output[1:], args[2][1:])


def test_no_primary_vertices_means_no_neighbor_smoothing_even_with_multiple_weights():
    args = list(neighbor_fixture())
    args[2][0] = [.4, .6, 0, 0]
    output, metrics = kernel.optimize_weights(*args, threshold=.001, maximum=2)
    assert np.array_equal(output, args[2])
    assert metrics["primary_count"] == metrics["neighbor_candidates"] == 0


def test_fixed_support_fit_cannot_create_new_subthreshold_weight():
    threshold = .001
    target = np.array([.99999, .00001])
    output, ok = kernel._solve_support(np.eye(2), -target, [0, 1], np.array([.8, .2]),
                                       threshold=threshold)
    assert ok
    assert np.all(output > threshold)
    assert output[1] == pytest.approx(kernel.output_floor(threshold, 2), abs=1e-12)
    assert output.sum() == pytest.approx(1)
    _, _, _, primary = kernel.proportional_baseline(output[None, :], threshold, 12)
    assert len(primary) == 0


@pytest.mark.parametrize("small_weight", [.0011, np.nextafter(.001, np.inf)])
def test_neighbor_floor_is_part_of_fixed_support_box_constrained_fit(small_weight):
    threshold, delta = .001, .02
    original = np.array([1 - small_weight, small_weight])
    lower = np.maximum(original - delta, np.minimum(original, kernel.output_floor(threshold, 2)))
    upper = np.minimum(original + delta, 1.)
    output, ok = kernel._solve_support(np.eye(2), -np.array([1., 0.]), [0, 1], original,
                                       bounds=list(zip(lower, upper)), threshold=threshold)
    assert ok
    assert np.all(output > threshold)
    assert np.all(np.abs(output - original) <= delta + 1e-12)
    assert np.array_equal(output > 0, original > 0)
    assert output.sum() == pytest.approx(1)


def test_roundoff_sum_repair_stays_inside_guarded_floor_bounds():
    threshold = .49999999999999
    floor = kernel.output_floor(threshold, 2)
    output = kernel._repair_sum(np.array([.500000000000001, .5]),
                                 np.full(2, floor), np.ones(2))
    assert output is not None
    assert np.all(output >= floor)
    assert np.all(output > threshold)
    assert output.sum() == 1


@pytest.mark.parametrize("threshold", [0., .49999999999999])
def test_guarded_floor_remains_feasible_near_simplex_limit_and_at_zero(threshold):
    output, ok = kernel._solve_support(np.eye(2), -np.array([.9, .1]), [0, 1],
                                       np.array([.5, .5]), threshold=threshold)
    assert ok
    assert np.all(output > threshold)
    assert output.sum() == pytest.approx(1)


@pytest.mark.parametrize("changes", [{"neighbor_rings": -1}, {"neighbor_rings": 3},
    {"neighbor_rings": True}, {"neighbor_max_delta": 0}, {"neighbor_max_delta": float("nan")},
    {"neighbor_max_delta": 1.1}])
def test_neighbor_parameter_limits_fail_before_any_host_import(changes):
    engine = importlib.import_module(PACKAGE + ".dair_engine")
    with pytest.raises(ValueError):
        engine.validate_params({"skin_clusters": ["explicit"], **changes})


@pytest.mark.parametrize("threshold,maximum", [(0., 1), (.001, 12)])
def test_tiny_positive_support_is_pruned_despite_sub_tolerance_mass(threshold, maximum):
    original = np.array([[1 - 2e-14, 1e-14, 1e-14]])
    paths = ["|a", "|b", "|c"]
    masks, moments, count = kernel.compress_poses(paths, {"|a": [0., 0, 0],
        "|b": [1., 0, 0], "|c": [0., 1, 0]})
    output, metrics = kernel.optimize_weights(np.array([[.5, .5, 0.]]), [], original,
        masks, moments, count, threshold=threshold, maximum=maximum)
    assert output.tolist() == [[1., 0., 0.]]
    assert metrics["changed_vertices"] == 1


def test_threshold_excluded_columns_still_contribute_to_the_full_deformation_target():
    from scipy.optimize import minimize_scalar
    points = np.array([[1., 1., 0.]])
    paths = ["|a", "|a|c", "|b"]
    pivots = {"|a": [0., 0, 0], "|a|c": [3., 0, 0], "|b": [5., 0, 0]}
    masks, moments, count = kernel.compress_poses(paths, pivots)
    original = np.array([[.5, .005, .495]])
    coupling, _ = kernel.build_coupling(points, [])
    metric = kernel.DeformationMetric(points, masks, moments, count)
    def direct_energy(amount):
        candidate = np.array([[amount, 0, 1 - amount]])
        return kernel.evaluate_energy(original, candidate, np.array([0]), coupling, metric)
    expected = minimize_scalar(direct_energy, bounds=(0., 1.), method="bounded",
                               options={"xatol": 1e-13})
    output, _ = kernel.optimize_weights(points, [], original, masks, moments, count,
                                        threshold=.01, maximum=2)
    assert output[0, 0] == pytest.approx(expected.x, abs=1e-8)
    assert output[0, 1] == 0


@pytest.mark.parametrize("weights", [np.zeros((1, 3)), np.array([[1, -.1, .1]]),
                                    np.array([[float("nan"), 0, 1]])])
def test_invalid_weight_rows_are_refused(weights):
    with pytest.raises(ValueError):
        kernel.proportional_baseline(weights, 0, 2)


def test_original_threshold_and_cap_ties_have_stable_index_order():
    _, baseline, eligible, _ = kernel.proportional_baseline(np.array([[.005, .005, .49, .5]]), .005, 1)
    assert baseline.tolist() == [[0, 0, 0, 1]]
    assert eligible.tolist() == [[False, False, True, True]]
    _, baseline, _, _ = kernel.proportional_baseline(np.array([[.5, .5]]), 0, 1)
    assert baseline.tolist() == [[1, 0]]


def test_command_transport_removes_pending_arrays_even_when_host_dispatch_raises(monkeypatch):
    spec = importlib.util.spec_from_file_location("cgi_weight_write_test", OWNER / "undoable_weights.py")
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    cmds = types.ModuleType("maya.cmds")
    def fail(token):
        assert token in writer._exchange()
        raise RuntimeError("host dispatch failed")
    setattr(cmds, writer.COMMAND, fail)
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    with pytest.raises(RuntimeError, match="host dispatch failed"):
        writer.write_weights([{"before": [1], "after": [1]}])
    assert writer._exchange() == {}


def managed_numeric_test_dir(label):
    root = Path(__file__).parents[1] / "projects/default/PL295"
    if not (root / ".info/run_state.json").is_file():
        pytest.skip("registered PL295 acceptance workspace unavailable")
    return root / "work/tests/dair_numeric_boundary" / (label + "_" + uuid.uuid4().hex)


def test_registered_numeric_worker_real_probe_and_npz_roundtrip():
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    try:
        solver.runtime_config()
    except ImportError as exc:
        pytest.skip("registered Windows numerical runtime unavailable: " + str(exc))
    workdir = managed_numeric_test_dir("roundtrip")
    dependencies = solver.require_deps()
    assert dependencies["numeric_worker"]["probe_solution"] == pytest.approx([.2, .8])
    args = solve_fixture()
    expected, _ = kernel.optimize_weights(*args, threshold=.11, maximum=2)
    actual, metrics = solver.solve(*args, {"threshold": .11, "max_influences": 2,
        "solver_workdir": str(workdir)})
    assert actual == pytest.approx(expected, abs=1e-12)
    artifacts = Path(metrics["solver_artifact_dir"])
    assert artifacts.is_relative_to(workdir)
    assert (artifacts / "input.npz").is_file()
    assert (artifacts / "output.npz").is_file()
    assert (artifacts / "result.json").is_file()


def test_numeric_failure_retains_owned_request_evidence_without_output(monkeypatch):
    import subprocess
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    try:
        solver.runtime_config()
    except ImportError as exc:
        pytest.skip("registered Windows numerical runtime unavailable: " + str(exc))
    workdir = managed_numeric_test_dir("failure")
    monkeypatch.setattr(solver, "_execute", lambda *args: subprocess.CompletedProcess(
        ["numeric-worker"], 2, stdout="", stderr="injected numeric failure"))
    with pytest.raises(RuntimeError, match="before Maya weights were written"):
        solver.solve(*solve_fixture(), {"threshold": .11, "max_influences": 2,
            "solver_workdir": str(workdir)})
    requests = list(workdir.glob("dair_*"))
    assert len(requests) == 1
    assert (requests[0] / "request.json").is_file()
    assert (requests[0] / "input.npz").is_file()
    assert "injected numeric failure" in (requests[0] / "stderr.log").read_text()
    assert not (requests[0] / "output.npz").exists()


def test_fake_marker_outside_managed_roots_cannot_authorize_numeric_writes(tmp_path):
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    marker = tmp_path / ".info"
    marker.mkdir()
    (marker / "run_state.json").write_text(json.dumps({
        "wf_id": "PL295", "project": "default", "business_task_id": "PL295"}))
    candidate = tmp_path / "work/skin_solver"
    with pytest.raises(ValueError, match="unprotected managed CGI workspace"):
        solver.resolve_workdir(str(candidate))
    assert not candidate.exists()


def test_explicit_registered_workspace_is_accepted_and_identity_is_checked():
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    candidate = managed_numeric_test_dir("identity")
    assert solver.resolve_workdir(str(candidate)) == candidate.resolve()
    fake = candidate / "fake_workspace"
    marker = fake / ".info"
    marker.mkdir(parents=True)
    (marker / "run_state.json").write_text(json.dumps({
        "wf_id": "fake", "project": "default", "business_task_id": "PL295"}))
    with pytest.raises(ValueError, match="workspace identity is invalid"):
        solver.resolve_workdir(str(fake / "work"))


def test_real_pl295_input_output_threshold_and_reuse():
    source = Path(__file__).parents[1] / "projects/default/PL295/work/skin_solver/dair_4310b9a629b34d10a9b231918d83fb4e/input.npz"
    if not source.is_file():
        pytest.skip("registered PL295 original numeric acceptance input unavailable")
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    with np.load(source, allow_pickle=False) as data:
        args = [data[key].copy() for key in ("points", "edges", "weights", "masks", "moments")]
        args.append(int(data["pose_count"]))
    options = {"threshold": .001, "max_influences": 12, "neighbor_rings": 1,
               "neighbor_max_delta": .02, "solver_workdir": str(managed_numeric_test_dir("real_floor"))}
    output, metrics = solver.solve(*args, options)
    original, _, _, primary = kernel.proportional_baseline(args[2], .001, 12)
    neighbors = kernel.neighbor_vertices(args[1], primary, 1, len(original))
    effective = output / output.sum(axis=1)[:, None]
    assert np.all(effective[effective > 0] > .001)
    assert np.max(np.count_nonzero(output, axis=1)) <= 12
    assert np.allclose(output.sum(axis=1), 1, atol=1e-7, rtol=0)
    assert np.array_equal(output[neighbors] > 0, original[neighbors] > 0)
    assert np.max(np.abs(output[neighbors] - original[neighbors])) <= .02 + 1e-10
    assert metrics["optimized_error"] <= metrics["baseline_error"] + 1e-10
    _, _, _, repeated = kernel.proportional_baseline(output, .001, 12)
    assert len(repeated) == 0
    summary = {"source": str(source), "shape": list(output.shape),
               "min_positive": float(np.min(effective[effective > 0])),
               "max_nonzero": int(np.max(np.count_nonzero(output, axis=1))),
               "repeat_primary_count": len(repeated), "metrics": metrics}
    target = Path(metrics["solver_artifact_dir"]) / "acceptance.json"
    target.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("PL295_FLOOR_ACCEPTANCE=" + str(target))


@pytest.mark.parametrize("mode,row", [("inspect", [.999, .001]), ("apply", [.9, .1])])
def test_inspect_and_unchanged_apply_do_not_prepare_solver_geometry(monkeypatch, mode, row):
    engine = importlib.import_module(PACKAGE + ".dair_engine")
    scene = importlib.import_module(PACKAGE + ".dair_scene")
    data = importlib.import_module(PACKAGE + ".dair_data")
    checks, timings = [], {}
    snapshot = {"name": "skin", "count": 1, "width": 2, "weights": np.array(row),
                "attrs": {"normalizeWeights": 1}, "influences": [{}, {}]}
    monkeypatch.setattr(engine.ng_engine, "_capture", lambda *args: snapshot)
    monkeypatch.setattr(scene, "preflight", lambda *args: checks.append("mechanism"))
    monkeypatch.setattr(scene, "inspect_input", lambda *args: checks.append("membership"))
    def forbid(*args):
        raise AssertionError("solver geometry/poses must not be prepared")
    for owner, name in [(scene, "input_mesh"), (scene, "sample_pivots"), (data, "compress_poses")]:
        monkeypatch.setattr(owner, name, forbid)
    options = engine.validate_params({"skin_clusters": ["skin"], "mode": mode})
    result = engine._prepare("skin", options, None, None, None, 100, timings)
    assert checks == ["mechanism", "membership"]
    assert result["metrics"]["geometry_pose_deferred"]
    assert result["metrics"]["sampled_pose_count"] is None
    assert "inspect_geometry_checks" in timings and "numeric_solve" not in timings


def source_preflight_test_host(monkeypatch):
    engine = importlib.import_module(PACKAGE + ".dair_engine")
    cmds = types.ModuleType("maya.cmds")
    cmds.method = 0
    cmds.ls = lambda name, **kw: [name + "_uuid"]
    cmds.getAttr = lambda plug: cmds.method if plug.endswith("skinningMethod") else 0
    cmds.attributeQuery = lambda *a, **kw: False
    cmds.listConnections = lambda *a, **kw: []
    maya, api = types.ModuleType("maya"), types.ModuleType("maya.api")
    om, oma = types.ModuleType("maya.api.OpenMaya"), types.ModuleType("maya.api.OpenMayaAnim")
    maya.cmds, maya.api, api.OpenMaya, api.OpenMayaAnim = cmds, api, om, oma
    for name, module in [("maya", maya), ("maya.cmds", cmds), ("maya.api", api),
                         ("maya.api.OpenMaya", om), ("maya.api.OpenMayaAnim", oma)]:
        monkeypatch.setitem(sys.modules, name, module)
    return engine, cmds


def test_source_preflight_keeps_mechanism_checks_without_full_weight_capture(monkeypatch):
    engine, cmds = source_preflight_test_host(monkeypatch)
    shape = types.SimpleNamespace(fullPathName=lambda: "|mesh")
    monkeypatch.setattr(engine.ng_engine, "_access", lambda name, *a: (name, None, shape, None, 5, [1, 2]))
    def forbid(*args):
        raise AssertionError("source weights must not be batch-read by compatibility preflight")
    monkeypatch.setattr(engine.ng_engine, "_capture", forbid)
    result = engine.preflight_sources(["skin"])
    assert result["skins"][0]["influence_count"] == 2
    assert result["timing_seconds"]["source_preflight"] >= 0
    cmds.method = 2
    with pytest.raises(ValueError, match="native LBS"):
        engine.preflight_sources(["skin"])


@pytest.mark.parametrize("counts,blocked", [([32_000_001], True), ([16_000_001, 16_000_001], True),
                                           ([2_000_000, 2_000_000], False)])
def test_source_metadata_budget_blocks_single_and_cumulative_overflow(monkeypatch, counts, blocked):
    engine, _ = source_preflight_test_host(monkeypatch)
    budgets = []
    def access(name, cmds, om, oma, remaining):
        budgets.append(remaining)
        count = counts[int(name)]
        if count > remaining:
            raise ValueError("metadata weight budget exceeded")
        shape = types.SimpleNamespace(fullPathName=lambda: "|mesh" + name)
        return name, None, shape, None, count, [1]
    monkeypatch.setattr(engine.ng_engine, "_access", access)
    if blocked:
        with pytest.raises(ValueError, match="budget exceeded"):
            engine.preflight_sources([str(i) for i in range(len(counts))])
    else:
        assert len(engine.preflight_sources(["0", "1"])["skins"]) == 2
    assert budgets == [32_000_000] + ([32_000_000 - counts[0]] if len(counts) > 1 else [])


def test_cleanup_reads_full_actual_zero_columns_once_then_only_remaining_ids(monkeypatch):
    engine = importlib.import_module(PACKAGE + ".dair_engine")
    calls = []
    snapshot = {"name": "skin", "count": 1, "width": 2, "weights": np.array([1., 0.]),
                "influences": [{"uuid": "a"}, {"uuid": "b"}]}
    def capture(*args):
        calls.append("full_capture")
        return snapshot
    monkeypatch.setattr(engine.ng_engine, "_capture", capture)
    monkeypatch.setattr(engine, "_influence_ids", lambda *args: {"a"})
    cmds = types.SimpleNamespace(skinCluster=lambda *a, **kw: calls.append("detach"),
        getAttr=lambda plug: 12 if plug.endswith("maxInfluences") else True)
    options = {"remove_unused_influences": True, "max_influences": 12}
    assert engine._cleanup(snapshot, options, cmds, None, None) == ["b"]
    assert calls == ["full_capture", "detach"]


def test_tiered_threshold_only_never_calls_deformation_solver(monkeypatch):
    args = list(neighbor_fixture())
    args[2][0] = [.5999, .4, .0001, 0]
    monkeypatch.setattr(kernel, "optimize_weights", lambda *a, **k: pytest.fail("no cap violation"))
    output, metrics = kernel.optimize_tiered(*args, maximum=2, neighbor_rings=2)
    assert metrics["cap_count"] == metrics["solver_vertex_count"] == 0
    assert metrics["threshold_count"] == 1
    np.testing.assert_allclose(output[0, :2], args[2][0, :2] / .9999)
    np.testing.assert_array_equal(output[2:], args[2][2:])
    assert np.max(np.abs(output[1] - args[2][1])) <= .02


def test_tiered_cap_crops_unrelated_shell_and_matches_full_cap_objective():
    args = neighbor_fixture()
    expected, _ = kernel.optimize_weights(*args, maximum=2, neighbor_rings=2)
    actual, metrics = kernel.optimize_tiered(*args, maximum=2, neighbor_rings=2)
    np.testing.assert_allclose(actual, expected, atol=1e-10)
    assert metrics["solver_vertex_count"] == 2
    assert metrics["cap_count"] == 1


def test_two_rings_are_unique_and_do_not_cross_shells():
    edges = np.array([[0, 1], [1, 2], [2, 3], [4, 5]])
    assert kernel.neighbor_vertices(edges, [0], 2, 6).tolist() == [1, 2]


def test_direct_qp_matches_scipy_objectives_and_constraints():
    from scipy.optimize import minimize
    rng = np.random.default_rng(56)
    for count in (2, 4, 8, 12):
        for _ in range(6):
            a = rng.normal(size=(count, count))
            h = a.T @ a + np.eye(count) * .001
            g = rng.normal(size=count)
            initial = np.full(count, 1 / count)
            lower, upper = np.full(count, .0011), np.full(count, .8)
            actual = kernel._box_simplex(h, g, initial, lower, upper)
            assert actual is not None
            oracle = minimize(lambda x: x @ h @ x + 2 * g @ x, initial,
                jac=lambda x: 2 * (h @ x + g), bounds=list(zip(lower, upper)),
                constraints={"type": "eq", "fun": lambda x: x.sum() - 1,
                             "jac": lambda x: np.ones(count)}, method="SLSQP",
                options={"ftol": 1e-12, "maxiter": 300})
            assert oracle.success
            assert actual.sum() == pytest.approx(1., abs=1e-12)
            assert np.all(actual >= lower - 1e-12) and np.all(actual <= upper + 1e-12)
            assert actual @ h @ actual + 2 * g @ actual == pytest.approx(oracle.fun, abs=1e-8)


def test_numeric_session_reuses_one_worker_and_closes_it():
    solver = importlib.import_module(PACKAGE + ".dair_solver")
    options = {"threshold": .11, "max_influences": 2, "tiered": True,
               "solver_workdir": str(managed_numeric_test_dir("session"))}
    with solver.numeric_session():
        first, _ = solver.solve(*solve_fixture(), options)
        process = solver._SESSION["process"]
        second, _ = solver.solve(*solve_fixture(), options)
        assert solver._SESSION["process"] is process
    assert process.poll() == 0
    np.testing.assert_array_equal(first, second)
