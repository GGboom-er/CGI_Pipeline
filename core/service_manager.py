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

# DCC Worker 队列映射
_DCC_QUEUE_MAP = {'maya': 'dcc_queue', 'blender': 'blender_queue', 'workflow': 'workflow_queue'}

# ── 管理的子进程 ──
_managed_procs = []


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


def _kill_orphan_mayapy():
    """兜底：扫描 CGI_WORKER_ID 标记的 mayapy 进程，凡父进程已死的都杀掉。

    用于 celery worker 异常退出（崩溃、硬杀）后清理残留 mayapy。
    """
    try:
        import psutil
    except ImportError:
        return
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'environ', 'ppid']):
        try:
            name = (proc.info.get('name') or '').lower()
            if 'maya' not in name:
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
                print(f'[ServiceManager] 清理孤儿 mayapy: PID={proc.pid}')
                _kill_process_tree(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


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
    """启动内嵌 Redis 服务器（幂等，已运行则跳过）"""
    if is_redis_alive():
        return True

    redis_exe = PROJECT_ROOT / 'redis_server' / 'redis-server.exe'
    if not redis_exe.exists():
        print(f'[ServiceManager] Redis 可执行文件不存在: {redis_exe}')
        return False

    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    try:
        proc = subprocess.Popen(
            [str(redis_exe)],
            cwd=str(PROJECT_ROOT / 'redis_server'),
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
    if dcc == 'pipeline':
        dcc = 'maya'

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


def start_worker(dcc: str = 'maya') -> bool:
    """启动指定 DCC 的 Celery Worker（幂等，已运行则跳过）"""
    if dcc == 'pipeline':
        dcc = 'maya'
    if dcc not in ('maya', 'blender', 'workflow'):
        return False

    alive, _ = is_worker_alive(dcc)
    if alive:
        return True

    queue = _DCC_QUEUE_MAP.get(dcc, 'dcc_queue')
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
    logfile = LOGS_DIR / f'worker_{dcc}.log'

    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    # 加载 .env 环境变量
    env = os.environ.copy()
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
        return True
    except Exception as e:
        print(f'[ServiceManager] {dcc} Worker 启动失败: {e}')
        return False


# ═══════════════════════════════════════════════════
# 全局状态查询
# ═══════════════════════════════════════════════════

def get_service_status() -> dict:
    """返回所有服务的运行状态"""
    redis_ok = is_redis_alive()
    maya_alive, maya_pid = is_worker_alive('maya')
    blender_alive, blender_pid = is_worker_alive('blender')

    return {
        'redis': {'alive': redis_ok, 'host': '127.0.0.1', 'port': 6379},
        'worker_maya': {'alive': maya_alive, 'pid': maya_pid},
        'worker_blender': {'alive': blender_alive, 'pid': blender_pid},
        'dashboard': {'alive': True},
    }


def ensure_ready(dcc: str = 'maya') -> tuple[bool, str]:
    """
    确保执行基础设施就绪。返回 (ok, error_msg)。
    前端执行按钮和 MCP 提交前都调用此函数。
    """
    # 1. Redis
    if not is_redis_alive():
        if not start_redis():
            return False, 'Redis 未运行且启动失败'

    # 2. Worker
    alive, _ = is_worker_alive(dcc)
    if not alive:
        start_worker(dcc)
        # Worker 启动需要几秒，这里不阻塞等待

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

    # 3. 杀 PID 文件记录的 worker（可能由上一轮进程拉起，非本进程 _managed_procs）
    for dcc in ('maya', 'blender', 'workflow'):
        pidfile = RUNTIME_DIR / f'worker_{dcc}.pid'
        if not pidfile.exists():
            continue
        try:
            pid = int(pidfile.read_text().strip())
            if _is_pid_alive(pid):
                _kill_process_tree(pid)
        except Exception:
            pass
        finally:
            pidfile.unlink(missing_ok=True)

    # 4. 兜底：扫描孤儿 mayapy（celery worker 已死但 mayapy 还活）
    try:
        _kill_orphan_mayapy()
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
