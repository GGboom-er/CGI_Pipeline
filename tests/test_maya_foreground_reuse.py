from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cgi_pipeline.core import run_archive
from cgi_pipeline.hosts.maya.worker import MayaCommandPortWorker
from cgi_pipeline.server import foreground_client


class _FakeConnection:
    def __init__(self):
        self.namespace = {"_cgi_run_api_json": object(), "_cgi_run_batch_json": object()}
        self.execute_calls = 0
        self._config = {}
        self.pings = 0
        self.closes = 0
        self.dead = False

    def execute(self, _code):
        self.execute_calls += 1

    def ping(self):
        self.pings += 1
        if self.dead:
            raise EOFError('connection lost')

    def close(self):
        self.closes += 1


def test_warm_calls_do_not_open_probe_connections(monkeypatch):
    from cgi_pipeline.hosts.maya import worker as module
    probes, connections = [], []
    conn = _FakeConnection()
    monkeypatch.setattr(module, 'is_rpyc_available', lambda *args: probes.append(args) or True)
    monkeypatch.setattr(module.rpyc.classic, 'connect', lambda *args: connections.append(args) or conn)
    worker = MayaCommandPortWorker()
    for _ in range(3):
        worker.start()
        assert worker._ensure_connection() is conn
        worker._get_remote_func(conn)
        worker._get_remote_func(conn, batch=True)
    assert len(probes) == len(connections) == conn.execute_calls == 1
    assert conn.pings == 3


def test_dead_connection_rebuilds_both_entrypoints_before_execution(monkeypatch):
    from cgi_pipeline.hosts.maya import worker as module
    worker = MayaCommandPortWorker()
    old, new = _FakeConnection(), _FakeConnection()
    worker._conn = old
    worker._get_remote_func(old)
    old.dead = True
    monkeypatch.setattr(module, 'is_rpyc_available', lambda *args: True)
    monkeypatch.setattr(module.rpyc.classic, 'connect', lambda *args: new)
    assert worker._ensure_connection() is new
    assert old.closes == 1
    assert worker._get_remote_func(new, batch=True) is new.namespace['_cgi_run_batch_json']
    assert new.execute_calls == 1
    assert worker._get_remote_func(new) is new.namespace['_cgi_run_api_json']


def test_transport_error_is_not_retried_and_connection_is_closed():
    worker = MayaCommandPortWorker()
    conn = _FakeConnection()
    calls = []
    def fail(*args):
        calls.append(args)
        raise EOFError('lost after dispatch')
    conn.modules = {'maya.utils': SimpleNamespace(executeInMainThreadWithResult=fail)}
    worker._conn = conn
    result = worker.run_api({'api_id': 'test', 'task_id': 'once'})
    assert result['status'] == 'ERROR'
    assert len(calls) == conn.closes == 1
    assert worker._conn is worker._remote_func is worker._remote_batch_func is None


def test_maya_foreground_reuses_remote_entrypoint_for_one_connection():
    worker = MayaCommandPortWorker.__new__(MayaCommandPortWorker)
    worker._remote_func = None
    conn = _FakeConnection()

    first = worker._get_remote_func(conn)
    second = worker._get_remote_func(conn)

    assert first is second
    assert conn.execute_calls == 1


def test_maya_foreground_reinjects_on_new_connection():
    worker = MayaCommandPortWorker.__new__(MayaCommandPortWorker)
    worker._remote_func = None
    conn = _FakeConnection()

    worker._get_remote_func(conn)

    assert conn.execute_calls == 1


def test_foreground_client_reuses_worker_for_same_maya_port(monkeypatch):
    created = []

    class _FakeWorker:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(foreground_client, "MayaCommandPortWorker", _FakeWorker)
    foreground_client._WORKERS.clear()
    foreground_client._WORKER_LOCKS.clear()

    first, first_lock = foreground_client._get_worker(7123)
    second, second_lock = foreground_client._get_worker(7123)

    assert first is second
    assert first_lock is second_lock
    assert created == [{"host": "127.0.0.1", "port": 7123}]


def test_foreground_exec_path_does_not_create_task_sandbox(monkeypatch):
    class _FakeWorker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

        def run_api(self, payload):
            return {
                "api_id": payload["api_id"],
                "status": "SUCCESS",
                "output": {"result": {"changed": True}},
            }

    sandbox_calls = []
    monkeypatch.setattr(foreground_client, "MayaCommandPortWorker", _FakeWorker)
    monkeypatch.setattr(foreground_client, "_write_audit", lambda *args: None)
    monkeypatch.setattr(
        run_archive, "create_run_dir",
        lambda *args, **kwargs: sandbox_calls.append((args, kwargs)),
    )
    foreground_client._WORKERS.clear()
    foreground_client._WORKER_LOCKS.clear()

    result = foreground_client.submit_foreground_task(
        {
            "task_id": "foreground-no-sandbox",
            "api_id": "maya.asset.exec_code",
            "api_context": {"foreground_port": 7123},
            "api_params": {"code": "result = {'changed': True}"},
        },
        sync=True,
    )

    assert result["status"] == "SUCCESS"
    assert sandbox_calls == []


def test_foreground_terminal_audits_preserve_execution_mode_and_port(monkeypatch, tmp_path):
    class _FakeWorker:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            return None

        def run_api(self, payload):
            return {"api_id": payload["api_id"], "status": "SUCCESS", "output": {}}

        def run_batch(self, payloads, atomic=True):
            results = [
                {"api_id": payload["api_id"], "status": "SUCCESS", "output": {}}
                for payload in payloads
            ]
            return {"status": "SUCCESS", "results": results, "requested": len(payloads),
                    "completed": len(results), "atomic": atomic, "rolled_back": False}

    monkeypatch.setattr(foreground_client, "MayaCommandPortWorker", _FakeWorker)
    monkeypatch.setattr(foreground_client, "AUDIT_DIR", tmp_path)
    foreground_client._WORKERS.clear()
    foreground_client._WORKER_LOCKS.clear()

    foreground_client.submit_foreground_task(
        {
            "task_id": "foreground-audit-api",
            "api_id": "maya.scene.selection.get",
            "api_context": {"execution_mode": "foreground", "foreground_port": 7123},
            "api_params": {},
        },
        sync=True,
    )
    foreground_client.submit_foreground_batch_task(
        {
            "task_id": "foreground-audit-batch",
            "api_chain": [{"api_id": "maya.scene.selection.get", "parameters": {}}],
            "api_context": {"execution_mode": "foreground", "foreground_port": 7123},
        }
    )

    for task_id in ("foreground-audit-api", "foreground-audit-batch"):
        audit_path = next(tmp_path.rglob(f"{task_id}.json"))
        terminal = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
        assert terminal["status"] == "SUCCESS"
        assert terminal["execution_mode"] == "foreground"
        assert terminal["foreground_port"] == 7123


def _bootstrap_clock(monkeypatch, ready_at=None):
    from cgi_pipeline.hosts.maya import worker as module
    clock = SimpleNamespace(now=0.0, sends=0)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(module.time, 'sleep', lambda delay: setattr(clock, 'now', clock.now + delay))
    monkeypatch.setattr(module, 'is_maya_commandport_available', lambda *args: True)
    monkeypatch.setattr(module, 'is_rpyc_available',
                        lambda *args: ready_at is not None and clock.now >= ready_at)
    worker = MayaCommandPortWorker()

    def send(deadline):
        clock.sends += 1
        clock.deadline = deadline
        return 'ImportError: actual Maya dependency error', False

    monkeypatch.setattr(worker, '_send_bootstrap', send)
    return worker, clock


def test_bootstrap_accepts_service_ready_after_old_five_second_window(monkeypatch):
    monkeypatch.delenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', raising=False)
    worker, clock = _bootstrap_clock(monkeypatch, ready_at=6.0)
    worker._bootstrap_server()
    assert clock.now == 6.0
    assert clock.sends == 1


def test_bootstrap_timeout_preserves_observation_and_host_error_without_install_guess(monkeypatch):
    monkeypatch.setenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', '0.3')
    worker, clock = _bootstrap_clock(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        worker._bootstrap_server()
    message = str(exc.value)
    assert clock.now == 0.3
    assert clock.sends == 1
    assert '0.3 秒' in message
    assert 'actual Maya dependency error' in message
    assert 'MCP 已加载 rpyc' in message
    assert 'pip install' not in message
    assert '最常见原因' not in message


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'invalid', ''])
def test_invalid_bootstrap_timeout_never_dispatches(monkeypatch, value):
    monkeypatch.setenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', value)
    worker, clock = _bootstrap_clock(monkeypatch)
    with pytest.raises(ValueError, match='必须是有限正数'):
        worker._bootstrap_server()
    assert clock.sends == 0


def _bootstrap_socket(monkeypatch, responses, ready_at=None):
    from cgi_pipeline.hosts.maya import worker as module
    worker = MayaCommandPortWorker()
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(module.time, 'sleep', lambda delay: setattr(clock, 'now', clock.now + delay))
    monkeypatch.setattr(module, 'is_maya_commandport_available', lambda *args: True)
    monkeypatch.setattr(module, 'is_rpyc_available',
                        lambda *args: ready_at is not None and clock.now >= ready_at)
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: SimpleNamespace(hex='testnonce'))

    class Socket:
        def __init__(self):
            self.sent = []
            self.closed = False
            self.responses = list(responses)
            self.reads = 0

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            self.address = address

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, size):
            assert size == 8192
            self.reads += 1
            if not self.responses:
                clock.now += self.timeout
                raise module.socket.timeout()
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            if callable(response):
                response = response(self)
            return response

        def close(self):
            self.closed = True

    sock = Socket()
    monkeypatch.setattr(module.socket, 'socket', lambda *args: sock)
    return worker, clock, sock


@pytest.mark.parametrize('responses', [
    [b'Bifrost startup\n\x00', b'CGI_RPYC_BOOTSTRAP_test', b'nonce\n\x00'],
    [b'Bifrost startup\n\x00CGI_RPYC_BOOTSTRAP_testnonce\n\x00'],
])
def test_bootstrap_waits_for_correlated_complete_frame_after_echo(monkeypatch, responses):
    worker, clock, sock = _bootstrap_socket(monkeypatch, responses)
    reply, _ = worker._send_bootstrap(30.0)
    assert sock.reads == len(responses)
    assert 'Bifrost startup' in reply
    assert reply.endswith('CGI_RPYC_BOOTSTRAP_testnonce')
    assert sock.closed
    assert len(sock.sent) == 1
    assert sock.address == ('127.0.0.1', 7001)
    assert clock.now == 0.0


def test_bootstrap_code_echo_containing_nonce_is_not_completion(monkeypatch):
    worker, clock, sock = _bootstrap_socket(monkeypatch, [
        lambda sock: sock.sent[0].rstrip(b'\n') + b'\n\x00',
    ])
    reply, _ = worker._send_bootstrap(0.3)
    assert clock.now == 0.3
    assert '等待期限' in reply
    assert sock.reads == 2
    assert sock.closed
    assert len(sock.sent) == 1


@pytest.mark.parametrize('response', [
    b'CGI_RPYC_BOOTSTRAP_oldnonce\n\x00', b'CGI_RPYC_BOOTSTRAP_testnonce',
])
def test_bootstrap_requires_matching_nonce_and_frame_terminator(monkeypatch, response):
    worker, _, sock = _bootstrap_socket(monkeypatch, [response, b''])
    assert 'EOF' in worker._send_bootstrap(30.0)[0]
    assert sock.reads == 2
    assert sock.closed


def test_bootstrap_preserves_host_error_after_noise_and_eof(monkeypatch):
    worker, _, sock = _bootstrap_socket(monkeypatch, [
        b'Bifrost startup\n\x00', b'ImportError: actual dependency error\n\x00', b'',
    ])
    reply, _ = worker._send_bootstrap(30.0)
    assert 'Bifrost startup' in reply
    assert 'ImportError: actual dependency error' in reply
    assert 'EOF' in reply
    assert sock.reads == 3
    assert len(sock.sent) == 1
    assert sock.closed


def test_bootstrap_eof_without_response_is_not_silent(monkeypatch):
    worker, _, sock = _bootstrap_socket(monkeypatch, [b''])
    assert 'EOF' in worker._send_bootstrap(30.0)[0]
    assert sock.closed


def test_bootstrap_socket_failure_preserves_exception_and_closes(monkeypatch):
    worker, _, sock = _bootstrap_socket(monkeypatch, [ConnectionResetError('actual socket reset')])
    with pytest.raises(ConnectionResetError, match='actual socket reset'):
        worker._send_bootstrap(30.0)
    assert sock.closed
    assert len(sock.sent) == 1


@pytest.mark.parametrize('stage', ['connect', 'sendall'])
def test_bootstrap_dispatch_failure_is_not_retried_and_closes(monkeypatch, stage):
    worker, _, sock = _bootstrap_socket(monkeypatch, [])
    def fail(_value):
        raise OSError('actual dispatch failure')
    monkeypatch.setattr(sock, stage, fail)
    with pytest.raises(OSError, match='actual dispatch failure'):
        worker._send_bootstrap(30.0)
    assert sock.sent == []
    assert sock.closed


def test_bootstrap_response_wait_and_readiness_share_one_deadline(monkeypatch):
    monkeypatch.setenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', '0.3')
    def delayed_echo(_sock):
        clock.now += 0.2
        return b'Bifrost startup\n\x00'
    worker, clock, sock = _bootstrap_socket(monkeypatch, [delayed_echo])
    with pytest.raises(RuntimeError, match='0.3 秒'):
        worker._bootstrap_server()
    assert clock.now == 0.3
    assert len(sock.sent) == 1
    assert sock.closed


def test_bootstrap_readiness_can_end_wait_without_command_reply(monkeypatch):
    worker, clock, sock = _bootstrap_socket(monkeypatch, [], ready_at=0.5)
    worker._bootstrap_server()
    assert clock.now == 0.5
    assert len(sock.sent) == 1
    assert sock.closed


def test_bootstrap_probe_time_is_deducted_before_receiving(monkeypatch):
    from cgi_pipeline.hosts.maya import worker as module
    worker, clock, sock = _bootstrap_socket(monkeypatch, [])
    def probe(_host, _port, timeout):
        clock.now += min(0.2, timeout)
        return False
    monkeypatch.setattr(module, 'is_rpyc_available', probe)
    assert '等待期限' in worker._send_bootstrap(0.3)[0]
    assert clock.now == 0.3
    assert sock.timeout == pytest.approx(0.1)
    assert sock.closed


def test_bootstrap_unterminated_stream_has_bounded_memory_and_cannot_forge_ack(monkeypatch):
    import tracemalloc
    chunk = b'x' * 8192
    responses = [chunk] * 1024 + [b'CGI_RPYC_BOOTSTRAP_testnonce\n\x00', b'']
    worker, _, sock = _bootstrap_socket(monkeypatch, responses)
    tracemalloc.start()
    try:
        reply, _ = worker._send_bootstrap(30.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert 'EOF' in reply
    assert sock.reads == len(responses)
    assert peak < 1_000_000
    assert sock.closed


def test_bootstrap_preserves_readiness_confirmed_at_deadline(monkeypatch):
    from cgi_pipeline.hosts.maya import worker as module
    monkeypatch.setenv('RPYC_BOOTSTRAP_TIMEOUT_SEC', '0.3')
    worker, clock, sock = _bootstrap_socket(monkeypatch, [])
    def probe(*args):
        if len(args) == 2:
            return False
        clock.now += args[2]
        return True
    monkeypatch.setattr(module, 'is_rpyc_available', probe)
    worker._bootstrap_server()
    assert clock.now == 0.3
    assert len(sock.sent) == 1
    assert sock.closed


def test_bootstrap_payload_has_non_none_result_and_executes_only_once(monkeypatch):
    worker, _, sock = _bootstrap_socket(monkeypatch, [b'CGI_RPYC_BOOTSTRAP_testnonce\n\x00'])
    worker._send_bootstrap(30.0)
    calls = []
    result = eval(sock.sent[0].decode(), {'exec': lambda *args: calls.append(args)})
    assert result == 'CGI_RPYC_BOOTSTRAP_testnonce'
    assert len(calls) == 1
