"""API adapter for the migrated operation implementation."""

from api.operations.maya_build_mesh_from_abc.maya_build_mesh_from_abc import execute as _implementation
from api.operations.runtime import execute_legacy

def execute(params, context):
    return execute_legacy(_implementation, params, context)
