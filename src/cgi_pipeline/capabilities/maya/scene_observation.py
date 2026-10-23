"""Reusable identity, full-port polygon observations and image-pixel picking."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from uuid import uuid4

from cgi_pipeline.contracts import make_api_receipt

from . import scene_geometry as geometry
from . import scene_observation_io as observation_io
from . import scene_observation_view as observation_view

_SESSION_KEY = "_cgi_maya_scene_observation_session_v1"
SCOPE = {
    "method": "nearest_polygon_geometry",
    "pixel_coordinates": "integer original PNG indices, origin top-left; exact 1:1 full modelPanel port",
    "visibility": "Nearest camera-clipped base polygon intersection; no full-object visibility assertion.",
    "unsupported": ["XGen/grooms", "plugin or nonpolygon drawables",
                    "smooth preview", "isolate/filter/template", "bbox/unshaded/hidden/alpha drawing overrides",
                    "non-default display layers", "ghosting/hideOnPlayback", "custom render overrides"],
    "drawing_overrides": "Native normal/reference full shaded drawing is recorded for every mesh and ancestor; reference lookup does not certify UI selection or editability.",
    "appearance": "All visible polygon meshes remain ray candidates. Unknown/transparent/refraction/displacement appearance at the nearest intersection yields UNSUPPORTED_PIXEL_APPEARANCE with no target match; it never exposes an opaque mesh behind it. Only explicitly supported classic opaque base shaders permit geometric matching.",
    "depth_ambiguity": "All near-depth candidates are retained relative to the final minimum depth. Fixed scale-aware float32 tolerance only rejects ambiguity; it never certifies the GPU winner or numerical exactness.",
    "not_certified": ["semantic labels such as hair or foot", "shader/texture/lighting freshness",
                      "auxiliary icons, annotation text/arrows or HUD overlay pixel ownership", "final-render pixel ownership"],
}
NATIVE_SCOPE = {
    "method": "maya.cmds.hitTest_current_view",
    "pixel_coordinates": "integer current full-port PNG indices, origin top-left; passed unchanged to Maya hitTest",
    "interpretation": "Native candidates only; no depth-order, nearest/frontmost or shader-pixel ownership guarantee.",
    "freshness": "Cheap scene, frame, view, session and selection checks; not an immutable image or geometry observation.",
    "candidate_budget": "At most 200 native hit/path resolutions across the batch; excess rejects, never truncates.",
    "edit_guard_token": False,
}


def require_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Maya scene observation requires the Python main thread")


def _process_created_at():
    """Native Windows process birth; PID alone is insufficient after a restart."""
    if sys.platform != "win32":
        raise RuntimeError("Maya observation process identity currently supports Windows only")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(creation),
                                  ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ((creation.dwHighDateTime << 32) | creation.dwLowDateTime) / 10000000.0 - 11644473600.0


def _new_session(cmds):
    import maya.api.OpenMaya as om
    state = {"schema_version": 1, "pid": os.getpid(), "process_created_at": _process_created_at(),
             "session_nonce": uuid4().hex, "scene_epoch": 0, "callbacks": []}
    def scene_changed(*_args):
        state["scene_epoch"] += 1
    try:
        for event in (om.MSceneMessage.kAfterNew, om.MSceneMessage.kAfterOpen):
            callback = om.MSceneMessage.addCallback(event, scene_changed)
            if not callback:
                raise RuntimeError("Maya scene callback registration returned no callback ID")
            state["callbacks"].append(callback)
    except Exception:
        for callback in state["callbacks"]:
            om.MMessage.removeCallback(callback)
        raise
    # The closure retains only this narrow dictionary, never a reloadable business function.
    state["callback_function"] = scene_changed
    setattr(sys, _SESSION_KEY, state)
    return state


def session_identity(cmds):
    require_main_thread()
    state = getattr(sys, _SESSION_KEY, None)
    if state is None:
        state = _new_session(cmds)
    if (state.get("schema_version") != 1 or state.get("pid") != os.getpid()
            or len(state.get("callbacks", [])) != 2):
        raise RuntimeError("Invalid native scene observation session state")
    return {key: state[key] for key in ("schema_version", "pid", "process_created_at",
                                       "session_nonce", "scene_epoch")} | {
        "maya_api_version": int(cmds.about(apiVersion=True))}


def _basic_identity(cmds, path):
    matches = cmds.ls(path, long=True) or []
    uuids = cmds.ls(path, uuid=True) or []
    if matches != [path] or len(uuids) != 1:
        raise ValueError("Exact existing unambiguous node path required: " + str(path))
    node_type = cmds.nodeType(path)
    return {"path": path, "uuid": uuids[0], "type": node_type, "node_type": node_type}


def node_identity(cmds, path):
    require_main_thread()
    record = _basic_identity(cmds, path)
    record.update(instance_number=None, is_instanced=False, all_instance_paths=[], parents=[])
    if not path.startswith("|"):
        return record
    import maya.api.OpenMaya as om
    dag = geometry.dag_path(path)
    all_paths = sorted(p.fullPathName() for p in om.MFnDagNode(dag).getAllPaths())
    parents = ["|" + "|".join(path.split("|")[1:index])
               for index in range(2, len(path.split("|")))]
    record.update(instance_number=int(dag.instanceNumber()), is_instanced=bool(dag.isInstanced()),
                  all_instance_paths=all_paths, parents=[_basic_identity(cmds, p) for p in parents])
    return record


def _budgets(max_meshes, max_vertices):
    if type(max_meshes) is not int or not 1 <= max_meshes <= 2000:
        raise ValueError("max_meshes must be an integer in 1..2000")
    if type(max_vertices) is not int or not 1 <= max_vertices <= 2000000:
        raise ValueError("max_vertices must be an integer in 1..2000000")
    return {"max_meshes": max_meshes, "max_vertices": max_vertices,
            "hard_max_meshes": 2000, "hard_max_vertices": 2000000, "truncated": False}


def snapshot(cmds, panel, targets, max_meshes=1000, max_vertices=250000):
    require_main_thread()
    limits = _budgets(max_meshes, max_vertices)
    session = session_identity(cmds)
    view = observation_view.native_view(cmds, panel)
    view_record = observation_view.view_state(cmds, panel, view, node_identity)
    meshes, counts = geometry.collect(cmds, view_record["display"], node_identity, max_meshes, max_vertices)
    target_records = geometry.target_records(cmds, targets, meshes, node_identity, view,
                                            observation_view.project)
    if session_identity(cmds) != session:
        raise RuntimeError("Scene identity changed during observation")
    state = {"schema_version": 1, "session": session,
             "scene": cmds.file(query=True, sceneName=True), "view": view_record,
             "targets": target_records, "meshes": meshes, "counts": counts,
             "limits": limits, "scope": SCOPE}
    observation_io.canonical(state)  # Reject nonfinite native values before publishing a claim.
    return state


def publish(state, png_path, width, height):
    return observation_io.publish(state, png_path, width, height)


def validate(cmds, reference):
    require_main_thread()
    record = observation_io.read(reference)
    state = record["state"]
    current = snapshot(cmds, state["view"]["panel"], [t["path"] for t in state["targets"]],
                       state["limits"]["max_meshes"], state["limits"]["max_vertices"])
    if current != state:
        changed = [key for key in current if current[key] != state.get(key)]
        raise RuntimeError("Stale polygon observation; changed state sections: " + ", ".join(changed))
    return record


def _pixels(values, width, height):
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ValueError("pixels requires 1..20 [x,y] original-image integer indices")
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(type(v) is not int for v in pair):
            raise ValueError("Each pixel must contain exactly two integer indices; no NaN, infinity or bool")
        if not 0 <= pair[0] < width or not 0 <= pair[1] < height:
            raise ValueError("Pixel lies outside the original image")
    return values


def _pick(cmds, params):
    record = validate(cmds, params["observation"])
    state, image = record["state"], record["image"]
    pixels = _pixels(params["pixels"], image["width"], image["height"])
    expected = params.get("expected_target")
    target = next((t for t in state["targets"] if t["path"] == expected), None)
    if expected is not None and target is None:
        raise ValueError("expected_target must be an exact target in this observation")
    view = observation_view.native_view(cmds, state["view"]["panel"])
    results, function_sets = [], {}
    for x, y in pixels:
        port_y = image["height"] - 1 - y
        origin, direction, maximum = observation_view.clipped_ray(view, x, port_y, state["view"])
        hit = geometry.nearest_hit(state["meshes"], origin, direction, maximum, function_sets)
        results.append(_pixel_result(x, y, port_y, hit, target))
    # Queries may evaluate procedural geometry; never return hits against a changed candidate set.
    validate(cmds, params["observation"])
    return {"items": results, "count": len(results), "truncated": False,
            "observation": params["observation"], "scope": SCOPE}


def _pixel_result(x, y, port_y, hit, target):
    unsupported = hit is not None and hit.get("pixel_lookup_supported") is not True
    row = {"image_xy_top_left": [x, y], "port_xy_bottom_left": [x, port_y],
           "status": "NEAREST_POLYGON_GEOMETRY" if hit else "NO_POLYGON_HIT",
           "nearest_hit": hit, "geometry_candidate": None,
           "depth_tolerance": hit.get("depth_tolerance") if hit else None}
    if hit and hit.get("ambiguous") is True:
        row.update(status="AMBIGUOUS_POLYGON_GEOMETRY", nearest_hit=None,
                   geometry_candidates=hit["geometry_candidates"], candidate_count=hit["candidate_count"],
                   expected_target_match=None)
    elif unsupported:
        row.update(status="UNSUPPORTED_PIXEL_APPEARANCE", nearest_hit=None,
                   geometry_candidate=hit, expected_target_match=None)
    elif target:
        row["expected_target_match"] = bool(hit and hit["path"] in target["mesh_paths"])
    return row


def _native_state(cmds, panel):
    # Reuse the ordinary capture metadata owner; no strict polygon/display policy or scene scan.
    from .asset.maya_capture_viewport import maya_capture_viewport as capture
    view = observation_view.native_view(cmds, panel)
    editor = cmds.modelPanel(panel, q=True, modelEditor=True)
    control = cmds.editor(editor, q=True, control=True)
    if not isinstance(control, str) or not control or control == "NONE":
        raise ValueError("Native pick requires an existing modelEditor control")
    state = {"observation": {**capture._observation(cmds, [panel]), "scope": "viewport",
                            "width": view.portWidth(), "height": view.portHeight()},
             "control": control, "session": session_identity(cmds),
             "selection": cmds.ls(selection=True, long=True) or [],
             "modified": cmds.file(q=True, modified=True),
             "model_view_matrix": list(view.modelViewMatrix()), "projection_matrix": list(view.projectionMatrix())}
    observation_io.canonical(state)
    return state


def _native_candidates(cmds, hits, remaining):
    if not isinstance(hits, (list, tuple)) or any(not isinstance(hit, str) for hit in hits):
        raise RuntimeError("Maya hitTest returned malformed native candidates")
    if len(hits) > remaining:
        raise ValueError("Native candidate resolution exceeds the 200-path batch budget; no truncation")
    candidates, unresolved, used = {}, [], 0
    for hit in hits:
        # allPaths is only for ambiguous short names; expanding an exact path would merge instances.
        paths = [hit] if hit.startswith("|") else sorted(set(cmds.ls(hit, long=True, allPaths=True) or []))
        used += max(1, len(paths))
        if used > remaining:
            raise ValueError("Native candidate resolution exceeds the 200-path batch budget; no truncation")
        if not paths:
            unresolved.append({"raw_hit": hit, "error": "No exact DAG candidate resolved"})
        for path in paths:
            try:
                if not path.startswith("|"):
                    raise ValueError("Native candidate is not a DAG path")
                candidates[path] = node_identity(cmds, path)
            except Exception as exc:
                unresolved.append({"raw_hit": hit, "path": path, "error": str(exc)})
    return list(candidates.values()), unresolved, used


def _pick_native(cmds, params):
    panel = params.get("panel")
    before = _native_state(cmds, panel)
    current = before["observation"]
    pixels = _pixels(params["pixels"], current["width"], current["height"])
    expected = params.get("expected_view")
    if expected is not None and (not isinstance(expected, dict) or any(
            expected.get(key) != value for key, value in current.items())):
        raise RuntimeError("Current view differs from expected_view; acquire a fresh viewport image")
    target = params.get("expected_target")
    if target is not None:
        if not isinstance(target, str) or not target.startswith("|"):
            raise ValueError("expected_target requires an exact current full DAG path")
        node_identity(cmds, target)
    rows, remaining = [], 200
    for x, y in pixels:
        hits = cmds.hitTest(before["control"], x, y) or []
        candidates, unresolved, used = _native_candidates(cmds, hits, remaining)
        remaining -= used
        match = None
        if target is not None and not unresolved and len(candidates) <= 1:
            match = bool(candidates and (candidates[0]["path"] == target
                                        or candidates[0]["path"].startswith(target + "|")))
        rows.append({"image_xy_top_left": [x, y], "raw_hits": hits, "candidates": candidates,
                     "unresolved_hits": unresolved, "expected_target_match": match,
                     "status": "NATIVE_VIEWPORT_CANDIDATES" if hits else "NO_NATIVE_HIT"})
    if _native_state(cmds, panel) != before:
        raise RuntimeError("Scene, current view, session or selection changed during native pick")
    return {"mode": "native", "items": rows, "count": len(rows), "truncated": False,
            "observation": current, "session": before["session"], "scope": NATIVE_SCOPE}


def pick_pixels(params, context):
    started = time.perf_counter()
    api_id = context.extras.get("_api_id", "maya.scene.viewport.pick")
    version = context.extras.get("_api_version", "1.1.0")
    try:
        require_main_thread()
        cmds = context.extras.get("cmds_module")
        if cmds is None:
            raise RuntimeError("Maya execution context is required")
        mode = params.get("mode", "verified")
        if mode not in ("verified", "native"):
            raise ValueError("mode must be verified or native")
        output = _pick_native(cmds, params) if mode == "native" else _pick(cmds, params)
        return make_api_receipt(api_id, version, "SUCCESS", started, input_data=dict(params), output=output)
    except Exception as exc:
        return make_api_receipt(api_id, version, "ERROR", started, input_data=dict(params),
                                error_code="MAYA_OBSERVATION_REJECTED", error=str(exc),
                                recovery_hint="Use an explicit current panel and fresh viewport image for native mode; verified mode requires a fresh supported grounded PNG.")
