"""API adapter for the migrated operation implementation."""

from cgi_pipeline.capabilities.maya.asset.maya_freeze_transforms.maya_freeze_transforms import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
