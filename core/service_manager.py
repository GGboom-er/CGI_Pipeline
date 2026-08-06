# core/service_manager.py
# ── 统一服务生命周期管理器 ──
#
# 所有 Worker/Redis 的检测、启动、关闭逻辑在此集中。
# dashboard/app.py 和 mcp_server/internals.py 都调用这个模块，
# 消除重复代码。

import os
import sys
import subprocess
import socket
import signal
from pathlib import Path

from core.bootstrap import cfg as _cfg

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
RUNTIME_DIR = PROJECT_ROOT / 'runtime'
LOGS_DIR = PROJECT_ROOT / 'logs'

# Celery 只有一个 CGI Worker。DCC 名称保留为调用兼容别名，不能再映射到
# 不同的 Celery 进程或队列。
_CANONICAL_WORKER = 'cgi'
_WORKER_ALIASES = {
    'cgi': _CANONICAL_WORKER,
    'maya': _CANONICAL_WORKER,
    'blender': _CANONICAL_WORKER,
    'ue': _CANONICAL_WORKER,
    'pipeline': _CANONICAL_WORKER,
    'workflow': _CANONICAL_WORKER,
}
_DCC_QUEUE_MAP = {name: 'cgi_queue' for name in _WORKER_ALIASES}
_WORKER_DCCS = (_CANONICAL_WORKER,)
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 5.0

# ── 管理的子进程 ──
_managed_procs = []


def _managed_python_env(base: dict | None = None) -> dict:
    """Return a child environment that can load the Notes Python base reliably.

    The server is intentionally launched with the absolute interpreter path, not
    ``conda activate``.  On Windows that leaves ``Library/bin`` out of ``PATH``;
    NumPy/MKL (and other compiled packages) then fail with 0xc06d007f.  Keep the
    fix at the single worker-launch boundary so every Notes tool sharing this
    base inherits the same DLL search path without creating another environment.
    """
    env = dict(os.environ if base is None else base)
    prefix = Path(sys.prefix)
    managed_dirs = [
        prefix,
        prefix / 'Library' / 'mingw-w64' / 'bin',
        prefix / 'Library' / 'usr' / 'bin',
        prefix / 'Library' / 'bin',
        prefix / 'Scripts',
    ]
    existing = [item for item in env.get('PATH', '').split(os.pathsep) if item]
    prefix_items = [str(item) for item in managed_dirs if item.exists()]
    seen: set[str] = set()
    env['PATH'] = os.pathsep.join(
        item for item in prefix_items + existing
        if not (item.lower() in seen or seen.add(item.lower()))
    )
    env.setdefault('PYTHONNOUSERSITE', '1')
    return env


def _normalize_dcc(dcc: str) -> str:
    """把 DCC/工作流别名统一到唯一的 CGI Worker key。"""
    name = str(dcc or _CANONICAL_WORKER).lower()
    return _WORKER_ALIASES.get(name, name)


def _queue_for_dcc(dcc: str) -> str:
    return _DCC_QUEUE_MAP.get(_normalize_dcc(dcc), 'cgi_queue')


def _worker_hostname(dcc: str) -> str:
    """唯一 CGI Worker 的稳定 Celery 节点名。"""
    return 'cgi@%h'


def _active_queues_include(active_queues: dict | None, queue: str) -> tuple[bool, list[str]]:
    """检查 Celery inspect.active_queues() 结果里是否有 worker 消费指定队列。"""
    if not isinstance(active_queues, dict):
        return False, []
    nodes = []
    for worker_name, queues in active_queues.items():
        if not isinstance(queues, list):
            continue
        queue_names = [
            q.get('name') for q in queues
            if isinstance(q, dict) and q.get('name')
        ]
        if queue in queue_names:
            nodes.append(worker_name)
    return bool(nodes), nodes


def _inspect_active_queues(timeout_sec: float = DEFAULT_HEARTBEAT_TIMEOUT_SEC) -> tuple[dict, str]:
    """通过 Celery inspect 获取 worker 心跳。只用于服务健康检查，不限制 DCC 任务耗时。"""
    try:
        celery_app = get_celery_app()
        inspector = celery_app.control.inspect(timeout=timeout_sec)
        return inspector.active_queues() or {}, ''
    except Exception as e:
        return {}, f'{type(e).__name__}: {e}'


def _is_pid_alive(pid: int, expected_name: str = 'python') -> bool:
    """检查进程是否存活，且进程名包含 expected_name 防止 PID 复用"""
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        try:
            proc = psutil.Process(pid)
            if expected_name.lower() in proc.name().lower():
                return True
            return False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    except ImportError:
        # 没装 psutil 时用 os.kill 检测（退化模式）
        try:
            os.kill(pid, 0)
        except Exception:
            return False
        return True


def _kill_process_tree(pid: int, timeout: float = 5.0) -> bool:
    """递归杀整棵进程树。Windows 下 celery worker 的 mayapy 子进程必须这样杀。

    Returns True 如果用了 psutil 成功杀完；False 则已回退到 os.kill（可能漏杀子进程）。
    """
    try:
        import psutil
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        _, alive = psutil.wait_procs([parent] + children, timeout=timeout)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except ImportError:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return False


def _kill_orphan_dcc_processes():
    """清理 CGI worker 崩溃后遗留的 Maya/Blender 子进程。

    DCC 子进程都带有 CGI_WORKER_ID。仅清理父进程已不存在的、且确实由
    CGI worker 启动的进程，避免误杀用户前台 DCC。
    """
    try:
        import psutil
    except ImportError:
        return
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'environ', 'ppid']):
        try:
            name = (proc.info.get('name') or '').lower()
            if not any(token in name for token in ('maya', 'blender')):
                continue
            env = proc.info.get('environ') or {}
            if not env.get('CGI_WORKER_ID'):
                continue
            ppid = proc.info.get('ppid')
            parent_alive = False
            if ppid and ppid != my_pid:
                try:
                    parent_alive = psutil.pid_exists(ppid) and psutil.Process(ppid).is_running()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    parent_alive = False
            if not parent_alive:
                print(f'[ServiceManager] 清理孤儿 DCC 进程: PID={proc.pid} name={name}')
                _kill_process_tree(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def _kill_orphan_mayapy():
    """向后兼容的旧入口；实际清理 Maya 与 Blender 两类 CGI 子进程。"""
    _kill_orphan_dcc_processes()


# ═══════════════════════════════════════════════════
# Redis 管理
# ═══════════════════════════════════════════════════

def is_redis_alive(host='127.0.0.1', port=6379) -> bool:
    """通过 TCP PING 检测 Redis 是否存活"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.send(b'PING\r\n')
        data = s.recv(16)
        s.close()
        return b'PONG' in data
    except Exception:
        return False


def start_redis() -> bool:
    """启动 Notes 受管 Redis 服务器（幂等，已运行则跳过）。"""
    if is_redis_alive():
        return True

    configured = os.getenv('REDIS_SERVER_PATH', '').strip()
    candidates = [
        Path(configured) if configured else None,
        PROJECT_ROOT.parent / 'redis' / 'redis-server.exe',
        PROJECT_ROOT / 'redis_server' / 'redis-server.exe',
    ]
    redis_exe = next((path for path in candidates if path and path.exists()), None)
    if redis_exe is None:
        locations = ', '.join(str(path) for path in candidates if path)
        print(f'[ServiceManager] Redis 可执行文件不存在，已检查: {locations}')
        return False

    configured_config = os.getenv('REDIS_CONFIG_PATH', '').strip()
    config_path = Path(configured_config) if configured_config else redis_exe.with_name('redis.windows.conf')
    command = [str(redis_exe)]
    if config_path.exists():
        command.append(str(config_path))

    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(redis_exe.parent),
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _managed_procs.append(proc)
        # 等待就绪
        import time
        for _ in range(10):
            time.sleep(0.5)
            if is_redis_alive():
                print('[ServiceManager] Redis 已启动')
                return True
        print('[ServiceManager] Redis 启动超时')
        return False
    except Exception as e:
        print(f'[ServiceManager] Redis 启动失败: {e}')
        return False


# ═══════════════════════════════════════════════════
# Worker 管理
# ═══════════════════════════════════════════════════

def is_worker_alive(dcc: str = 'maya') -> tuple[bool, int | None]:
    """检查指定 DCC Worker 是否存活。返回 (alive, pid)。"""
    dcc = _normalize_dcc(dcc)

    pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
    if not pidfile.exists():
        return False, None

    try:
        pid = int(pidfile.read_text().strip())
        alive = _is_pid_alive(pid)
        if not alive:
            pidfile.unlink(missing_ok=True)
            return False, None
        return True, pid
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)
        return False, None


def get_worker_health(dcc: str = 'maya', heartbeat_timeout_sec: float = DEFAULT_HEARTBEAT_TIMEOUT_SEC) -> dict:
    """返回 worker 的 PID 与 Celery 队列心跳状态。"""
    dcc = _normalize_dcc(dcc)
    queue = _queue_for_dcc(dcc)
    if dcc not in _WORKER_DCCS:
        return {
            'dcc': dcc,
            'queue': queue,
            'state': 'UNSUPPORTED',
            'pid_alive': False,
            'pid': None,
            'celery_alive': False,
            'workers': [],
            'error': f'暂不支持的 worker 类型: {dcc}',
        }

    pid_alive, pid = is_worker_alive(dcc)
    health = {
        'dcc': dcc,
        'queue': queue,
        'state': 'DEAD',
        'pid_alive': pid_alive,
        'pid': pid,
        'celery_alive': False,
        'workers': [],
    }
    if not pid_alive:
        return health
    if not is_redis_alive():
        health.update({
            'state': 'NO_REDIS',
            'error': 'Redis 未运行，无法进行 Celery 心跳检查。',
        })
        return health

    active_queues, error = _inspect_active_queues(heartbeat_timeout_sec)
    queue_alive, workers = _active_queues_include(active_queues, queue)
    health.update({
        'celery_alive': queue_alive,
        'workers': workers,
    })
    if error:
        health['inspect_error'] = error
        health['state'] = 'INSPECT_ERROR'
    elif queue_alive:
        health['state'] = 'HEALTHY'
    else:
        health['state'] = 'NO_HEARTBEAT'
        health['error'] = f'未检测到消费队列 {queue} 的 Celery worker。'
    return health


def restart_worker(dcc: str = 'maya', wait_sec: float = 1.0) -> bool:
    """杀掉指定 worker 进程树并重新启动。"""
    dcc = _normalize_dcc(dcc)
    if dcc not in _WORKER_DCCS:
        return False

    stop_worker(dcc)  # 权威停：确认死才删 pidfile（不再无条件 unlink）
    return start_worker(dcc)  # 诚实起：等到 HEALTHY 才返回 True


def ensure_worker_healthy(
    dcc: str = 'maya',
    heartbeat_timeout_sec: float = DEFAULT_HEARTBEAT_TIMEOUT_SEC,
    restart_on_stale: bool = True,
) -> tuple[bool, str]:
    """确保 Redis 与指定 worker 可用；PID 存活但心跳异常时自动重启。"""
    dcc = _normalize_dcc(dcc)
    if dcc not in _WORKER_DCCS:
        return False, f'暂不支持的 worker 类型: {dcc}'

    if not start_redis():
        return False, 'Redis 未运行且启动失败'

    pid_alive, _ = is_worker_alive(dcc)
    if not pid_alive:
        ok = start_worker(dcc)
        return ok, (f'{dcc} Worker 已启动' if ok else f'{dcc} Worker 启动失败')

    health = get_worker_health(dcc, heartbeat_timeout_sec)
    if health.get('state') == 'HEALTHY':
        return True, f'{dcc} Worker 健康 (PID={health.get("pid")})'

    # PID 存活但 inspect 探活超时/出错：solo 单进程 worker 正忙于长任务（同步等 maya/blender
    # 子任务，数十秒到数百秒）时无法在超时窗口内应答控制命令——这是「忙」不是「死」。
    # 不再杀活进程重启（旧逻辑会把正在跑 workflow 的 worker 误杀，导致反复重启 + 任务被
    # acks_late 重投 + 调用方看到的「孤立」）；新任务会在队列里排队等它腾出。
    # 仅 PID 不存在时才拉起（上面已处理）；restart_on_stale 保留作 API 兼容，活进程一律不强杀。
    stale_states = {'NO_HEARTBEAT', 'INSPECT_ERROR'}
    if health.get('state') in stale_states:
        return True, (
            f'{dcc} Worker PID={health.get("pid")} 存活但繁忙'
            f'（inspect {heartbeat_timeout_sec}s 超时），按存活处理、不重启'
        )

    return False, health.get('error') or f'{dcc} Worker 状态异常: {health.get("state")}'


def _await_worker_ready(dcc: str, timeout_sec: float = 30.0) -> bool:
    """轮询等 worker 真正就绪（Celery 心跳在消费队列），就绪返回 True，超时 False。

    治「发射后不管的谎言」：start 不再 Popen 完就报成功，而是等到 get_worker_health
    == HEALTHY（队列真被消费）才算起来。pidfile 由 celery 初始化完才写，心跳才是真凭据。
    """
    import time
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if get_worker_health(dcc, heartbeat_timeout_sec=2.0).get('state') == 'HEALTHY':
            return True
        time.sleep(1.0)
    return False


def _clear_stale_pidfile(dcc: str):
    """Popen 前清理残留 pidfile：文件在但进程已死则删，防 celery O_EXCL 撞死残留。"""
    dcc = _normalize_dcc(dcc)
    pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
    if not pidfile.exists():
        return
    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)  # 坏 pidfile 直接清
        return
    if not _is_pid_alive(pid):
        pidfile.unlink(missing_ok=True)


def _acquire_start_lock(dcc: str, stale_after_sec: float) -> bool:
    """原子抢启动锁，串行化并发 start（跨进程：dashboard vs MCP）。

    抢到返回 True；已有 starter 持锁返回 False。锁文件 mtime 超过 stale_after_sec
    视为上个 starter 崩溃残留，偷锁重来（不因单次崩溃永久堵死）。
    """
    import time
    dcc = _normalize_dcc(dcc)
    lockpath = RUNTIME_DIR / f'worker_{dcc}.starting.lock'
    try:
        fd = os.open(str(lockpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lockpath.stat().st_mtime
        except OSError:
            return False
        if age > stale_after_sec:
            # 残留锁：偷锁（删掉重抢，抢不到就让给刚介入的那个）
            lockpath.unlink(missing_ok=True)
            try:
                fd = os.open(str(lockpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                return False
        return False


def _release_start_lock(dcc: str):
    dcc = _normalize_dcc(dcc)
    (RUNTIME_DIR / f'worker_{dcc}.starting.lock').unlink(missing_ok=True)


def start_worker(dcc: str = 'maya', wait: bool = True, timeout_sec: float = 30.0) -> bool:
    """启动指定 DCC 的 Celery Worker（幂等，已运行则跳过）。

    wait=True（默认）：等到 worker 真正 HEALTHY 才返回 True；超时返回 False（诚实报失败）。
    wait=False：仅发起启动即返回（一次性工具/不需确认时用），不保证已就绪。
    """
    dcc = _normalize_dcc(dcc)
    if dcc not in _WORKER_DCCS:
        return False

    alive, _ = is_worker_alive(dcc)
    if alive:
        return True

    # 目录先建：_acquire_start_lock 要在 RUNTIME_DIR 里 os.open 锁文件，
    # 必须先确保目录存在——否则 runtime/ 缺失（新检出/被清）时锁文件创建报 Errno 2。
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 抢启动锁：只允许一个 starter 真正 Popen，其余等健康——治 LockFailed 并发抢 pidfile 竞态。
    # 输家不 Popen 竞争（那会让 celery O_EXCL 撞车），改等就绪，天然幂等符合「已运行则跳过」。
    if not _acquire_start_lock(dcc, stale_after_sec=timeout_sec + 5.0):
        return _await_worker_ready(dcc, timeout_sec) if wait else False

    queue = _queue_for_dcc(dcc)
    pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
    logfile = LOGS_DIR / f'worker_{dcc}.log'

    # 拿锁后二次确认：抢锁瞬间可能已被别的 starter 起好；再清残留 pidfile 防 celery O_EXCL 撞车
    alive, _ = is_worker_alive(dcc)
    if alive:
        _release_start_lock(dcc)
        return True
    _clear_stale_pidfile(dcc)

    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    # 加载 .env 环境变量
    env = _managed_python_env()
    env['PYTHONUNBUFFERED'] = '1'
    dotenv_path = PROJECT_ROOT / '.env'
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env.setdefault(k.strip(), v.strip())

    cmd = [
        sys.executable, "-m", "celery",
        "-A", "core.tasks", "worker",
        "-Q", queue, "--pool=solo", "-c", "1", "-l", "info",
        f"--hostname={_worker_hostname(dcc)}",
        f"--pidfile={pidfile}",
        f"--logfile={logfile}",
    ]

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), creationflags=flags, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _managed_procs.append(proc)
        print(f'[ServiceManager] {dcc} Worker 已拉起 (PID={proc.pid})')
        if not wait:
            return True
        # 诚实确认：等到 Celery 心跳真在消费队列才算起来；超时报失败（不再"发射后不管")
        if _await_worker_ready(dcc, timeout_sec):
            print(f'[ServiceManager] {dcc} Worker 就绪确认 (PID={proc.pid})')
            return True
        print(f'[ServiceManager] {dcc} Worker {timeout_sec}s 内未就绪，判失败')
        return False
    except Exception as e:
        print(f'[ServiceManager] {dcc} Worker 启动失败: {e}')
        return False
    finally:
        # 无论成败都释放启动锁：Popen 已发起（celery 接手 pidfile），锁使命已尽，
        # 保留会误堵下一个合法 starter；wait=False 时也在此释放。
        _release_start_lock(dcc)


def stop_worker(dcc: str = 'maya', timeout: float = 8.0) -> bool:
    """权威停指定 worker：按 pidfile 杀整棵进程树 → 确认死 → 才删 pidfile。

    治「杀失败也删 pidfile → 下次起竞争 worker」：只有确认进程真死才删 pidfile；
    漏杀则保留 pidfile 交下次补刀。pidfile 是跨进程单一真相源，不靠 _managed_procs。
    返回 True=已确认停止（或本就没在跑）；False=杀失败仍存活。
    """
    import time
    dcc = _normalize_dcc(dcc)
    if dcc not in _WORKER_DCCS:
        return False
    pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
    if not pidfile.exists():
        return True  # 本就没在跑
    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)  # 坏 pidfile，清掉
        return True
    if not _is_pid_alive(pid):
        pidfile.unlink(missing_ok=True)  # 进程已不在，清残留 pidfile
        return True
    _kill_process_tree(pid, timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            pidfile.unlink(missing_ok=True)  # 确认死了才删
            print(f'[ServiceManager] {dcc} Worker 已停止 (PID={pid})')
            return True
        time.sleep(0.3)
    print(f'[ServiceManager] {dcc} Worker 杀失败仍存活 (PID={pid})，保留 pidfile 待补刀')
    return False


# ═══════════════════════════════════════════════════
# 全局状态查询
# ═══════════════════════════════════════════════════

def get_service_status(include_heartbeat: bool = False) -> dict:
    """返回 Redis 与唯一 CGI Worker 的状态。

    ``worker_maya``/``worker_blender``/``worker_workflow`` 保留为只读兼容别名，
    三者内容始终指向同一个 ``worker_cgi``，不代表三套进程。
    """
    redis_ok = is_redis_alive()
    worker_alive, worker_pid = is_worker_alive(_CANONICAL_WORKER)

    status = {
        'redis': {'alive': redis_ok, 'host': '127.0.0.1', 'port': 6379},
        'worker_cgi': {'alive': worker_alive, 'pid': worker_pid},
        'dashboard': {'alive': True},
    }
    for alias in ('maya', 'blender', 'workflow'):
        status[f'worker_{alias}'] = dict(status['worker_cgi'])
    if include_heartbeat:
        health = get_worker_health(_CANONICAL_WORKER, DEFAULT_HEARTBEAT_TIMEOUT_SEC)
        worker_state = {
            'alive': health.get('state') == 'HEALTHY',
            'pid': health.get('pid', status['worker_cgi']['pid']),
            'health': health,
        }
        status['worker_cgi'].update(worker_state)
        for alias in ('maya', 'blender', 'workflow'):
            status[f'worker_{alias}'].update(worker_state)
    return status


def ensure_ready(dcc: str = 'maya') -> tuple[bool, str]:
    """
    确保执行基础设施就绪。返回 (ok, error_msg)。
    前端执行按钮和 MCP 提交前都调用此函数。
    """
    # 1. Redis
    if not is_redis_alive():
        if not start_redis():
            return False, 'Redis 未运行且启动失败'

    # 2. Worker：PID 存活但 Celery 心跳丢失时会自动重启
    ok, err = ensure_worker_healthy(dcc)
    if not ok:
        return False, err

    # 3. 顺便清理过期审计日志（非阻塞，异常不影响主流程）
    try:
        cleanup_old_audits()
    except Exception:
        pass

    return True, ''


# ═══════════════════════════════════════════════════
# 审计日志清理（按日期目录，保留 30 天）
# ═══════════════════════════════════════════════════

def cleanup_old_audits(max_age_days: int = 30):
    """清理超过 max_age_days 天的审计日志目录"""
    import datetime
    import shutil

    audit_dir = PROJECT_ROOT / 'audit'
    if not audit_dir.exists():
        return

    cutoff = datetime.date.today() - datetime.timedelta(days=max_age_days)
    removed = 0

    for entry in audit_dir.iterdir():
        if entry.is_dir() and len(entry.name) == 10:
            try:
                dir_date = datetime.date.fromisoformat(entry.name)
                if dir_date < cutoff:
                    shutil.rmtree(entry)
                    removed += 1
            except ValueError:
                continue  # 非日期格式的目录跳过

    # 清理根目录下的扁平审计文件（旧格式遗留）
    for f in audit_dir.glob('*.json'):
        try:
            mtime = datetime.date.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            continue

    if removed > 0:
        print(f'[ServiceManager] 已清理 {removed} 个过期审计记录')


# ═══════════════════════════════════════════════════
# 清理
# ═══════════════════════════════════════════════════

def shutdown_all():
    """关闭所有由本管理器拉起的子进程。

    顺序（每一步失败都会降级到下一步）：
      1. 优雅通知：让 warm pool 的 MayaWorker/BlenderWorker 发 __DIE__ 退出
      2. 终止 celery worker：进程树杀法（保证 mayapy 子进程一起死）
      3. 扫描孤儿 mayapy：清理环境变量标记的残留进程
    """
    # 1. 优雅让 warm pool 的 DCC worker 自退（__DIE__ 信号）
    try:
        from core import dcc_factory
        pool = getattr(dcc_factory, '_warm_pool', {})
        for dcc_type, entry in list(pool.items()):
            try:
                worker = entry.get('worker')
                if worker and hasattr(worker, 'shutdown'):
                    worker.shutdown()
            except Exception as e:
                print(f'[ServiceManager] {dcc_type} warm worker 优雅关闭失败: {e}')
        pool.clear()
    except Exception:
        pass

    # 2. 杀本进程记录的 celery worker 子进程（进程树杀法）
    for proc in _managed_procs:
        try:
            if proc.poll() is None:
                _kill_process_tree(proc.pid)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _managed_procs.clear()

    # 3. 权威停 PID 文件记录的 worker（跨进程真相源；确认死才删 pidfile，杀失败保留待补刀）
    stop_worker(_CANONICAL_WORKER)

    # 4. 兜底：扫描孤儿 DCC（Celery worker 已死但 mayapy/Blender 还活）
    try:
        _kill_orphan_dcc_processes()
    except Exception:
        pass

    print('[ServiceManager] 所有子进程已清理')


# ── 自动注册退出清理钩子（防孤儿进程）──
import atexit


def _signal_handler(signum, frame):
    """信号处理：收到 SIGTERM/SIGINT 时清理子进程后退出"""
    shutdown_all()
    sys.exit(0)


_hooks_installed = False


def install_exit_hooks():
    """长期驻留进程（dashboard 主进程）显式调用，确保退出时清理 worker。

    一次性工具脚本不应调用 — 否则脚本退出时会连带杀掉正在运行的 worker。
    幂等：重复调用无副作用。
    """
    global _hooks_installed
    if _hooks_installed:
        return
    atexit.register(shutdown_all)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _signal_handler)
        except (OSError, ValueError):
            # Worker 子线程中可能无法设置信号处理器，忽略
            pass
    _hooks_installed = True


# ── Celery 单例 ──
_celery_instance = None


def get_celery_app():
    """懒加载 Celery 实例，所有 API 共用"""
    global _celery_instance
    if _celery_instance is None:
        from core.tasks import app as celery_app
        _celery_instance = celery_app
    return _celery_instance
