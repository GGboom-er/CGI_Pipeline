# dccs/ue/worker.py
# ── CGI Pipeline v2.0 — UE Editor Worker（进程管理）──
#
# UE Python 仅限 Editor 模式。
# 通过 UnrealEditor-Cmd.exe -ExecutePythonScript 启动。

import subprocess, json, os, time, uuid, psutil, traceback
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

UE_EDITOR_CMD = os.getenv('UE_EDITOR_CMD',
    r'C:\Program Files\Epic Games\UE_5.5\Engine\Binaries\Win64\UnrealEditor-Cmd.exe')
UE_PROJECT    = os.getenv('UE_PROJECT_PATH', '')
PROJECT_ROOT  = Path(os.getenv('PROJECT_ROOT', '.'))
ADAPTER       = str(PROJECT_ROOT / 'dccs' / 'ue' / 'adapter.py')
IPC_TIMEOUT   = float(os.getenv('IPC_TIMEOUT_SEC', '0') or 0)
MAX_ASSETS    = int(os.getenv('WORKER_MAX_ASSETS', '50'))
MAX_MEM_GB    = float(os.getenv('WORKER_MAX_MEMORY_GB', '4.0'))  # UE 占用更大


def _build_env(worker_id: str) -> dict:
    return {
        'SYSTEMROOT':       'C:\\Windows',
        'SYSTEMDRIVE':      'C:',
        'PATH':             'C:\\Windows\\System32',
        'TEMP':             'C:\\Temp',
        'TMP':              'C:\\Temp',
        'CGI_WORKER_ID':    worker_id,
        'CGI_PROJECT_ROOT': str(PROJECT_ROOT),
        'CGI_DCC_TYPE':     'ue',
        'IPC_POLL_INTERVAL_SEC': '1.0',  # UE 轮询间隔较大
    }


class UEWorker:
    """Unreal Editor 进程管理器"""

    def __init__(self):
        self.worker_id   = str(uuid.uuid4())[:8]
        self.assets_done = 0
        self.process     = None
        self.cmd_file    = PROJECT_ROOT / 'ipc' / 'cmd' / self.worker_id / 'pending.json'
        self.result_dir  = PROJECT_ROOT / 'ipc' / 'result' / self.worker_id
        self.cmd_file.parent.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        """启动 UE Editor Commandlet"""
        if not os.path.exists(UE_EDITOR_CMD):
            raise FileNotFoundError(f'UE Editor 未找到: {UE_EDITOR_CMD}')

        env = _build_env(self.worker_id)
        cmd = [UE_EDITOR_CMD]
        if UE_PROJECT:
            cmd.append(UE_PROJECT)
        cmd.extend(['-ExecutePythonScript', ADAPTER, '-unattended', '-nopause'])

        self.process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        time.sleep(15)  # UE 初始化较慢
        if self.process.poll() is not None:
            stderr = self.process.stderr.read().decode('utf-8', errors='replace')[:500]
            raise RuntimeError(f'UE Editor 启动失败: {stderr}')

    def _needs_recycle(self) -> bool:
        if self.assets_done >= MAX_ASSETS:
            return True
        if self.process and self.process.poll() is None:
            mem = psutil.Process(self.process.pid).memory_info().rss / 1024**3
            return mem > MAX_MEM_GB
        return False

    def recycle(self):
        if self.process and self.process.poll() is None:
            self._guillotine(self.process)
            self.process.wait(timeout=30)  # UE 关闭慢
        self.assets_done = 0
        self.start()

    def run_api(self, payload: dict) -> dict:
        if self._needs_recycle():
            self.recycle()
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
                self.assets_done += 1
                return result
            time.sleep(1.0)  # UE 轮询间隔较大
        if self.process and self.process.poll() is None:
            self._guillotine(self.process)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        self.assets_done = 0
        raise TimeoutError(f'Task {task_id} IPC timeout after {timeout}s')

    def _guillotine(self, proc):
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
