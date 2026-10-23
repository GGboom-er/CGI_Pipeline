"""Explicit-panel Maya framing and self-contained, compare-and-set restoration.

Camera numbers compare with rel_tol=1e-10/abs_tol=1e-8. No global restore store,
panel rebinding, dirty-flag clearing, or undo-history rewind is used.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager


TRANSFORM_FIELDS = tuple(prefix + axis for prefix in ("translate", "rotate", "scale")
                         for axis in ("X", "Y", "Z")) + ("shearXY", "shearXZ", "shearYZ")
SHAPE_FIELDS = (
    "orthographic", "orthographicWidth", "focalLength", "nearClipPlane", "farClipPlane",
    "filmFit", "centerOfInterest", "horizontalFilmAperture", "verticalFilmAperture",
    "filmFitOffset", "horizontalFilmOffset", "verticalFilmOffset", "lensSqueezeRatio",
    "overscan", "cameraScale", "preScale", "postScale", "panZoomEnabled",
    "horizontalPan", "verticalPan", "zoom",
)
STATE_KEYS = {"session_identity", "panel", "camera", "panel_bindings", "transform_fields",
              "shape_fields", "world_matrix", "selection", "selection_identities", "time"}


@contextmanager
def inspection_display(cmds, panel, targets, *, rotation=(0, 0, 0), near_clip=1., camera_state=None,
                       visibility_overrides=None, attribute_overrides=None, reuse_display=False, image_size=None):
    """Reusable isolated material/wireframe observation with restored host state.

    Targets use exact DAG paths; caller owns camera/framing authorization.
    Does not save, edit shaders or change rig visibility connections.
    """
    _camera(cmds, panel)
    for target in targets:
        _exact(cmds, target)
    visibility_before = {}
    attribute_before = {}
    for plug, value in (attribute_overrides or {}).items():
        if '.' not in plug or type(value) not in (bool, int, float) or not math.isfinite(value):
            raise ViewError('Observation state requires literal numeric or boolean plugs')
        _exact(cmds, plug.rsplit('.', 1)[0])
        if not cmds.getAttr(plug, settable=True) or cmds.listConnections(plug, s=True, d=False):
            raise ViewError('Observation state driver must be editable and undriven: ' + plug)
        if cmds.getAttr(plug, type=True) not in ('bool', 'enum', 'long', 'short', 'byte', 'double', 'float'):
            raise ViewError('Unsupported observation state attribute: ' + plug)
        attribute_before[plug] = cmds.getAttr(plug)
    for node, value in (visibility_overrides or {}).items():
        _exact(cmds, node)
        if type(value) is not bool or not cmds.getAttr(node + ".visibility", settable=True):
            raise ViewError("Temporary visibility needs an explicit boolean and an editable, undriven plug")
        visibility_before[node] = cmds.getAttr(node + ".visibility")
    if near_clip not in (0.1, 1., 10.):
        raise ViewError("near_clip must be 0.1, 1 or 10")
    if image_size is not None:
        from .asset.maya_capture_viewport.maya_capture_viewport import _image_size
        image_size = _image_size({'image_size':image_size}, 'viewport')
    camera = _camera(cmds, panel)
    shape, transform = camera["shape"]["path"], camera["transform"]["path"]
    fields = ("orthographic", "orthographicWidth", "nearClipPlane", "farClipPlane",
              "filmFit", "horizontalFilmOffset", "verticalFilmOffset", "panZoomEnabled")
    old_camera = {a: cmds.getAttr(shape + "." + a) for a in fields}
    old_matrix = cmds.xform(transform, q=True, matrix=True, worldSpace=True)
    from .scene_inspection_style import FLAGS
    flags = {} if reuse_display else FLAGS
    old_flags = {k: cmds.modelEditor(panel, q=True, **{k: True}) for k in flags}
    old_selection = cmds.ls(selection=True, long=True) or []
    isolated = cmds.isolateSelect(panel, q=True, state=True)
    old_view_selected = cmds.modelEditor(panel, q=True, viewSelected=True)
    isolate_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
    members = cmds.sets(isolate_set, q=True) or [] if isolate_set else []
    old_view = None if reuse_display else cmds.colorManagementPrefs(q=True, viewTransformName=True)
    light = "hardwareRenderingGlobals.defaultLightIntensity"
    old_light = cmds.getAttr(light) if not reuse_display and cmds.objExists(light) else None
    try:
        for plug, value in (attribute_overrides or {}).items():
            cmds.setAttr(plug, value)
        for node, value in (visibility_overrides or {}).items():
            cmds.setAttr(node + ".visibility", value)
        for key, value in flags.items():
            cmds.modelEditor(panel, edit=True, **{key: value})
        if old_light is not None:
            cmds.setAttr(light, 1.)
        if not reuse_display:
            cmds.colorManagementPrefs(edit=True, viewTransformName="Un-tone-mapped (sRGB)")
        cmds.isolateSelect(panel, state=False)
        cmds.select(targets, replace=True)
        cmds.modelEditor(panel, edit=True, viewSelected=True)
        cmds.isolateSelect(panel, state=True)
        cmds.isolateSelect(panel, addSelected=True)
        active_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
        if not active_set or not (cmds.sets(active_set, q=True) or []):
            raise ViewError("Isolated target set was not established")
        cmds.setAttr(shape + ".orthographic", True)
        cmds.setAttr(shape + '.filmFit', 1)
        cmds.setAttr(shape + '.horizontalFilmOffset', 0.)
        cmds.setAttr(shape + '.verticalFilmOffset', 0.)
        cmds.setAttr(shape + '.panZoomEnabled', False)
        cmds.xform(transform, worldSpace=True, rotation=rotation)
        from maya.api import OpenMaya as om
        visible = []
        for target in targets:
            shapes = cmds.listRelatives(target, shapes=True, noIntermediate=True, fullPath=True) or [target]
            for node in shapes:
                selection = om.MSelectionList()
                selection.add(node)
                if selection.getDagPath(0).isVisible():
                    visible.append(node)
        if not visible:
            raise ViewError("No visible target for framing; choose its rig display state explicitly")
        cmds.select(visible, replace=True)
        cmds.viewFit(shape, *visible, fitFactor=.9)
        # Native viewFit may clip tall objects in a wide model panel. Fit both
        # projected bounds against the actual viewport, with a consistent margin.
        from maya.api import OpenMayaUI as omui
        cmds.refresh(force=True)
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        aspect = (image_size[0]/image_size[1] if image_size else
                  float(view.portWidth()) / max(1, view.portHeight()))
        bounds = cmds.exactWorldBoundingBox(visible)
        inverse = om.MMatrix(cmds.xform(transform,q=True,matrix=True,worldSpace=True)).inverse()
        corners = [om.MPoint(x,y,z)*inverse for x in (bounds[0],bounds[3])
                   for y in (bounds[1],bounds[4]) for z in (bounds[2],bounds[5])]
        dx = max(p.x for p in corners)-min(p.x for p in corners)
        dy = max(p.y for p in corners)-min(p.y for p in corners)
        cmds.setAttr(shape+'.orthographicWidth',max(dx,dy*aspect)*1.15)
        cmds.setAttr(shape + ".nearClipPlane", near_clip)
        cmds.setAttr(shape + ".farClipPlane", 10000.)
        if camera_state:
            cmds.xform(transform, worldSpace=True, matrix=camera_state["matrix"])
            # Keep the vertical field when a foreground window was widened.
            reference_aspect = camera_state.get('aspect', aspect)
            cmds.setAttr(shape + ".orthographicWidth", camera_state["width"]*max(1.,aspect/reference_aspect))
        cmds.select(clear=True)
        cmds.refresh(force=True)
        yield {"matrix": cmds.xform(transform, q=True, matrix=True, worldSpace=True),
               "width": cmds.getAttr(shape + ".orthographicWidth"), "near_clip": near_clip, 'aspect':aspect,
               "view_transform": "Un-tone-mapped (sRGB)", "two_sided_lighting": True,
               "light_intensity": 1., "targets": list(targets)}
    finally:
        for plug, value in attribute_before.items():
            cmds.setAttr(plug, value)
        for node, value in visibility_before.items():
            cmds.setAttr(node + ".visibility", value)
        cmds.isolateSelect(panel, state=False)
        if isolated:
            cmds.select(members, replace=True) if members else cmds.select(clear=True)
            cmds.modelEditor(panel, edit=True, viewSelected=True)
            cmds.isolateSelect(panel, state=True)
            cmds.isolateSelect(panel, addSelected=True)
        cmds.modelEditor(panel, edit=True, viewSelected=old_view_selected)
        for key, value in old_flags.items():
            cmds.modelEditor(panel, edit=True, **{key: value})
        if not reuse_display:
            cmds.colorManagementPrefs(edit=True, viewTransformName=old_view)
        if old_light is not None:
            cmds.setAttr(light, old_light)
        for key, value in old_camera.items():
            cmds.setAttr(shape + "." + key, value)
        cmds.xform(transform, worldSpace=True, matrix=old_matrix)
        cmds.select(old_selection, replace=True) if old_selection else cmds.select(clear=True)


class ViewError(RuntimeError):
    def __init__(self, message, code="VIEW_CONTRACT_ERROR", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def require_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise ViewError("显式 frame/restore 必须在 Maya 主线程调用；本入口不自动调度。")


def _session(cmds):
    from cgi_pipeline.capabilities.maya.scene_observation import session_identity
    return session_identity(cmds)


def _exact(cmds, value):
    if not isinstance(value, str) or not value.startswith("|") or any(c in value for c in ".*?[]"):
        raise ViewError("显式 frame 仅接受完整 DAG 路径，不接受短名、通配或组件。")
    if (cmds.ls(value, long=True) or []) != [value]:
        raise ViewError("完整 DAG 路径不存在或不唯一: " + value)
    uuids = cmds.ls(value, uuid=True) or []
    if len(uuids) != 1:
        raise ViewError("无法确定唯一节点 UUID: " + value)
    return {"path": value, "uuid": uuids[0]}


def _camera(cmds, panel):
    if not isinstance(panel, str) or cmds.getPanel(typeOf=panel) != "modelPanel":
        raise ViewError("panel 必须是现存 modelPanel；请明确指定目标视口。")
    names = cmds.ls(cmds.modelEditor(panel, query=True, camera=True), long=True) or []
    if len(names) != 1:
        raise ViewError("panel 相机不能唯一解析；请使用独立且名称明确的相机。")
    node = names[0]
    shapes = [node] if cmds.nodeType(node) == "camera" else (
        cmds.listRelatives(node, shapes=True, fullPath=True, type="camera") or [])
    if len(shapes) != 1:
        raise ViewError("相机 transform 必须只有一个 camera shape。")
    parents = cmds.listRelatives(shapes[0], allParents=True, fullPath=True) or []
    if len(parents) != 1:
        raise ViewError("不支持实例化相机；请选择独立相机视口。")
    return {"shape": _exact(cmds, shapes[0]), "transform": _exact(cmds, parents[0])}


def _panels(cmds, panel, camera):
    bindings = {}
    for other in sorted(cmds.getPanel(type="modelPanel") or []):
        candidate = _camera(cmds, other)
        bindings[other] = candidate
        shared = any(candidate[k]["uuid"] == camera[k]["uuid"] for k in ("shape", "transform"))
        if other != panel and shared:
            raise ViewError("当前相机也用于其他面板；请选择使用独立相机的视口。")
    return bindings


def _preflight(cmds, camera):
    for kind, fields in (("transform", TRANSFORM_FIELDS), ("shape", SHAPE_FIELDS)):
        node = camera[kind]["path"]
        if cmds.referenceQuery(node, isNodeReferenced=True):
            raise ViewError("拒绝修改引用相机；请选择独立可写相机。")
        for field in fields:
            plug = node + "." + field
            if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, settable=True):
                raise ViewError("相机字段锁定或不可写，请先选择可写相机: " + plug)
            if cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                raise ViewError("相机字段存在输入连接，请选择无驱动相机: " + plug)


def _values(cmds, node, fields):
    values = {field: cmds.getAttr(node + "." + field) for field in fields}
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values.values()):
        raise ViewError("相机字段必须是有限数值。")
    return values


def _selection_ids(cmds, paths):
    identities = [_exact(cmds, path) for path in paths]
    if any(not cmds.objectType(path, isAType="dagNode") for path in paths):
        raise ViewError("显式 frame/restore 首版只支持对象级完整 DAG 选择；请手动切换对象选择。")
    return identities


def _snapshot(cmds, panel):
    identity = _session(cmds)
    selection = list(cmds.ls(selection=True, long=True) or [])
    selection_ids = _selection_ids(cmds, selection)
    camera = _camera(cmds, panel)
    return {"session_identity": identity, "panel": panel, "camera": camera,
            "panel_bindings": _panels(cmds, panel, camera),
            "transform_fields": _values(cmds, camera["transform"]["path"], TRANSFORM_FIELDS),
            "shape_fields": _values(cmds, camera["shape"]["path"], SHAPE_FIELDS),
            "world_matrix": list(cmds.xform(camera["transform"]["path"], query=True,
                                             worldSpace=True, matrix=True)),
            "selection": selection, "selection_identities": selection_ids,
            "time": cmds.currentTime(query=True)}


def _same(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            left[k] == right[k] if k in {"session_identity", "time"} else _same(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-8)
    return left == right


def _changes(before, after):
    changed = []
    for kind in ("transform", "shape"):
        field_key = kind + "_fields"
        for attribute, value in before[field_key].items():
            if not _same(value, after[field_key][attribute]):
                changed.append({"kind": kind, "attribute": attribute, "before": value,
                                "after": after[field_key][attribute]})
    return changed


def _capsule(before, after):
    return {"schema_version": 2, "kind": "maya_select_objects.view_restore",
            "before": before, "expected_after": after,
            "changed_fields": _changes(before, after) if after is not None else None,
            "float_tolerance": {"relative": 1e-10, "absolute": 1e-8}}


def _validate_state(state):
    if set(state) != STATE_KEYS:
        raise ViewError("restore_view 状态字段不完整或 schema 已变化。")
    matrix = state["world_matrix"]
    if not isinstance(matrix, list) or len(matrix) != 16 or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in matrix):
        raise ViewError("restore_view 世界矩阵无效。")
    if not isinstance(state["time"], (int, float)) or not math.isfinite(state["time"]):
        raise ViewError("restore_view 时间无效。")
    if not isinstance(state["selection"], list) or not all(isinstance(v, str) for v in state["selection"]):
        raise ViewError("restore_view 选择状态无效。")
    identities = state["selection_identities"]
    if not isinstance(identities, list) or len(identities) != len(state["selection"]):
        raise ViewError("restore_view 缺少完整 selection_identities。")
    for path, row in zip(state["selection"], identities):
        if not isinstance(row, dict) or set(row) != {"path", "uuid"} or row["path"] != path:
            raise ViewError("restore_view 选择路径与身份不一致。")
        if not isinstance(row["uuid"], str) or not row["uuid"]:
            raise ViewError("restore_view 选择 UUID 无效。")


def _validate_capsule(capsule, panel):
    if not isinstance(capsule, dict) or capsule.get("schema_version") != 2:
        raise ViewError("restore_view 必须是原 frame 返回的含选择身份的 v2 capsule；旧 capsule 需重新取景。")
    if capsule.get("kind") != "maya_select_objects.view_restore":
        raise ViewError("restore_view kind 不匹配。")
    before, after = capsule.get("before"), capsule.get("expected_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ViewError("缺少原始 before/expected_after；不能盲目恢复。")
    if before.get("panel") != panel or after.get("panel") != panel:
        raise ViewError("restore_view 与显式 panel 不匹配。")
    for state in (before, after):
        _validate_state(state)
        if set(state.get("transform_fields", {})) != set(TRANSFORM_FIELDS):
            raise ViewError("restore_view transform 字段不完整。")
        if set(state.get("shape_fields", {})) != set(SHAPE_FIELDS):
            raise ViewError("restore_view shape 字段不完整。")
        values = list(state["transform_fields"].values()) + list(state["shape_fields"].values())
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ViewError("restore_view 相机字段必须是有限数值。")
    for key in ("session_identity", "camera", "panel_bindings"):
        if before.get(key) != after.get(key):
            raise ViewError("原操作发生会话/相机绑定漂移，拒绝自动恢复。")
    recorded = capsule.get("changed_fields")
    key = lambda row: (row["kind"], row["attribute"])
    if not isinstance(recorded, list) or not _same(sorted(recorded, key=key), sorted(_changes(before, after), key=key)):
        raise ViewError("restore_view changed_fields 与原状态不一致。")


def _write_before(cmds, before, current, errors):
    for change in _changes(before, current):
        plug = before["camera"][change["kind"]]["path"] + "." + change["attribute"]
        try:
            cmds.setAttr(plug, change["before"])
        except (RuntimeError, TypeError, ValueError) as error:
            errors.append({"plug": plug, "error_type": type(error).__name__, "error": str(error)})
    try:
        if before["time"] != current["time"]:
            cmds.currentTime(before["time"], edit=True)
        if before["selection"] != current["selection"]:
            cmds.select(before["selection"], replace=True) if before["selection"] else cmds.select(clear=True)
    except (RuntimeError, TypeError, ValueError) as error:
        errors.append({"operation": "selection/time", "error_type": type(error).__name__, "error": str(error)})


def _apply_before(cmds, before, current):
    errors, readback = [], None
    try:
        cmds.undoInfo(openChunk=True, chunkName="CGI_restore_explicit_view")
        try:
            _write_before(cmds, before, current, errors)
        finally:
            cmds.undoInfo(closeChunk=True)
    except Exception as error:
        errors.append({"operation": "restore_write", "error_type": type(error).__name__, "error": str(error)})
    try:
        readback = _snapshot(cmds, before["panel"])
    except Exception as error:
        errors.append({"operation": "restore_readback", "error_type": type(error).__name__, "error": str(error)})
    differences = [] if _same(before, readback) else [{"expected": before, "actual": readback}]
    return {"restored": not errors and not differences, "restore_errors": errors,
            "restore_differences": differences, "readback": readback}


def _recover(cmds, before):
    current = None
    try:
        current = _snapshot(cmds, before["panel"])
        for key in ("session_identity", "camera", "panel_bindings"):
            if before[key] != current[key]:
                raise ViewError("失败后会话/相机绑定变化，停止恢复以免覆盖其他状态。")
        _check_before_selection(cmds, before)
        _preflight(cmds, current["camera"])
        capsule = _capsule(before, current)
        return capsule, _apply_before(cmds, before, current)
    except Exception as error:
        return _capsule(before, current), {"restored": False, "restore_differences": [],
                                      "restore_errors": [{"error_type": type(error).__name__, "error": str(error),
                                                          "details": getattr(error, "details", {})}]}


def _check_before_selection(cmds, before):
    actual, reason = None, None
    try:
        actual = _selection_ids(cmds, before["selection"])
    except (ViewError, RuntimeError, TypeError) as error:
        reason = str(error)
    expected = before["selection_identities"]
    if actual != expected:
        conflict = {"expected": expected, "actual": actual, "error": reason}
        raise ViewError("原选择对象已缺失、替换或身份变化，拒绝在恢复相机前覆盖状态。",
                        "VIEW_RESTORE_CONFLICT", {"selection_conflict": conflict, "restored": False,
                                                  "restore_attempted": False, "restore_errors": [],
                                                  "restore_differences": [{"selection_conflict": conflict}]})


def restore(cmds, panel, capsule):
    require_main_thread()
    _validate_capsule(capsule, panel)
    current = _snapshot(cmds, panel)
    if not _same(current, capsule["expected_after"]):
        raise ViewError("取景后状态已变化，CAS 拒绝覆盖；请保留当前视角或重新观察。",
                        "VIEW_RESTORE_CONFLICT", {"view_restore": capsule, "current": current,
                                                  "restored": False, "restore_attempted": False,
                                                  "restore_errors": [], "restore_differences": [
                                                      {"expected_after": capsule["expected_after"], "current": current}]})
    try:
        _check_before_selection(cmds, capsule["before"])
    except ViewError as error:
        error.details.update(view_restore=capsule, current=current)
        raise
    _preflight(cmds, current["camera"])
    result = _apply_before(cmds, capsule["before"], current)
    result.update(view_restore=capsule, mode="restore_view", before=current["selection"],
                  after=(result["readback"] or {}).get("selection"), resolved=[], truncated=False)
    if not result["restored"]:
        raise ViewError("视图恢复回读不一致，请查看 restore_errors/differences。", "VIEW_RESTORE_FAILED", result)
    return result


def _resolve_targets(cmds, params):
    values = params.get("objects") or params.get("nodes") or params.get("patterns") or []
    if not isinstance(values, list) or not values:
        raise ViewError("显式 frame 必须提供非空 objects 完整路径列表。")
    maximum = int(params.get("max_items", 200))
    if not 1 <= maximum <= 2000 or len(values) > maximum:
        raise ViewError("显式 frame 不接受截断；请缩小 targets 或提高合法 max_items。")
    targets = [_exact(cmds, value) for value in values]
    paths = list(dict.fromkeys(target["path"] for target in targets))
    if any(not cmds.objectType(path, isAType="dagNode") for path in paths):
        raise ViewError("frame targets 必须为 DAG 节点。")
    bbox = list(cmds.exactWorldBoundingBox(*paths))
    if len(bbox) != 6 or not all(math.isfinite(v) and abs(v) < 1e19 for v in bbox):
        raise ViewError("targets 没有有效世界包围盒；拒绝空几何 sentinel。")
    return paths, bbox


def frame(cmds, params):
    require_main_thread()
    near_clip = params.get("near_clip_plane")
    if near_clip is not None and (type(near_clip) not in (int, float) or
                                  near_clip not in (0.1, 1.0, 10.0)):
        raise ViewError("near_clip_plane 只接受数值档位 0.1、1、10；省略则保留原值。")
    panel = params.get("panel")
    mode = str(params.get("mode") or "replace").lower()
    if params.get("frame") is not True or mode not in {"replace", "add", "remove"}:
        raise ViewError("显式 panel 仅用于 frame=true，mode 为 replace/add/remove。")
    _session(cmds)
    paths, bbox = _resolve_targets(cmds, params)
    before = _snapshot(cmds, panel)
    _preflight(cmds, before["camera"])
    if near_clip is not None and near_clip >= before["shape_fields"]["farClipPlane"]:
        raise ViewError("near_clip_plane 必须小于现有 farClipPlane；本操作不自动扩大远裁剪面。")
    completed, failure, after, recovery, capsule = False, None, None, None, None
    try:
        _select_and_fit(cmds, paths, mode, before["camera"]["shape"]["path"], near_clip)
        after = _snapshot(cmds, panel)
        if near_clip is not None and not _same(after["shape_fields"]["nearClipPlane"], near_clip):
            raise ViewError("近裁剪面写后回读不一致。")
        capsule = _capsule(before, after)
        _validate_capsule(capsule, panel)
        completed = True
    except Exception as error:
        failure = {"error_type": type(error).__name__, "error": str(error)}
    finally:
        if not completed:
            capsule, recovery = _recover(cmds, before)
    if not completed:
        raise ViewError("显式 frame 失败: " + str(failure), "VIEW_FRAME_FAILED",
                        {"operation_error": failure, "view_restore": capsule, **recovery})
    return {"before": before["selection"], "after": after["selection"], "resolved": paths,
            "mode": mode, "truncated": False, "world_bbox": bbox, "view_restore": capsule,
            "clip_planes": {"camera": after["camera"]["shape"], "requested_near": near_clip,
                            "near": after["shape_fields"]["nearClipPlane"],
                            "far": after["shape_fields"]["farClipPlane"],
                            "coverage": "Attribute readback only; inspect the image for clipping or z-fighting."},
            "restore_limit": "同会话同场景 CAS；不清 dirty flag，不倒退 undo history。"}


def _select_and_fit(cmds, paths, mode, camera_shape, near_clip=None):
    flags = {"replace": {"replace": True}, "add": {"add": True}, "remove": {"deselect": True}}
    cmds.undoInfo(openChunk=True, chunkName="CGI_select_explicit_frame")
    try:
        cmds.select(paths, **flags[mode])
        cmds.viewFit(camera_shape, *paths, animate=False)
        if near_clip is not None:
            cmds.setAttr(camera_shape + ".nearClipPlane", float(near_clip))
    finally:
        cmds.undoInfo(closeChunk=True)
