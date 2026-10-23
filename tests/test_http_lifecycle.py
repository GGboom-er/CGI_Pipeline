"""Admission and drain race tests without using the production HTTP listener."""

import asyncio
import threading

import pytest

from cgi_pipeline.server.http_lifecycle import HttpLifecycle, LifecycleError


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


def test_drain_refuses_new_roots_but_allows_children_of_admitted_work():
    lifecycle = HttpLifecycle()
    with lifecycle.activity("request"):
        drained = lifecycle.drain(0, 15)
        assert drained["active_count"] == 1
        with pytest.raises(LifecycleError, match="draining") as rejected:
            with lifecycle.activity("new request"):
                pytest.fail("new work ran during drain")
        assert rejected.value.code == "HTTP_DRAINING"
        with lifecycle.activity("already admitted child", inherit=True):
            assert lifecycle.status()["active_count"] == 2
    assert lifecycle.status()["active_count"] == 0


def test_expired_lease_restores_admission_and_invalidates_stale_stop():
    clock = Clock()
    lifecycle = HttpLifecycle(clock=clock)
    epoch = lifecycle.drain(0, 2)["lease_epoch"]
    clock.now += 2
    with lifecycle.activity("request after lost manager"):
        assert lifecycle.status()["state"] == "accepting"
    with pytest.raises(LifecycleError) as rejected:
        lifecycle.commit_stop(epoch, lambda: pytest.fail("stale manager stopped server"))
    assert rejected.value.code == "HTTP_LEASE_CONFLICT"


def test_renew_resume_and_stop_use_exact_epochs():
    clock = Clock()
    lifecycle = HttpLifecycle(clock=clock)
    epoch = lifecycle.drain(0, 2)["lease_epoch"]
    clock.now += 1
    assert lifecycle.drain(epoch, 5)["lease_epoch"] == epoch
    assert lifecycle.status()["lease_remaining_seconds"] == 5
    resumed = lifecycle.resume(epoch)
    assert resumed["lease_epoch"] > epoch
    with pytest.raises(LifecycleError):
        lifecycle.resume(epoch)
    with pytest.raises(LifecycleError):
        lifecycle.drain(epoch, 2)


def test_stop_requires_zero_actual_work_and_commits_only_once():
    lifecycle = HttpLifecycle()
    stopped = []
    with lifecycle.activity("request"):
        epoch = lifecycle.drain(0, 15)["lease_epoch"]
        with pytest.raises(LifecycleError) as rejected:
            lifecycle.commit_stop(epoch, lambda: stopped.append(True))
        assert rejected.value.code == "HTTP_ACTIVE_WORK"
    assert lifecycle.commit_stop(epoch, lambda: stopped.append(True))["state"] == "stopping"
    assert stopped == [True]
    with pytest.raises(LifecycleError):
        lifecycle.commit_stop(epoch, lambda: stopped.append(True))
    with pytest.raises(LifecycleError):
        lifecycle.resume(epoch)
    with pytest.raises(LifecycleError):
        with lifecycle.activity("too late"):
            pass
    assert stopped == [True]


@pytest.mark.parametrize("lease", [None, True, "15", 0, -1, 301, float("nan"), float("inf")])
def test_invalid_leases_have_no_state_effect(lease):
    lifecycle = HttpLifecycle()
    with pytest.raises(LifecycleError) as rejected:
        lifecycle.drain(0, lease)
    assert rejected.value.code == "HTTP_CONTROL_INVALID"
    assert lifecycle.status() == {"state": "accepting", "active_count": 0,
                                  "lease_epoch": 0, "lease_remaining_seconds": 0}


@pytest.mark.parametrize("epoch", [None, True, "0", 0.0])
def test_invalid_epoch_types_are_rejected(epoch):
    lifecycle = HttpLifecycle()
    with pytest.raises(LifecycleError) as rejected:
        lifecycle.drain(epoch, 15)
    assert rejected.value.code == "HTTP_CONTROL_INVALID"


def test_request_exception_releases_token_and_foreign_context_cannot_derive():
    first, second = HttpLifecycle(), HttpLifecycle()
    second.drain(0, 15)
    with pytest.raises(ValueError):
        with first.activity("request"):
            with pytest.raises(LifecycleError):
                with second.activity("foreign parent", inherit=True):
                    pass
            raise ValueError("request failed")
    assert first.status()["active_count"] == 0


def test_cancelled_async_caller_does_not_release_live_executor_thread():
    async def scenario():
        lifecycle = HttpLifecycle()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def blocking():
            started.set()
            assert release.wait(3)
            finished.set()

        with lifecycle.activity("request"):
            task = asyncio.create_task(lifecycle.run_sync(blocking))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lifecycle.status()["active_count"] == 2
        assert lifecycle.status()["active_count"] == 1
        epoch = lifecycle.drain(0, 15)["lease_epoch"]
        with pytest.raises(LifecycleError):
            lifecycle.commit_stop(epoch, lambda: pytest.fail("live thread was lost"))
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        for _ in range(100):
            if lifecycle.status()["active_count"] == 0:
                break
            await asyncio.sleep(0.001)
        assert lifecycle.status()["active_count"] == 0

    asyncio.run(scenario())


def test_run_sync_can_derive_after_drain_and_releases_on_failure():
    async def scenario():
        lifecycle = HttpLifecycle()
        with lifecycle.activity("root"):
            lifecycle.drain(0, 15)

            def failure():
                assert lifecycle.status()["active_count"] == 2
                raise ValueError("worker failed")

            with pytest.raises(ValueError, match="worker failed"):
                await lifecycle.run_sync(failure)
            assert lifecycle.status()["active_count"] == 1
        assert lifecycle.status()["active_count"] == 0
    asyncio.run(scenario())


def test_cancelled_queued_executor_work_stays_registered_until_it_really_runs():
    from concurrent.futures import ThreadPoolExecutor

    async def scenario():
        lifecycle = HttpLifecycle()
        occupied, release, executed = threading.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

        def occupy_executor():
            occupied.set()
            assert release.wait(3)

        holder = loop.run_in_executor(None, occupy_executor)
        assert occupied.wait(2)
        with lifecycle.activity("root"):
            task = asyncio.create_task(lifecycle.run_sync(executed.set))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lifecycle.status()["active_count"] == 2
        assert lifecycle.status()["active_count"] == 1
        assert not executed.is_set()
        release.set()
        await holder
        for _ in range(100):
            if lifecycle.status()["active_count"] == 0:
                break
            await asyncio.sleep(0.001)
        assert executed.is_set() and lifecycle.status()["active_count"] == 0
    asyncio.run(scenario())


def test_executor_submission_failure_releases_registration(monkeypatch):
    async def scenario():
        lifecycle = HttpLifecycle()
        loop = asyncio.get_running_loop()

        def fail(*args, **kwargs):
            raise RuntimeError("executor unavailable")

        monkeypatch.setattr(loop, "run_in_executor", fail)
        with pytest.raises(RuntimeError, match="executor unavailable"):
            await lifecycle.run_sync(lambda: None)
        assert lifecycle.status()["active_count"] == 0
    asyncio.run(scenario())


def test_detached_thread_registered_before_start_and_until_finally(monkeypatch):
    lifecycle = HttpLifecycle()
    release, started = threading.Event(), threading.Event()
    original_start = threading.Thread.start

    def inspect_start(thread):
        assert lifecycle.status()["active_count"] == 2
        return original_start(thread)

    def work():
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(threading.Thread, "start", inspect_start)
    with lifecycle.activity("root"):
        lifecycle.drain(0, 15)
        thread = lifecycle.spawn_thread(work)
        assert started.wait(2)
    assert lifecycle.status()["active_count"] == 1
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert lifecycle.status()["active_count"] == 0


def test_thread_start_failure_releases_registration(monkeypatch):
    lifecycle = HttpLifecycle()

    def fail(thread):
        raise RuntimeError("cannot start")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="cannot start"):
        lifecycle.spawn_thread(lambda: None)
    assert lifecycle.status()["active_count"] == 0


def test_admission_and_drain_race_never_permits_unregistered_work():
    for _ in range(20):
        lifecycle = HttpLifecycle()
        barrier, release = threading.Barrier(2), threading.Event()
        outcomes = []

        def request():
            barrier.wait()
            try:
                with lifecycle.activity("racer"):
                    outcomes.append("admitted")
                    release.wait(2)
            except LifecycleError:
                outcomes.append("refused")

        thread = threading.Thread(target=request)
        thread.start()
        barrier.wait()
        epoch = lifecycle.drain(0, 15)["lease_epoch"]
        if lifecycle.status()["active_count"]:
            with pytest.raises(LifecycleError):
                lifecycle.commit_stop(epoch, lambda: None)
        else:
            lifecycle.commit_stop(epoch, lambda: None)
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert outcomes in (["admitted"], ["refused"])
        assert lifecycle.status()["active_count"] == 0
