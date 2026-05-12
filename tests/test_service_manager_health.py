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
        "cgi_maya@host": [{"name": "dcc_queue"}, {"name": "celery"}],
        "cgi_blender@host": [{"name": "blender_queue"}],
    }
    ok, nodes = sm._active_queues_include(active, "dcc_queue")
    _check("找到 dcc_queue", ok)
    _check("返回消费节点", nodes == ["cgi_maya@host"], nodes)

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
            pidfile = sm.RUNTIME_DIR / "worker_maya.pid"
            pidfile.write_text("12345", encoding="utf-8")
            sm._is_pid_alive = lambda pid, expected_name="python": False

            health = sm.get_worker_health("maya")
            _check("状态为 DEAD", health["state"] == "DEAD", health)
            _check("坏 pidfile 被移除", not pidfile.exists())
        finally:
            sm.RUNTIME_DIR = original_runtime
            sm._is_pid_alive = original_is_pid_alive


def test_ensure_restarts_stale_worker():
    print("\n=== Test: 心跳丢失自动重启 ===")
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
        _check("pipeline 映射到 maya 并重启成功", ok and calls == ["maya"], calls)
        _check("返回自动重启说明", "自动重启" in msg, msg)
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
        sm.is_worker_alive = lambda dcc: (True, {"maya": 1, "blender": 2, "workflow": 3}[dcc])

        def fake_health(dcc, heartbeat_timeout_sec=sm.DEFAULT_HEARTBEAT_TIMEOUT_SEC):
            state = "HEALTHY" if dcc == "blender" else "NO_HEARTBEAT"
            return {
                "state": state,
                "pid_alive": True,
                "pid": {"maya": 1, "blender": 2, "workflow": 3}[dcc],
                "celery_alive": state == "HEALTHY",
            }

        sm.get_worker_health = fake_health

        status = sm.get_service_status(include_heartbeat=True)
        _check("Maya PID 存活但无心跳时不算 alive", status["worker_maya"]["alive"] is False, status)
        _check("Blender 心跳健康时 alive", status["worker_blender"]["alive"] is True, status)
    finally:
        sm.is_redis_alive = originals["is_redis_alive"]
        sm.is_worker_alive = originals["is_worker_alive"]
        sm.get_worker_health = originals["get_worker_health"]


if __name__ == "__main__":
    print("=== service_manager health tests ===")
    test_active_queues_include()
    test_default_heartbeat_timeout()
    test_dead_pidfile_cleanup()
    test_ensure_restarts_stale_worker()
    test_ensure_healthy_no_restart()
    test_service_status_alive_uses_heartbeat()
    print("\n✅ all pass")
