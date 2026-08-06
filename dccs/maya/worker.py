# dccs/maya/worker.py
# ── CGI Pipeline v2.0 — 每链一进程生命周期 ──
import subprocess, json, os, time, uuid, psutil, shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MAYAPY       = os.getenv('MAYAPY_PATH')
MAYA_BIN_DIR = os.getenv('MAYA_BIN_DIR', r'C:\Program Files\Autodesk\Maya2025\bin')
PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', '.'))
ADAPTER      = str(PROJECT_ROOT / 'dccs' / 'maya' / 'adapter.py')
IPC_TIMEOUT  = float(os.getenv('IPC_TIMEOUT_SEC', '0') or 0)
RPYC_SYNC_TIMEOUT = float(os.getenv('RPYC_SYNC_TIMEOUT_SEC', '3600') or 3600)


def _build_env(worker_id: str) -> dict:
    env = os.environ.copy()
    env.update({
        'PATH':                f'{MAYA_BIN_DIR};{env.get("PATH", "")}',
        'MAYA_NO_HOME':        '1',
        'MAYA_DISABLE_CIP':    '1',
        'MAYA_SKIP_UNSUPPORTED_PLUGINS_DIALOG': '1',
        'MAYA_NO_WARNING_FOR_MISSING_DEFAULT_RENDERER': '1',
        'MAYA_DISABLE_ADP':    '1',
        'MAYA_OPENCL_IGNORE_DRIVER_VERSION': '1',
        'PYMEL_SKIP_MEL_INIT': '1',
        'CGI_WORKER_ID':       worker_id,
        'CGI_PROJECT_ROOT':    str(PROJECT_ROOT),
        'IPC_POLL_INTERVAL_SEC': '0.2',
    })
    return env


class MayaWorker:
    """一次性 Maya 进程：start → run_api × N → shutdown"""

    def __init__(self):
        self.worker_id  = str(uuid.uuid4())[:8]
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
        env = _build_env(self.worker_id)
        log_dir = PROJECT_ROOT / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_log = open(log_dir / f'mayapy_{self.worker_id}.log', 'w')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen(
            [MAYAPY, ADAPTER],
            env=env, stdout=subprocess.DEVNULL, stderr=self._stderr_log,
            creationflags=flags
        )
        time.sleep(20)
        if self.process.poll() is not None:
            self._stderr_log.close()
            err = (log_dir / f'mayapy_{self.worker_id}.log').read_text()[:500]
            raise RuntimeError(f'Maya failed to start: {err}')

    def run_api(self, payload: dict) -> dict:
        task_id = payload['task_id']
        result_file = self.result_dir / f'{task_id}.json'
        tmp = self.cmd_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.cmd_file)
        timeout = payload.get('timeout', IPC_TIMEOUT)
        deadline = time.time() + timeout if timeout and timeout > 0 else None
        while deadline is None or time.time() < deadline:
            if self.process and self.process.poll() is not None:
                return {
                    'status': 'ERROR',
                    'detail': f'Maya 进程已退出 (exit code: {self.process.returncode})，任务 {task_id} 中断'
                }
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
        try:
            if self.cmd_file.parent.exists():
                shutil.rmtree(self.cmd_file.parent, ignore_errors=True)
            if self.result_dir.exists():
                shutil.rmtree(self.result_dir, ignore_errors=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# RPyC 全透明代理模式 — 由 CommandPort 引导
# ══════════════════════════════════════════════════════════════
import socket
import rpyc

MAYA_CMD_PORT = int(os.getenv('MAYA_CMD_PORT', '7001'))
MAYA_CMD_HOST = os.getenv('MAYA_CMD_HOST', '127.0.0.1')

def is_maya_commandport_available(host=MAYA_CMD_HOST, port=MAYA_CMD_PORT) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, TimeoutError, OSError, AttributeError):
        pass
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False

def is_rpyc_available(host, port) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


class MayaCommandPortWorker:
    """通过 RPyC 直连已打开的 Maya 实例执行API（由 commandPort 负责自动引导）。
    
    架构说明：
    使用持久化连接池，在整个 Worker 生命周期内复用同一个 RPyC 连接。
    消除了每次 run_api() 都 connect/close 导致的 WinError 10054 (EOFError)。
    只在 shutdown() 时才优雅断开连接。
    """

    def __init__(self, host=MAYA_CMD_HOST, port=MAYA_CMD_PORT):
        self.host = host
        self.port = port
        self.rpyc_port = port + 10000  # 动态端口映射规则：7029 -> 17029
        self.worker_id = f'rpyc-{self.rpyc_port}'
        self._consecutive_failures = 0
        self._max_failures = 3
        self._conn = None  # 持久化连接

    def _send_bootstrap(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((self.host, self.port))

        # 读取 RPyC 服务器脚本，并把 MCP server 进程的 site-packages 路径
        # 注入到脚本顶部的占位常量 _CGI_INJECT_SITE_PACKAGES 里。
        # 这样 Maya 会沿仓库 conda env 找到 rpyc，不需要往 mayapy pip install。
        server_script = (PROJECT_ROOT / 'dccs' / 'maya' / 'rpyc_server.py').read_text(encoding='utf-8')

        import site as _site
        inject_paths = list(dict.fromkeys(  # 去重，保留顺序
            [p for p in _site.getsitepackages() if p]
            + [_site.getusersitepackages()]
        ))
        inject_literal = '[' + ', '.join(repr(p) for p in inject_paths) + ']'
        server_script = server_script.replace(
            '_CGI_INJECT_SITE_PACKAGES = []',
            f'_CGI_INJECT_SITE_PACKAGES = {inject_literal}',
            1,
        )
        server_script += f"\n_start_rpyc_server(port={self.rpyc_port})\n"

        import base64
        b64_script = base64.b64encode(server_script.encode('utf-8')).decode('ascii')

        # 终极一阳指：单行注入拉起服务
        payload = f"exec(__import__('base64').b64decode('{b64_script}').decode('utf-8'), globals())\n"
        try:
            s.sendall(payload.encode('utf-8'))
        finally:
            s.close()

    def _ensure_connection(self):
        """获取或创建持久化 RPyC 连接。连接断开时自动重建。"""
        if self._conn is not None:
            try:
                self._conn.ping()  # 心跳探活
                return self._conn
            except Exception:
                # 连接已死，清理后重建
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        
        self._conn = rpyc.classic.connect(self.host, self.rpyc_port)
        self._conn._config['sync_request_timeout'] = RPYC_SYNC_TIMEOUT
        return self._conn

    def start(self):
        if is_rpyc_available(self.host, self.rpyc_port):
            self._consecutive_failures = 0
            return
            
        if not is_maya_commandport_available(self.host, self.port):
            raise RuntimeError(
                f'Maya commandPort 不可用 ({self.host}:{self.port})。'
                f'请在 Maya 中执行: cmds.commandPort(name=":{self.port}", sourceType="python")'
            )
        
        # 引导启动 RPyC Server
        self._send_bootstrap()

        # 轮询等待 RPyC 服务器上线 (最多等 5 秒)
        for _ in range(10):
            time.sleep(0.5)
            if is_rpyc_available(self.host, self.rpyc_port):
                self._consecutive_failures = 0
                return

        # Bootstrap 失败。此时 Maya 已收到注入脚本但 rpyc 没起——
        # 99% 是 conda env 里没 rpyc（被人手动删了 / environment.yml 没同步）。
        # 不再尝试往 mayapy 装：那是不可移植的蠢招，且写 Program Files 要管理员权限。
        import site as _site
        hint_paths = [p for p in _site.getsitepackages() if p] + [_site.getusersitepackages()]
        raise RuntimeError(
            f'RPyC Server bootstrap failed on port {self.rpyc_port}。\n'
            f'Maya commandPort 活着，但 RPyC 服务在 5 秒内没起来。\n'
            f'最常见原因：当前 MCP server 进程的 Python 环境里没装 rpyc。\n'
            f'检查过的 site-packages：\n  - ' + '\n  - '.join(hint_paths) + '\n'
            f'修复方法：在仓库根目录跑 `bin\\setup.bat`，或手动 `pip install rpyc==6.0.2` 进 Notes 受管 prefix（Tools/_managed/conda_envs/cgi_pipeline）。\n'
            f'注意：rpyc 必须装进 MCP server 运行的那个 Python 环境，不是 mayapy。'
        )

    def get_memory_gb(self) -> float:
        return -1.0

    def shutdown(self):
        """优雅关闭持久化连接"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def run_api(self, payload: dict) -> dict:
        task_id = payload['task_id']
        api_id = payload.get('api_id', '')
        if not api_id:
            return {
                'status': 'ERROR',
                'detail': '缺少 api_id；Maya Worker 只接受 API 调用。',
            }
        
        try:
            conn = self._ensure_connection()
            
            # 注入一个代理执行函数：为了保证管线数据的安全切断，边界通信依然使用 JSON
            remote_run_code = f"""
def _cgi_run_api_json(payload_json):
    import sys, json
    project_path = r'{str(PROJECT_ROOT)}'
    if project_path not in sys.path:
        sys.path.insert(0, project_path)
    from api.runner import execute_api
    import maya.cmds as cmds
    payload = json.loads(payload_json)
    context = dict(payload.get('api_context') or {{}})
    context.setdefault('execution_mode', 'foreground')
    context.setdefault('source_path', payload.get('source_path', ''))
    context.setdefault('project', payload.get('project', ''))
    context.setdefault('asset_name', payload.get('asset_name', ''))
    context['cmds_module'] = cmds
    res = execute_api(payload['api_id'], payload.get('api_params') or {{}}, context)
    return json.dumps(res, ensure_ascii=False, default=str)
"""
            conn.execute(remote_run_code)
            remote_func = conn.namespace['_cgi_run_api_json']
            
            # 在 Maya 主线程安全调度执行，杜绝崩溃风险
            result_json = conn.modules['maya.utils'].executeInMainThreadWithResult(
                remote_func, json.dumps(payload)
            )
            
            self._consecutive_failures = 0
            return json.loads(result_json)
                
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._consecutive_failures += 1
            # 连接可能已损坏，标记为 None 以便下次重建
            self._conn = None
            if self._consecutive_failures >= self._max_failures:
                return {
                    'status': 'ERROR',
                    'detail': f'RPyC 连续 {self._consecutive_failures} 次调用失败。Maya 可能已卡死或崩溃。{e}'
                }
            return {
                'status': 'ERROR',
                'detail': f'RPyC 执行失败: {e}\n{tb}'
            }
