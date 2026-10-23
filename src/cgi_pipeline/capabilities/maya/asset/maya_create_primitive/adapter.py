"""API adapter for Maya polygon primitive creation."""

from cgi_pipeline.capabilities.maya.asset.maya_create_primitive.maya_create_primitive import (
    execute as _implementation,
)
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
