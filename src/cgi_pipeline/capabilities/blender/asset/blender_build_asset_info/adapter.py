"""API adapter for the migrated operation implementation."""

from cgi_pipeline.capabilities.blender.asset.blender_build_asset_info.blender_build_asset_info import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
