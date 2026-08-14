"""API adapter for the migrated operation implementation."""

from cgi_pipeline.capabilities.maya.rig.maya_fix_asset_hierarchy.maya_fix_asset_hierarchy import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
