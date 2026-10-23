"""Maya API 2.0 command: batch skin writes with genuine native undo/redo.

No scene access occurs until write_weights is called in Maya's main thread.
The tiny in-process exchange transports arrays to the plugin command without
serializing millions of weights or keeping pending data after dispatch.
"""
from __future__ import annotations

import os
import sys
import types
import uuid

COMMAND = "cgiDairWriteWeights1"
EXCHANGE = "_cgi_dair_weight_exchange_v1"


def _exchange():
    if EXCHANGE not in sys.modules:
        shared = types.ModuleType(EXCHANGE)
        shared.pending = {}
        sys.modules[EXCHANGE] = shared
    return sys.modules[EXCHANGE].pending


def write_weights(records):
    """records contain skin UUID, influence UUID order, before and after arrays."""
    import maya.cmds as cmds
    if not hasattr(cmds, COMMAND):
        cmds.loadPlugin(os.path.abspath(__file__), quiet=True)
    token = uuid.uuid4().hex
    _exchange()[token] = records
    try:
        getattr(cmds, COMMAND)(token)
    finally:
        _exchange().pop(token, None)


def maya_useNewAPI():
    pass


def _command_class():
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma

    class WriteWeights(om.MPxCommand):
        def __init__(self):
            super().__init__()
            self.records = []

        def isUndoable(self):
            return True

        @staticmethod
        def creator():
            return WriteWeights()

        def doIt(self, args):
            token = args.asString(0)
            self.records = _exchange().pop(token)
            self.redoIt()

        def _assign(self, record, key):
            selection = om.MSelectionList()
            selection.add(record["name"])
            node = selection.getDependNode(0)
            if om.MFnDependencyNode(node).uuid().asString() != record["uuid"]:
                raise RuntimeError("skin identity changed before weight write")
            skin = oma.MFnSkinCluster(node)
            paths = skin.influenceObjects()
            ids = [om.MFnDependencyNode(path.node()).uuid().asString() for path in paths]
            if set(ids) != set(record["influence_uuids"]):
                raise RuntimeError("influence identities changed before weight write")
            lookup = {value: index for index, value in enumerate(record["influence_uuids"])}
            order = [lookup[value] for value in ids]
            values = record[key]
            width = len(order)
            if order == list(range(width)):
                reordered = values
            else:
                import numpy as np
                reordered = np.asarray(values).reshape((-1, width))[:, order].ravel().tolist()
            outputs = skin.getOutputGeometry()
            if len(outputs) != 1:
                raise RuntimeError("skin output changed before weight write")
            path = om.MDagPath.getAPathTo(outputs[0])
            component_fn = om.MFnSingleIndexedComponent()
            component = component_fn.create(om.MFn.kMeshVertComponent)
            if "vertex_indices" in record:
                component_fn.addElements(record["vertex_indices"])
            else:
                component_fn.setCompleteData(record["count"])
            skin.setWeights(path, component, om.MIntArray(range(width)),
                            om.MDoubleArray(reordered), False)

        def redoIt(self):
            written = []
            try:
                for record in self.records:
                    self._assign(record, "after")
                    written.append(record)
            except Exception:
                # A command whose doIt fails is not placed on Maya's undo queue.
                # Restore even the currently attempted record, which may have
                # been partially changed by a failed host call.
                attempted = self.records[:len(written) + 1]
                for record in reversed(attempted):
                    self._assign(record, "before")
                raise

        def undoIt(self):
            for record in reversed(self.records):
                self._assign(record, "before")

    return WriteWeights


def initializePlugin(plugin):
    import maya.api.OpenMaya as om
    command = _command_class()
    om.MFnPlugin(plugin, "CGI Pipeline", "1.0", "Any").registerCommand(COMMAND, command.creator)


def uninitializePlugin(plugin):
    import maya.api.OpenMaya as om
    om.MFnPlugin(plugin).deregisterCommand(COMMAND)
