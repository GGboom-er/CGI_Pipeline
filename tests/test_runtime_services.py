from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.runtime_services import RuntimeService
from cgi_pipeline.core.runtime_state import snapshot
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT


class FakeProcess:
    def __init__(self, pid, command, environment, *, birth=100, children=()):
        self.pid, self.command, self.environment = pid, command, environment
        self.birth, self.descendants, self.alive, self.denied = birth, list(children), True, False

    def is_running(self):
        if self.denied:
            raise psutil.AccessDenied(self.pid)
        return self.alive

    def status(self):
        return 'running'

    def environ(self):
        return dict(self.environment)

    def create_time(self):
        return self.birth

    def cmdline(self):
        return list(self.command)

    def children(self, recursive):
        return self.descendants


class Harness:
    def __init__(self, root, **kwargs):
        self.pool = RuntimePool(root, memory_probe=lambda: 64 * 1024 ** 3, allow_external_root=True)
        self.processes, self.spawns, self.kills, self.probes = {}, [], [], []
        self.next_pid = 500
        self.service = RuntimeService(self.pool, spawn=self.spawn, kill_tree=self.kill,
                                      process_factory=self.process, redis_start=lambda: True,
                                      queue_probe=kwargs.pop('queue_probe', self.queue_ready), **kwargs)

    def process(self, pid):
        if pid not in self.processes or not self.processes[pid].alive:
            raise psutil.NoSuchProcess(pid)
        return self.processes[pid]

    def spawn(self, command, *, cwd, env):
        self.next_pid += 1
        process = FakeProcess(self.next_pid, command, env)
        self.processes[process.pid] = process
        self.spawns.append((command, cwd, env))
        return process

    def kill(self, pid, timeout):
        self.kills.append(pid)
        process = self.processes[pid]
        process.alive = False
        for child in process.descendants:
            child.alive = False
        return True

    def queue_ready(self, lease, identity):
        self.probes.append((lease['queue'], identity['node']))
        return True

    def start(self, wid='one'):
        lease = self.pool.acquire('default', wid)
        identity = self.service.ensure(lease)
        return lease, identity

    def host(self, lease, identity, *, pid=700, foreground=False):
        environment = dict(self.processes[identity['pid']].environment)
        if foreground:
            environment = {}
        process = FakeProcess(pid, ['dcc', 'adapter.py'], environment, birth=200)
        self.processes[pid] = process
        self.processes[identity['pid']].descendants.append(process)
        if not foreground:
            data = {**snapshot(pid, self.process), 'host': 'maya', 'worker_id': 'host-one'}
            root = Path(lease['root']) / 'hosts'
            root.mkdir(exist_ok=True)
            (root / 'host-one.json').write_text(json.dumps(data), encoding='utf-8')
            launches = Path(lease['root']) / 'host_launches'
            launches.mkdir(exist_ok=True)
            (launches / 'host-one.json').write_text(json.dumps({**data, 'state': 'COMMITTED'}), encoding='utf-8')
        return process


def test_start_is_scoped_and_environment_preserves_source_and_parent(tmp_path, monkeypatch):
    monkeypatch.setenv('MAYA_APP_DIR', 'C:/shared/maya')
    monkeypatch.setenv('BLENDER_USER_SCRIPTS', 'C:/shared/blender_scripts')
    monkeypatch.setenv('PYTHONPATH', 'C:/shared/python')
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    command, cwd, env = harness.spawns[0]
    assert cwd == REPOSITORY_ROOT
    assert command[command.index('-Q') + 1] == lease['queue']
    assert '--pool=solo' in command and command[command.index('-c') + 1] == '1'
    assert env['CGI_RUNTIME_ID'] == lease['runtime_id']
    assert env['CGI_RUNTIME_GENERATION'] == lease['generation']
    assert env['CGI_RUNTIME_POOL_ROOT'] == str(harness.pool.root)
    assert env['CGI_RUNTIME_THREADS'] == '6'
    assert f'--pidfile={Path(lease["root"]) / ("worker-" + lease["generation"] + ".pid")}' in command
    assert env['TEMP'] == str(Path(lease['root']) / 'temp')
    assert env['MAYA_APP_DIR'] == 'C:/shared/maya'
    assert env['BLENDER_USER_SCRIPTS'] == 'C:/shared/blender_scripts'
    assert str(SOURCE_ROOT) in env['PYTHONPATH'] and 'C:/shared/python' in env['PYTHONPATH']
    assert harness.pool.validate(lease)['worker'] == identity
    assert (Path(lease['root']) / 'owner.json').is_file()
    assert __import__('os').environ['MAYA_APP_DIR'] == 'C:/shared/maya'
    assert 'CGI_RUNTIME_ID' not in __import__('os').environ


def test_two_clients_racing_ensure_spawn_once_and_release_refuses_starting(tmp_path):
    entered, proceed = threading.Event(), threading.Event()
    harness = Harness(tmp_path)
    original_probe = harness.service.queue_probe

    def probe(lease, identity):
        entered.set()
        assert proceed.wait(timeout=5)
        return original_probe(lease, identity)

    harness.service.queue_probe = probe
    lease = harness.pool.acquire('default', 'one')
    other = RuntimeService(harness.pool, spawn=harness.spawn, process_factory=harness.process,
                           kill_tree=harness.kill, redis_start=lambda: True, queue_probe=probe)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(harness.service.ensure, lease)
        assert entered.wait(timeout=5)
        second = executor.submit(other.ensure, lease)
        with pytest.raises(RuntimePoolError) as failure:
            harness.pool.begin_release(lease)
        assert failure.value.code == 'RUNTIME_BUSY'
        proceed.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert len(harness.spawns) == 1
    assert harness.pool.set_worker_info(lease, first.result())['state'] == 'IDLE'


def test_busy_health_and_reuse_never_wait_for_celery_heartbeat(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    harness.pool.reserve(lease, 'operation')
    harness.pool.set_busy(lease, 'operation')
    harness.service.queue_probe = lambda *args: pytest.fail('Busy health must not query broker.')
    assert harness.service.ensure(lease) == identity
    health = harness.service.health(lease)
    assert health['state'] == 'LIVE' and health['dirty_state'] == 'UNKNOWN'
    assert not health['confirmed_dead']


@pytest.mark.parametrize('failure', ['broker', 'queue'])
def test_start_failures_roll_back_only_after_confirming_process_death(tmp_path, failure):
    harness = Harness(tmp_path, timeout_sec=0)
    if failure == 'broker':
        harness.service.redis_start = lambda: False
    if failure == 'queue':
        harness.service.queue_probe = lambda *args: False
    lease = harness.pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError) as caught:
        harness.service.ensure(lease)
    assert caught.value.code == 'RUNTIME_START_FAILED'
    assert harness.pool.status() == []
    assert not any(process.alive for process in harness.processes.values())


def test_spawn_loses_return_after_launch_retains_unknown_slot(tmp_path):
    harness = Harness(tmp_path)
    def spawn(*args, **kwargs):
        harness.spawn(*args, **kwargs)
        raise OSError('launch return lost')
    harness.service.spawn = spawn
    lease = harness.pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError) as caught:
        harness.service.ensure(lease)
    assert caught.value.code == 'RUNTIME_START_UNCONFIRMED'
    assert harness.pool.validate(lease)['state'] == 'STARTING'
    assert harness.processes[501].alive
    with pytest.raises(RuntimePoolError):
        harness.service.ensure(lease)
    assert len(harness.spawns) == 1


def test_queue_response_does_not_publish_a_dead_worker(tmp_path):
    harness = Harness(tmp_path)
    def probe(lease, identity):
        harness.processes[identity['pid']].alive = False
        return True
    harness.service.queue_probe = probe
    lease = harness.pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError):
        harness.service.ensure(lease)
    assert not any(row['state'] == 'IDLE' for row in harness.pool.status())


def test_unconfirmed_start_cleanup_retains_assignment(tmp_path):
    harness = Harness(tmp_path, timeout_sec=0, queue_probe=lambda *args: False)
    harness.service.kill_tree = lambda *args, **kwargs: False
    lease = harness.pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError) as failure:
        harness.service.ensure(lease)
    assert failure.value.code == 'RUNTIME_START_UNCONFIRMED'
    assert harness.pool.validate(lease)['state'] == 'STARTING'
    assert (Path(lease['root']) / 'owner.json').is_file()


def test_recover_published_worker_after_starter_disappeared(tmp_path):
    harness = Harness(tmp_path)
    lease = harness.pool.acquire('default', 'one')
    process = harness.spawn(harness.service._command(lease), cwd=REPOSITORY_ROOT,
                            env=harness.service._environment(lease))
    identity = {**snapshot(process.pid, harness.process), 'node': harness.service._node(lease)}
    (Path(lease['root']) / 'owner.json').write_text(json.dumps(identity), encoding='utf-8')
    assert harness.service.ensure(lease) == identity
    assert harness.pool.validate(lease)['state'] == 'IDLE'
    assert len(harness.spawns) == 1


def test_unknown_launch_intent_never_starts_duplicate_worker_or_frees_slot(tmp_path):
    harness = Harness(tmp_path)
    lease = harness.pool.acquire('default', 'one')
    root = Path(lease['root'])
    root.mkdir()
    (root / 'launch.json').write_text(json.dumps({'generation': lease['generation']}), encoding='utf-8')
    with pytest.raises(RuntimePoolError) as failure:
        harness.service.ensure(lease)
    assert failure.value.code == 'RUNTIME_START_UNCONFIRMED'
    assert harness.service.health(lease)['state'] == 'UNKNOWN'
    assert harness.pool.validate(lease)['state'] == 'STARTING'
    assert harness.spawns == []


def test_access_denied_after_spawn_retains_unconfirmed_launch(tmp_path):
    harness = Harness(tmp_path)
    original_spawn = harness.service.spawn
    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        process.denied = True
        return process
    harness.service.spawn = spawn
    lease = harness.pool.acquire('default', 'one')
    with pytest.raises(RuntimePoolError) as failure:
        harness.service.ensure(lease)
    assert failure.value.code == 'RUNTIME_IDENTITY_UNKNOWN'
    assert len(harness.pool.status()) == 1
    assert harness.kills == []


@pytest.mark.parametrize('changed', ['birth', 'command', 'environment', 'denied'])
def test_stop_refuses_reused_foreign_or_uninspectable_identity(tmp_path, changed):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    process = harness.processes[identity['pid']]
    if changed == 'birth':
        process.birth += 1
    elif changed == 'command':
        process.command = ['another', 'foreground']
    elif changed == 'environment':
        process.environment['CGI_RUNTIME_GENERATION'] = 'foreign'
    else:
        process.denied = True
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease) is False
    assert harness.kills == []
    assert harness.service.health(lease)['confirmed_dead'] is False


def test_stop_only_owned_tree_and_does_not_touch_other_runtime_or_foreground(tmp_path):
    harness = Harness(tmp_path)
    first, first_worker = harness.start('first')
    second, second_worker = harness.start('second')
    host = harness.host(first, first_worker)
    unrelated = FakeProcess(999, ['maya', 'foreground'], {})
    harness.processes[999] = unrelated
    with pytest.raises(RuntimePoolError):
        harness.service.stop(first)
    harness.pool.begin_release(first)
    assert harness.service.stop(first) is True
    assert harness.kills == [first_worker['pid']]
    assert not host.alive
    assert unrelated.alive and harness.processes[second_worker['pid']].alive
    assert harness.service.health(second)['state'] == 'LIVE'


def test_foreign_descendant_prevents_entire_tree_stop(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    foreground = harness.host(lease, identity, foreground=True)
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease) is False
    assert foreground.alive and harness.kills == []


def test_published_orphan_host_is_checked_after_worker_death(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    host = harness.host(lease, identity)
    harness.processes[identity['pid']].alive = False
    assert harness.service.health(lease)['state'] == 'ORPHANED'
    assert harness.service.health(lease)['confirmed_dead'] is False
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease) is True
    assert harness.kills == [host.pid]
    health = harness.service.health(lease)
    assert health['confirmed_dead'] and health['state'] == 'DEAD'
    assert harness.pool.mark_lost(lease, confirmed_dead=health['confirmed_dead'])['state'] == 'LOST'


def test_dead_active_owner_does_not_infer_complete_host_publication(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    harness.pool.reserve(lease, 'active')
    harness.pool.set_busy(lease, 'active')
    launches = Path(lease['root']) / 'host_launches'
    launches.mkdir()
    (launches / 'uncertain.json').write_text(json.dumps({
        'runtime_id': lease['runtime_id'], 'generation': lease['generation'],
        'root': lease['root'], 'state': 'PENDING'}))
    harness.processes[identity['pid']].alive = False
    health = harness.service.health(lease)
    assert health['state'] == 'UNKNOWN' and not health['confirmed_dead']


def test_complete_publication_can_prove_dead_active_runtime(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    host = harness.host(lease, identity)
    harness.pool.reserve(lease, 'active')
    harness.pool.set_busy(lease, 'active')
    harness.processes[identity['pid']].alive = False
    host.alive = False
    assert harness.service.health(lease)['confirmed_dead'] is True


def test_dead_record_without_launch_intent_is_unknown(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    host = harness.host(lease, identity)
    (Path(lease['root']) / 'host_launches' / 'host-one.json').unlink()
    harness.processes[identity['pid']].alive = False
    host.alive = False
    health = harness.service.health(lease)
    assert health['state'] == 'UNKNOWN' and not health['confirmed_dead']


def test_uncommitted_host_launch_prevents_even_explicit_scoped_stop(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    launches = Path(lease['root']) / 'host_launches'
    launches.mkdir()
    (launches / 'uncertain.json').write_text(json.dumps({
        'runtime_id': lease['runtime_id'], 'generation': lease['generation'],
        'root': lease['root'], 'state': 'PENDING'}))
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease) is False
    assert harness.processes[identity['pid']].alive and harness.kills == []


@pytest.mark.parametrize('generation', [None, '', 3, False])
def test_corrupt_launch_generation_is_not_ignored_as_old(tmp_path, generation):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    launches = Path(lease['root']) / 'host_launches'
    launches.mkdir()
    (launches / 'uncertain.json').write_text(json.dumps({'generation': generation}))
    harness.processes[identity['pid']].alive = False
    health = harness.service.health(lease)
    assert health['state'] == 'UNKNOWN' and not health['confirmed_dead']


def test_new_generation_does_not_reuse_a_crashed_celery_pidfile(tmp_path):
    harness = Harness(tmp_path)
    lease, _ = harness.start()
    old_pidfile = next(value.split('=', 1)[1] for value in harness.service._command(lease) if value.startswith('--pidfile='))
    Path(old_pidfile).write_text('35920')
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease)
    harness.pool.finish_release(lease, True)
    next_lease = harness.pool.acquire('default', 'replacement')
    new_pidfile = next(value.split('=', 1)[1] for value in harness.service._command(next_lease) if value.startswith('--pidfile='))
    assert new_pidfile != old_pidfile and not Path(new_pidfile).exists()
    assert Path(old_pidfile).read_text() == '35920'


def test_corrupt_host_record_refuses_stop_and_fresh_dirty_claim(tmp_path):
    harness = Harness(tmp_path)
    lease, identity = harness.start()
    path = Path(lease['root']) / 'hosts'
    path.mkdir()
    (path / 'unknown.json').write_text('{broken', encoding='utf-8')
    harness.pool.begin_release(lease)
    assert harness.service.stop(lease) is False
    assert harness.service.health(lease)['state'] == 'UNKNOWN'
    assert harness.kills == []


def test_default_readiness_checks_only_exact_node_and_queue(monkeypatch):
    calls = []
    inspector = SimpleNamespace(active_queues=lambda: {'node': [{'name': 'expected'}],
                                                      'other': [{'name': 'foreign'}]})
    def inspect(**kwargs):
        calls.append(kwargs)
        return inspector
    app = SimpleNamespace(control=SimpleNamespace(inspect=inspect))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.get_celery_app', lambda: app)
    assert RuntimeService._queue_ready({'queue': 'expected'}, {'node': 'node'})
    assert not RuntimeService._queue_ready({'queue': 'foreign'}, {'node': 'node'})
    assert calls == [{'destination': ['node'], 'timeout': 1}] * 2


def test_two_pool_roots_sharing_broker_have_distinct_queues_and_nodes(tmp_path):
    first, second = Harness(tmp_path / 'one'), Harness(tmp_path / 'two')
    shared_broker = {}
    def queue_ready(lease, identity):
        shared_broker.setdefault(identity['node'], lease['queue'])
        return shared_broker[identity['node']] == lease['queue']
    first.service.queue_probe = second.service.queue_probe = queue_ready
    a, a_identity = first.start()
    b, b_identity = second.start()
    assert a['runtime_id'] == b['runtime_id'] == 'slot-1'
    assert a['queue'] != b['queue']
    assert a_identity['node'] != b_identity['node']
    assert len(shared_broker) == 2
