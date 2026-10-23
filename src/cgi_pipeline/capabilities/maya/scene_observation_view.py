"""Explicit native modelPanel state and full-port projection, without UI changes."""

from __future__ import annotations

import math

# These affect candidate membership or invalidate a base-polygon ray interpretation.
_FALSE_FLAGS = ("isFiltered", "viewSelected", "activeOnly", "xray", "backfaceCulling",
                "smallObjectCulling", "occlusionCulling")
_EMPTY_FLAGS = ("objectFilter", "filter", "rendererOverrideName", "sceneRenderFilter", "viewType")
_DISPLAY_FLAGS = ("displayAppearance", "wireframeOnShaded", "displayTextures", "displayLights",
                  "exposure", "gamma", "twoSidedLighting", "useDefaultMaterial", "grid",
                  "headsUpDisplay", "selectionHiliteDisplay", "nurbsCurves", "nurbsSurfaces",
                  "subdivSurfaces", "strokes", "hairSystems", "fluids", "dynamics",
                  "nParticles", "particleInstancers", "pluginShapes", "imagePlane")
_CAMERA_ATTRS = ("orthographic", "orthographicWidth", "focalLength", "nearClipPlane",
                 "farClipPlane", "horizontalFilmAperture", "verticalFilmAperture", "filmFit",
                 "lensSqueezeRatio", "cameraScale", "filmFitOffset", "horizontalFilmOffset",
                 "verticalFilmOffset", "overscan", "panZoomEnabled", "horizontalPan",
                 "verticalPan", "zoom", "renderPanZoom")


def native_view(cmds, panel):
    if not isinstance(panel, str) or not panel:
        raise ValueError("Grounding requires an explicit visible modelPanel")
    if cmds.about(batch=True) or cmds.getPanel(typeOf=panel) != "modelPanel":
        raise ValueError("Grounding requires a Maya GUI modelPanel")
    if panel not in (cmds.getPanel(visiblePanels=True) or []):
        raise ValueError("Grounding panel is not visible")
    import maya.api.OpenMayaUI as omui
    view = omui.M3dView.getM3dViewFromModelPanel(panel)
    if not view.isVisible() or min(view.portWidth(), view.portHeight()) < 1:
        raise ValueError("Grounding panel has no visible port")
    if list(view.viewport()) != [0, 0, view.portWidth(), view.portHeight()]:
        raise ValueError("Only the original full port with zero viewport origin is supported")
    return view


def display_state(cmds, panel):
    flags = _FALSE_FLAGS + _EMPTY_FLAGS + _DISPLAY_FLAGS + ("rendererName", "polymeshes",
                                                          "mainListConnection")
    result = {flag: cmds.modelEditor(panel, query=True, **{flag: True}) for flag in flags}
    if cmds.isolateSelect(panel, query=True, state=True):
        raise ValueError("Isolate select is unsupported for grounded observations")
    blocked = [flag for flag in _FALSE_FLAGS + _EMPTY_FLAGS if result[flag]]
    if blocked:
        raise ValueError("Unsupported modelPanel filtering/drawing state: " + ", ".join(blocked))
    if result["rendererName"] not in ("vp2Renderer", "base_OpenGL_Renderer"):
        raise ValueError("Unsupported modelPanel renderer")
    connection = result["mainListConnection"]
    connection_type = cmds.selectionConnection(connection, query=True, identify=True) if connection else None
    result["mainListConnectionType"] = connection_type
    # Maya's own buildDefaultSceneUI/resetModelEditor lock activeList while turning
    # isolate OFF. This editor connection is provenance, not the VP2 draw set.
    # Native isFiltered/viewSelected/isolate above establish supported membership.
    if not result["polymeshes"] or result["displayAppearance"] not in ("smoothShaded", "flatShaded"):
        raise ValueError("Grounding requires shaded polygon display")
    return result


def view_state(cmds, panel, view, identity):
    camera = view.getCamera()
    camera_path = camera.fullPathName()
    values = {attr: cmds.getAttr(camera_path + "." + attr) for attr in _CAMERA_ATTRS}
    if not 0 < values["nearClipPlane"] < values["farClipPlane"]:
        raise ValueError("Camera clipping range must be finite, positive and ordered")
    if not all(math.isfinite(float(values[a])) for a in _CAMERA_ATTRS):
        raise ValueError("Nonfinite camera parameters cannot be grounded")
    return {"panel": panel, "scope": "viewport", "fit": "full_port_no_crop",
            "port_size": [view.portWidth(), view.portHeight()], "viewport": list(view.viewport()),
            "camera": identity(cmds, camera_path), "camera_params": values,
            "camera_world_matrix_row_major": list(camera.inclusiveMatrix()),
            "model_view_matrix_row_major": list(view.modelViewMatrix()),
            "projection_matrix_row_major": list(view.projectionMatrix()),
            "display": display_state(cmds, panel),
            "time": cmds.currentTime(query=True),
            "selection": cmds.ls(selection=True, long=True) or []}


def project(view, point):
    import maya.api.OpenMaya as om
    x, y, unclipped = view.worldToView(om.MPoint(*point))
    width, height = view.portWidth(), view.portHeight()
    return {"port_xy_bottom_left": [int(x), int(y)],
            "image_xy_top_left": [int(x), int(height - 1 - y)],
            "api_unclipped": bool(unclipped),
            "inside_port_bounds": 0 <= x < width and 0 <= y < height,
            "projection_is_visibility": False}


def clipped_ray(view, x, y, view_record):
    """Clip the documented native ray to camera near/far planes, including ortho."""
    import maya.api.OpenMaya as om
    origin, direction = om.MPoint(), om.MVector()
    view.viewToWorld(x, y, origin, direction)
    if direction.length() <= 0:
        raise RuntimeError("Native viewToWorld returned a zero ray")
    direction = direction.normal()
    matrix = om.MMatrix(view_record["model_view_matrix_row_major"])
    camera_origin, camera_direction = origin * matrix, direction * matrix
    depth, rate = -camera_origin.z, -camera_direction.z
    if not math.isfinite(depth) or not math.isfinite(rate) or rate <= 0:
        raise RuntimeError("Native ray does not travel into the camera clipping range")
    near, far = (view_record["camera_params"][k] for k in ("nearClipPlane", "farClipPlane"))
    start, stop = max(0.0, (near - depth) / rate), (far - depth) / rate
    if stop <= start or not math.isfinite(stop):
        raise RuntimeError("Native ray has no valid camera clipping interval")
    return origin + direction * start, direction, float(stop - start)
