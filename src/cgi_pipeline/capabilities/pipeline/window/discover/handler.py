"""Bounded process-filtered top-level window discovery."""
import time

from cgi_pipeline.contracts import make_api_receipt
from ..native import DEFAULT_PROCESSES, NativeWindows, positive_int, observe_session
from .. import displays

VERSION = '1.1.0'


def execute(params, context):
    started = time.perf_counter()
    try:
        limit = positive_int(params.get('max_windows', 50), 'max_windows', 200)
        include_monitors = params.get('include_monitors', False)
        if type(include_monitors) is not bool:
            raise ValueError('include_monitors must be a boolean')
        names = params.get('process_names', DEFAULT_PROCESSES)
        if not isinstance(names, list) or not 0 < len(names) <= 100 or any(
            not isinstance(name, str) or not name.strip() or '/' in name or '\\' in name for name in names
        ):
            raise ValueError('process_names must contain 1 to 100 executable basenames')
        names = {name.casefold() for name in names}
        native, rows, skipped = NativeWindows(), [], 0
        truncated = False

        def visit(hwnd):
            nonlocal skipped, truncated
            try:
                row = native.describe(hwnd)
            except (OSError, RuntimeError):
                skipped += 1
                return True
            if row['process_name'].casefold() in names:
                if len(rows) == limit:
                    truncated = True
                    return False
                rows.append(row)
            return True

        native.enumerate(visit)
        return make_api_receipt('pipeline.window.discover', VERSION, 'SUCCESS', started,
                                input_data=params, output={'windows': rows, 'window_count': len(rows),
                                'truncated': truncated, 'unavailable_window_count': skipped,
                                'session': observe_session(native),
                                **({'monitors': displays.enumerate_monitors(native)} if include_monitors else {})})
    except Exception as exc:
        return make_api_receipt('pipeline.window.discover', VERSION, 'ERROR', started,
                                input_data=params, error_code='WINDOW_DISCOVERY_FAILED', error=str(exc))
