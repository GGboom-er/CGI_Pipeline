"""API adapter for the migrated operation implementation."""

from api.operations.maya_build_asset_info.maya_build_asset_info import execute as _implementation
from api.operations.runtime import execute_legacy

def execute(params, context):
    return execute_legacy(_implementation, params, context)
