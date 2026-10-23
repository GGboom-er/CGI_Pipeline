"""API adapter for Maya object inspection."""

from cgi_pipeline.capabilities.maya.asset.maya_get_object_info.maya_get_object_info import (
    execute as _implementation,
)
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
