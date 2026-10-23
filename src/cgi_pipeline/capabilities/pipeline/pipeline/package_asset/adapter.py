from cgi_pipeline.capabilities.pipeline.pipeline.package_asset.package_asset import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation


def execute(params, context):
    return execute_operation(_implementation, params, context)
