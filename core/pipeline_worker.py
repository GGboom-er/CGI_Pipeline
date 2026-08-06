# core/pipeline_worker.py
# 轻量级 Pipeline Worker — 不启动 DCC 进程，直接在 Celery 进程内执行API

import importlib
import json
import uuid
import os
import sys

from core.bootstrap import cfg as _cfg

_PROJECT_ROOT = _cfg.PROJECT_ROOT
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class PipelineWorker:

    def __init__(self):
        self.worker_id = str(uuid.uuid4())[:8]
        self.process = None

    def start(self):
        pass

    def run_api(self, payload: dict) -> dict:
        api_id = payload.get('api_id', '')
        task_id = payload.get('task_id', 'unknown')
        try:
            mod = importlib.import_module(f'api.operations.{api_id}')
            importlib.reload(mod)
            result = mod.execute(payload)
            if isinstance(result, dict):
                status = result.get('status', 'SUCCESS')
                return {
                    'task_id': task_id,
                    'status': status,
                    'detail': json.dumps(result, ensure_ascii=False, default=str),
                }
            return {'task_id': task_id, 'status': 'SUCCESS', 'detail': str(result)}
        except Exception as e:
            import traceback
            return {
                'task_id': task_id,
                'status': 'ERROR',
                'detail': json.dumps({
                    'status': 'ERROR',
                    'error': f'{type(e).__name__}: {e}',
                    'traceback': traceback.format_exc(),
                }, ensure_ascii=False),
            }

    def shutdown(self):
        pass

    def get_memory_gb(self):
        return 0.0
