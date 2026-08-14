# cgi_pipeline/hosts/blender/worker.py
# ── Blender 每链一进程生命周期 ──
import subprocess, json, os, time, uuid, psutil, shutil
from pathlib import Path
from dotenv import load_dotenv
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT

load_dotenv()

BLENDER_PATH = os.getenv('BLENDER_PATH', r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe')
PROJECT_ROOT = REPOSITORY_ROOT
ADAPTER      = str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'blender' / 'adapter.py')
IPC_TIMEOUT  = float(os.getenv('IPC_TIMEOUT_SEC', '0') or 0)


def _build_env(worker_id: str) -> dict:
    env = os.environ.copy()
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
        self.cmd_file   = PROJECT_ROOT / 'ipc' / 'cmd' / self.worker_id / 'pending.json'
        self.result_dir = PROJECT_ROOT / 'ipc' / 'result' / self.worker_id
        self.cmd_file.parent.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self._clean_stale_ipc()

    def _clean_stale_ipc(self):
        if self.cmd_file.exists():
            self.cmd_file.unlink(missing_ok=True)
        for f in self.result_dir.glob('*.json'):
            f.unlink(missing_ok=True)

    def start(self):
        if not os.path.exists(BLENDER_PATH):
            raise FileNotFoundError(f'Blender 未找到: {BLENDER_PATH}')
        env = _build_env(self.worker_id)
        log_dir = PROJECT_ROOT / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_log = open(log_dir / f'blender_{self.worker_id}.log', 'w')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        
        cmd = [BLENDER_PATH, '--background']
        if self.source_path and os.path.isfile(self.source_path):
            cmd.append(self.source_path)
        cmd.extend(['--python', ADAPTER])
        
        self.process = subprocess.Popen(
            cmd,
            env=env, stdout=subprocess.DEVNULL, stderr=self._stderr_log,
            creationflags=flags,
        )
        time.sleep(3)
        if self.process.poll() is not None:
            self._stderr_log.close()
            err = (log_dir / f'blender_{self.worker_id}.log').read_text()[:500]
            raise RuntimeError(f'Blender 启动失败: {err}')

    def run_api(self, payload: dict) -> dict:
        task_id = payload['task_id']
        result_file = self.result_dir / f'{task_id}.json'
        tmp = self.cmd_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.cmd_file)
        timeout = payload.get('timeout', IPC_TIMEOUT)
        deadline = time.time() + timeout if timeout and timeout > 0 else None
        while deadline is None or time.time() < deadline:
            if result_file.exists():
                result = json.loads(result_file.read_text())
                result_file.unlink()
                return result
            time.sleep(0.5)
        self._kill_process_tree()
        raise TimeoutError(f'Task {task_id} IPC timeout after {timeout}s')

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
                die_payload = json.dumps({'api_id': '__DIE__'})
                tmp = self.cmd_file.with_suffix('.tmp')
                tmp.write_text(die_payload)
                os.replace(tmp, self.cmd_file)
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._kill_process_tree()
            except Exception:
                self._kill_process_tree()
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
        try:
            if self.cmd_file.parent.exists():
                shutil.rmtree(self.cmd_file.parent, ignore_errors=True)
            if self.result_dir.exists():
                shutil.rmtree(self.result_dir, ignore_errors=True)
        except Exception:
            pass
