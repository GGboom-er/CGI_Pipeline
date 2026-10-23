"""Canonical API adapter; this file operation does not start a DCC."""

from cgi_pipeline.capabilities.pipeline.pipeline.package_textures.package_textures import execute as _implementation
from cgi_pipeline.capabilities.runtime import execute_operation


def execute(params, context):
    return execute_operation(_implementation, params, context)
