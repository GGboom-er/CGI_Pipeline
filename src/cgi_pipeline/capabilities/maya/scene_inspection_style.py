"""One reversible display preset per inspection batch, independent of framing."""
from contextlib import contextmanager

FLAGS = {"displayAppearance": "smoothShaded", "wireframeOnShaded": True,
         "displayTextures": True, "useDefaultMaterial": False, "displayLights": "default",
         "twoSidedLighting": True, "smoothWireframe": True, "xray": False,
         "grid": False, "headsUpDisplay": False,
         "nurbsCurves": False, "joints": False, "locators": False, "polymeshes": True}
VIEW_TRANSFORM = "Un-tone-mapped (sRGB)"


@contextmanager
def inspection_style(cmds, panel, *, default_material=False, match_current=False):
    """Apply once around a capture batch; restore the user's display on exit."""
    flags = {**FLAGS, 'useDefaultMaterial': default_material}
    if match_current:
        flags['twoSidedLighting']=cmds.modelEditor(panel,q=True,twoSidedLighting=True)
    previous = {key: cmds.modelEditor(panel, q=True, **{key: True}) for key in flags}
    old_view = cmds.colorManagementPrefs(q=True, viewTransformName=True)
    view_transform=old_view if match_current else VIEW_TRANSFORM
    light = 'hardwareRenderingGlobals.defaultLightIntensity'
    old_light = cmds.getAttr(light) if cmds.objExists(light) else None
    try:
        for key, value in flags.items():
            if previous[key] != value:
                cmds.modelEditor(panel, edit=True, **{key: value})
        if old_light is not None and old_light != 1.:
            cmds.setAttr(light, 1.)
        if old_view != view_transform:
            cmds.colorManagementPrefs(edit=True, viewTransformName=view_transform)
        yield {'preset': 'material_wireframe', 'default_material': default_material,
               'view_transform': view_transform, 'two_sided_lighting': flags['twoSidedLighting'], 'light_intensity': 1.}
    finally:
        for key, value in previous.items():
            if cmds.modelEditor(panel, q=True, **{key: True}) != value:
                cmds.modelEditor(panel, edit=True, **{key: value})
        if cmds.colorManagementPrefs(q=True, viewTransformName=True) != old_view:
            cmds.colorManagementPrefs(edit=True, viewTransformName=old_view)
        if old_light is not None and cmds.getAttr(light) != old_light:
            cmds.setAttr(light, old_light)
