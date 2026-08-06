"""API adapter for the migrated operation implementation."""

from api.operations.blender_extract_materials.blender_extract_materials import execute as _implementation
from api.operations.runtime import execute_legacy

def execute(params, context):
    return execute_legacy(_implementation, params, context)
