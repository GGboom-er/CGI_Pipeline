"""Fault tests for observation-bound edits; Maya geometry validation has its own tests."""

import copy
import sys
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from cgi_pipeline.capabilities.maya import scene_batch, scene_core
from cgi_pipeline.contracts import ApiContext


class Maya:
    def __init__(self):
        self.values = {p: {a + x: float(v) for a, v in (("translate", 0), ("rotate", 0), ("scale", 1))
                           for x in "XYZ"} for p in ("|hair", "|sole")}
        self.uuids = {"|hair": "hair-id", "|sole": "sole-id"}
        self.node_types = {p: "transform" for p in self.values}
        self.node_types.update({p + "|shape": "mesh" for p in self.values})
        self.children = {p: [p + "|shape"] for p in self.values}
        self.locks, self.incoming, self.outgoing = set(), set(), set()
        self.undo_enabled = True
        self.undo_calls = self.writes = 0
        self.fail_at = self.wrong_at = 0
        self.mutate_before_error = self.undo_raises = self.undo_ineffective = False
        self.active_chunk = self.undo_name = ""
        self.chunk_snapshot = self.undo_snapshot = None
        self.close_raises = self.spoil_undo_name = False
        self.later_changes_prior = self.scalar_side_effect = False

    def ls(self, name, **flags):
        if flags.get("uuid"):
            return [self.uuids[name]] if name in self.uuids else []
        if name in self.uuids.values():
            return [p for p, value in self.uuids.items() if value == name]
        return [name] if name in self.node_types else []

    def nodeType(self, path):
        return self.node_types[path]

    def listRelatives(self, path, **flags):
        return self.children.get(path, [])

    def objExists(self, plug):
        path, attr = plug.split(".", 1)
        return path in self.values and attr in self.values[path]

    def getAttr(self, plug, **flags):
        if plug.endswith(".intermediateObject"):
            return False
        if flags.get("lock"):
            return plug in self.locks
        if flags.get("settable"):
            return plug not in self.locks | self.incoming
        path, attr = plug.split(".", 1)
        return self.values[path][attr]

    def listConnections(self, plug, **flags):
        blocked = self.incoming if flags.get("source") else self.outgoing
        return ["|driver.output"] if plug in blocked else []

    def _change(self, path, attrs, values):
        self.writes += 1
        failure = self.writes == self.fail_at
        if failure and not self.mutate_before_error:
            raise RuntimeError("injected second write failure")
        for attr, value in zip(attrs, values):
            self.values[path][attr] = value + (1 if self.writes == self.wrong_at else 0)
        if self.writes == 2 and self.later_changes_prior:
            self.values["|hair"]["translateX"] = 99
        if self.scalar_side_effect:
            self.values[path]["rotateZ"] = 42
        if failure:
            raise RuntimeError("injected write mutated before raising")

    def setAttr(self, plug, value):
        path, attr = plug.split(".", 1)
        self._change(path, [attr], [value])

    def xform(self, path, **flags):
        row = self.values[path]
        keys = {"translation": "translate", "rotation": "rotate", "scale": "scale"}
        if flags.get("matrix"):
            return [row["scaleX"], 0, 0, 0, 0, row["scaleY"], 0, 0,
                    0, 0, row["scaleZ"], 0, row["translateX"], row["translateY"], row["translateZ"], 1]
        for key, prefix in keys.items():
            if flags.get("query") and flags.get(key):
                return [row[prefix + axis] for axis in "XYZ"]
            if key in flags and not flags.get("query"):
                self._change(path, [prefix + axis for axis in "XYZ"], flags[key])

    def undoInfo(self, **flags):
        if flags.get("query"):
            return self.undo_enabled if flags.get("state") else self.undo_name
        if flags.get("openChunk"):
            self.active_chunk = flags["chunkName"]
            self.chunk_snapshot = copy.deepcopy(self.values)
        if flags.get("closeChunk"):
            if self.close_raises:
                raise RuntimeError("injected close failure")
            if self.values != self.chunk_snapshot:
                self.undo_snapshot = self.chunk_snapshot
                self.undo_name = "unrelated" if self.spoil_undo_name else self.active_chunk
            self.active_chunk = ""

    def undo(self):
        self.undo_calls += 1
        if self.undo_raises:
            raise RuntimeError("injected undo failure")
        if not self.undo_ineffective:
            self.values = copy.deepcopy(self.undo_snapshot)


@pytest.fixture
def setup(monkeypatch):
    maya = Maya()
    def validate(cmds, reference):
        assert cmds is maya and reference == {"path": "capture.json", "sha256": "sidecar-sha"}
        return {"state": {"targets": [
            {"path": p, "uuid": uid, "node_type": "transform", "mesh_paths": [p + "|shape"],
             "pixel_lookup_supported": True, "pixel_lookup_unsupported_reasons": []}
            for p, uid in maya.uuids.items()]}}
    module = types.ModuleType("cgi_pipeline.capabilities.maya.scene_observation")
    module.validate = validate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return maya, module


def call(maya, items=None, kind="transforms", **changes):
    params = {"items": [transform("|hair")] if items is None else items, "space": "world", "max_items": 10,
              "observation": {"path": "capture.json", "sha256": "sidecar-sha"}}
    if kind == "attributes":
        params.pop("space")
    params.update(changes)
    context = ApiContext.from_mapping({"cmds_module": maya, "_api_version": "1.1.0"}, "maya")
    function = scene_core.set_transforms_many if kind == "transforms" else scene_batch.set_attributes_many
    return function(params, context)


def transform(path, value=(1, 2, 3)):
    return {"node": path, "expected_uuid": path[1:] + "-id", "translation": list(value),
            "expected_before": {"translation": [0, 0, 0]}}


def attr(path, name="translateX", value=4, before=0):
    return {"node": path, "expected_uuid": path[1:] + "-id", "attribute": name,
            "value": value, "expected_before": before}


def test_successful_fixed_scope_transform_and_scalar_edits(setup):
    maya, _ = setup
    original_sole = copy.deepcopy(maya.values["|sole"])
    result = call(maya)
    assert result["status"] == "SUCCESS"
    assert result["output"]["items"][0]["after"]["translation"] == [1, 2, 3]
    assert result["output"]["guard"]["changed"] is True
    assert maya.values["|sole"] == original_sole
    result = call(maya, [attr("|hair", before=1)], kind="attributes")
    assert result["status"] == "SUCCESS" and result["output"]["items"][0]["after"] == 4
    assert maya.undo_calls == 0


@pytest.mark.parametrize("fault", ["uuid", "old_value", "short_name", "shape", "duplicate", "over_limit", "empty"])
def test_entire_batch_preflight_has_no_partial_writes(setup, fault):
    maya, _ = setup
    rows = [transform("|hair"), transform("|sole")]
    changes = {}
    if fault == "uuid": rows[1]["expected_uuid"] = "wrong-id"
    if fault == "old_value": rows[1]["expected_before"]["translation"] = [8, 8, 8]
    if fault == "short_name": rows[1]["node"] = "sole"
    if fault == "shape": rows[1]["node"] = "|sole|shape"
    if fault == "duplicate": rows[1] = transform("|hair")
    if fault == "over_limit": changes["max_items"] = 1
    if fault == "empty": rows = []
    result = call(maya, rows, **changes)
    assert result["status"] == "ERROR"
    assert result["output"]["guard"]["preflight_complete"] is False
    assert maya.writes == maya.undo_calls == 0


def test_stale_observation_prevents_even_the_first_write(setup):
    maya, module = setup
    def stale(*args):
        raise ValueError("PNG/geometry observation is stale")
    module.validate = stale
    result = call(maya)
    assert result["status"] == "ERROR" and "stale" in result["error"]
    assert maya.writes == maya.undo_calls == 0


@pytest.mark.parametrize("fault", ["lock", "incoming", "outgoing", "shape_consumer", "undo_off", "transform_instance", "descendant", "nonmesh"])
def test_rig_or_implicit_impact_is_refused(setup, fault):
    maya, _ = setup
    if fault == "lock": maya.locks.add("|sole.translateX")
    if fault == "incoming": maya.incoming.add("|sole.translateX")
    if fault == "outgoing": maya.outgoing.add("|sole")
    if fault == "shape_consumer": maya.outgoing.add("|sole|shape.worldMesh")
    if fault == "undo_off": maya.undo_enabled = False
    if fault == "transform_instance":
        maya.uuids["|anotherPath"] = "sole-id"
        maya.node_types["|anotherPath"] = "transform"
    if fault in ("descendant", "nonmesh"):
        maya.children["|sole"].append("|sole|other")
        maya.node_types["|sole|other"] = "transform" if fault == "descendant" else "nurbsCurve"
    result = call(maya, [transform("|hair"), transform("|sole")])
    assert result["status"] == "ERROR" and maya.writes == maya.undo_calls == 0


@pytest.mark.parametrize("mutate", [False, True])
def test_second_item_failure_rolls_back_actual_originals(setup, mutate):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.fail_at = 2; maya.mutate_before_error = mutate
    result = call(maya, [transform("|hair"), transform("|sole")])
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR" and "injected" in result["error"]
    assert guard["rollback_attempted"] and guard["rolled_back"]
    assert guard["writes_attempted"] == 2 and guard["writes_completed"] == 1
    assert maya.values == original and maya.undo_calls == 1


def test_first_write_failure_does_not_undo_previous_user_action(setup):
    maya, _ = setup
    maya.fail_at = 1; maya.undo_name = "previous_user_action"
    result = call(maya)
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR" and guard["changed"] is False
    assert not guard["rollback_attempted"] and maya.undo_calls == 0
    assert maya.undo_name == "previous_user_action"


def test_scalar_second_write_rolls_back_multiple_channels_on_one_target(setup):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.fail_at = 2
    rows = [attr("|hair"), attr("|hair", name="scaleZ", value=2, before=1)]
    result = call(maya, rows, kind="attributes")
    assert result["status"] == "ERROR" and result["output"]["guard"]["rolled_back"]
    assert maya.values == original and maya.undo_calls == 1


def test_first_write_mutation_then_raise_is_detected_and_restored(setup):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.fail_at = 1; maya.mutate_before_error = True
    result = call(maya)
    assert result["output"]["guard"]["rolled_back"] and maya.values == original


def test_mismatched_readback_is_error_and_not_a_successful_receipt(setup):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.wrong_at = 1
    result = call(maya)
    assert result["status"] == "ERROR" and "readback" in result["error"]
    assert result["output"]["items"][0]["readback_matches"] is False
    assert result["output"]["guard"]["rolled_back"] and maya.values == original


def test_final_readback_detects_a_later_item_altering_an_earlier_item(setup):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.later_changes_prior = True
    result = call(maya, [transform("|hair"), transform("|sole")])
    assert result["status"] == "ERROR" and "readback" in result["error"]
    assert result["output"]["guard"]["rolled_back"] and maya.values == original


def test_scalar_side_effect_on_unrequested_trs_is_not_hidden(setup):
    maya, _ = setup
    original = copy.deepcopy(maya.values)
    maya.scalar_side_effect = True
    result = call(maya, [attr("|hair")], kind="attributes")
    assert result["status"] == "ERROR" and "Other local TRS" in result["error"]
    assert result["output"]["guard"]["rolled_back"] and maya.values == original


@pytest.mark.parametrize("fault", ["undo_error", "undo_no_effect", "wrong_queue_top", "close_failure"])
def test_actual_failed_rollback_is_reported_separately(setup, fault):
    maya, _ = setup
    maya.fail_at = 2
    maya.undo_raises = fault == "undo_error"
    maya.undo_ineffective = fault == "undo_no_effect"
    maya.spoil_undo_name = fault == "wrong_queue_top"
    maya.close_raises = fault == "close_failure"
    result = call(maya, [transform("|hair"), transform("|sole")])
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR" and "injected second" in result["error"]
    assert not guard["rolled_back"] and guard["recovery_errors"]
    assert maya.values["|hair"]["translateX"] == 1
    if fault in ("wrong_queue_top", "close_failure"):
        assert not guard["rollback_attempted"] and maya.undo_calls == 0


@pytest.mark.parametrize("fault", ["bad_attr", "explicit_type", "duplicate", "missing_expected", "nan", "bool"])
def test_scalar_guard_does_not_admit_arbitrary_dg_or_invalid_values(setup, fault):
    maya, _ = setup
    item = attr("|hair")
    rows = [item]
    if fault == "bad_attr": item["attribute"] = "visibility"
    if fault == "explicit_type": item["type"] = "double"
    if fault == "duplicate": rows.append(dict(item))
    if fault == "missing_expected": item.pop("expected_before")
    if fault == "nan": item["value"] = float("nan")
    if fault == "bool": item["value"] = True
    result = call(maya, rows, kind="attributes")
    assert result["status"] == "ERROR" and maya.writes == maya.undo_calls == 0


def test_main_thread_required_before_maya_calls(setup):
    maya, _ = setup
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(call, maya).result()
    assert result["status"] == "ERROR" and "main Python thread" in result["error"]
    assert maya.writes == maya.undo_calls == 0


@pytest.mark.parametrize("before,after", [(100000000.0, 100000001.0), (0.0, 1e-7)])
def test_every_actual_snapshot_change_requires_owned_rollback(setup, before, after):
    maya, _ = setup
    maya.values["|hair"]["translateX"] = before
    original = copy.deepcopy(maya.values)
    item = transform("|hair", value=(after, 0, 0))
    item["expected_before"]["translation"] = [before, 0, 0]
    maya.fail_at = 2
    result = call(maya, [item, transform("|sole")])
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR"
    assert guard["changed"] is True and guard["rollback_attempted"]
    assert guard["rolled_back"] and maya.undo_calls == 1
    assert maya.values == original


@pytest.mark.parametrize("kind", ["transforms", "attributes"])
def test_large_coordinate_one_unit_old_value_drift_is_rejected(setup, kind):
    maya, _ = setup
    maya.values["|hair"]["translateX"] = 100000000.0
    if kind == "transforms":
        item = transform("|hair", value=(100000002.0, 0, 0))
        item["expected_before"]["translation"] = [100000001.0, 0, 0]
    else:
        item = attr("|hair", value=100000002.0, before=100000001.0)
    result = call(maya, [item], kind=kind)
    assert result["status"] == "ERROR" and "Expected old" in result["error"]
    assert not result["output"]["guard"]["preflight_complete"]
    assert maya.writes == maya.undo_calls == 0
    assert maya.values["|hair"]["translateX"] == 100000000.0


def test_large_coordinate_scalar_wrong_readback_is_not_a_success(setup):
    maya, _ = setup
    maya.values["|hair"]["translateX"] = 100000000.0
    original = copy.deepcopy(maya.values)
    maya.wrong_at = 1
    result = call(maya, [attr("|hair", value=100000001.0, before=100000000.0)], kind="attributes")
    assert result["status"] == "ERROR" and "readback" in result["error"]
    assert result["output"]["items"][0]["readback_matches"] is False
    assert result["output"]["guard"]["rolled_back"] and maya.undo_calls == 1
    assert maya.values == original


def test_large_coordinate_residual_after_undo_is_not_verified_restored(setup):
    maya, _ = setup
    maya.values["|hair"]["translateX"] = 100000000.0
    item = transform("|hair", value=(100000001.0, 0, 0))
    item["expected_before"]["translation"] = [100000000.0, 0, 0]
    maya.fail_at = 2; maya.undo_ineffective = True
    result = call(maya, [item, transform("|sole")])
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR" and guard["changed"] is True
    assert guard["rollback_attempted"] and not guard["rolled_back"]
    assert guard["recovery_errors"] and maya.undo_calls == 1
    assert maya.values["|hair"]["translateX"] == 100000001.0


def test_tiny_successful_edit_reports_actual_change_and_fixed_tolerance(setup):
    maya, _ = setup
    result = call(maya, [transform("|hair", value=(1e-7, 0, 0))])
    guard = result["output"]["guard"]
    assert result["status"] == "SUCCESS" and guard["changed"] is True
    assert guard["value_tolerance"]["absolute"] == 1e-6
    assert guard["value_tolerance"]["relative"] == 0
    assert maya.values["|hair"]["translateX"] == 1e-7


def test_tiny_residual_after_ineffective_undo_is_not_exactly_restored(setup):
    maya, _ = setup
    item = transform("|hair", value=(1e-7, 0, 0))
    maya.fail_at = 2; maya.undo_ineffective = True
    result = call(maya, [item, transform("|sole")])
    guard = result["output"]["guard"]
    assert result["status"] == "ERROR" and guard["changed"] is True
    assert guard["rollback_attempted"] and maya.undo_calls == 1
    assert not guard["rolled_back"] and guard["recovery_errors"]
    assert guard["rollback_after"]["|hair"]["channels"]["translateX"] == 1e-7
    assert maya.values["|hair"]["translateX"] == 1e-7


def _pixel_support(module, path, value):
    original = module.validate
    def validate(cmds, reference):
        record = original(cmds, reference)
        target = next(row for row in record["state"]["targets"] if row["path"] == path)
        if value is None:
            target.pop("pixel_lookup_supported")
        else:
            target["pixel_lookup_supported"] = value
        target["pixel_lookup_unsupported_reasons"] = ["Transparent appearance cannot certify pixel lookup"]
        return record
    module.validate = validate


@pytest.mark.parametrize("support", [False, None, 1])
@pytest.mark.parametrize("kind", ["transforms", "attributes"])
def test_second_unsupported_or_uncertified_target_rejects_entire_batch(setup, support, kind):
    maya, module = setup
    original = copy.deepcopy(maya.values)
    _pixel_support(module, "|sole", support)
    factory = transform if kind == "transforms" else attr
    result = call(maya, [factory("|hair"), factory("|sole")], kind=kind)
    assert result["status"] == "ERROR" and "pixel" in result["error"].lower()
    assert not result["output"]["guard"]["preflight_complete"]
    assert maya.writes == maya.undo_calls == 0 and maya.values == original


@pytest.mark.parametrize("kind", ["transforms", "attributes"])
def test_nontarget_transparent_mesh_does_not_block_supported_edit_target(setup, kind):
    maya, module = setup
    original_sole = copy.deepcopy(maya.values["|sole"])
    _pixel_support(module, "|sole", False)
    factory = transform if kind == "transforms" else attr
    result = call(maya, [factory("|hair")], kind=kind)
    assert result["status"] == "SUCCESS" and result["output"]["guard"]["preflight_complete"]
    assert maya.writes == 1 and maya.undo_calls == 0
    assert maya.values["|sole"] == original_sole
