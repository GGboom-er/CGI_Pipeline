import json
import time
from pathlib import Path
from datetime import datetime
from dccs.maya.worker import MayaCommandPortWorker

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / 'audit'

def _write_audit(task_id: str, status: str, skill_id: str, detail: str = ''):
    """同步写入审计日志，模拟 Celery 任务的状态流转"""
    entry = {
        'task_id': task_id,
        'skill_id': skill_id,
        'status': status,
        'ts': time.time(),
        'detail': detail,
        'execution_mode': 'foreground'
    }
    date_str = datetime.now().strftime('%Y-%m-%d')
    date_dir = AUDIT_DIR / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    audit_path = date_dir / f'{task_id}.json'

    with open(audit_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

import socket

def _auto_discover_maya_port() -> int:
    """自动扫描 7001-7010 寻找活跃的 Maya commandPort (无感连接核心)"""
    for p in range(7001, 7011):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(('127.0.0.1', p)) == 0:
                return p
    return None

def submit_foreground_task(payload: dict, sync: bool = False) -> dict:
    """通过 TCP Socket 将任务直接发送给 Maya commandPort。

    sync=False（默认）：fire-and-forget，调用方用 query_task 轮询拿结果（保持原有异步行为）。
    sync=True：端到端同步等 RPyC 返回，并把 receipt 直接塞进响应的 'detail' 字段，
               免去额外一次 query_task 往返。专用于 exec_code 交互式提速，亚秒级响应。
    """
    task_id = payload.get('task_id')
    skill_id = payload.get('skill_id', 'unknown')
    parameters = payload.get('parameters', {})

    port = parameters.get('foreground_port')
    if not port:
        port = _auto_discover_maya_port()
        if not port:
            err_msg = "无感连接失败：未能在 7001-7010 范围内发现活跃的 Maya 实例，请确保 Maya 已开启并执行了 userSetup.py"
            _write_audit(task_id, 'ERROR', skill_id, err_msg)
            return {
                'task_id': task_id,
                'status': 'SUBMIT_FAILED',
                'error': err_msg
            }
    port = int(port)

    # 立即写入 STARTED，让查询接口有数据
    _write_audit(task_id, 'STARTED', skill_id, 'Foreground execution initiated via commandPort')

    worker = MayaCommandPortWorker(host='127.0.0.1', port=port)

    try:
        worker.start()  # 检查端口存活 + bootstrap RPyC
    except Exception as e:
        err_msg = str(e)
        _write_audit(task_id, 'ERROR', skill_id, err_msg)
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': err_msg,
            'recovery_hint': f'请确保 Maya 中已执行 cmds.commandPort(name=":{port}", sourceType="python")'
        }

    # ── 执行并等待结果 ──
    receipt = None
    exec_error = None
    try:
        payload_copy = dict(payload)
        if 'parameters' in payload_copy:
            p = dict(payload_copy['parameters'])
            p.pop('execution_mode', None)
            p.pop('foreground_port', None)
            p.pop('sync', None)
            payload_copy['parameters'] = p

        result = worker.run_skill(payload_copy)

        # worker.run_skill 返回 skill receipt 本身；对齐 celery PipelineWorker 的
        # {status, detail=json.dumps(receipt)} 外壳写入 audit，query_task 才能读到 outputs/items。
        if isinstance(result, dict) and 'skill_id' in result and 'status' in result:
            status = result.get('status', 'ERROR')
            detail = json.dumps(result, ensure_ascii=False, default=str)
            receipt = result
        else:
            status = result.get('status', 'ERROR') if isinstance(result, dict) else 'ERROR'
            detail = result.get('detail', '') if isinstance(result, dict) else str(result)
            receipt = result if isinstance(result, dict) else None

        _write_audit(task_id, status, skill_id, detail)

    except Exception as e:
        exec_error = str(e)
        _write_audit(task_id, 'ERROR', skill_id, exec_error)

    # ── 同步模式：直接返回 receipt，免 query_task ──
    if sync:
        if exec_error is not None:
            return {
                'task_id': task_id,
                'status': 'ERROR',
                'skill_id': skill_id,
                'detail': exec_error,
                'execution_mode': 'foreground',
                'sync': True,
            }
        return {
            'task_id': task_id,
            'status': receipt.get('status', 'ERROR') if isinstance(receipt, dict) else 'ERROR',
            'skill_id': skill_id,
            'detail': receipt,
            'execution_mode': 'foreground',
            'sync': True,
        }

    # ── 异步模式：保持原语义返回 SUBMITTED ──
    return {
        'task_id': task_id,
        'status': 'SUBMITTED',
        'skill_id': skill_id,
        'message': f'任务已通过前台模式派发到 Maya 端口 {port} 并执行完毕',
        'execution_mode': 'foreground'
    }
