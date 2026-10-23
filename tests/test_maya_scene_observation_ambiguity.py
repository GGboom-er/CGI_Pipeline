"""Co-depth geometry has no uniquely established object winner, independent of enumeration."""

from __future__ import annotations

import math
import types

import pytest

from cgi_pipeline.capabilities.maya import scene_geometry as geometry
from cgi_pipeline.capabilities.maya import scene_observation as observation
from test_maya_scene_observation import install_om
from test_maya_scene_observation_visibility import native_case


def hit_scene(monkeypatch, distances, origin=0.0, same_uuid=False, unsupported=None):
    point = types.SimpleNamespace(x=origin, y=0.0, z=0.0)
    def function_set(path):
        value = distances[path]
        hit = (point, value, 0, 0, 0.2, 0.3)
        return types.SimpleNamespace(closestIntersection=lambda *a: hit)
    install_om(monkeypatch, MFnMesh=function_set, MFloatPoint=lambda *a: None,
               MFloatVector=lambda *a: None, MSpace=types.SimpleNamespace(kWorld=4))
    monkeypatch.setattr(geometry, "dag_path", lambda path: path)
    meshes = [{"path": path, "uuid": "shared" if same_uuid else "uuid:" + path, "instance_number": i,
               "transform": {"path": path.rsplit("|", 1)[0], "uuid": "transform:" + path},
               "appearance": {"pixel_lookup_supported": path != unsupported,
                              "unsupported_reasons": ["transparent"] if path == unsupported else []}}
              for i, path in enumerate(distances)]
    return point, meshes


@pytest.mark.parametrize("same_uuid", [False, True])
def test_exact_depth_ties_are_unknown_independent_of_path_order(monkeypatch, same_uuid):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 2.0, "|B|shape": 2.0}, same_uuid=same_uuid)
    rows = []
    for candidates in (meshes, list(reversed(meshes))):
        hit = geometry.nearest_hit(candidates, point, point, 100.0)
        rows.append(observation._pixel_result(5, 5, 4, hit, {"mesh_paths": ["|A|shape"]}))
    for row in rows:
        assert row["status"] == "AMBIGUOUS_POLYGON_GEOMETRY"
        assert row["nearest_hit"] is None and row["expected_target_match"] is None
        assert row["candidate_count"] == 2
        assert [c["path"] for c in row["geometry_candidates"]] == ["|A|shape", "|B|shape"]
    assert rows[0] == rows[1]


def test_ambiguous_transparent_candidate_cannot_select_opaque_peer(monkeypatch):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 2, "|B|shape": 2}, unsupported="|B|shape")
    hit = geometry.nearest_hit(meshes, point, point, 100.0)
    row = observation._pixel_result(5, 5, 4, hit, {"mesh_paths": ["|A|shape"]})
    assert row["status"] == "AMBIGUOUS_POLYGON_GEOMETRY"
    assert row["expected_target_match"] is None
    assert any(c["pixel_lookup_supported"] is False for c in row["geometry_candidates"])


@pytest.mark.parametrize("depth,origin", [(0.0, 0.0), (0.001, 0.0), (2.0, 0.0),
                                         (1e6, 0.0), (2.0, 1e8)])
def test_near_depth_rejection_uses_declared_scale_aware_tolerance(monkeypatch, depth, origin):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": depth, "|B|shape": depth}, origin=origin)
    policy = geometry.nearest_hit(meshes[:1], point, point, 1e12)["depth_tolerance"]
    tolerance = policy["effective_world_units"]
    assert math.isfinite(tolerance) and tolerance >= 1e-5
    assert policy["purpose"] == "conservative_ambiguity_rejection_only"
    for multiplier, ambiguous in ((0.5, True), (2.0, False)):
        point, meshes = hit_scene(monkeypatch, {"|A|shape": depth, "|B|shape": depth + tolerance * multiplier}, origin=origin)
        hit = geometry.nearest_hit(meshes, point, point, 1e12)
        assert bool(hit.get("ambiguous")) is ambiguous


def test_candidate_cluster_is_measured_from_final_minimum_not_pairwise_chain(monkeypatch):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 1.0, "|B|shape": 1.0 + 0.75e-5,
                                          "|C|shape": 1.0 + 1.5e-5})
    outputs = [geometry.nearest_hit(order, point, point, 100.0) for order in (meshes, list(reversed(meshes)))]
    for output in outputs:
        assert [c["path"] for c in output["geometry_candidates"]] == ["|A|shape", "|B|shape"]
        assert output["candidate_count"] == 2
    assert outputs[0] == outputs[1]


def test_expected_group_covering_both_instances_does_not_establish_unique_hit(monkeypatch):
    point, meshes = hit_scene(monkeypatch, {"|group|A|shape": 2, "|group|B|shape": 2}, same_uuid=True)
    hit = geometry.nearest_hit(meshes, point, point, 100.0)
    row = observation._pixel_result(5, 5, 4, hit, {"mesh_paths": [m["path"] for m in meshes]})
    assert row["expected_target_match"] is None
    assert len({c["instance_number"] for c in row["geometry_candidates"]}) == 2


def test_depth_threshold_is_inclusive_and_next_representable_value_is_outside(monkeypatch):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 0.0, "|B|shape": 1e-5})
    assert geometry.nearest_hit(meshes, point, point, 100)["candidate_count"] == 2
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 0.0, "|B|shape": math.nextafter(1e-5, math.inf)})
    assert geometry.nearest_hit(meshes, point, point, 100)["path"] == "|A|shape"


@pytest.mark.parametrize("distance", [math.nan, math.inf, -math.inf])
def test_nonfinite_native_intersections_cannot_establish_a_winner(monkeypatch, distance):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": distance})
    with pytest.raises(RuntimeError, match="Nonfinite native intersection"):
        geometry.nearest_hit(meshes, point, point, 100)


@pytest.mark.parametrize("origin", [math.nan, math.inf, -math.inf, 3.4e38, -3.4e38])
def test_invalid_world_origin_is_rejected_before_native_float_conversion(monkeypatch, origin):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 2.0}, origin=origin)
    import maya.api.OpenMaya as om
    conversions = []
    monkeypatch.setattr(om, "MFloatPoint", lambda *args: conversions.append("point"))
    monkeypatch.setattr(om, "MFloatVector", lambda *args: conversions.append("vector"))
    with pytest.raises(ValueError, match="finite and representable"):
        geometry.nearest_hit(meshes, point, point, 100)
    assert conversions == []


def test_pixel_batch_reuses_function_sets_by_full_instance_path_only_within_request(monkeypatch):
    point, meshes = hit_scene(monkeypatch, {"|A|shape": 2.0, "|B|shape": 5.0}, same_uuid=True)
    builds, paths, intersections, validations = [], [], [], []
    def function_set(path):
        builds.append(path)
        def intersect(*args):
            intersections.append(path)
            return (point, 2.0 if "|A|" in path else 5.0, 0, 0, 0.2, 0.3)
        return types.SimpleNamespace(closestIntersection=intersect)
    import maya.api.OpenMaya as om
    monkeypatch.setattr(om, "MFnMesh", function_set)
    monkeypatch.setattr(geometry, "dag_path", lambda path: paths.append(path) or path)
    record = {"state": {"view": {"panel": "p"}, "meshes": meshes,
                        "targets": [{"path": "|A", "mesh_paths": ["|A|shape"]}]},
              "image": {"width": 10, "height": 10}}
    monkeypatch.setattr(observation, "validate", lambda *args: validations.append(1) or record)
    monkeypatch.setattr(observation.observation_view, "native_view", lambda *args: None)
    monkeypatch.setattr(observation.observation_view, "clipped_ray", lambda *args: (point, point, 100.0))
    params = {"observation": {}, "pixels": [[4, 5], [5, 5]], "expected_target": "|A"}
    first = observation._pick(object(), params)
    assert builds == paths == ["|A|shape", "|B|shape"]
    assert len(intersections) == 4 and len(validations) == 2
    assert all(row["expected_target_match"] for row in first["items"])
    second = observation._pick(object(), params)
    assert second == first
    assert builds == paths == ["|A|shape", "|B|shape"] * 2
    assert len(intersections) == 8 and len(validations) == 4


@pytest.mark.parametrize("hits", [["|B|shape", "|A|shape"], ["shape"]])
def test_native_multiple_or_short_name_hits_remain_ambiguous(native_case, hits):
    case = native_case
    case.cmds.hits[(2, 1)] = hits
    case.cmds.aliases["shape"] = ["|A|shape", "|B|shape"]
    result = case.run(expected_target="|A")
    assert result["status"] == "SUCCESS", result
    row = result["output"]["items"][0]
    assert {record["path"] for record in row["candidates"]} == {"|A|shape", "|B|shape"}
    assert {record["uuid"] for record in row["candidates"]} == {"shared"}
    assert row["expected_target_match"] is None


def test_native_exact_shared_shape_instance_is_not_expanded_to_other_instance(native_case):
    case = native_case
    case.cmds.hits[(2, 1)] = ["|B|shape"]
    result = case.run(expected_target="|A")
    assert result["status"] == "SUCCESS", result
    row = result["output"]["items"][0]
    assert [candidate["path"] for candidate in row["candidates"]] == ["|B|shape"]
    assert row["candidates"][0]["instance_number"] == 1
    assert row["expected_target_match"] is False and case.cmds.expansions == []


def test_native_unresolved_hits_are_not_dropped_or_treated_as_unique(native_case):
    case = native_case
    case.cmds.hits[(2, 1)] = ["|A|shape", "missing", "|gone"]
    result = case.run(expected_target="|A")
    assert result["status"] == "SUCCESS", result
    row = result["output"]["items"][0]
    assert row["raw_hits"] == ["|A|shape", "missing", "|gone"]
    assert len(row["unresolved_hits"]) == 2 and row["expected_target_match"] is None


@pytest.mark.parametrize("short", [False, True])
def test_native_candidate_budget_rejects_excess_without_truncation(native_case, short):
    case = native_case
    hits = ["|A" + str(i) for i in range(201)]
    case.cmds.hits[(2, 1)] = ["many"] if short else hits
    case.cmds.aliases["many"] = hits
    result = case.run()
    assert result["status"] == "ERROR" and "200" in result["error"]


def test_native_candidate_budget_applies_to_whole_pixel_batch(native_case):
    case = native_case
    case.cmds.hits[(2, 1)] = ["|A" + str(i) for i in range(200)]
    case.cmds.hits[(3, 1)] = ["|oneMore"]
    result = case.run(pixels=[[2, 1], [3, 1]])
    assert result["status"] == "ERROR" and "200" in result["error"]
