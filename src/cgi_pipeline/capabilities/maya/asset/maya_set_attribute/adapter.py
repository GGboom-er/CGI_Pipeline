"""API adapter for Maya plug assignment."""

from cgi_pipeline.capabilities.maya.asset.maya_set_attribute.maya_set_attribute import (
    execute as _implementation,
)
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
