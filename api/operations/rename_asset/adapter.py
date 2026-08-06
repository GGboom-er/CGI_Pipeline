"""API adapter for the migrated operation implementation."""

from api.operations.rename_asset.rename_asset import execute as _implementation
from api.operations.runtime import execute_legacy

def execute(params, context):
    return execute_legacy(_implementation, params, context)
