"""Registered control entry for explicitly authorized legacy workspace adoption."""
import time

from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core.workspace import WorkspaceError
from cgi_pipeline.core.workspace_adoption import adopt_workspace


def adopt(params, context):
    started = time.perf_counter()
    try:
        return adopt_workspace(params, context)
    except WorkspaceError as exc:
        return make_api_receipt('pipeline.workspace.adopt', '1.0.0', 'BLOCKED', started,
            input_data={'params': params}, error_code=exc.code, error=str(exc),
            recovery_hint='Preserve the original operation ID; inspect sealed scope and native commit before retrying.')
