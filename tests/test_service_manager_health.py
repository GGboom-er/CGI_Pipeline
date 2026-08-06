"""service_manager Worker 心跳与自动重启单元测试。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import service_manager as sm


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
        _check("兼容别名与 canonical 状态一致", status["worker_maya"]["alive"] is False and status["worker_blender"]["alive"] is False, status)
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
    test_service_status_alive_uses_heartbeat()
    test_start_lock_acquire_release()
    test_clear_stale_pidfile()
    test_start_worker_loser_awaits_not_popen()
    test_start_worker_winner_popens_and_releases()
    print("\n✅ all pass")
