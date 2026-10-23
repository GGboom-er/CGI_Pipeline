"""Only proven native constraints are auxiliary; plugin drawing stays unsupported."""

from __future__ import annotations

import types

import pytest

from cgi_pipeline.capabilities.maya import scene_geometry as geometry
from test_maya_scene_observation import install_om


@pytest.fixture
def constraint_scene(monkeypatch):
    records = {"|mesh|shape": ("mesh", False), "|rig|constraint": ("parentConstraint", True)}
    class Cmds:
        def ls(self, **kwargs):
            return list(records)
        def nodeType(self, path):
            return records[path][0]
        def attributeQuery(self, *args, **kwargs):
            return False
        def listConnections(self, *args, **kwargs):
            return []
    install_om(monkeypatch, MFn=types.SimpleNamespace(kConstraint=933, kAnnotation=271))
    def native_path(path):
        obj = types.SimpleNamespace(hasFn=lambda fn: records[path][1] and fn == (
            271 if records[path][0] == "annotationShape" else 933))
        return types.SimpleNamespace(isVisible=lambda: True, node=lambda: obj)
    monkeypatch.setattr(geometry, "dag_path", native_path)
    monkeypatch.setattr(geometry, "mesh_record", lambda *a: {
        "path": a[1], "vertex_count": 8,
        "appearance": {"pixel_lookup_supported": True, "unsupported_reasons": []}})
    return Cmds(), records


@pytest.mark.parametrize("node_type", ["parentConstraint", "pointConstraint", "orientConstraint",
    "scaleConstraint", "aimConstraint", "geometryConstraint", "normalConstraint", "tangentConstraint",
    "poleVectorConstraint"])
def test_verified_native_constraint_is_auxiliary_not_surface(constraint_scene, node_type):
    cmds, nodes = constraint_scene
    nodes["|rig|constraint"] = (node_type, True)
    records, counts = geometry.collect(cmds, {}, None, 1000, 250000)
    assert [m["path"] for m in records] == ["|mesh|shape"]
    assert node_type in counts["auxiliary_types_excluded_from_surface_picking"]


@pytest.mark.parametrize("node_type,has_constraint_fn", [
    ("studioConstraintDrawable", True), ("pluginShape", False), ("parentConstraint", False)])
def test_unknown_plugin_and_name_only_constraints_are_not_silently_ignored(
        constraint_scene, node_type, has_constraint_fn):
    cmds, nodes = constraint_scene
    nodes["|rig|constraint"] = (node_type, has_constraint_fn)
    with pytest.raises(ValueError, match="Unsupported visible drawable type"):
        geometry.collect(cmds, {}, None, 1000, 250000)


def test_native_annotation_widget_is_auxiliary_with_explicit_provenance(constraint_scene):
    cmds, nodes = constraint_scene
    nodes["|rig|constraint"] = ("annotationShape", True)
    records, counts = geometry.collect(cmds, {}, None, 1000, 250000)
    assert len(records) == 1
    assert "annotationShape" in counts["auxiliary_types_excluded_from_surface_picking"]


@pytest.mark.parametrize("node_type", ["studioAnnotationShape", "annotationShape"])
def test_unknown_or_false_native_annotation_still_rejects(constraint_scene, node_type):
    cmds, nodes = constraint_scene
    nodes["|rig|constraint"] = (node_type, False)
    with pytest.raises(ValueError, match="Unsupported visible drawable type"):
        geometry.collect(cmds, {}, None, 1000, 250000)


def test_all_drawable_types_are_checked_before_expensive_mesh_records(constraint_scene, monkeypatch):
    cmds, nodes = constraint_scene
    nodes["|rig|constraint"] = ("studioOpaqueUnknown", False)
    processed = []
    monkeypatch.setattr(geometry, "mesh_record", lambda *a: processed.append(a[1]) or {
        "path": a[1], "vertex_count": 8, "appearance": {"pixel_lookup_supported": True}})
    with pytest.raises(ValueError, match="Unsupported visible drawable type"):
        geometry.collect(cmds, {}, None, 1000, 250000)
    assert processed == []
