"""API adapter for the migrated operation implementation."""

from cgi_pipeline.capabilities.pipeline.pipeline.resolve_asset_files.resolve_asset_files import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation

def execute(params, context):
    return execute_operation(_implementation, params, context)
