"""Canonical CGI adapter; foreground transport remains the existing execute_api owner."""
from cgi_pipeline.capabilities.runtime import execute_operation
from .proxy_rig import execute as implementation


def execute(params, context):
    return execute_operation(implementation, params, context)
