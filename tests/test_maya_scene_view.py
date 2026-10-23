"""Exercise explicit camera ownership, failure recovery and capsule CAS.

The fake verifies command boundaries/state transitions, not Maya viewFit geometry.
Native projection/framing acceptance remains a separate real-GUI check.
"""

import copy
import importlib
import json
import sys
import types

import pytest

from cgi_pipeline.capabilities.maya import scene_view


CAMERA = "|camera|cameraShape"
TARGET = "|root|head"
IDENTITY = {"schema_version": 1, "pid": 12, "process_created_at": 1234.125,
            "session_nonce": "session-a", "scene_epoch": 0, "maya_api_version": 20250000}


class FakeScene:
    def __init__(self):
        self.nodes = {"|camera": "transform", CAMERA: "camera", "|front": "transform",
                      "|front|frontShape": "camera", "|root": "transform", TARGET: "transform"}
        self.panels = {"modelPanel4": CAMERA, "modelPanel1": "|front|frontShape"}
        self.attrs, self.selection, self.time = {}, ["|root"], 1.0
        self.locks, self.incoming, self.references = set(), set(), set()
        self.fit_error, self.restore_fail_plug, self.instanced = False, None, False
        self.calls, self.writes, self.undo_depth = [], [], 0
        self.uuid_overrides, self.undo_calls = {}, []
        self.identity = copy.deepcopy(IDENTITY)
        for camera in (CAMERA, "|front|frontShape"):
            transform = camera.rsplit("|", 1)[0]
            self.attrs.update({transform + "." + field: 1.0 if field.startswith("scale") else 0.0
                               for field in scene_view.TRANSFORM_FIELDS})
            self.attrs.update({camera + "." + field: 1.0 for field in scene_view.SHAPE_FIELDS})
            self.attrs.update({camera + ".orthographic": True, camera + ".orthographicWidth": 28.0,
                               camera + ".centerOfInterest": 30.0, camera + ".focalLength": 35.0})

    def ls(self, value=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if kwargs.get("uuid"):
            return [self.uuid_overrides.get(value, "uuid:" + value)] if value in self.nodes else []
        return [path for path in self.nodes if value == path or value == path.rsplit("|", 1)[-1]]

    def getPanel(self, **kwargs):
        return list(self.panels) if kwargs.get("type") else (
            "modelPanel" if kwargs.get("typeOf") in self.panels else "")

    def modelEditor(self, panel, **kwargs):
        assert kwargs.get("query") and kwargs.get("camera"), "product must not rebind any panel"
        return self.panels[panel]

    def nodeType(self, node):
        return self.nodes[node]

    def listRelatives(self, node, **kwargs):
        if kwargs.get("shapes"):
            return [p for p, kind in self.nodes.items() if p.rsplit("|", 1)[0] == node and kind == "camera"]
        parent = node.rsplit("|", 1)[0]
        return [parent, "|otherCamera"] if self.instanced and node == CAMERA else [parent]

    def referenceQuery(self, node, **kwargs):
        return node in self.references

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"):
            return plug in self.locks
        if kwargs.get("settable"):
            return plug not in self.locks and plug not in self.incoming
        return self.attrs[plug]

    def listConnections(self, plug, **kwargs):
        return ["driver.output"] if plug in self.incoming else []

    def xform(self, node, **kwargs):
        assert kwargs == {"query": True, "worldSpace": True, "matrix": True}
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                self.attrs[node + ".translateX"], self.attrs[node + ".translateY"],
                self.attrs[node + ".translateZ"], 1]

    def currentTime(self, value=None, **kwargs):
        if kwargs.get("query"):
            return self.time
        self.writes.append(("time", value))
        self.time = value

    def select(self, items=None, **kwargs):
        self.writes.append(("select", list(items or []), kwargs))
        if kwargs.get("clear"):
            self.selection = []
        elif kwargs.get("add"):
            self.selection.extend(p for p in items if p not in self.selection)
        elif kwargs.get("deselect"):
            self.selection = [p for p in self.selection if p not in items]
        else:
            self.selection = list(items or [])

    def objectType(self, node, **kwargs):
        return node in self.nodes and kwargs.get("isAType") == "dagNode"

    def exactWorldBoundingBox(self, *paths):
        return [-2.0, -1.0, -3.0, 4.0, 5.0, 6.0]

    def viewFit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        for axis, value in zip("XYZ", (7.0, 8.0, 9.0)):
            self.attrs["|camera.translate" + axis] = value
        self.attrs[CAMERA + ".orthographicWidth"] = 5.0
        self.attrs[CAMERA + ".centerOfInterest"] = 10.0
        if self.fit_error:
            raise RuntimeError("native viewFit failed after partial camera changes")

    def setAttr(self, plug, value):
        self.writes.append(("setAttr", plug, value))
        if plug == self.restore_fail_plug:
            raise RuntimeError("injected restoration write failure")
        self.attrs[plug] = value

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        self.undo_depth += 1 if kwargs.get("openChunk") else -1
        assert self.undo_depth >= 0


@pytest.fixture
def runtime(monkeypatch):
    fake = FakeScene()
    maya = types.ModuleType("maya")
    maya.cmds = fake
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", fake)
    module = importlib.import_module("cgi_pipeline.capabilities.maya.asset.maya_select_objects.maya_select_objects")
    monkeypatch.setattr(module, "cmds", fake)
    monkeypatch.setattr(scene_view, "_session", lambda cmds: copy.deepcopy(cmds.identity))
    return fake, module


def frame(module, **overrides):
    params = {"objects": [TARGET], "panel": "modelPanel4", "frame": True}
    params.update(overrides)
    return module.execute({"parameters": params})


def restore(module, capsule):
    return module.execute({"parameters": {"panel": "modelPanel4", "restore_view": capsule}})


def test_explicit_frame_uses_real_panel_camera_and_capsule_restores_changed_shape_fields(runtime):
    fake, module = runtime
    before = scene_view._snapshot(fake, "modelPanel4")
    bindings = dict(fake.panels)
    receipt = frame(module)
    assert receipt["status"] == "SUCCESS"
    assert fake.calls == [((CAMERA, TARGET), {"animate": False})]
    capsule = json.loads(json.dumps(receipt["output"]["view_restore"], sort_keys=True))
    assert {c["attribute"] for c in capsule["changed_fields"]} >= {"orthographicWidth", "centerOfInterest"}
    assert receipt["output"]["world_bbox"] == [-2, -1, -3, 4, 5, 6]
    restored = restore(module, capsule)
    assert restored["status"] == "SUCCESS" and restored["output"]["restored"]
    assert not restored["output"]["restore_errors"] and not restored["output"]["restore_differences"]
    assert scene_view._snapshot(fake, "modelPanel4") == before
    assert fake.panels == bindings and fake.undo_depth == 0


def test_restore_capsule_survives_module_reload_without_global_store(runtime, monkeypatch):
    fake, module = runtime
    capsule = json.loads(json.dumps(frame(module)["output"]["view_restore"]))
    importlib.reload(scene_view)
    monkeypatch.setattr(scene_view, "_session", lambda cmds: copy.deepcopy(cmds.identity))
    assert restore(module, capsule)["status"] == "SUCCESS"


@pytest.mark.parametrize("preset", [0.1, 1.0, 10.0])
def test_near_clip_presets_read_back_and_restore_with_frame(runtime, preset):
    fake, module = runtime
    fake.attrs[CAMERA + ".farClipPlane"] = 10000.0
    fake.attrs[CAMERA + ".nearClipPlane"] = 0.25
    before = scene_view._snapshot(fake, "modelPanel4")
    receipt = frame(module, near_clip_plane=preset)
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["clip_planes"]["near"] == preset
    assert receipt["output"]["clip_planes"]["far"] == 10000.0
    capsule = receipt["output"]["view_restore"]
    assert any(c["attribute"] == "nearClipPlane" for c in capsule["changed_fields"])
    assert restore(module, capsule)["status"] == "SUCCESS"
    assert scene_view._snapshot(fake, "modelPanel4") == before
    assert fake.undo_depth == 0


@pytest.mark.parametrize("preset", [0, -1, 2, True, "1", float("nan"), float("inf")])
def test_invalid_clip_preset_refused_before_writes(runtime, preset):
    fake, module = runtime
    assert frame(module, near_clip_plane=preset)["status"] == "ERROR"
    assert not fake.calls and not fake.writes


@pytest.mark.parametrize("value", [0.1, 1, 1.0, 10, 10.0])
def test_public_manifest_accepts_json_integer_and_float_clip_presets(value):
    from cgi_pipeline.catalog import get_capability
    from cgi_pipeline.contracts import validate_params
    spec = get_capability("maya.asset.maya_select_objects")
    assert validate_params(spec, {"near_clip_plane": value})["near_clip_plane"] == value


@pytest.mark.parametrize("value", [True, "1", 2, 0])
def test_public_manifest_rejects_nonpreset_clip_values(value):
    from cgi_pipeline.catalog import get_capability
    from cgi_pipeline.contracts import ApiContractError, validate_params
    with pytest.raises(ApiContractError):
        validate_params(get_capability("maya.asset.maya_select_objects"), {"near_clip_plane": value})


def test_clip_preset_requires_panel_frame_and_valid_far_before_writes(runtime):
    fake, module = runtime
    assert frame(module, near_clip_plane=10)["status"] == "ERROR"
    assert frame(module, near_clip_plane=0.1, panel="")["status"] == "ERROR"
    assert frame(module, near_clip_plane=0.1, frame=False)["status"] == "ERROR"
    assert not fake.calls and not fake.writes


def test_clip_change_cas_refuses_user_change_and_cannot_combine_with_restore(runtime):
    fake, module = runtime
    receipt = frame(module, near_clip_plane=0.1)
    capsule = receipt["output"]["view_restore"]
    count = len(fake.writes)
    assert module.execute({"parameters": {"panel": "modelPanel4", "restore_view": capsule,
                                         "near_clip_plane": 0.1}})["status"] == "ERROR"
    fake.attrs[CAMERA + ".nearClipPlane"] = 0.2
    assert restore(module, capsule)["error_code"] == "VIEW_RESTORE_CONFLICT"
    assert len(fake.writes) == count


def test_clip_write_readback_failure_recovers_camera_and_selection(runtime, monkeypatch):
    fake, module = runtime
    before = scene_view._snapshot(fake, "modelPanel4")
    original = fake.setAttr
    def bad_write(plug, value):
        original(plug, 0.2 if plug == CAMERA + ".nearClipPlane" and value == 0.1 else value)
    monkeypatch.setattr(fake, "setAttr", bad_write)
    result = frame(module, near_clip_plane=0.1)
    assert result["error_code"] == "VIEW_FRAME_FAILED"
    assert result["output"]["restored"]
    assert scene_view._snapshot(fake, "modelPanel4") == before


@pytest.mark.parametrize("mutation", ["transform", "shape", "selection", "time", "epoch", "pid", "binding"])
def test_restore_cas_refuses_later_user_changes_without_any_write(runtime, mutation):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    if mutation == "transform": fake.attrs["|camera.translateX"] += 1
    elif mutation == "shape": fake.attrs[CAMERA + ".orthographicWidth"] += 1
    elif mutation == "selection": fake.selection = ["|root"]
    elif mutation == "time": fake.time += 1
    elif mutation == "epoch": fake.identity["scene_epoch"] += 1
    elif mutation == "pid": fake.identity["pid"] += 1
    else: fake.panels["modelPanel4"] = "|front|frontShape"
    count = len(fake.writes)
    result = restore(module, capsule)
    assert result["status"] == "ERROR"
    assert len(fake.writes) == count


@pytest.mark.parametrize("condition", ["lock", "connection", "shared", "reference", "instance"])
def test_frame_camera_preflight_refuses_unsafe_camera_before_selection(runtime, condition):
    fake, module = runtime
    if condition == "lock": fake.locks.add("|camera.translateX")
    elif condition == "connection": fake.incoming.add("|camera.translateX")
    elif condition == "shared": fake.panels["modelPanel1"] = CAMERA
    elif condition == "reference": fake.references.add("|camera")
    else: fake.instanced = True
    receipt = frame(module)
    assert receipt["status"] == "ERROR"
    assert not fake.calls and not fake.writes


@pytest.mark.parametrize("overrides", [
    {"objects": ["head"]}, {"objects": ["|root|*"]}, {"objects": [TARGET + ".vtx[0]"]},
    {"objects": []}, {"objects": [TARGET, "|root"], "max_items": 1},
    {"mode": "clear"}, {"frame": False}, {"panel": "missing"},
])
def test_explicit_frame_refuses_inexact_or_truncated_targets_without_mutation(runtime, overrides):
    fake, module = runtime
    assert frame(module, **overrides)["status"] == "ERROR"
    assert not fake.calls and not fake.writes


def test_frame_fault_restores_partial_native_camera_changes_and_original_selection(runtime):
    fake, module = runtime
    before = scene_view._snapshot(fake, "modelPanel4")
    fake.fit_error = True
    receipt = frame(module)
    assert receipt["status"] == "ERROR" and receipt["error_code"] == "VIEW_FRAME_FAILED"
    output = receipt["output"]
    assert "native viewFit failed" in output["operation_error"]["error"]
    assert output["restored"] and not output["restore_errors"] and not output["restore_differences"]
    assert scene_view._snapshot(fake, "modelPanel4") == before
    assert fake.undo_depth == 0


def test_frame_fault_preserves_original_and_restoration_errors_with_readback(runtime):
    fake, module = runtime
    fake.fit_error = True
    fake.restore_fail_plug = CAMERA + ".centerOfInterest"
    receipt = frame(module)
    assert receipt["status"] == "ERROR"
    output = receipt["output"]
    assert "native viewFit failed" in output["operation_error"]["error"]
    assert not output["restored"] and output["restore_differences"]
    assert output["restore_errors"][0]["plug"] == fake.restore_fail_plug
    assert "injected restoration" in output["restore_errors"][0]["error"]


def test_explicit_restore_failure_keeps_capsule_errors_and_partial_readback(runtime):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    fake.restore_fail_plug = CAMERA + ".centerOfInterest"
    receipt = restore(module, capsule)
    assert receipt["error_code"] == "VIEW_RESTORE_FAILED"
    assert receipt["output"]["view_restore"] == capsule
    assert receipt["output"]["restore_errors"] and receipt["output"]["readback"]


def test_restore_rejects_nan_capsule_before_any_write(runtime):
    fake, module = runtime
    capsule = copy.deepcopy(frame(module)["output"]["view_restore"])
    capsule["before"]["shape_fields"]["orthographicWidth"] = float("nan")
    capsule["changed_fields"] = scene_view._changes(capsule["before"], capsule["expected_after"])
    count = len(fake.writes)
    assert restore(module, capsule)["status"] == "ERROR"
    assert len(fake.writes) == count


def test_restore_checks_session_birth_exactly_not_camera_float_tolerance(runtime):
    fake, module = runtime
    fake.identity["process_created_at"] = 1792568124.7482471
    capsule = frame(module)["output"]["view_restore"]
    fake.identity["process_created_at"] += 0.1
    count = len(fake.writes)
    assert restore(module, capsule)["status"] == "ERROR"
    assert len(fake.writes) == count


def test_legacy_default_schema_still_uses_original_path_without_session_import(runtime, monkeypatch):
    fake, module = runtime
    monkeypatch.setattr(scene_view, "_session", lambda cmds: pytest.fail("legacy routed to new helper"))
    receipt = module.execute({"parameters": {"objects": [TARGET], "panel": "", "restore_view": {}, "frame": True}})
    assert receipt["status"] == "SUCCESS"
    assert fake.calls == [(([TARGET],), {"all": False})]


def test_add_mode_frames_explicit_targets_instead_of_the_combined_selection(runtime):
    fake, module = runtime
    assert frame(module, mode="add")["status"] == "SUCCESS"
    assert fake.selection == ["|root", TARGET]
    assert fake.calls == [((CAMERA, TARGET), {"animate": False})]


def test_restore_cannot_be_combined_with_objects_or_frame(runtime):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    count = len(fake.writes)
    receipt = module.execute({"parameters": {"restore_view": capsule, "panel": "modelPanel4", "objects": [TARGET]}})
    assert receipt["status"] == "ERROR" and len(fake.writes) == count


def test_restore_readback_error_preserves_capsule_and_restoration_error(runtime, monkeypatch):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    original, calls = scene_view._snapshot, []

    def fail_readback(cmds, panel):
        calls.append(panel)
        if len(calls) == 2:
            raise RuntimeError("injected post-restore snapshot failure")
        return original(cmds, panel)

    monkeypatch.setattr(scene_view, "_snapshot", fail_readback)
    receipt = restore(module, capsule)
    assert receipt["error_code"] == "VIEW_RESTORE_FAILED"
    assert receipt["output"]["view_restore"] == capsule
    assert receipt["output"]["readback"] is None
    assert receipt["output"]["restore_errors"][-1]["operation"] == "restore_readback"


def test_frame_fault_does_not_restore_into_a_new_scene_epoch(runtime, monkeypatch):
    fake, module = runtime
    original = fake.viewFit

    def change_scene(*args, **kwargs):
        original(*args, **kwargs)
        fake.identity["scene_epoch"] += 1
        raise RuntimeError("injected scene replacement during fit")

    monkeypatch.setattr(fake, "viewFit", change_scene)
    receipt = frame(module)
    assert receipt["status"] == "ERROR" and not receipt["output"]["restored"]
    assert "injected scene replacement" in receipt["output"]["operation_error"]["error"]
    assert "相机绑定变化" in receipt["output"]["restore_errors"][0]["error"]
    assert not any(row[0] == "setAttr" for row in fake.writes)


@pytest.mark.parametrize("delta,expected", [(1e-9, "SUCCESS"), (1e-4, "ERROR")])
def test_restore_documents_bounded_camera_float_tolerance(runtime, delta, expected):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    fake.attrs[CAMERA + ".orthographicWidth"] += delta
    assert restore(module, capsule)["status"] == expected


def test_explicit_nodes_alias_with_default_empty_objects_still_resolves_exact_target(runtime):
    fake, module = runtime
    receipt = frame(module, objects=[], nodes=[TARGET])
    assert receipt["status"] == "SUCCESS"
    assert fake.calls == [((CAMERA, TARGET), {"animate": False})]


def test_restore_checks_time_exactly_even_at_large_frame_numbers(runtime):
    fake, module = runtime
    fake.time = 1000000000.0
    capsule = frame(module)["output"]["view_restore"]
    fake.time += 0.001
    count = len(fake.writes)
    assert restore(module, capsule)["status"] == "ERROR"
    assert len(fake.writes) == count


@pytest.mark.parametrize("operation", ["frame", "restore"])
def test_explicit_entry_rejects_worker_thread_before_any_maya_or_session_access(runtime, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor

    _, module = runtime
    capsule = frame(module)["output"]["view_restore"] if operation == "restore" else None
    accesses, session_reads = [], []

    class NoMayaTouch:
        def __getattr__(self, name):
            accesses.append(name)
            raise RuntimeError("Maya access occurred before main-thread refusal: " + name)

    def tracked_session(cmds):
        session_reads.append(True)
        return copy.deepcopy(IDENTITY)

    monkeypatch.setattr(module, "cmds", NoMayaTouch())
    monkeypatch.setattr(scene_view, "_session", tracked_session)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(frame, module) if operation == "frame" else pool.submit(restore, module, capsule)
        receipt = future.result()
    assert receipt["status"] == "ERROR"
    assert accesses == [], {"maya_accesses": accesses, "session_reads": session_reads}
    assert session_reads == []
    assert "主线程" in receipt["error"]


@pytest.mark.parametrize("change", ["before_replaced", "before_missing", "after_replaced"])
def test_selection_identity_conflict_refuses_restore_before_camera_or_selection_write(runtime, change):
    fake, module = runtime
    capsule = frame(module)["output"]["view_restore"]
    if change == "before_missing": fake.nodes.pop("|root")
    elif change == "before_replaced": fake.uuid_overrides["|root"] = "replacement-root-uuid"
    else: fake.uuid_overrides[TARGET] = "replacement-selected-target-uuid"
    writes, undo_calls = len(fake.writes), len(fake.undo_calls)
    receipt = restore(module, capsule)
    assert receipt["status"] == "ERROR" and receipt["error_code"] == "VIEW_RESTORE_CONFLICT"
    assert len(fake.writes) == writes and len(fake.undo_calls) == undo_calls
    assert fake.selection == [TARGET] and fake.attrs["|camera.translateX"] == 7.0


@pytest.mark.parametrize("selection", [[TARGET + ".vtx[0]"], [TARGET + ".translateX"], ["lambert1"]])
def test_explicit_frame_rejects_unsupported_initial_selection_without_clearing(runtime, selection):
    fake, module = runtime
    fake.selection = selection
    receipt = frame(module)
    assert receipt["status"] == "ERROR"
    assert fake.selection == selection and not fake.writes and not fake.calls and not fake.undo_calls


def test_capsule_pins_before_and_after_selection_objects_and_rejects_old_format(runtime):
    fake, module = runtime
    capsule = json.loads(json.dumps(frame(module)["output"]["view_restore"], sort_keys=True))
    assert capsule["schema_version"] == 2
    assert capsule["before"]["selection_identities"] == [{"path": "|root", "uuid": "uuid:|root"}]
    assert capsule["expected_after"]["selection_identities"] == [{"path": TARGET, "uuid": "uuid:" + TARGET}]
    old = copy.deepcopy(capsule)
    old["schema_version"] = 1
    for key in ("before", "expected_after"): old[key].pop("selection_identities")
    writes, undo_calls = len(fake.writes), len(fake.undo_calls)
    assert restore(module, old)["status"] == "ERROR"
    assert len(fake.writes) == writes and len(fake.undo_calls) == undo_calls


def test_frame_failure_does_not_restore_before_selection_replacement(runtime, monkeypatch):
    fake, module = runtime
    original = fake.viewFit

    def replace_before_selection(*args, **kwargs):
        original(*args, **kwargs)
        fake.uuid_overrides["|root"] = "replacement-root-uuid"
        raise RuntimeError("injected fit error after original selection replacement")

    monkeypatch.setattr(fake, "viewFit", replace_before_selection)
    receipt = frame(module)
    assert receipt["status"] == "ERROR" and not receipt["output"]["restored"]
    assert "injected fit error" in receipt["output"]["operation_error"]["error"]
    assert "选择" in receipt["output"]["restore_errors"][0]["error"]
    assert not any(row[0] == "setAttr" for row in fake.writes) and fake.selection == [TARGET]
