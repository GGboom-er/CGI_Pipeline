# api/operations/blender_exec_code/blender_exec_code.py
# 在 Blender Python 环境中执行任意代码（链引擎打开文件等场景使用）

import time
import traceback
import os
import sys

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt
from core.path_guard import is_protected_path


def _make_guarded_builtins():
    """包装 builtins，拦截对受保护路径的写操作"""
    import builtins
    _original_open = builtins.open

    def _guarded_open(file, mode='r', *args, **kwargs):
        if any(c in mode for c in ('w', 'a', 'x', '+')):
            path_str = str(file)
            if is_protected_path(path_str):
                raise PermissionError(
                    f'[path_guard] 禁止写入受保护路径: {path_str}'
                )
        return _original_open(file, mode, *args, **kwargs)

    guarded = dict(vars(builtins))
    guarded['open'] = _guarded_open
    return guarded


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    code = params.get('code', '')
    description = params.get('description', '(未描述)')

    if not code or not code.strip():
        return make_receipt('blender_exec_code', 'ERROR', t0,
                            summary_action=description,
                            error='未提供可执行代码')

    namespace = {'__builtins__': _make_guarded_builtins()}

    try:
        compiled = compile(code, f'<blender_exec_code: {description}>', 'exec')
        exec(compiled, namespace)

        result_data = namespace.get('result', None)
        if result_data is None:
            result_data = {}

        return make_receipt(
            api_id='blender_exec_code',
            status='SUCCESS',
            start_time=t0,
            summary_action=description,
            outputs={'result': result_data},
        )

    except Exception as e:
        return make_receipt(
            api_id='blender_exec_code',
            status='ERROR',
            start_time=t0,
            summary_action=description,
            error=f'{type(e).__name__}: {e}',
            recovery_hint=traceback.format_exc(),
        )
