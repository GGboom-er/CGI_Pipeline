"""Bounded pure/transaction tests; actual Maya geometry remains live-test scope."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cgi_pipeline.capabilities.maya.asset.mesh_reduce import geometry as geo
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import ops, skin


def mesh_item(name="|source", offset=0):
    return {"source": name, "uuid": name + "-id", "shape": name + "|shape",
            "counts": {"vertices": 3, "faces": 1},
            "points": [(offset, 0, 0), (offset + 1, 0, 0), (offset, 1, 0)],
            "face_counts": [3], "face_indices": [0, 1, 2],
            "uvs": {"map1": [(0, 0), (1, 0), (0, 1)]},
            "uv_partition": {"map1": [0, 1, 2]},
            "materials": ["shader-id"], "skin_cluster": "", "history": [], "visibility": {}}


@pytest.mark.parametrize("ratio", [0, -0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_reduction_ratio_rejects_invalid_inputs(ratio):
    with pytest.raises(ValueError, match="keep_face_ratio"):
        ops.validate_entry({"output_name": "character_hair", "keep_face_ratio": ratio})


@pytest.mark.parametrize("name", [None, "", "name with space", "|root|mesh", "mesh*", "9mesh"])
def test_output_names_are_literal(name):
    with pytest.raises(ValueError, match="output_name"):
        ops.validate_entry({"output_name": name})


def test_reduce_preserves_skin_by_default_and_has_no_aggressive_default():
    assert ops.validate_entry({"output_name": "character_hair"}) == (1.0, "auto")
    assert ops.validate_entry({"output_name": "character_hair", "preserve_skin": False}) == (1.0, False)
    with pytest.raises(ValueError, match="preserve_skin"):
        ops.validate_entry({"output_name": "character_hair", "preserve_skin": 1})


def test_clean_output_verification_is_against_reduced_construction():
    reduced = mesh_item()
    output = deepcopy(reduced)
    reduced["history"] = [("reduce1", "polyReduce")]
    assert all(geo.verify_clean(reduced, output).values())


@pytest.mark.parametrize("field", ["points", "materials", "uvs", "uv_partition", "history"])
def test_clean_output_rejects_geometry_material_uv_or_history_corruption(field):
    expected = mesh_item()
    actual = deepcopy(expected)
    changes = {"points": [(100, 0, 0)] * 3, "materials": ["other-shader"],
               "uvs": {"map1": [(0, 0)] * 3}, "uv_partition": {"map1": [0, 0, 0]},
               "history": [("merge", "polyUnite")]} 
    actual[field] = changes[field]
    with pytest.raises(RuntimeError, match="verification failed"):
        geo.verify_clean(expected, actual)


def test_concatenate_tracks_order_topology_and_distinct_uv_seams():
    first, second = mesh_item(), mesh_item("|second", offset=2)
    expected = geo.concatenate([first, second])
    assert expected["face_indices"] == [0, 1, 2, 3, 4, 5]
    assert expected["uv_partition"] == {"map1": [0, 1, 2, 3, 4, 5]}
    assert expected["materials"] == ["shader-id", "shader-id"]
    assert expected["points"][3] == (2, 0, 0)
    second["uvs"] = {"extra": second["uvs"]["map1"]}
    with pytest.raises(ValueError, match="UV set names"):
        geo.concatenate([first, second])


def test_concatenate_offsets_sparse_and_unassigned_uv_labels_in_one_input_pass():
    class SinglePass:
        def __init__(self, values):
            self.values = values
            self.reads = 0

        def __iter__(self):
            self.reads += 1
            assert self.reads == 1, "UV labels must be streamed once"
            return iter(self.values)

    items = [mesh_item("|" + str(index)) for index in range(4)]
    streams = [SinglePass(values) for values in ([None, None, None], [0, 4, 0],
                                               [None, 2, 0], [0, 0, 1])]
    for item, stream in zip(items, streams):
        item["uv_partition"]["map1"] = stream
    assert geo.concatenate(items)["uv_partition"]["map1"] == [
        None, None, None, 0, 4, 0, None, 7, 5, 8, 8, 9]
    assert all(stream.reads == 1 for stream in streams)


def context(influence="joint-a", bind=1, method=0):
    return {"settings": {"skinningMethod": method}, "geom_matrix": [1] * 16,
            "bindings": {influence: {"name": influence, "matrix_source": influence + ".worldMatrix[0]", "bind_pre": [bind] * 16}}}


def test_binding_matrix_roundoff_does_not_hide_pose_or_connection_changes():
    a, b = context(), context()
    b["bindings"]["joint-a"]["bind_pre"][12] += 5e-14
    assert skin.bindings_equal(a["bindings"], b["bindings"])
    b["bindings"]["joint-a"]["bind_pre"][12] += .001
    assert not skin.bindings_equal(a["bindings"], b["bindings"])
    b = context()
    b["bindings"]["joint-a"]["matrix_source"] = "other.worldMatrix[0]"
    assert not skin.bindings_equal(a["bindings"], b["bindings"])


def test_binding_compatibility_allows_different_influence_sets_but_not_context_conflicts():
    skin.compatible([context(), context("joint-b")])
    skin.compatible([None, None])
    for values in ([context(), None], [context(), context(method=1)], [context(), context(bind=2)]):
        with pytest.raises(ValueError):
            skin.compatible(values)
    different_space = context()
    different_space["geom_matrix"][12] = 2
    with pytest.raises(ValueError, match="geometry binding matrices"):
        skin.compatible([context(), different_space])


def test_exact_skin_comparison_uses_uuid_keys_instead_of_physical_columns():
    left = {"weights": [{"a": 0.3, "b": 0.7}], "blend_weights": [0.25]}
    right = {"weights": [{"b": 0.7, "a": 0.3, "zero": 0}], "blend_weights": [0.25]}
    assert all(skin.verify_weights(left, right).values())
    right["weights"][0]["a"] = 0.31
    assert not skin.verify_weights(left, right)["weights"]


class NativeUndo:
    """Models native undo ownership, independently from geometry helper stubs."""

    def __init__(self):
        self.nodes = {"source-id"}
        self.selection = ["|source"]
        self.calls = []
        self.saved = None
        self.snapshot = mesh_item()

    def ls(self, **kwargs):
        return list(self.selection) if kwargs.get("selection") else sorted(self.nodes)

    def objExists(self, node):
        return node in self.nodes or (node == "|source" and "source-id" in self.nodes)

    def undoInfo(self, **kwargs):
        if kwargs.get("query"):
            return True
        self.calls.append(kwargs)
        if kwargs.get("openChunk"):
            self.saved = (set(self.nodes), deepcopy(self.snapshot), list(self.selection))

    def createNode(self, _type, **kwargs):
        self.nodes.add(kwargs["name"])
        return kwargs["name"]

    def delete(self, node):
        self.nodes.remove(node)

    def select(self, values=None, **kwargs):
        self.selection = [] if kwargs.get("clear") else list(values)

    def undo(self):
        self.calls.append("undo")
        self.nodes, self.snapshot, self.selection = deepcopy(self.saved)


def prepared(fake):
    return [{"source": "|source", "snapshot": deepcopy(fake.snapshot)}]


def test_failed_batch_undo_restores_created_nodes_original_geometry_and_selection(monkeypatch):
    fake = NativeUndo()
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "snapshot", lambda *_: deepcopy(fake.snapshot))
    originals = prepared(fake)

    def fail():
        fake.nodes.add("partial-mesh-id")
        fake.snapshot["points"] = []
        fake.selection = ["|partial"]
        raise RuntimeError("injected native failure")

    with pytest.raises(RuntimeError, match="rollback_verified=True"):
        ops._transaction("mesh", originals, fail, "character")
    assert fake.nodes == {"source-id"}
    assert fake.snapshot["points"] == mesh_item()["points"]
    assert fake.selection == ["|source"]
    assert fake.calls[-1] == "undo"
    assert fake.calls[-2] == {"closeChunk": True}


def test_successful_batch_leaves_source_and_one_native_undo_chunk(monkeypatch):
    fake = NativeUndo()
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "snapshot", lambda *_: deepcopy(fake.snapshot))

    def create():
        fake.nodes.add("output-id")
        fake.selection = ["|output"]
        return [{"result": "|output"}]

    result = ops._transaction("mesh", prepared(fake), create, "character")
    assert result["rows"] == [{"result": "|output"}]
    assert result["transaction"]["sources_unchanged"] is True
    assert fake.nodes == {"source-id", "output-id"}
    assert fake.selection == ["|source"]
    assert fake.calls == [{"openChunk": True, "chunkName": "mesh"}, {"closeChunk": True}]


def test_caller_owned_transaction_propagates_failure_without_undoing_outer_chunk(monkeypatch):
    fake = NativeUndo()
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))

    def fail():
        fake.selection = ["|partial"]
        raise ValueError("outer owner must roll back")

    with pytest.raises(ValueError, match="outer owner"):
        ops._transaction("mesh", prepared(fake), fail, "character", manage_undo=False)
    assert fake.calls == []
    assert fake.selection == ["|source"]


def test_consumed_input_failure_restores_original_uuid_and_snapshot(monkeypatch):
    fake = NativeUndo()
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "snapshot", lambda *_: deepcopy(fake.snapshot))
    originals = prepared(fake)
    originals[0]["consume"] = True

    def fail_after_consume():
        fake.nodes.remove("source-id")
        fake.nodes.add("partial-merged-id")
        fake.snapshot["points"] = []
        raise RuntimeError("combine readback failed")

    with pytest.raises(RuntimeError, match="rollback_verified=True"):
        ops._transaction("consume", originals, fail_after_consume, "character")
    assert fake.nodes == {"source-id"}
    assert fake.snapshot == mesh_item()


@pytest.mark.parametrize("use_cache,damage", [(False, None), (True, None),
                                             (True, "uvs"), (True, "weights")])
def test_consume_combine_calls_native_directly_without_recopying_or_rebinding(monkeypatch, use_cache, damage):
    items = {"|source": mesh_item(), "|second": mesh_item("|second", offset=2)}
    calls = []
    concatenate = geo.concatenate
    assembled = []
    preflighted = []
    guards = []
    readbacks = []
    binding = context() if damage == "weights" else None
    if binding:
        for item in items.values():
            item.update(skin_cluster="inputSkin", weights=[{"joint-a": 0.25, "joint-b": 0.75}] * 3,
                        blend_weights=[0.0] * 3)

    def expected_once(values):
        assembled.append(values)
        return concatenate(values)

    def native(sources, **kwargs):
        calls.append(("native", list(sources), kwargs))
        combined = concatenate([items[path] for path in sources])
        combined.update(source="|native", uuid="merged-id", shape="|native|shape",
                        skin_cluster="combinedSkin" if binding else "",
                        history=[], visibility={}, counts={"faces": 2, "vertices": 6})
        if damage == "uvs":
            combined["uvs"]["map1"][0] = (3.0, 4.0)
        elif damage == "weights":
            combined["weights"][0] = {"joint-a": 0.45, "joint-b": 0.55}
        items["|native"] = combined
        return ["|native"]

    def rename(path, name):
        output = "|" + name
        item = items.pop(path)
        item.update(source=output, shape=output + "|shape")
        items[output] = item
        return output

    def delete(paths):
        calls.append(("delete", list(paths)))
        for path in paths:
            items.pop(path)

    fake = SimpleNamespace(polyUnite=native, polyUniteSkinned=native, objExists=lambda path: path in items,
                           nodeType=lambda _path: "transform", rename=rename,
                           listRelatives=lambda *_args, **_kwargs: [],
                           ls=lambda path, **_kwargs: [path], delete=delete)
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "concatenate", expected_once)
    def snapshot(path, *_args):
        readbacks.append(path)
        return deepcopy(items[path])

    monkeypatch.setattr(geo, "snapshot", snapshot)
    monkeypatch.setattr(geo, "protect_dependencies", lambda *_: "combinedSkin" if binding else "")
    monkeypatch.setattr(geo, "copy_visibility", lambda *_: None)
    monkeypatch.setattr(ops, "_names_available", lambda *_: None)
    monkeypatch.setattr(ops, "_shape", lambda path: path + "|shape")
    def preflight(path, _identity):
        preflighted.append(path)
        return {"source": path, "snapshot": deepcopy(items[path]), "skin": binding, "parent": None}

    def guard(path, *_args):
        guards.append(path)
        return {"frame": -10}

    monkeypatch.setattr(ops, "_preflight", preflight)
    monkeypatch.setattr(ops, "_cache_guard", guard)
    monkeypatch.setattr(ops, "_transaction", lambda _label, _prepared, operation, *_args: {"rows": operation()})
    monkeypatch.setattr(ops, "_copy", lambda *_: pytest.fail("consume must not duplicate inputs"))
    monkeypatch.setattr(skin, "bind_copy", lambda *_: pytest.fail("consume must not rebind inputs"))
    monkeypatch.setattr(skin, "restore_binding_context", lambda name, value: calls.append(("restore_bind_context", name, value)))
    prepared_cache = {item["uuid"]: {"guard": {"frame": -10}, "prepared": {
        "source": path, "snapshot": deepcopy(item), "skin": binding, "parent": None}}
        for path, item in items.items()} if use_cache else None
    groups = [{"sources": ["|source", "|second"], "expected_uuids": ["|source-id", "|second-id"],
               "output_name": "character_hair", "consume_inputs": True}]
    if damage:
        failed_check = "uv_corners" if damage == "uvs" else "weights"
        with pytest.raises(RuntimeError, match="'" + failed_check + "': False"):
            ops.combine_groups(groups, manage_undo=False, prepared_cache=prepared_cache)
        assert guards == ["|source", "|second"]
        assert preflighted == []
        assert readbacks == ["|character_hair"]
        assert calls[0][0] == "native"
        assert len(assembled) == 1
        assert set(prepared_cache) == {"|source-id", "|second-id"}
        return
    result = ops.combine_groups(groups, manage_undo=not use_cache, prepared_cache=prepared_cache)
    assert calls[0] == ("native", ["|source", "|second"],
                        {"constructionHistory": False, "mergeUVSets": 1})
    assert set(items) == {"|character_hair"}
    assert result["rows"][0]["consume_inputs"] is True
    assert result["rows"][0]["source_uuid"] == "|source-id"
    assert len(assembled) == 1
    assert preflighted == ([] if use_cache else ["|source", "|second"])
    assert prepared_cache == ({} if use_cache else None)


def test_prepared_cache_changed_guard_rejects_before_native_combine(monkeypatch):
    monkeypatch.setattr(ops, "_cache_guard", lambda *_args: {"frame": 11})
    monkeypatch.setattr(ops, "_preflight", lambda *_args: pytest.fail("stale cache must fail closed"))
    cache = {"source-id": {"guard": {"frame": 10}, "prepared": {"source": "|source"}}}
    with pytest.raises(RuntimeError, match="changed before combine"):
        ops._combine_preflight("|source", "source-id", cache)


def test_cache_guard_tracks_shape_identity_pose_and_binding_metadata_without_snapshot(monkeypatch):
    values = {"shape": "|source|shape", "frame": -10, "matrix": [1] * 16,
              "shape_uuid": "shape-id", "visibility": {"visible": True},
              "history": ["skin1"], "context": context()}
    fake = SimpleNamespace(
        ls=lambda node, **_kwargs: [values["shape_uuid"] if node == values["shape"] else node + "-id"],
        currentTime=lambda **_kwargs: values["frame"],
        xform=lambda *_args, **_kwargs: list(values["matrix"]),
        listRelatives=lambda *_args, **_kwargs: ["|parent"],
        listHistory=lambda *_args, **_kwargs: list(values["history"]))
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "resolve", lambda *_args: values["shape"])
    monkeypatch.setattr(geo, "protect_dependencies", lambda *_args: "skin1")
    monkeypatch.setattr(geo, "visibility_signature", lambda *_args: deepcopy(values["visibility"]))
    monkeypatch.setattr(geo, "snapshot", lambda *_args: pytest.fail("light guard must not snapshot"))
    captured = []

    def binding(*_args, **kwargs):
        captured.append(kwargs)
        return deepcopy(values["context"])

    monkeypatch.setattr(skin, "binding_context", binding)
    original = ops._cache_guard("|source", "source-id")
    for key, changed in (("shape_uuid", "replaced-shape"), ("frame", 11),
                         ("matrix", [2] * 16), ("visibility", {"visible": False}),
                         ("history", ["skin1", "new-history"]), ("context", context(bind=2))):
        previous = values[key]
        values[key] = changed
        assert ops._cache_guard("|source", "source-id") != original
        values[key] = previous
    assert all(call == {"require_rest": False} for call in captured)


@pytest.mark.parametrize("cache,manage_undo", [({}, True), ([], False), ("foreign", False)])
def test_prepared_cache_requires_internal_caller_owned_transaction(cache, manage_undo):
    with pytest.raises(ValueError, match="internal caller-owned"):
        ops._cache_option(cache, manage_undo)


def test_registered_adapter_passes_explicit_arrays_to_the_same_python_core(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import adapter
    captured = []
    monkeypatch.setattr(ops, "reduce_entries", lambda entries, **kwargs: captured.append((entries, kwargs)) or {"rows": []})
    ctx = SimpleNamespace(extras={"_api_id": "maya.asset.mesh_reduce", "_api_version": "1.0.0"})
    result = adapter.execute({"mode": "reduce", "entries": [{"source": "|source"}], "character": "character"}, ctx)
    assert result["status"] == "SUCCESS"
    assert captured == [([{"source": "|source"}], {"character": "character"})]
    assert adapter.execute({"mode": "unknown"}, ctx)["status"] == "ERROR"


@pytest.mark.parametrize("ratio", [0.5, 1.0])
@pytest.mark.parametrize("use_cache", [False, True])
def test_reduce_operates_on_owned_construction_and_skips_it_when_not_reducing(monkeypatch, ratio, use_cache):
    fake = NativeUndo()
    items = {"|source": mesh_item()}
    events = []
    monkeypatch.setattr(geo, "modules", lambda: (fake, None, None))
    monkeypatch.setattr(geo, "snapshot", lambda source, *_: deepcopy(items[source]))
    monkeypatch.setattr(ops, "_names_available", lambda _names: None)
    guard = {"skin": None, "parent": None, "frame": -10}
    monkeypatch.setattr(ops, "_cache_guard", lambda *_args, **_kwargs: guard)
    monkeypatch.setattr(ops, "_preflight", lambda *_args, **_kwargs: {
        "source": "|source", "snapshot": deepcopy(items["|source"]), "skin": None, "parent": None})

    def copy(source, name):
        events.append(("copy", source, name))
        path = "|" + name
        items[path] = deepcopy(items[source])
        items[path].update(source=path, uuid=path + "-id", history=[])
        return path

    def reduce(source, **kwargs):
        events.append(("reduce", source, kwargs["percentage"]))
        items[source]["history"] = [("reduce1", "polyReduce")]
        items[source]["points"][1] = (0.9, 0, 0)

    def delete(node):
        events.append(("delete", node))
        items.pop(node, None)
        fake.nodes.discard(node)

    monkeypatch.setattr(ops, "_copy", copy)
    monkeypatch.setattr(geo, "copy_visibility", lambda *_: None)
    fake.polyReduce = reduce
    fake.delete = delete
    cache = {} if use_cache else None
    result = ops.reduce_entries([{"source": "|source", "expected_uuid": "source-id",
                                  "output_name": "character_hair", "keep_face_ratio": ratio}],
                                manage_undo=not use_cache, prepared_cache=cache)
    if ratio < 1:
        assert events[:3] == [("copy", "|source", "character_hair_construction"),
                              ("reduce", "|character_hair_construction", 50.0),
                              ("copy", "|character_hair_construction", "character_hair")]
    else:
        assert events[0] == ("copy", "|source", "character_hair")
        assert not any(event[0] == "reduce" for event in events)
    assert "|character_hair_construction" not in items
    assert items["|source"] == mesh_item()
    assert result["rows"][0]["lossy"] is (ratio < 1)
    assert result["rows"][0]["after"]["history_types"] == []
    if use_cache:
        cached = cache[result["rows"][0]["result_uuid"]]
        assert cached["guard"] == guard
        assert cached["prepared"]["snapshot"] == items["|character_hair"]


def test_manifest_declares_batched_explicit_scope_and_real_handler():
    root = Path(__file__).resolve().parents[1]
    path = root / "src/cgi_pipeline/capabilities/maya/asset/mesh_reduce/capability.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["api_id"] == "maya.asset.mesh_reduce"
    assert spec["inputs"]["entries"]["type"] == "array"
    assert spec["inputs"]["groups"]["type"] == "array"
    assert spec["mode_binding"]["input"] == "mode"
    assert spec["modes"] == ["inspect", "apply"]
    assert {case['value']:case['mode'] for case in spec['mode_binding']['cases']} == {
        'analyze':'inspect','reduce':'apply','combine':'apply','regional':'apply','construct':'apply'}
    assert "foreground" in spec["execution_modes"]
    assert spec["handler"].endswith("mesh_reduce.adapter:execute")
