from cgi_pipeline.capabilities.runtime import execute_operation


def operation(payload):
    import time
    import maya.cmds as cmds
    from cgi_pipeline.core.receipt import make_receipt
    from .core import apply
    started = time.time()
    try:
        result = apply(cmds, payload.get('parameters') or {})
        return make_receipt('controller_tags', 'SUCCESS', started, output=result)
    except (ValueError, RuntimeError) as exc:
        return make_receipt('controller_tags', 'ERROR', started, error=str(exc))


def execute(params, context):
    return execute_operation(operation, params, context)
