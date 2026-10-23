"""Canonical receipts for synchronous runtime control strategy operations."""
import time

from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core.runtime_control import RuntimeControl
from cgi_pipeline.core.runtime_pool import RuntimePoolError


def _execute(params, context, action):
    started = time.perf_counter()
    api_id = str(context.extras.get('_api_id') or f'pipeline.runtime.{action}')
    version = str(context.extras.get('_api_version') or ('1.0.1' if action == 'release' else '1.0.0'))
    try:
        result = getattr(RuntimeControl(), action)(params)
        result = dict(result)
        status = result.pop('status', 'SUCCESS')
        code, error = result.pop('error_code', ''), result.pop('error', '')
    except RuntimePoolError as exc:
        status, code, error, result = 'BLOCKED', exc.code, str(exc), {'released': False}
    except Exception as exc:
        status, code, error, result = 'ERROR', 'RUNTIME_CONTROL_FAILED', str(exc), {}
    return make_api_receipt(api_id, version, status, started, input_data={'params': dict(params)},
                            output=result, error_code=code, error=error,
                            recovery_hint='Inspect runtime status and exact generation before retrying.' if error else '')


def status(params, context):
    return _execute(params, context, 'status')


def release(params, context):
    return _execute(params, context, 'release')
