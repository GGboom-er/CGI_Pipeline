# cgi_pipeline/hosts/maya/worker.py
# ── Maya 每链一进程生命周期 ──
import subprocess, json, os, time, uuid, psutil, math
from pathlib import Path
from dotenv import load_dotenv
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT
from cgi_pipeline.contracts import validate_batch_result
from cgi_pipeline.hosts.file_ipc import FileIpcTransport
from cgi_pipeline.hosts.runtime import build_host_env, host_runtime, record_host_process, record_host_launch

load_dotenv()

MAYAPY       = os.getenv('MAYAPY_PATH')
MAYA_BIN_DIR = os.getenv('MAYA_BIN_DIR', r'C:\Program Files\Autodesk\Maya2025\bin')
PROJECT_ROOT = REPOSITORY_ROOT
ADAPTER      = str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'maya' / 'adapter.py')
IPC_TIMEOUT  = float(os.getenv('IPC_TIMEOUT_SEC', '0') or 0)
RPYC_SYNC_TIMEOUT = float(os.getenv('RPYC_SYNC_TIMEOUT_SEC', '3600') or 3600)


def _build_env(worker_id: str, environ=None) -> dict:
    env = build_host_env('maya', PROJECT_ROOT, environ)
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
        'PYTHONNOUSERSITE':    '1',
    })
    existing_pythonpath = [item for item in env.get('PYTHONPATH', '').split(os.pathsep) if item]
    env['PYTHONPATH'] = os.pathsep.join([str(SOURCE_ROOT), *existing_pythonpath])
    return env


class MayaWorker:
    """可复用 Maya 进程：start → run_api/run_batch × N → shutdown。"""

    def __init__(self):
        self.worker_id  = str(uuid.uuid4())[:8]
        self.process    = None
        self._environment = os.environ.copy()
        self.runtime = host_runtime(PROJECT_ROOT, self._environment)
        self.ipc = FileIpcTransport(self.worker_id, self.runtime.root, clean_stale=True)
        self.cmd_file = self.ipc.cmd_file
        self.result_dir = self.ipc.result_dir

    def start(self):
        env = _build_env(self.worker_id, self._environment)
        log_dir = self.runtime.root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_log = open(log_dir / f'mayapy_{self.worker_id}.log', 'w')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        record_host_launch(self.runtime, 'maya', self.worker_id)
        self.process = subprocess.Popen(
            [MAYAPY, ADAPTER],
            env=env, stdout=subprocess.DEVNULL, stderr=self._stderr_log,
            creationflags=flags
        )
        try:
            record_host_process(self.runtime, 'maya', self.worker_id, self.process)
        except Exception:
            self._kill_process_tree()
            self._stderr_log.close()
            raise
        time.sleep(20)
        if self.process.poll() is not None:
            self._stderr_log.close()
            err = (log_dir / f'mayapy_{self.worker_id}.log').read_text()[:500]
            raise RuntimeError(f'Maya failed to start: {err}')

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
            if batch:
                raise RuntimeError(f'Maya 进程已退出 (exit code: {self.process.returncode})')
            return {'status': 'ERROR',
                    'detail': f'Maya 进程已退出 (exit code: {self.process.returncode})，任务 {task_id} 中断'}
        return None

    def run_batch(self, payloads: list[dict], atomic: bool = True) -> dict:
        """Execute literal API payloads in one Maya IPC round-trip."""
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

def is_rpyc_available(host, port, timeout=0.5) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
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
        self._remote_func = None  # 当前 RPyC 连接对应的远程执行入口
        self._remote_batch_func = None

    def _send_bootstrap(self, deadline):
        # 读取 RPyC 服务器脚本，并把 MCP server 进程的 site-packages 路径
        # 注入到脚本顶部的占位常量 _CGI_INJECT_SITE_PACKAGES 里。
        # 这样 Maya 会沿仓库 conda env 找到 rpyc，不需要往 mayapy pip install。
        server_script = (SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / 'maya' / 'rpyc_server.py').read_text(encoding='utf-8')

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

        # A non-None eval result prevents commandPort from exec'ing it again.
        marker = f'CGI_RPYC_BOOTSTRAP_{uuid.uuid4().hex}'
        payload = (f"(exec(__import__('base64').b64decode('{b64_script}').decode('utf-8'), "
                   f"globals()), {marker!r})[1]\n")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 'bootstrap 发送前等待期限已结束。', False
            s.settimeout(min(3, remaining))
            s.connect((self.host, self.port))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 'bootstrap 发送前等待期限已结束。', False
            s.settimeout(remaining)
            s.sendall(payload.encode('utf-8'))
            return self._read_bootstrap_reply(s, marker, deadline)
        finally:
            s.close()

    def _read_bootstrap_reply(self, sock, marker, deadline):
        """Ignore echoes until a complete nonce frame or the RPyC endpoint is ready."""
        reply, pending, truncated = '', b'', False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return f'{reply}\n{pending.decode("utf-8", errors="replace")}\ncommandPort 等待期限已结束；命令可能仍在 Maya 主线程等待。'[-2000:], False
            if is_rpyc_available(self.host, self.rpyc_port, min(0.5, remaining)):
                return f'{reply}\nRPyC 端口已就绪。', True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            sock.settimeout(min(0.5, remaining))
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                return f'{reply}\n{pending.decode("utf-8", errors="replace")}\ncommandPort EOF：连接已关闭，未收到关联完成帧。'[-2000:], False
            pending += chunk
            while b'\x00' in pending:
                frame, pending = pending.split(b'\x00', 1)
                frame = frame.decode('utf-8', errors='replace').strip('\r\n')
                reply = f'{reply}\n{frame}'[-2000:]
                if not truncated and frame == marker:
                    return reply, False
                truncated = False
            if len(pending) > 2000:
                pending, truncated = pending[-2000:], True

    def _ensure_connection(self):
        """获取或创建持久化 RPyC 连接。连接断开时自动重建。"""
        if self._conn is not None:
            try:
                self._conn.ping()  # 心跳探活
                return self._conn
            except Exception:
                # 连接已死，清理后重建
                self.shutdown()

        self.start()
        return self._conn

    def _get_remote_func(self, conn, batch=False):
        """Install both entrypoints once per connection; refresh code on change."""
        attribute = '_remote_batch_func' if batch else '_remote_func'
        cached = getattr(self, attribute, None)
        if cached is not None:
            return cached

        remote_run_code = f"""
def _cgi_execute_json(payload_json, batch=False):
    import sys, json
    payload = json.loads(payload_json)
    source_path = r'{str(SOURCE_ROOT)}'
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    from cgi_pipeline.core.code_reload import capability_session
    with capability_session() as snapshot:
        import cgi_pipeline.execution as execution
        import maya.cmds as cmds
        first = (payload.get('batch') or [{{}}])[0] if batch else payload
        context = dict(first.get('api_context') or {{}})
        context['execution_mode'] = 'foreground'
        context.setdefault('source_path', first.get('source_path', ''))
        context.setdefault('project', first.get('project', ''))
        context.setdefault('asset_name', first.get('asset_name', ''))
        context['cmds_module'] = cmds
        if batch:
            res = execution.execute_batch(payload.get('batch') or [], context=context,
                                          atomic=bool(payload.get('atomic', True)),
                                          catalog_snapshot=snapshot)
        else:
            res = execution.execute_api(payload['api_id'], payload.get('api_params') or {{}},
                                        context, catalog_snapshot=snapshot)
    return json.dumps(res, ensure_ascii=False, default=str)

def _cgi_run_api_json(payload_json):
    return _cgi_execute_json(payload_json)

def _cgi_run_batch_json(payload_json):
    return _cgi_execute_json(payload_json, batch=True)
"""
        conn.execute(remote_run_code)
        self._remote_func = conn.namespace['_cgi_run_api_json']
        self._remote_batch_func = conn.namespace['_cgi_run_batch_json']
        return getattr(self, attribute)

    def start(self):
        if self._conn is not None:
            return
        self._bootstrap_server()
        self._conn = rpyc.classic.connect(self.host, self.rpyc_port)
        self._conn._config['sync_request_timeout'] = RPYC_SYNC_TIMEOUT

    def _bootstrap_server(self):
        if is_rpyc_available(self.host, self.rpyc_port):
            self._consecutive_failures = 0
            return
            
        if not is_maya_commandport_available(self.host, self.port):
            raise RuntimeError(
                f'Maya commandPort 不可用 ({self.host}:{self.port})。'
                f'请在 Maya 中执行: cmds.commandPort(name=":{self.port}", sourceType="python")'
            )
        
        try:
            timeout = float(os.getenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', '30'))
        except ValueError as exc:
            raise ValueError('RPYC_BOOTSTRAP_TIMEOUT_SEC 必须是有限正数。') from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('RPYC_BOOTSTRAP_TIMEOUT_SEC 必须是有限正数。')

        # A busy Maya main thread can accept the command before running it.
        deadline = time.monotonic() + timeout
        reply, ready = self._send_bootstrap(deadline)
        if ready:
            self._consecutive_failures = 0
            return
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if is_rpyc_available(self.host, self.rpyc_port, min(0.5, remaining)):
                self._consecutive_failures = 0
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

        # This observation cannot identify missing dependencies or prove that the
        # accepted bootstrap command will never finish. Preserve actual evidence.
        raise RuntimeError(
            f'RPyC bootstrap 尚未就绪 ({self.host}:{self.rpyc_port})，等待窗口 {timeout:g} 秒已结束。\n'
            f'bootstrap 调用未观察到服务就绪；不能据此判断 Maya 执行完成或依赖缺失。\n'
            f'MCP 已加载 rpyc {rpyc.__version__}: {rpyc.__file__}\n'
            f'commandPort 响应: {reply or "无执行响应"}\n'
            f'请先核对 Maya 主线程/Script Editor 和当前端口状态；不自动重发任务或重装依赖。'
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
            self._remote_func = None
        self._remote_batch_func = None

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
            remote_func = self._get_remote_func(conn)
            
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
            self.shutdown()
            if self._consecutive_failures >= self._max_failures:
                return {
                    'status': 'ERROR',
                    'detail': f'RPyC 连续 {self._consecutive_failures} 次调用失败。Maya 可能已卡死或崩溃。{e}'
                }
            return {
                'status': 'ERROR',
                'detail': f'RPyC 执行失败: {e}\n{tb}'
            }

    def run_batch(self, payloads: list[dict], atomic: bool = True) -> dict:
        """Execute a literal batch through one persistent RPyC/main-thread call."""
        if not payloads:
            return {'status': 'ERROR', 'error_code': 'BATCH_EMPTY',
                    'error': 'batch 至少需要一个 API 调用', 'results': [], 'completed': 0, 'requested': 0,
                    'atomic': bool(atomic), 'rolled_back': False}
        try:
            conn = self._ensure_connection()
            fn = self._get_remote_func(conn, batch=True)
            raw = conn.modules['maya.utils'].executeInMainThreadWithResult(
                fn, json.dumps({'batch': payloads, 'atomic': atomic}))
            result = json.loads(raw)
            validate_batch_result(result, len(payloads), atomic)
            self._consecutive_failures = 0
            return result
        except Exception as exc:
            self.shutdown()
            raise RuntimeError(f'RPyC 批量执行失败: {exc}') from exc
