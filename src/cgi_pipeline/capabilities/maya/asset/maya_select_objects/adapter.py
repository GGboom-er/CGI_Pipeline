"""API adapter for bounded Maya selection."""

from cgi_pipeline.capabilities.maya.asset.maya_select_objects.maya_select_objects import (
    execute as _implementation,
)
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
