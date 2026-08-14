"""
一键执行（显式调试工具）：清理旧 CGI Worker → 清 Redis 队列 → 启动唯一 Worker → 提交工作流 → 等结果 → 关闭进程树

经验教训（Lesson #33/#34/#38/#41）：
- 杀进程必须用进程树杀法（psutil），否则 mayapy 子进程泄漏
- 清 Redis 必须在 Worker 存活时或直连 Redis 删 key，不能依赖 Celery broadcast
- Worker 退出必须杀整棵进程树，terminate 只杀父进程
- log_file 必须 try/finally 保证关闭，避免文件锁冲突
"""
import subprocess, time, json, sys, os, signal, shutil

PYTHON = sys.executable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_ROOT = os.path.join(PROJECT_ROOT, "src")
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)
CWD = PROJECT_ROOT

PID_FILES = [
    os.path.join(CWD, 'runtime', 'worker_cgi.pid'),
]


def _kill_process_tree(pid):
    """用 psutil 递归杀整棵进程树（含所有子进程）。"""
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        # 等待所有进程真正退出
        gone, alive = psutil.wait_procs([parent] + children, timeout=5)
        if alive:
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        return True
    except Exception:
        # psutil 不可用时回退到 os.kill
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return False


def _kill_by_pid_files():
    """精确杀旧 Worker：PID 文件中记录的进程 + 整棵子进程树。"""
    for pf in PID_FILES:
        if not os.path.isfile(pf):
            continue
        try:
            pid = int(open(pf).read().strip())
            if _kill_process_tree(pid):
                print(f"  杀旧进程树: PID={pid} ({os.path.basename(pf)})")
            else:
                print(f"  杀旧进程: PID={pid} ({os.path.basename(pf)})")
        except (ValueError, OSError):
            pass
        try:
            os.remove(pf)
        except OSError:
            pass


def _kill_celery_workers():
    """杀旧 Celery Worker 进程树（精确匹配 celery 命令行）。"""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if 'celery' in cmdline and 'cgi_pipeline.core.tasks' in cmdline:
                    print(f"  杀 Celery 进程树: PID={proc.pid}")
                    _kill_process_tree(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        # 回退到 PowerShell 方式
        try:
            subprocess.run(
                ['powershell', '-Command',
                 'Get-CimInstance Win32_Process | '
                 'Where-Object { $_.CommandLine -match "celery" -and $_.CommandLine -match "cgi_pipeline.core.tasks" } | '
                 'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'],
                capture_output=True, text=True, timeout=10
            )
        except Exception as e:
            print(f"  celery 清理警告: {e}")


def _kill_orphan_mayapy():
    """杀 CGI_WORKER_ID 环境变量标记的孤儿 mayapy 进程。"""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'environ']):
            try:
                env = proc.info.get('environ') or {}
                if env.get('CGI_WORKER_ID') and 'maya' in (proc.info.get('name') or '').lower():
                    print(f"  杀孤儿 mayapy: PID={proc.pid}")
                    _kill_process_tree(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except ImportError:
        pass


def _flush_redis():
    """直连 Redis 清空 Celery 使用的 DB（broker DB0 + result DB1）。
    
    不依赖 Celery control API，不需要 Worker 存活。
    """
    try:
        import redis
        # 清 broker 队列（DB 0）
        r0 = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_timeout=3)
        r0.flushdb()
        # 清 result backend（DB 1）
        r1 = redis.Redis(host='127.0.0.1', port=6379, db=1, socket_timeout=3)
        r1.flushdb()
        print("  ✓ Redis DB0/DB1 已清空")
    except ImportError:
        # redis-py 不可用，回退到子进程方式
        try:
            subprocess.run([PYTHON, '-c',
                'from cgi_pipeline.core.tasks import app; app.control.purge()'],
                cwd=CWD, capture_output=True, timeout=10)
        except Exception:
            pass
    except Exception as e:
        print(f"  Redis 清空警告: {e}")





# ══════════════════════════════════════════════════════════════
# 主流程（全包裹在 try/finally 中，保证资源释放）
# ══════════════════════════════════════════════════════════════

worker_proc = None
log_file = None

try:
    # ── 1. 清理旧进程 + 队列 + IPC ──
    print("[1/5] 清理旧进程/队列/IPC...")
    _kill_by_pid_files()
    _kill_celery_workers()
    _kill_orphan_mayapy()
    time.sleep(2)
    # 进程杀完后再清 Redis（确保无活跃消费者，flushdb 彻底干净）
    _flush_redis()

    print("  ✓ 全部清理完成")

    # ── 2. 启动新 Celery Worker ──
    print("[2/5] 启动新 Celery Worker...")
    log_path = os.path.join(CWD, 'logs', 'worker_celery.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_file = open(log_path, 'w')
    worker_proc = subprocess.Popen(
        [PYTHON, '-m', 'celery', '-A', 'cgi_pipeline.core.tasks', 'worker', '-Q', 'cgi_queue',
         '--hostname=cgi@%h', '-l', 'info', '--pool=solo', '-c', '1'],
        cwd=CWD,
        stdout=log_file, stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    # 等 Worker 就绪
    for i in range(30):
        time.sleep(1)
        try:
            with open(log_path, 'r') as f:
                content = f.read()
            if 'ready.' in content:
                print(f"  ✓ Worker 已就绪 (PID={worker_proc.pid}, 耗时 {i+1}s)")
                break
        except Exception:
            pass
    else:
        print("  ✗ Worker 启动超时!")
        _kill_process_tree(worker_proc.pid)
        sys.exit(1)

    # ── 3. 提交工作流 ──
    print("[3/5] 提交 tex_to_rig_verify_and_sync 工作流...")
    from cgi_pipeline.core.tasks import execute_workflow

    task_id = f"auto_qc_{int(time.time())}"
    payload = {
        'task_id': task_id,
        'workflow_id': 'tex_to_rig_verify_and_sync',
        'source_path': r'X:\Project\ysj\pub\assets\chr\mihouwang\tex\texMaster\ysj_chr_mihouwang_tex_texMaster_v003.blend',
        'project': 'ysj',
        'asset_name': 'mihouwang',
        'extra_params': {
            'rig_path': r'S:\project\ysj\work\assets\chr\mihouwang\rig\rigMaster\ysj_chr_mihouwang_rig_rigMaster_v007.ma'
        }
    }

    celery_task = execute_workflow.delay(payload)
    print(f"  ✓ 已提交, Celery ID={celery_task.id}")

    # ── 4. 等待结果 ──
    print("[4/5] 等待执行完成...")
    t_start = time.time()
    deadline = t_start + 600  # 10分钟超时（含 Maya 拼装）
    while time.time() < deadline:
        if celery_task.ready():
            break
        elapsed = int(time.time() - t_start)
        print(f"  ... 已等待 {elapsed}s", end='\r')
        time.sleep(3)

    if celery_task.ready():
        result = celery_task.result
        elapsed_total = int(time.time() - t_start)
        print(f"\n  ✓ 执行完成! 状态: {result.get('status', 'UNKNOWN')} (总耗时 {elapsed_total}s)")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # 检查报告
        report_dir = os.path.join(CWD, 'reports')
        if os.path.isdir(report_dir):
            for f in sorted(os.listdir(report_dir), reverse=True):
                if task_id in f:
                    report_path = os.path.join(report_dir, f)
                    print(f"\n[报告路径] {report_path}")
                    with open(report_path, 'r', encoding='utf-8') as rf:
                        print(rf.read())
                    break
    else:
        print("\n  ✗ 执行超时!")

finally:
    # ── 5. 关闭 Worker（进程树杀法，保证 mayapy 子进程一起杀干净）──
    print("\n[5/5] 关闭 Worker...")
    if worker_proc and worker_proc.poll() is None:
        _kill_process_tree(worker_proc.pid)
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass
    print("  ✓ Worker 已关闭, 全部完成!")
