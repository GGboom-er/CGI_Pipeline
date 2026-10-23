"""Observation-bound, preflighted leaf-mesh transform placement edits.

The legacy setters stay unrestricted. This optional branch deliberately excludes
rig channels, arbitrary DG plugs, descendant transforms and driven consumers.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from numbers import Real

from cgi_pipeline.contracts import make_api_receipt

_CHANNELS = {"translation": "translate", "rotation": "rotate", "scale": "scale"}
_ATTRS = {prefix + axis for prefix in _CHANNELS.values() for axis in "XYZ"}
_ABS_TOL = 1e-6


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError("TRS values must be finite numbers, excluding booleans")
    return float(value)


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("TRS vectors must have exactly three finite numbers")
    return [_number(x) for x in value]


def _equal(left, right):
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, Real) and isinstance(right, Real):
        return math.isclose(left, right, rel_tol=0.0, abs_tol=_ABS_TOL)
    return left == right


def _snapshot(cmds, path):
    return {
        "channels": {a: _number(cmds.getAttr(f"{path}.{a}")) for a in sorted(_ATTRS)},
        "matrix": [_number(x) for x in cmds.xform(path, query=True, objectSpace=True, matrix=True)],
    }


def _read_transform(cmds, path, space):
    flags = {"query": True, "worldSpace": space == "world", "objectSpace": space == "object"}
    return {key: _vector(cmds.xform(path, **flags, **{key: True})) for key in _CHANNELS}


def _targets(cmds, reference):
    from cgi_pipeline.capabilities.maya.scene_observation import validate
    record = validate(cmds, reference)
    targets = record["state"]["targets"]
    result = {row["path"]: row for row in targets}
    if not result or len(result) != len(targets):
        raise ValueError("Observation target identities must be nonempty and unique")
    return result


def _mesh_leaf(cmds, path, target):
    children = cmds.listRelatives(path, children=True, fullPath=True) or []
    meshes = []
    for child in children:
        if cmds.nodeType(child) != "mesh":
            raise ValueError(f"Guard supports only leaf transforms with mesh shapes: {child}")
        if not cmds.getAttr(f"{child}.intermediateObject"):
            meshes.append(child)
        for attr in ("worldMesh", "outMesh"):
            if cmds.listConnections(f"{child}.{attr}", source=False, destination=True, plugs=True):
                raise ValueError(f"Mesh has DG consumers outside placement scope: {child}.{attr}")
    if not meshes or set(meshes) != set(target.get("mesh_paths", [])):
        raise ValueError(f"Captured mesh children do not match leaf transform: {path}")


def _identity(cmds, item, targets):
    path = item.get("node")
    if not isinstance(path, str) or not path.startswith("|") or "." in path:
        raise ValueError("Guard requires an exact full DAG transform path")
    if (cmds.ls(path, long=True) or []) != [path] or cmds.nodeType(path) != "transform":
        raise ValueError(f"Guard requires an existing ordinary transform: {path}")
    actual = cmds.ls(path, uuid=True) or []
    expected = item.get("expected_uuid")
    target = targets.get(path)
    if not expected or actual != [expected] or not target or target.get("uuid") != expected:
        raise ValueError(f"Expected/captured/current UUID does not match: {path}")
    if target.get("node_type", target.get("type")) != "transform":
        raise ValueError(f"Captured target is not a transform: {path}")
    if target.get("pixel_lookup_supported") is not True:
        reasons = target.get("pixel_lookup_unsupported_reasons", [])
        raise ValueError(f"Captured target is not certified for pixel lookup: {path}; reasons={reasons}")
    paths = cmds.ls(expected, long=True, allPaths=True) or []
    if paths != [path]:
        raise ValueError(f"Transform DAG instancing is outside guarded scope: {path}")
    if cmds.listConnections(path, source=False, destination=True, plugs=True):
        raise ValueError(f"Transform has outgoing DG consumers: {path}")
    _mesh_leaf(cmds, path, target)
    return path


def _writable(cmds, path, attrs):
    for attr in attrs:
        plug = f"{path}.{attr}"
        if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, settable=True):
            raise ValueError(f"Channel is absent, locked or not settable: {plug}")
        if cmds.listConnections(plug, source=True, destination=False, plugs=True):
            raise ValueError(f"Channel has an incoming connection: {plug}")


def _transform_row(cmds, item, path, space):
    requested = {k: _vector(item[k]) for k in _CHANNELS if k in item}
    expected = item.get("expected_before")
    if not requested or not isinstance(expected, dict) or set(expected) != set(requested):
        raise ValueError("expected_before must contain exactly the supplied TRS vectors")
    expected = {key: _vector(value) for key, value in expected.items()}
    before = _read_transform(cmds, path, space)
    if any(not _equal(before[k], value) for k, value in expected.items()):
        raise ValueError(f"Expected old transform values changed: {path}")
    attrs = [prefix + axis for key, prefix in _CHANNELS.items() if key in requested for axis in "XYZ"]
    _writable(cmds, path, attrs)
    return {"path": path, "space": space, "before": before, "requested": requested}


def _attribute_row(cmds, item, path):
    attribute = item.get("attribute")
    if attribute not in _ATTRS or item.get("type"):
        raise ValueError("Guarded attributes are numeric translate/rotate/scale X/Y/Z only")
    plug = f"{path}.{attribute}"
    value = _number(item.get("value")); expected = _number(item.get("expected_before"))
    _writable(cmds, path, [attribute])
    before = _number(cmds.getAttr(plug))
    if not _equal(before, expected):
        raise ValueError(f"Expected old attribute value changed: {plug}")
    return {"path": path, "plug": plug, "before": before, "requested": value}


def _preflight(cmds, params, kind):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Guarded Maya edits must run on the Maya main Python thread")
    maximum = params.get("max_items", 200 if kind == "transforms" else 500)
    items = params.get("items")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 2000:
        raise ValueError("max_items must be an integer between 1 and 2000")
    if not isinstance(items, list) or not 1 <= len(items) <= maximum:
        raise ValueError("Guarded edits reject empty or oversized batches; they never truncate")
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Guarded editing requires Maya undo to be enabled")
    space = params.get("space", "world")
    if kind == "transforms" and space not in ("world", "object"):
        raise ValueError("space must be world or object")
    targets = _targets(cmds, params["observation"])
    rows, originals, seen = [], {}, set()
    for item in items:
        path = _identity(cmds, item, targets)
        row = (_transform_row(cmds, item, path, space) if kind == "transforms"
               else _attribute_row(cmds, item, path))
        key = row.get("plug", path)
        if key in seen:
            raise ValueError(f"Duplicate guarded edit target: {key}")
        seen.add(key); rows.append(row)
        if path not in originals:
            originals[path] = _snapshot(cmds, path)
    paths = list(originals)
    if any(a != b and b.startswith(a + "|") for a in paths for b in paths):
        raise ValueError("Parent/child guarded targets can affect each other")
    return rows, originals


def _write(cmds, row, kind):
    if kind == "attributes":
        cmds.setAttr(row["plug"], row["requested"])
    else:
        flags = {"worldSpace": row["space"] == "world", "objectSpace": row["space"] == "object"}
        for key, value in row["requested"].items():
            cmds.xform(row["path"], **flags, **{key: value})
    _readback(cmds, row, kind)


def _readback(cmds, row, kind):
    if kind == "attributes":
        row["after"] = _number(cmds.getAttr(row["plug"]))
        matches = _equal(row["after"], row["requested"])
    else:
        row["after"] = _read_transform(cmds, row["path"], row["space"])
        expected = {**row["before"], **row["requested"]}
        matches = _equal(row["after"], expected)
    row["readback_matches"] = matches
    if not matches:
        raise RuntimeError(f"Native readback differs from requested value: {row.get('plug', row['path'])}")


def _current(cmds, originals):
    return {path: _snapshot(cmds, path) for path in originals}


def _scalar_channels(cmds, rows, originals):
    expected = {path: dict(state["channels"]) for path, state in originals.items()}
    for row in rows:
        expected[row["path"]][row["plug"].split(".", 1)[1]] = row["requested"]
    for path, channels in expected.items():
        actual = {a: _number(cmds.getAttr(f"{path}.{a}")) for a in sorted(_ATTRS)}
        if not _equal(actual, channels):
            raise RuntimeError(f"Other local TRS channels changed during scalar editing: {path}")


def _rollback(cmds, originals, guard):
    try:
        current = _current(cmds, originals)
        guard["changed"] = current != originals
        guard["rollback_before"] = current
    except Exception as exc:
        guard["recovery_errors"].append(f"Inspect failed edits: {exc}")
    if guard["changed"] is False:
        return
    if not guard["chunk_closed"]:
        guard["recovery_errors"].append("Owned undo chunk did not close; automatic undo was not attempted")
        return
    try:
        if cmds.undoInfo(query=True, undoName=True) != guard["chunk_name"]:
            raise RuntimeError("Undo queue top is not this operation's chunk; refusing unrelated undo")
        guard["rollback_attempted"] = True
        cmds.undo()
        restored = _current(cmds, originals)
        guard["rollback_after"] = restored
        guard["rolled_back"] = restored == originals
        if not guard["rolled_back"]:
            raise RuntimeError("Undo returned but original channel/matrix values were not restored")
    except Exception as exc:
        guard["recovery_errors"].append(str(exc))


def _apply(cmds, rows, originals, kind, guard):
    error = None
    try:
        cmds.undoInfo(openChunk=True, chunkName=guard["chunk_name"])
        guard["chunk_opened"] = True
        for row in rows:
            guard["writes_attempted"] += 1
            _write(cmds, row, kind)
            guard["writes_completed"] += 1
        for row in rows:
            _readback(cmds, row, kind)
        if kind == "attributes":
            _scalar_channels(cmds, rows, originals)
    except Exception as exc:
        error = exc
    finally:
        if guard["chunk_opened"]:
            try:
                cmds.undoInfo(closeChunk=True)
                guard["chunk_closed"] = True
            except Exception as exc:
                guard["recovery_errors"].append(f"Close owned undo chunk: {exc}")
                error = error or exc
    if error is None:
        try:
            guard["changed"] = _current(cmds, originals) != originals
        except Exception as exc:
            error = exc
    if error is not None:
        _rollback(cmds, originals, guard)
        raise error


def execute(params, context, kind):
    """Preserve failure evidence in an ordinary public API receipt."""
    started = time.perf_counter()
    fallback = f"maya.scene.{kind}.set_many"
    api_id = str(context.extras.get("_api_id") or fallback)
    version = str(context.extras.get("_api_version") or "1.1.0")
    guard = {"validated": False, "preflight_complete": False, "write_unit": "item", "writes_attempted": 0,
             "writes_completed": 0, "changed": None, "chunk_opened": False,
             "chunk_closed": False, "chunk_name": "cgi_guard_" + uuid.uuid4().hex,
             "rollback_attempted": False, "rolled_back": False, "recovery_errors": [],
             "value_tolerance": {"absolute": _ABS_TOL, "relative": 0.0, "units": "Native queried units",
                                 "scope": "Expected old values and desired readback; rollback uses exact equality"},
             "change_detection": "Exact local TRS and object matrix snapshot comparison",
             "rollback_verification": "Exact native local TRS and object matrices of edited transforms; not a whole-scene proof."}
    output = {"guard": guard, "items": [], "count": 0, "truncated": False,
              "scope": "Captured leaf mesh transform TRS placement; no arbitrary DG or rig editing."}
    try:
        cmds = context.extras.get("cmds_module")
        if cmds is None:
            raise RuntimeError("Maya execution context is required")
        rows, originals = _preflight(cmds, params, kind)
        guard["validated"] = guard["preflight_complete"] = True
        output["items"] = rows
        _apply(cmds, rows, originals, kind, guard)
        output["count"] = len(rows)
        return make_api_receipt(api_id, version, "SUCCESS", started, input_data=dict(params), output=output)
    except Exception as exc:
        output["count"] = guard["writes_completed"]
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params), output=output,
            error_code="MAYA_GUARDED_EDIT_FAILED", error=str(exc),
            recovery_hint="Inspect guard/readback/rollback evidence. Capture a fresh observation before retrying.",
        )
