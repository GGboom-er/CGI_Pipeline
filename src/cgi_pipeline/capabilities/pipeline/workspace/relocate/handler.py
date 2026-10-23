"""One registered control capability owns preview and relocation."""
import time
from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core.workspace import WorkspaceError
from cgi_pipeline.core.workspace_relocation import relocate_workspace, API_ID, VERSION


def relocate(params, context):
    started = time.perf_counter()
    try:
        return relocate_workspace(params, context)
    except WorkspaceError as exc:
        return make_api_receipt(API_ID, VERSION, 'BLOCKED', started, input_data={'params': params},
            error_code=exc.code, error=str(exc),
            recovery_hint='Recover the original operation ID before any retry; preserve both roots and commit witnesses.')
