"""API adapter for the migrated operation implementation."""

from cgi_pipeline.capabilities.blender.asset.blender_exec_code.blender_exec_code import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
