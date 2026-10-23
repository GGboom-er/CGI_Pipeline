"""API adapter for opening a Maya scene through the canonical contract."""

from cgi_pipeline.capabilities.maya.asset.maya_open_scene.maya_open_scene import (
    execute as _implementation,
)
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
