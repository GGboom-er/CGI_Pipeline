"""Host-safe atomic file transport for background Maya and Blender workers."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from cgi_pipeline.paths import REPOSITORY_ROOT, ipc_root


def _segment(value: str, name: str) -> str:
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or any(token in value for token in '/\\:\0')):
        raise ValueError(f'IPC {name} must be a single path segment')
    return value


class FileIpcTransport:
    """Own one worker's files; process lifecycle and business decoding stay outside."""

    def __init__(self, worker_id: str, project_root: str | Path = REPOSITORY_ROOT, *,
                 clean_stale: bool = False):
        self.worker_id = _segment(worker_id, 'worker_id')
        self.root = ipc_root(project_root).resolve()
        self._command_dir = self.root / 'cmd' / self.worker_id
        self._result_dir = self.root / 'result' / self.worker_id
        self.cmd_file = self._command_dir / 'pending.json'
        self.result_dir = self._result_dir
        for directory in self._owned_directories():
            directory.mkdir(parents=True, exist_ok=True)
        if clean_stale:
            self.cmd_file.unlink(missing_ok=True)
            for path in self.result_dir.glob('*.json'):
                path.unlink(missing_ok=True)

    def _owned_directories(self) -> tuple[Path, Path]:
        for directory in (self._command_dir, self._result_dir):
            resolved = directory.resolve()
            if resolved != directory or not resolved.is_relative_to(directory.parent):
                raise ValueError(f'IPC directory escapes worker ownership: {directory}')
        return self._command_dir, self._result_dir

    @staticmethod
    def _publish(path: Path, payload: dict) -> None:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload))
        os.replace(temporary, path)

    def send(self, payload: dict) -> None:
        if 'task_id' in payload:
            _segment(payload['task_id'], 'task_id')
        self._publish(self.cmd_file, payload)

    def claim(self) -> dict | None:
        if not self.cmd_file.exists():
            return None
        processing = self.cmd_file.with_name('processing.json')
        try:
            os.replace(self.cmd_file, processing)
        except OSError:
            time.sleep(0.01)
            return None
        raw = processing.read_text()
        processing.unlink()
        return json.loads(raw)

    def publish_result(self, task_id: str, status: str, detail='') -> None:
        task_id = _segment(task_id, 'task_id')
        self._publish(self.result_dir / f'{task_id}.json',
                      {'task_id': task_id, 'status': status, 'detail': detail})

    def exchange(self, payload: dict, *, timeout: float | None = 0,
                 check_process: Callable[[], dict | None] | None = None) -> dict:
        task_id = _segment(payload['task_id'], 'task_id')
        result_file = self.result_dir / f'{task_id}.json'
        self.send(payload)
        deadline = time.time() + timeout if timeout and timeout > 0 else None
        while deadline is None or time.time() < deadline:
            if check_process is not None:
                stopped = check_process()
                if stopped is not None:
                    return stopped
            if result_file.exists():
                result = json.loads(result_file.read_text())
                result_file.unlink()
                if (not isinstance(result, dict) or result.get('task_id') != task_id
                        or not isinstance(result.get('status'), str) or not result['status'].strip()
                        or 'detail' not in result):
                    raise ValueError(f'IPC result must contain matching task_id={task_id}, status and detail')
                return result
            time.sleep(0.5)
        raise TimeoutError(f'Task {task_id} IPC timeout after {timeout}s')

    def close(self) -> None:
        for directory in self._owned_directories():
            if directory.exists():
                shutil.rmtree(directory)
