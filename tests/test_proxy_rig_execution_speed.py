"""Keep orchestration reuse local and retain write-time safety while timing stages."""
import json
from pathlib import Path
import sys
import types

import pytest

from cgi_pipeline.capabilities.maya.asset.proxy_rig import core


def test_source_preflight_uses_metadata_contract_not_full_weight_prepare(monkeypatch):
    calls = []
    engine = types.SimpleNamespace(preflight_sources=lambda skins: calls.append(skins) or {
        "skins": [{"skin_cluster": skin} for skin in skins],
        "weight_validation": "deferred_to_geometry_inheritance_and_final_output"})
    monkeypatch.setattr(core, "_weight_engine", lambda _: engine)
    monkeypatch.setattr(core.outside, "preflight_discard", lambda *_: None)
    monkeypatch.setattr(core.material_cleanup, "preflight", lambda *_: None)
    monkeypatch.setattr(core.safety, "check_consumers", lambda *_: None)
    monkeypatch.setattr(core.safety, "preflight_names", lambda *_: None)
    monkeypatch.setattr(core.safety, "resolve", lambda _, uid: "extraSkin" if uid == "extra" else uid)
    normalized = {"discard": [], "discard_by_id": {}, "cleanup_unused_materials": False,
                  "changed": ["b", "a"], "by_id": {
                      "a": {"skin": {"name": "sourceSkin", "uuid": "source"}},
                      "b": {"skin": {"name": "sourceSkin", "uuid": "source"}},
                      "c": {"skin": {"name": "extraSkin", "uuid": "extra"}}},
                  "weights": {"method": "dair_inspired", "additional_skin_uuids": ["extra"]}}
    for row in normalized["by_id"].values():
        row["vertices"] = 5
        row["skin"]["influences"] = ["joint"]
    result = core.preflight(types.SimpleNamespace(undoInfo=lambda **_: True), normalized)
    assert calls == [["extraSkin", "sourceSkin"]]
    assert result["undo_enabled"] is True
    assert result["weight_sources"]["weight_validation"] == "geometry inheritance and serial final skin verification"


@pytest.mark.parametrize("skinned", [False, True])
@pytest.mark.parametrize("mode", ["preview", "apply"])
@pytest.mark.parametrize("options, error", [
    ({"neighbor_rings": -1}, "neighbor_rings"),
    ({"neighbor_rings": True}, "neighbor_rings"),
    ({"neighbor_max_delta": 0}, "neighbor_max_delta"),
    ({"neighbor_max_delta": float("nan")}, "neighbor_max_delta"),
    ({"remove_unused_influences": 1}, "remove_unused_influences"),
    ({"remove_unused_influences": "yes"}, "remove_unused_influences"),
    ({"solver_workdir": ""}, "solver_workdir"),
    ({"solver_workdir": 7}, "solver_workdir"),
])
def test_invalid_weight_options_refuse_before_geometry_even_without_skins(
        monkeypatch, tmp_path, skinned, mode, options, error):
    cmds = _Cmds()
    normalized = _normalized()
    normalized["weights"] = {"method": "dair_inspired", **options}
    if skinned:
        normalized["by_id"]["source"] = {"skin": {"name": "skin1", "uuid": "skin1Uuid"}}
        normalized["changed"] = ["source"]
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    monkeypatch.setattr(core.inventory, "inspect", lambda *_, **__: _snapshot())
    monkeypatch.setattr(core.safety, "artifact_root", lambda *_: tmp_path)
    monkeypatch.setattr(core.safety, "write_report", lambda *args: "inventory.json")
    monkeypatch.setattr(core.safety, "managed", lambda value, **_: Path(value))
    monkeypatch.setattr(core.safety, "output_path", lambda *_: tmp_path / "proxy.ma")
    monkeypatch.setattr(core.plans, "validate", lambda *_: normalized)
    monkeypatch.setattr(core.outside, "preflight_discard", lambda *_: None)
    monkeypatch.setattr(core.material_cleanup, "preflight", lambda *_: None)
    monkeypatch.setattr(core.safety, "check_consumers", lambda *_: None)
    monkeypatch.setattr(core.safety, "preflight_names", lambda *_: None)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Invalid options must be refused before host access/dependencies or geometry writes")

    monkeypatch.setattr(core, "_weight_engine", unexpected)
    monkeypatch.setattr(core, "_reduce", unexpected)
    monkeypatch.setattr(core.safety, "resolve", unexpected)
    with pytest.raises(ValueError, match=error):
        core.run({"project": "ysj", "parameters": {"mode": mode, "plan": {
            "source_master": {"path": "/formal/master.ma", "version": "v003"}}}})
    assert cmds.ids == {"original"}
    assert cmds.undo_calls == []


def test_valid_options_without_skins_only_use_parameter_sentinel(monkeypatch):
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
    captured = []
    validate = dair_engine.validate_params

    def record(params):
        captured.append(params["skin_clusters"])
        return validate(params)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Parameter-only validation must not resolve or prepare a skin")

    monkeypatch.setattr(dair_engine, "validate_params", record)
    monkeypatch.setattr(core, "_weight_engine", unexpected)
    monkeypatch.setattr(core.safety, "resolve", unexpected)
    monkeypatch.setattr(core.outside, "preflight_discard", lambda *_: None)
    monkeypatch.setattr(core.material_cleanup, "preflight", lambda *_: None)
    monkeypatch.setattr(core.safety, "check_consumers", lambda *_: None)
    monkeypatch.setattr(core.safety, "preflight_names", lambda *_: None)
    normalized = _normalized()
    normalized["weights"] = {"method": "dair_inspired"}
    result = core.preflight(types.SimpleNamespace(undoInfo=lambda **_: True), normalized)
    assert captured == [["__proxy_parameter_validation__"]]
    assert result["weight_sources"] == {"skins": [], "weight_validation": "not_applicable"}
    assert "__proxy_parameter_validation__" not in json.dumps(result)


def test_proxy_builds_final_groups_once_without_intermediate_skin_combine(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import construction
    calls = []
    normalized = {"character": "pengMoWang", "weights": {"method": "dair_inspired"},
        "reductions": [{"source_uuid": "a", "keep_face_ratio": .6}],
        "merges": [{"sources": ["a", "b"]}],
        "by_id": {"a": {"path": "|a"}, "b": {"path": "|b"}}}
    def construct(groups, options, **kwargs):
        calls.append((groups, options, kwargs))
        return {"weights": [], "timing_seconds": {}, "rows": [{"source_uuid":"a"}],
                "groups": [{"result_uuid":"combined"}], "skin_nodes_created":1, "weight_write_calls":2}
    monkeypatch.setattr(construction, "construct_groups", construct)
    monkeypatch.setattr(core, "_weight_engine", lambda _: types.SimpleNamespace(validate_params=lambda p:p))
    reduced = core._reduce(None, normalized)
    combined = core._combine(None, normalized, reduced)
    assert len(calls)==1 and combined==[{"result_uuid":"combined"}]
    assert calls[0][0][0]['entries']==[
        {'source':'|a','expected_uuid':'a','keep_face_ratio':.6},
        {'source':'|b','expected_uuid':'b','keep_face_ratio':1.}]
    assert calls[0][2]['manage_undo'] is False
    assert normalized['construction_stats']['skin_nodes_created']==1


class _Cmds:
    def __init__(self):
        self.ids = {"original"}
        self.undo_calls = []

    def ls(self, **kwargs):
        return [] if kwargs.get("selection") else sorted(self.ids)

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)

    def createNode(self, *_args, **_kwargs):
        self.ids.add("marker")
        return "marker"

    def delete(self, node):
        self.ids.remove(node)

    def select(self, *_args, **_kwargs):
        pass


def _snapshot():
    return {"scene_path": "/managed/source.ma", "scene_fingerprint": "initial",
            "project": "ysj", "meshes": [], "mesh_count": 0, "faces": 0,
            "cache_root": "|cache", "outside_cache_summary": {}, "policy_version": "test",
            "categories": {}, "fingerprint_scope": [], "policy": {}, "cache_candidates": []}


def _normalized():
    return {"character": "pengMoWang", "discard": [], "discard_by_id": {},
            "cleanup_unused_materials": False, "keep": [], "protect": [], "by_id": {},
            "changed": [], "full_scene_coverage": False, "unscoped_meshes": 0,
            "reductions": [], "merges": [], "weights": {}}


def _stub_apply_dependencies(monkeypatch, cache_log):
    def reduce(_cmds, _normalized, *, prepared_cache):
        assert prepared_cache == {}
        prepared_cache["verified"] = object()
        cache_log.append(prepared_cache)
        return []

    def combine(_cmds, _normalized, _reduced, *, prepared_cache):
        assert prepared_cache is cache_log[-1]
        assert "verified" in prepared_cache
        return []

    monkeypatch.setattr(core, "_reduce", reduce)
    monkeypatch.setattr(core, "_combine", combine)
    monkeypatch.setattr(core, "_outputs", lambda *_: [])
    monkeypatch.setattr(core, "_prune", lambda *_: {})
    monkeypatch.setattr(core, "_replace", lambda *_: [])
    monkeypatch.setattr(core.material_cleanup, "run", lambda *_: {"deleted": []})
    monkeypatch.setattr(core.inventory, "inspect", lambda *_, **__: _snapshot())
    save = types.ModuleType("cgi_pipeline.capabilities.maya.asset.save_scene.save_scene")
    save.execute = lambda payload: {"status": "SUCCESS", "output": {
        "output_path": payload["parameters"]["save_path"]}}
    monkeypatch.setitem(sys.modules, save.__name__, save)


def test_apply_cache_lifetime_is_one_transaction_and_timings_are_not_parent_sums(monkeypatch):
    cache_log = []
    _stub_apply_dependencies(monkeypatch, cache_log)
    reports = [core.apply(_Cmds(), {}, _normalized(), _snapshot(), {}, Path("/proxy.ma"))
               for _ in range(2)]
    assert len(cache_log) == 2 and cache_log[0] is not cache_log[1]
    for report in reports:
        phases = report["timing_seconds"]
        assert {"reduce", "combine", "outputs", "weights", "replace", "discard",
                "material_cleanup", "inventory_final", "retained_validation", "save",
                "report_prepare"} <= phases.keys()
        assert all(value >= 0 for value in phases.values())
        assert "apply_total" not in phases
        assert report["timing_totals_seconds"]["apply_total"] >= sum(phases.values())


@pytest.mark.parametrize("mode", ["preview", "apply"])
def test_public_run_preflights_once_and_writes_build_report_once(monkeypatch, tmp_path, mode):
    cache_log, preflights, artifacts = [], [], []
    _stub_apply_dependencies(monkeypatch, cache_log)
    maya = types.ModuleType("maya")
    maya.cmds = _Cmds()
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", maya.cmds)
    monkeypatch.setattr(core.safety, "artifact_root", lambda *_: tmp_path)
    monkeypatch.setattr(core.safety, "managed", lambda value, **_: Path(value))
    monkeypatch.setattr(core.safety, "output_path", lambda *_: tmp_path / "proxy.ma")
    monkeypatch.setattr(core.plans, "validate", lambda *_: _normalized())
    monkeypatch.setattr(core, "preflight", lambda *_: preflights.append(mode) or {"validated": True})

    def write_report(root, label, data):
        # Serialize now, as the production writer does: report_write is only
        # known after this call and must not force a second full report write.
        artifacts.append((label, json.loads(json.dumps(data))))
        return str(root / (label + ".json"))

    monkeypatch.setattr(core.safety, "write_report", write_report)
    status, result = core.run({"project": "ysj", "parameters": {"mode": mode,
        "plan": {"source_master": {"path": "/formal/master.ma", "version": "v003"}}}})
    assert preflights == [mode]
    assert result["plan_valid"] is True
    assert "preflight" in result["timing_seconds"]
    if mode == "preview":
        assert status == "SUCCESS"
        assert [label for label, _ in artifacts] == ["inventory"]
        assert cache_log == []
    else:
        assert status == "PARTIAL"
        assert [label for label, _ in artifacts] == ["inventory", "build"]
        persisted = artifacts[-1][1]
        assert persisted["preflight"] == {"validated": True}
        assert "save" in persisted["timing_seconds"]
        assert "report_write" not in persisted["timing_seconds"]
        assert "api_total" not in persisted["timing_totals_seconds"]
        assert "report_write" in result["timing_seconds"]
        assert result["timing_totals_seconds"]["api_total"] >= result["timing_totals_seconds"]["api_before_report"]


def test_final_inventory_skips_retained_sources_before_expensive_row_read(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.proxy_rig import inventory
    seen=[]
    monkeypatch.setattr(inventory.target_scope,'resolve',lambda *_:('|cache',[
        '|cache|originals|old|oldShape','|cache|proxy|proxyShape'],['|cache']))
    monkeypatch.setattr(inventory.outside,'inspect',lambda *_:[])
    def read_row(_cmds,shape,*_):
        seen.append(shape)
        return {'uuid':'proxy','path':'|cache|proxy','faces':24,'bbox':[0]*6,
                'category_candidate':'hair','protected_reasons':[]}
    monkeypatch.setattr(inventory,'row',read_row)
    class Cmds:
        def file(self,**_):return '/managed/candidate.ma'
        def currentTime(self,**_):return 1.
    snapshot=inventory.inspect(Cmds(),exclude_transforms=['|cache|originals|old'])
    assert seen==['|cache|proxy|proxyShape']
    assert snapshot['mesh_count']==1 and snapshot['faces']==24


@pytest.mark.parametrize('missing',[False,True])
def test_retained_uuid_lookup_uses_resolved_paths_and_rejects_actual_missing(missing):
    from cgi_pipeline.capabilities.maya.asset.proxy_rig.verify_delivery import _retained_paths
    class Cmds:
        def ls(self,values,**flags):
            if flags.get('long'):return [] if missing else ['|originals|mesh']
            if flags.get('uuid'):
                assert values==['|originals|mesh'],'UUID-input flag form is not reliable'
                return ['mesh-uuid']
    if missing:
        with pytest.raises(RuntimeError,match='original meshes are missing'):
            _retained_paths(Cmds(),[{'uuid':'mesh-uuid'}])
    else:
        assert _retained_paths(Cmds(),[{'uuid':'mesh-uuid'}])==['|originals|mesh']
