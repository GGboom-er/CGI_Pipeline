import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
        return
    print(f"  [FAIL] {name} {detail}")
    raise AssertionError(name)


def test_warm_worker_proxy_start_contract():
    print("\n=== Test: WarmWorkerProxy 兼容 CLI start() ===")
    from core.dcc_factory import WarmWorkerProxy

    _check("WarmWorkerProxy 暴露 start 方法", callable(getattr(WarmWorkerProxy, "start", None)))


def test_cli_still_starts_created_worker():
    print("\n=== Test: CLI run-skill worker 接口约定 ===")
    from core.dcc_factory import WarmWorkerProxy

    source = open(os.path.join(ROOT, "cli.py"), encoding="utf-8").read()
    if "worker.start()" in source:
        _check("CLI 显式 start 时 WarmWorkerProxy 可兼容", callable(getattr(WarmWorkerProxy, "start", None)))
    _check("CLI 显式关闭 worker", "worker.shutdown()" in source)


if __name__ == "__main__":
    print("=== cli worker contract tests ===")
    test_warm_worker_proxy_start_contract()
    test_cli_still_starts_created_worker()
    print("\n✅ all pass")
