from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError


@pytest.fixture(autouse=True)
def default_slot_budget(monkeypatch):
    """Pin the documented default; the host .env (loaded on import) may configure another one."""
    monkeypatch.setenv('CGI_RUNTIME_MAX_SLOTS', '3')


def ample_memory():
    return 64 * 1024 ** 3


def pool_at(root, **kwargs):
    return RuntimePool(root, memory_probe=ample_memory, allow_external_root=True, **kwargs)


def ready(pool, wid='one'):
    lease = pool.acquire('default', wid)
    return pool.set_worker_info(lease, {k: lease[k] for k in ('runtime_id', 'generation', 'root')})


def acquire_in_process(root, wid, barrier, results):
    barrier.wait(timeout=30)
    try:
        value = pool_at(root).acquire('default', wid)
        results.put(('ok', value))
    except RuntimePoolError as exc:
        results.put(('error', exc.code))


def test_three_slots_and_fourth_rejected_without_mutating_bound_slots(tmp_path):
    pool = pool_at(tmp_path)
    leases = [pool.acquire('default', str(i)) for i in range(3)]
    before = pool.status()
    with pytest.raises(RuntimePoolError) as failure:
        pool.acquire('default', 'fourth')
    assert failure.value.code == 'RUNTIME_CAPACITY_BUSY'
    assert failure.value.dispatched is False
    assert {row['queue'] for row in leases} == {f'cgi_slot_{i}_{pool.namespace}' for i in range(1, 4)}
    assert len({row['generation'] for row in leases}) == 3
    assert pool.status() == before
    assert pool.acquire('default', '0') == leases[0]


def test_allocation_is_atomic_across_independent_processes(tmp_path):
    context = multiprocessing.get_context('spawn')
    barrier, results = context.Barrier(6), context.Queue()
    processes = [context.Process(target=acquire_in_process,
                                 args=(str(tmp_path), str(i), barrier, results)) for i in range(6)]
    for process in processes:
        process.start()
    rows = [results.get(timeout=45) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    accepted = [value for status, value in rows if status == 'ok']
    assert len(accepted) == 3
    assert len({lease['runtime_id'] for lease in accepted}) == 3
    assert [value for status, value in rows if status == 'error'] == ['RUNTIME_CAPACITY_BUSY'] * 3
    assert len(pool_at(tmp_path).status()) == 3


def test_same_workspace_affinity_across_clients_and_projects(tmp_path):
    def acquire(_):
        return pool_at(tmp_path).acquire('default', 'same')
    with ThreadPoolExecutor(max_workers=8) as executor:
        leases = list(executor.map(acquire, range(16)))
    assert len({row['generation'] for row in leases}) == 1
    assert len(pool_at(tmp_path).status()) == 1
    assert pool_at(tmp_path).acquire('other', 'same')['runtime_id'] == 'slot-2'


def test_memory_admission_checks_actual_free_ram_only_for_new_assignments(tmp_path):
    pool = pool_at(tmp_path)
    lease = pool.acquire('default', 'one')
    low = RuntimePool(tmp_path, min_free_gb=8, memory_probe=lambda: 7 * 1024 ** 3,
                      allow_external_root=True)
    assert low.acquire('default', 'one') == lease
    with pytest.raises(RuntimePoolError) as failure:
        low.acquire('default', 'two')
    assert failure.value.code == 'RUNTIME_MEMORY_LOW'
    assert len(pool.status()) == 1


@pytest.mark.parametrize('field,value', [('generation', 'foreign'), ('workspace_id', 'other'),
                                       ('project', 'other'), ('root', 'C:/foreign'),
                                       ('queue', 'cgi_queue'), ('runtime_id', 'slot-9')])
def test_every_lease_identity_field_is_fenced(tmp_path, field, value):
    pool = pool_at(tmp_path)
    lease = pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError) as failure:
        pool.validate({**lease, field: value})
    assert failure.value.code == 'RUNTIME_GENERATION_MISMATCH'
    assert pool.validate({**lease, 'state': 'invented'})['state'] == 'STARTING'


def test_pending_active_and_draining_ownership(tmp_path):
    pool = pool_at(tmp_path)
    lease = ready(pool)
    pool.reserve(lease, 'first')
    pool.reserve(lease, 'second')
    with pytest.raises(RuntimePoolError, match='Accepted or active'):
        pool.begin_release(lease)
    # Broker acceptance order can differ from reservation order.
    pool.set_busy(lease, 'second')
    with pytest.raises(RuntimePoolError, match='another active'):
        pool.set_busy(lease, 'first')
    with pytest.raises(RuntimePoolError, match='Only the active'):
        pool.set_idle(lease, 'first')
    pool.set_idle(lease, 'second')
    pool.unreserve(lease, 'first')
    draining = pool.begin_release(lease)
    assert draining['state'] == 'DRAINING'
    with pytest.raises(RuntimePoolError, match='accepting'):
        pool.reserve(lease, 'late')
    assert pool.finish_release(lease, False)['state'] == 'IDLE'
    pool.begin_release(lease)
    assert pool.finish_release(lease, True)['state'] == 'RELEASED'
    assert pool.status() == []
    next_lease = pool.acquire('default', 'two')
    assert next_lease['runtime_id'] == lease['runtime_id']
    assert next_lease['generation'] != lease['generation']
    with pytest.raises(RuntimePoolError):
        pool.validate(lease)


def test_unreserved_task_cannot_execute_and_duplicate_reservation_is_idempotent(tmp_path):
    pool = pool_at(tmp_path)
    lease = ready(pool)
    with pytest.raises(RuntimePoolError) as failure:
        pool.set_busy(lease, 'not-sent')
    assert failure.value.code == 'RUNTIME_TASK_NOT_RESERVED'
    pool.reserve(lease, 'one')
    pool.reserve(lease, 'one')
    assert len(pool.validate(lease)['pending']) == 1
    pool.set_busy(lease, 'one')
    pool.reserve(lease, 'one')
    assert pool.validate(lease)['pending'] == {}


def test_lost_binding_survives_slot_reuse_and_needs_explicit_open(tmp_path):
    pool = pool_at(tmp_path, max_slots=1)
    lease = ready(pool)
    with pytest.raises(RuntimePoolError) as failure:
        pool.mark_lost(lease)
    assert failure.value.code == 'RUNTIME_DEATH_UNCONFIRMED'
    assert pool.mark_lost(lease, confirmed_dead=True)['state'] == 'LOST'
    second = pool.acquire('default', 'two')
    assert pool.status('default', 'one')[0]['state'] == 'LOST'
    with pytest.raises(RuntimePoolError) as failure:
        pool.acquire('default', 'one')
    assert failure.value.code == 'RUNTIME_LOST'
    pool.rollback(second)
    rebuilt = pool.acquire('default', 'one', rebuild=True)
    assert rebuilt['generation'] != lease['generation']
    assert {row['state'] for row in pool.status('default', 'one')} == {'STARTING', 'LOST'}
    pool.set_worker_info(rebuilt, {k: rebuilt[k] for k in ('runtime_id', 'generation', 'root')})
    assert len(pool.status('default', 'one')) == 1


def test_failed_rebuild_retains_lost_binding_and_still_requires_open(tmp_path):
    pool = pool_at(tmp_path)
    lease = ready(pool, 'one')
    pool.mark_lost(lease, confirmed_dead=True)
    rebuilt = pool.acquire('default', 'one', rebuild=True)
    pool.rollback(rebuilt)
    assert pool.status('default', 'one')[0]['state'] == 'LOST'
    with pytest.raises(RuntimePoolError) as failure:
        pool.acquire('default', 'one')
    assert failure.value.code == 'RUNTIME_LOST'


def test_start_failure_rollback_frees_only_unstarted_assignment(tmp_path):
    pool = pool_at(tmp_path, max_slots=1)
    lease = pool.acquire('default', 'one')
    pool.rollback(lease)
    second = ready(pool, 'two')
    with pytest.raises(RuntimePoolError) as failure:
        pool.rollback(second)
    assert failure.value.code == 'RUNTIME_ROLLBACK_UNSAFE'


@pytest.mark.parametrize('project,wid', [('', 'one'), ('../escape', 'one'), ('default', '../escape'),
                                       ('default', ''), ('default', None)])
def test_invalid_binding_does_not_create_ledger(tmp_path, project, wid):
    pool = pool_at(tmp_path)
    with pytest.raises(RuntimePoolError):
        pool.acquire(project, wid)
    assert not pool.path.exists()


def test_corrupt_ledger_is_not_reset_or_reallocated(tmp_path):
    pool = pool_at(tmp_path)
    lease = pool.acquire('default', 'one')
    pool.path.write_text('{broken', encoding='utf-8')
    with pytest.raises(RuntimePoolError) as failure:
        pool.acquire('default', 'two')
    assert failure.value.code == 'RUNTIME_STATE_INVALID'
    assert pool.path.read_text() == '{broken'


def test_configuration_and_status_filters(tmp_path, monkeypatch):
    monkeypatch.setenv('CGI_RUNTIME_MAX_SLOTS', '1')
    monkeypatch.setenv('CGI_RUNTIME_MIN_FREE_GB', '4')
    monkeypatch.setenv('CGI_RUNTIME_THREADS', '2')
    pool = pool_at(tmp_path)
    assert (pool.max_slots, pool.min_free_gb, pool.threads) == (1, 4, 2)
    lease = pool.acquire('default', 'one')
    assert lease['threads'] == 2
    assert pool.status('other') == []
    assert pool.status(workspace_id='one') == [lease]
    assert json.loads(pool.path.read_text())['version'] == 1
    with pytest.raises(ValueError):
        pool_at(tmp_path, max_slots=0)


def test_production_root_environment_and_constructor_are_managed(tmp_path, monkeypatch):
    from cgi_pipeline.paths import REPOSITORY_ROOT
    foreign = REPOSITORY_ROOT.parent / 'outside_runtime_pool'
    monkeypatch.setenv('CGI_RUNTIME_POOL_ROOT', str(foreign))
    with pytest.raises(RuntimePoolError) as failure:
        RuntimePool()
    assert failure.value.code == 'RUNTIME_ROOT_INVALID'
    with pytest.raises(RuntimePoolError):
        RuntimePool(foreign)
    managed = REPOSITORY_ROOT / 'projects' / 'default' / 'PL274' / 'work' / 'live_runtime'
    monkeypatch.setenv('CGI_RUNTIME_POOL_ROOT', str(managed))
    assert RuntimePool().root == managed.resolve()


def test_default_queue_names_remain_stable_and_root_namespace_is_deterministic(tmp_path, monkeypatch):
    from cgi_pipeline.paths import REPOSITORY_ROOT
    monkeypatch.delenv('CGI_RUNTIME_POOL_ROOT', raising=False)
    assert RuntimePool().root == (REPOSITORY_ROOT / 'runtime' / 'slots').resolve()
    assert RuntimePool().namespace == ''
    one = pool_at(tmp_path / 'pool_one')
    again = pool_at(tmp_path / 'pool_one' / '..' / 'pool_one')
    two = pool_at(tmp_path / 'pool_two')
    assert one.namespace == again.namespace
    assert one.namespace != two.namespace
    assert one.acquire('default', 'one')['queue'] != two.acquire('default', 'one')['queue']


def test_unreserve_never_changes_active_owner_and_absent_pending_is_a_noop(tmp_path):
    pool = pool_at(tmp_path)
    lease = ready(pool)
    pool.reserve(lease, 'active')
    pool.set_busy(lease, 'active')
    before = pool.path.read_bytes()
    with pytest.raises(RuntimePoolError) as failure:
        pool.unreserve(lease, 'active')
    assert failure.value.code == 'RUNTIME_BUSY'
    assert pool.path.read_bytes() == before
    assert pool.unreserve(lease, 'absent')['active_task'] == 'active'
    assert pool.path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('pid', 4321), ('create_time', 2.5), ('cmdline', ['foreign']),
                                       ('generation', 'foreign'), ('root', 'foreign')])
def test_unreserve_with_worker_proof_rejects_foreign_process_atomically(tmp_path, field, value):
    pool = pool_at(tmp_path)
    lease = pool.acquire('default', 'one')
    identity = {**{key: lease[key] for key in ('runtime_id', 'generation', 'root')},
                'pid': 1234, 'create_time': 1.25, 'cmdline': ['worker']}
    lease = pool.set_worker_info(lease, identity)
    pool.reserve(lease, 'one')
    before = pool.path.read_bytes()
    with pytest.raises(RuntimePoolError) as failure:
        pool.unreserve(lease, 'one', worker={**identity, field: value})
    assert failure.value.code == 'RUNTIME_PROCESS_MISMATCH'
    assert pool.path.read_bytes() == before
    assert pool.unreserve(lease, 'one', worker=identity)['pending'] == {}
