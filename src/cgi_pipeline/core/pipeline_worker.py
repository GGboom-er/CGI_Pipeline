# cgi_pipeline/core/pipeline_worker.py
# 轻量级 Pipeline Worker — 不启动 DCC 进程，直接在 Celery 进程内执行API

import json
import uuid
import sys

from cgi_pipeline.core.bootstrap import cfg as _cfg

_PROJECT_ROOT = _cfg.PROJECT_ROOT
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cgi_pipeline.execution import execute_api


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
            removed = sorted({'params', 'parameters', 'context'} & set(payload))
            if removed:
                raise ValueError(
                    'removed API transport fields: '
                    f"{', '.join(removed)}; use api_params/api_context"
                )
            params = payload.get('api_params', {})
            context = dict(payload.get('api_context') or {})
            context.setdefault('execution_mode', 'background')
            for key in ('source_path', 'project', 'asset_name', 'run_dir'):
                context.setdefault(key, payload.get(key, ''))
            context.setdefault('task_id', task_id)
            context.setdefault('extra_params', payload.get('extra_params', {}))
            result = execute_api(api_id, params, context)
            return {
                'task_id': task_id,
                'status': result['status'],
                'detail': json.dumps(result, ensure_ascii=False, default=str),
            }
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
