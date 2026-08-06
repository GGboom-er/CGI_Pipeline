# api/operations/copy_files/copy_files.py
# 通用文件拷贝：source → destination，文件或目录均可

import time
import shutil
import os
import sys

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item
from core.path_guard import is_protected_path


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    source = params.get('source', '')
    destination = params.get('destination', '')
    overwrite = params.get('overwrite', False)

    if not source:
        return make_receipt('copy_files', 'ERROR', t0, error='缺少 source 参数')
    if not destination:
        return make_receipt('copy_files', 'ERROR', t0, error='缺少 destination 参数')

    source = os.path.normpath(source)
    destination = os.path.normpath(destination)

    if is_protected_path(destination):
        return make_receipt('copy_files', 'BLOCKED', t0,
                            summary_action=f'→ {destination}',
                            error=f'目标路径 "{destination}" 位于只读/受保护区域，禁止写入。')

    if not os.path.exists(source):
        return make_receipt('copy_files', 'ERROR', t0,
                            error=f'源路径不存在: {source}')

    copied = []
    skipped = []
    total_bytes = 0

    if os.path.isfile(source):
        dst_path = destination
        if os.path.isdir(destination):
            dst_path = os.path.join(destination, os.path.basename(source))
        if os.path.exists(dst_path) and not overwrite:
            skipped.append(make_item(os.path.basename(source), '已存在，跳过'))
        else:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(source, dst_path)
            fsize = os.path.getsize(dst_path)
            total_bytes += fsize
            copied.append(make_item(os.path.basename(source), _format_size(fsize)))
    else:
        for root, dirs, files in os.walk(source):
            rel = os.path.relpath(root, source)
            dst_dir = os.path.join(destination, rel) if rel != '.' else destination
            os.makedirs(dst_dir, exist_ok=True)
            for fname in files:
                src_file = os.path.join(root, fname)
                dst_file = os.path.join(dst_dir, fname)
                if os.path.exists(dst_file) and not overwrite:
                    skipped.append(make_item(fname, '已存在，跳过'))
                    continue
                shutil.copy2(src_file, dst_file)
                fsize = os.path.getsize(dst_file)
                total_bytes += fsize
                copied.append(make_item(fname, _format_size(fsize)))

    if not copied and not skipped:
        return make_receipt('copy_files', 'ERROR', t0,
                            summary_action=f'{source} → {destination}',
                            error='源路径下没有任何文件')

    return make_receipt(
        api_id='copy_files',
        status='SUCCESS',
        start_time=t0,
        summary_input=source,
        summary_action=f'→ {destination}',
        summary_count=len(copied),
        summary_label='文件',
        items=copied + skipped,
        outputs={
            'output_path': destination,
            'result': {
                'total_bytes': total_bytes,
                'copied_count': len(copied),
                'skipped_count': len(skipped),
            },
        },
    )


def _format_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f'{nbytes} B'
    elif nbytes < 1024 * 1024:
        return f'{nbytes / 1024:.1f} KB'
    elif nbytes < 1024 * 1024 * 1024:
        return f'{nbytes / (1024 * 1024):.1f} MB'
    return f'{nbytes / (1024 * 1024 * 1024):.2f} GB'
