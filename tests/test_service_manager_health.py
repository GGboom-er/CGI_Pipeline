"""service_manager Worker 心跳与自动重启单元测试。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cgi_pipeline.core import service_manager as sm


def _check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
        return True
    print(f"  [FAIL] {name} {detail}")
    raise AssertionError(name)


def test_active_queues_include():
    print("\n=== Test: Celery active_queues 队列匹配 ===")
    active = {
        "cgi@host": [{"name": "cgi_queue"}, {"name": "celery"}],
    }
    ok, nodes = sm._active_queues_include(active, "cgi_queue")
    _check("找到 cgi_queue", ok)
    _check("返回消费节点", nodes == ["cgi@host"], nodes)

    ok, nodes = sm._active_queues_include(active, "missing_queue")
    _check("缺失队列返回 false", not ok and nodes == [])


def test_default_heartbeat_timeout():
    print("\n=== Test: 默认心跳窗口 ===")
    _check("默认心跳窗口不少于 5 秒", sm.DEFAULT_HEARTBEAT_TIMEOUT_SEC >= 5.0)


def test_dead_pidfile_cleanup():
    print("\n=== Test: 死 PID 文件清理 ===")
    original_runtime = sm.RUNTIME_DIR
    original_is_pid_alive = sm._is_pid_alive
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            pidfile = sm.RUNTIME_DIR / "worker_cgi.pid"
            pidfile.write_text("12345", encoding="utf-8")
            sm._is_pid_alive = lambda pid, expected_name="python": False

            health = sm.get_worker_health("maya")
            _check("状态为 DEAD", health["state"] == "DEAD", health)
            _check("坏 pidfile 被移除", not pidfile.exists())
        finally:
            sm.RUNTIME_DIR = original_runtime
            sm._is_pid_alive = original_is_pid_alive


def test_ensure_busy_worker_not_killed():
    print("\n=== Test: PID 存活但心跳超时=繁忙，不强杀重启（de15f62 行为）===")
    originals = {
        "start_redis": sm.start_redis,
        "is_worker_alive": sm.is_worker_alive,
        "get_worker_health": sm.get_worker_health,
        "restart_worker": sm.restart_worker,
    }
    calls = []
    try:
        sm.start_redis = lambda: True
        sm.is_worker_alive = lambda dcc: (True, 777)
        sm.get_worker_health = lambda dcc, heartbeat_timeout_sec=sm.DEFAULT_HEARTBEAT_TIMEOUT_SEC: {
            "state": "NO_HEARTBEAT",
            "pid": 777,
            "error": "stale",
        }
        sm.restart_worker = lambda dcc: calls.append(dcc) or True

        ok, msg = sm.ensure_worker_healthy("pipeline")
        _check("pipeline 映射到 maya、按存活处理返回成功", ok, msg)
        _check("繁忙活进程不被强杀重启", calls == [], calls)
        _check("返回繁忙说明", "繁忙" in msg, msg)
    finally:
        sm.start_redis = originals["start_redis"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.get_worker_health = originals["get_worker_health"]
        sm.restart_worker = originals["restart_worker"]


def test_ensure_healthy_no_restart():
    print("\n=== Test: 健康 Worker 不重启 ===")
    originals = {
        "start_redis": sm.start_redis,
        "is_worker_alive": sm.is_worker_alive,
        "get_worker_health": sm.get_worker_health,
        "restart_worker": sm.restart_worker,
    }
    calls = []
    try:
        sm.start_redis = lambda: True
        sm.is_worker_alive = lambda dcc: (True, 888)
        sm.get_worker_health = lambda dcc, heartbeat_timeout_sec=sm.DEFAULT_HEARTBEAT_TIMEOUT_SEC: {
            "state": "HEALTHY",
            "pid": 888,
        }
        sm.restart_worker = lambda dcc: calls.append(dcc) or True

        ok, msg = sm.ensure_worker_healthy("maya")
        _check("健康状态返回成功", ok, msg)
        _check("没有触发重启", calls == [], calls)
    finally:
        sm.start_redis = originals["start_redis"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.get_worker_health = originals["get_worker_health"]
        sm.restart_worker = originals["restart_worker"]


def test_ensure_available_skips_heartbeat_for_live_worker():
    print("\n=== Test: 提交热路径不广播探活 ===")
    originals = {
        "start_redis": sm.start_redis,
        "is_worker_alive": sm.is_worker_alive,
        "get_worker_health": sm.get_worker_health,
        "start_worker": sm.start_worker,
    }
    try:
        sm.start_redis = lambda: True
        sm.is_worker_alive = lambda dcc: (True, 999)
        sm.get_worker_health = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected heartbeat"))
        sm.start_worker = lambda dcc: (_ for _ in ()).throw(AssertionError("unexpected restart"))

        ok, msg = sm.ensure_worker_available("pipeline")
        _check("活 PID 直接可用", ok, msg)
        _check("返回 canonical PID", "999" in msg, msg)
    finally:
        sm.start_redis = originals["start_redis"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.get_worker_health = originals["get_worker_health"]
        sm.start_worker = originals["start_worker"]


def test_ensure_available_starts_missing_worker():
    print("\n=== Test: 提交时缺 Worker 走验证启动 ===")
    originals = {
        "start_redis": sm.start_redis,
        "is_worker_alive": sm.is_worker_alive,
        "start_worker": sm.start_worker,
    }
    starts = []
    try:
        sm.start_redis = lambda: True
        sm.is_worker_alive = lambda dcc: (False, None)
        sm.start_worker = lambda dcc: starts.append(dcc) or True

        ok, msg = sm.ensure_worker_available("blender")
        _check("缺 Worker 时启动成功", ok, msg)
        _check("只启动 canonical Worker", starts == ["cgi"], starts)
    finally:
        sm.start_redis = originals["start_redis"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.start_worker = originals["start_worker"]


def test_service_status_alive_uses_heartbeat():
    print("\n=== Test: 服务状态按心跳判活 ===")
    originals = {
        "is_redis_alive": sm.is_redis_alive,
        "is_worker_alive": sm.is_worker_alive,
        "get_worker_health": sm.get_worker_health,
    }
    try:
        sm.is_redis_alive = lambda: True
        sm.is_worker_alive = lambda dcc: (True, 1)

        def fake_health(dcc, heartbeat_timeout_sec=sm.DEFAULT_HEARTBEAT_TIMEOUT_SEC):
            state = "NO_HEARTBEAT"
            return {
                "state": state,
                "pid_alive": True,
                "pid": 1,
                "celery_alive": False,
            }

        sm.get_worker_health = fake_health

        status = sm.get_service_status(include_heartbeat=True)
        _check("唯一 CGI Worker 无心跳时不算 alive", status["worker_cgi"]["alive"] is False, status)
        _check("服务状态只暴露唯一 Worker", not ({"worker_maya", "worker_blender", "worker_workflow"} & set(status)), status)
    finally:
        sm.is_redis_alive = originals["is_redis_alive"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.get_worker_health = originals["get_worker_health"]


def test_start_lock_acquire_release():
    print("\n=== Test: 启动锁 抢/放/偷 ===")
    original_runtime = sm.RUNTIME_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            _check("首次抢锁成功", sm._acquire_start_lock("maya", stale_after_sec=60.0))
            _check("持锁时二次抢失败", not sm._acquire_start_lock("maya", stale_after_sec=60.0))
            sm._release_start_lock("maya")
            _check("释放后可再抢", sm._acquire_start_lock("maya", stale_after_sec=60.0))

            # 残留锁：把 mtime 退回，验证偷锁
            lock = sm.RUNTIME_DIR / "worker_cgi.starting.lock"
            old = os.stat(lock).st_mtime - 999
            os.utime(lock, (old, old))
            _check("残留锁被偷", sm._acquire_start_lock("maya", stale_after_sec=60.0))
            sm._release_start_lock("maya")
        finally:
            sm.RUNTIME_DIR = original_runtime


def test_clear_stale_pidfile():
    print("\n=== Test: Popen 前清残留 pidfile ===")
    original_runtime = sm.RUNTIME_DIR
    original_is_pid_alive = sm._is_pid_alive
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            pidfile = sm.RUNTIME_DIR / "worker_cgi.pid"

            pidfile.write_text("12345", encoding="utf-8")
            sm._is_pid_alive = lambda pid, expected_name="python": False
            sm._clear_stale_pidfile("cgi")
            _check("死 PID 的 pidfile 被清", not pidfile.exists())

            pidfile.write_text("999", encoding="utf-8")
            sm._is_pid_alive = lambda pid, expected_name="python": True
            sm._clear_stale_pidfile("cgi")
            _check("活 PID 的 pidfile 保留", pidfile.exists())
        finally:
            sm.RUNTIME_DIR = original_runtime
            sm._is_pid_alive = original_is_pid_alive


def test_http_owner_cleanup_is_scoped():
    print("\n=== Test: HTTP Worker owner cleanup stays scoped ===")
    original_runtime = sm.RUNTIME_DIR
    original_stop_worker = sm.stop_worker
    stopped = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            sm.stop_worker = lambda dcc: stopped.append(dcc) or True
            sm.claim_worker_owner(1001)

            _check("非 owner 不停止 Worker", not sm.shutdown_worker_owned_by(2002))
            _check("非 owner 保留记录", sm._worker_owner_path().exists())
            _check("非 owner 未调用 stop", stopped == [], stopped)

            _check("owner 停止 Worker", sm.shutdown_worker_owned_by(1001))
            _check("只停止 canonical Worker", stopped == ["cgi"], stopped)
            _check("owner 记录已清理", not sm._worker_owner_path().exists())
        finally:
            sm.RUNTIME_DIR = original_runtime
            sm.stop_worker = original_stop_worker


def test_start_worker_loser_awaits_not_popen():
    print("\n=== Test: 抢锁输家等就绪、不 Popen 竞争 ===")
    import subprocess as _subp
    original = {
        "RUNTIME_DIR": sm.RUNTIME_DIR,
        "is_worker_alive": sm.is_worker_alive,
        "await": sm._await_worker_ready,
        "popen": _subp.Popen,
    }
    popen_calls = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            # 模拟已有别的 starter 持锁
            sm._acquire_start_lock("maya", stale_after_sec=60.0)
            sm.is_worker_alive = lambda dcc: (False, None)
            sm._await_worker_ready = lambda dcc, t=30.0: True
            _subp.Popen = lambda *a, **k: popen_calls.append(a) or None

            ok = sm.start_worker("cgi", wait=True, timeout_sec=1.0)
            _check("输家返回就绪成功", ok)
            _check("输家未 Popen 竞争", popen_calls == [], popen_calls)
            sm._release_start_lock("maya")
        finally:
            sm.RUNTIME_DIR = original["RUNTIME_DIR"]
            sm.is_worker_alive = original["is_worker_alive"]
            sm._await_worker_ready = original["await"]
            _subp.Popen = original["popen"]


def test_start_worker_winner_popens_and_releases():
    print("\n=== Test: 抢锁赢家 Popen 一次并释放锁 ===")
    import subprocess as _subp
    original = {
        "RUNTIME_DIR": sm.RUNTIME_DIR,
        "is_worker_alive": sm.is_worker_alive,
        "await": sm._await_worker_ready,
        "popen": _subp.Popen,
        "managed": list(sm._managed_procs),
    }
    popen_calls = []

    class _FakeProc:
        pid = 4242

    with tempfile.TemporaryDirectory() as tmp:
        try:
            sm.RUNTIME_DIR = Path(tmp)
            sm.is_worker_alive = lambda dcc: (False, None)
            sm._await_worker_ready = lambda dcc, t=30.0: True
            _subp.Popen = lambda *a, **k: popen_calls.append(a) or _FakeProc()

            ok = sm.start_worker("cgi", wait=True, timeout_sec=1.0)
            _check("赢家启动成功", ok)
            _check("恰好 Popen 一次", len(popen_calls) == 1, popen_calls)
            lock = sm.RUNTIME_DIR / "worker_cgi.starting.lock"
            _check("启动锁已释放", not lock.exists())
        finally:
            sm.RUNTIME_DIR = original["RUNTIME_DIR"]
            sm.is_worker_alive = original["is_worker_alive"]
            sm._await_worker_ready = original["await"]
            _subp.Popen = original["popen"]
            sm._managed_procs[:] = original["managed"]


if __name__ == "__main__":
    print("=== service_manager health tests ===")
    test_active_queues_include()
    test_default_heartbeat_timeout()
    test_dead_pidfile_cleanup()
    test_ensure_busy_worker_not_killed()
    test_ensure_healthy_no_restart()
    test_ensure_available_skips_heartbeat_for_live_worker()
    test_ensure_available_starts_missing_worker()
    test_service_status_alive_uses_heartbeat()
    test_start_lock_acquire_release()
    test_clear_stale_pidfile()
    test_http_owner_cleanup_is_scoped()
    test_start_worker_loser_awaits_not_popen()
    test_start_worker_winner_popens_and_releases()
    print("\n✅ all pass")
