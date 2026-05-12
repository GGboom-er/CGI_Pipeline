import time
import traceback
import os, sys

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt
from core.path_guard import is_protected_path


def _make_guarded_open(original_open):
    """包装 open()，拦截对受保护路径的写操作"""
    def guarded_open(file, mode='r', *args, **kwargs):
        if any(m in str(mode) for m in ('w', 'a', 'x', '+')):
            path_str = str(file)
            if is_protected_path(path_str):
                raise PermissionError(
                    f'路径保护拦截: "{path_str}" 位于只读/受保护区域，禁止写入。'
                )
        return original_open(file, mode, *args, **kwargs)
    return guarded_open


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    code = params.get('code', '')
    description = params.get('description', '(未描述)')

    if not code or not code.strip():
        return make_receipt('exec_code', 'ERROR', t0,
                            summary_action=description,
                            error='未提供可执行代码')

    import builtins
    namespace = {
        '__builtins__': builtins,
        'open': _make_guarded_open(builtins.open),
    }

    try:
        compiled = compile(code, f'<exec_code: {description}>', 'exec')
        exec(compiled, namespace)

        result_data = namespace.get('result', None)
        if result_data is None:
            result_data = {}

        # 从 result_data 中提取结构化信息作为 items
        items = []
        report_content = ''
        if isinstance(result_data, dict):
            for k, v in result_data.items():
                if k == 'status':
                    continue  # 跳过 status 字段
                if isinstance(v, list):
                    items.append({'name': str(k), 'detail': f'{len(v)} 项'})
                elif isinstance(v, dict):
                    items.append({'name': str(k), 'detail': f'{len(v)} 个字段'})
                elif isinstance(v, (int, float)):
                    items.append({'name': str(k), 'detail': str(v)})
                elif isinstance(v, str) and len(v) < 200:
                    items.append({'name': str(k), 'detail': v})
                elif isinstance(v, str):
                    # 长文本作为嵌入式报告内容
                    report_content = v

        receipt = make_receipt(
            skill_id='exec_code',
            status='SUCCESS',
            start_time=t0,
            summary_action=description,
            summary_input=os.path.basename(payload.get('source_path', '')),
            outputs={'result': result_data},
            items=items if items else None,
        )
        if report_content:
            receipt['report_content'] = report_content
        return receipt

    except Exception as e:
        return make_receipt(
            skill_id='exec_code',
            status='ERROR',
            start_time=t0,
            summary_action=description,
            error=f'{type(e).__name__}: {e}',
            recovery_hint=traceback.format_exc(),
        )
