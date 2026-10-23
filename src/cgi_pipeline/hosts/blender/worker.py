# cgi_pipeline/hosts/blender/worker.py
# ── Blender 每链一进程生命周期 ──
import subprocess, json, os, time, uuid, psutil
from pathlib import Path
from dotenv import load_dotenv
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT
from cgi_pipeline.contracts import validate_batch_result
from cgi_pipeline.hosts.file_ipc import FileIpcTransport
from cgi_pipeline.hosts.runtime import build_host_env, host_runtime, record_host_process, record_host_launch

load_dotenv()

BLENDER_PATH = os.getenv('BLENDER_PATH', r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe')
PROJECT_ROOT = REPOSITORY_ROOT
ADAPTER      = str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'blender' / 'adapter.py')
IPC_TIMEOUT  = float(os.getenv('IPC_TIMEOUT_SEC', '0') or 0)


def _build_env(worker_id: str, environ=None) -> dict:
    env = build_host_env('blender', PROJECT_ROOT, environ)
    env.update({
        'CGI_WORKER_ID':        worker_id,
        'CGI_PROJECT_ROOT':     str(PROJECT_ROOT),
        'CGI_DCC_TYPE':         'blender',
        'IPC_POLL_INTERVAL_SEC': '0.2',
    })
    existing_pythonpath = [item for item in env.get('PYTHONPATH', '').split(os.pathsep) if item]
    env['PYTHONPATH'] = os.pathsep.join([str(SOURCE_ROOT), *existing_pythonpath])
    return env


class BlenderWorker:
    """可复用 Blender 进程：start → run_api × N → shutdown。"""

    def __init__(self, source_path: str = None):
        self.worker_id  = str(uuid.uuid4())[:8]
        self.source_path = source_path
        self.process    = None
        self._environment = os.environ.copy()
        self.runtime = host_runtime(PROJECT_ROOT, self._environment)
        self.ipc = FileIpcTransport(self.worker_id, self.runtime.root, clean_stale=True)
        self.cmd_file = self.ipc.cmd_file
        self.result_dir = self.ipc.result_dir

    def start(self):
        if not os.path.exists(BLENDER_PATH):
            raise FileNotFoundError(f'Blender 未找到: {BLENDER_PATH}')
        env = _build_env(self.worker_id, self._environment)
        log_dir = self.runtime.root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_log = open(log_dir / f'blender_{self.worker_id}.log', 'w')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        
        cmd = [BLENDER_PATH, '--background']
        if self.source_path and os.path.isfile(self.source_path):
            cmd.append(self.source_path)
        if self.runtime.threads is not None:
            cmd.extend(['--threads', str(self.runtime.threads)])
        cmd.extend(['--python', ADAPTER])
        
        record_host_launch(self.runtime, 'blender', self.worker_id)
        self.process = subprocess.Popen(
            cmd,
            env=env, stdout=subprocess.DEVNULL, stderr=self._stderr_log,
            creationflags=flags,
        )
        try:
            record_host_process(self.runtime, 'blender', self.worker_id, self.process)
        except Exception:
            self._kill_process_tree()
            self._stderr_log.close()
            raise
        time.sleep(3)
        if self.process.poll() is not None:
            self._stderr_log.close()
            err = (log_dir / f'blender_{self.worker_id}.log').read_text()[:500]
            raise RuntimeError(f'Blender 启动失败: {err}')

    def run_api(self, payload: dict) -> dict:
        task_id = payload['task_id']
        timeout = payload.get('timeout', IPC_TIMEOUT)
        try:
            return self.ipc.exchange(payload, timeout=timeout,
                                     check_process=lambda: self._check_process(task_id))
        except TimeoutError:
            self._kill_process_tree()
            raise TimeoutError(f'Task {task_id} IPC timeout after {timeout}s') from None

    def _check_process(self, task_id: str, *, batch: bool = False) -> dict | None:
        if self.process and self.process.poll() is not None:
            message = f'Blender 进程已退出 (exit code: {self.process.returncode})'
            if batch:
                raise RuntimeError(message)
            return {'task_id': task_id, 'status': 'ERROR',
                    'detail': f'{message}，任务 {task_id} 中断'}
        return None

    def run_batch(self, payloads: list[dict], atomic: bool = True) -> dict:
        """Execute a literal API batch in one Blender IPC round-trip."""
        if not payloads:
            return {'status': 'ERROR', 'error_code': 'BATCH_EMPTY',
                    'error': 'batch 至少需要一个 API 调用', 'results': [], 'completed': 0, 'requested': 0,
                    'atomic': bool(atomic), 'rolled_back': False}
        task_id = payloads[0].get('task_id', f'batch-{uuid.uuid4().hex[:8]}')
        command = {'api_id': '__BATCH__', 'task_id': task_id,
                   'batch': payloads, 'atomic': bool(atomic)}
        timeout = payloads[0].get('timeout', IPC_TIMEOUT)
        try:
            result = self.ipc.exchange(command, timeout=timeout,
                                       check_process=lambda: self._check_process(task_id, batch=True))
        except TimeoutError:
            self._kill_process_tree()
            raise TimeoutError(f'Batch task {task_id} IPC timeout after {timeout}s') from None
        detail = result['detail']
        detail = json.loads(detail) if isinstance(detail, str) else detail
        return validate_batch_result(detail, len(payloads), atomic)

    def get_memory_gb(self) -> float:
        try:
            if self.process and self.process.poll() is None:
                return psutil.Process(self.process.pid).memory_info().rss / 1024**3
        except psutil.NoSuchProcess:
            pass
        return 0.0

    def shutdown(self):
        """优雅退出：发送 __DIE__ → 等待退出 → 清理 IPC"""
        if self.process and self.process.poll() is None:
            try:
                self.ipc.send({'api_id': '__DIE__'})
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._kill_process_tree()
            except Exception:
                self._kill_process_tree()
        if hasattr(self, '_stderr_log') and self._stderr_log:
            self._stderr_log.close()
        self._cleanup_ipc_dirs()

    def _kill_process_tree(self):
        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass

    def _cleanup_ipc_dirs(self):
        self.ipc.close()
