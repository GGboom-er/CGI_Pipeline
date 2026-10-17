"""Real loopback protocol tests; fake bpy verifies main-thread scheduling only."""

import importlib
import json
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from cgi_pipeline.hosts.blender import native_bridge as bridge


class Timers:
    def __init__(self):
        self.callbacks = []

    def register(self, callback, *, first_interval, persistent):
        assert threading.current_thread() is threading.main_thread()
        assert persistent is True
        self.callbacks.append(callback)

    def is_registered(self, callback):
        return any(item is callback for item in self.callbacks)

    def unregister(self, callback):
        assert self.is_registered(callback)
        self.callbacks = [item for item in self.callbacks if item is not callback]

    def tick(self):
        for callback in list(self.callbacks):
            callback()


class MainThreadData:
    filepath = "X:/unchanged scene.blend"
    is_dirty = True

    def __getattribute__(self, name):
        assert threading.current_thread() is threading.main_thread()
        return object.__getattribute__(self, name)


@pytest.fixture
def host(monkeypatch):
    timers = Timers()
    bpy = SimpleNamespace(
        app=SimpleNamespace(background=False, version_string="5.0.1", timers=timers),
        data=MainThreadData(),
        context=SimpleNamespace(scene=SimpleNamespace(frame_current=267),
                                selected_objects=["original"]),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    monkeypatch.setenv("BLENDER_FOREGROUND_PORT_START", str(port))
    monkeypatch.setenv("BLENDER_FOREGROUND_PORT_END", str(port))
    yield bpy, timers, port
    bridge.stop()


def call(timers, function, *args, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(function, *args, **kwargs)
        until = time.monotonic() + 3
        while not future.done() and time.monotonic() < until:
            timers.tick()
            time.sleep(0.002)
        return future.result(timeout=1)


def read_packet(connection):
    header = b""
    while len(header) < 8:
        header += connection.recv(8 - len(header))
    assert header[:4] == b"CGI1"
    remaining = struct.unpack("!I", header[4:])[0]
    data = b""
    while len(data) < remaining:
        data += connection.recv(remaining - len(data))
    return json.loads(data)


def test_health_roundtrip_preserves_scene_state(host):
    bpy, timers, port = host
    info = bridge.start()
    assert info["port"] == port
    response = call(timers, bridge.send_request, port, {"type": "health"}, 1)
    assert response["status"] == "ok"
    assert response["result"] == info
    assert info["bridge"] == "cgi_pipeline"
    assert info["protocol_version"] == 1
    assert info["dcc"] == "blender"
    assert info["file"] == "X:/unchanged scene.blend"
    assert bpy.data.is_dirty is True
    assert bpy.context.scene.frame_current == 267
    assert bpy.context.selected_objects == ["original"]


def test_execute_result_streams_exception_and_main_thread(host):
    _, timers, port = host
    bridge.start()
    code = ("import sys, threading\nprint('hello')\nprint('warn', file=sys.stderr)\n"
            "result={'value':42,'main':threading.current_thread() is threading.main_thread()}")
    response = call(timers, bridge.send_request, port, {"type": "execute", "code": code}, 1)
    assert response == {"status": "ok", "result": {"value": 42, "main": True},
                        "stdout": "hello\n", "stderr": "warn\n"}
    response = call(timers, bridge.send_request, port,
                    {"type": "execute", "code": "print('before'); raise ValueError('boom')"}, 1)
    assert response["status"] == "error"
    assert response["stdout"] == "before\n"
    assert "Traceback" in response["message"] and "ValueError: boom" in response["message"]


def test_fragmented_request_is_received_completely(host):
    _, timers, port = host
    bridge.start()
    payload = json.dumps({"type": "health"}).encode()
    packet = b"CGI1" + struct.pack("!I", len(payload)) + payload

    def fragmented():
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            for byte in packet:
                connection.sendall(bytes([byte]))
            return read_packet(connection)

    assert call(timers, fragmented)["status"] == "ok"


@pytest.mark.parametrize("packet, expected", [
    (b"FAIL" + struct.pack("!I", 0), "magic"),
    (b"CGI1" + struct.pack("!I", 16 * 1024 * 1024 + 1), "size"),
    (b"CGI1" + struct.pack("!I", 1) + b"{", "JSON"),
    (b"CGI1" + struct.pack("!I", 2) + b"[]", "object"),
])
def test_malformed_frame_returns_error_without_breaking_listener(host, packet, expected):
    _, timers, port = host
    bridge.start()

    def malformed():
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(packet)
            return read_packet(connection)

    response = call(timers, malformed)
    assert response["status"] == "error"
    assert expected.lower() in response["message"].lower()
    assert call(timers, bridge.send_request, port, {"type": "health"}, 1)["status"] == "ok"


@pytest.mark.parametrize("message", [{"type": "unknown"}, {"type": "execute", "code": 42}])
def test_invalid_request_has_traceback(host, message):
    _, timers, port = host
    bridge.start()
    response = call(timers, bridge.send_request, port, message, 1)
    assert response["status"] == "error" and "Traceback" in response["message"]


def test_expired_queued_request_never_executes(host):
    bpy, timers, port = host
    bridge.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.send_request, port,
                             {"type": "execute", "code": "bpy.context.scene.frame_current=999"}, 0.04)
        time.sleep(0.09)
        with pytest.raises(bridge.NativeBridgeError):
            future.result()
    timers.tick()
    assert bpy.context.scene.frame_current == 267


def test_repeated_start_reload_stop_are_symmetric(host):
    _, timers, port = host
    first = bridge.start()
    assert bridge.start(port) == first
    importlib.reload(bridge)
    assert bridge.start() == first
    assert len(timers.callbacks) == 1
    with pytest.raises(bridge.NativeBridgeError, match="already"):
        bridge.start(port + 1)
    bridge.stop()
    bridge.stop()
    assert not timers.callbacks
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", port))


def test_explicit_conflict_does_not_connect_to_other_server(host):
    _, timers, port = host
    with socket.socket() as other:
        other.bind(("127.0.0.1", port))
        other.listen()
        with pytest.raises(bridge.NativeBridgeError, match="bind"):
            bridge.start(port)
        assert not timers.callbacks


def test_auto_start_skips_conflicting_port(host, monkeypatch):
    _, timers, _ = host
    with socket.socket() as other, socket.socket() as next_port:
        other.bind(("127.0.0.1", 0))
        port = other.getsockname()[1]
        if port == 65535:
            pytest.skip("ephemeral range exhausted")
        try:
            next_port.bind(("127.0.0.1", port + 1))
        except OSError:
            pytest.skip("adjacent ephemeral port occupied")
        next_port.close()
        other.listen()
        monkeypatch.setenv("BLENDER_FOREGROUND_PORT_START", str(port))
        monkeypatch.setenv("BLENDER_FOREGROUND_PORT_END", str(port + 1))
        assert bridge.start()["port"] == port + 1
        assert call(timers, bridge.send_request, port + 1, {"type": "health"}, 1)["status"] == "ok"


def test_background_start_rejected_and_main_thread_required(host):
    bpy, timers, _ = host
    bpy.app.background = True
    with pytest.raises(bridge.NativeBridgeError, match="background"):
        bridge.start()
    bpy.app.background = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(bridge.NativeBridgeError, match="main thread"):
            pool.submit(bridge.start).result()
    assert not timers.callbacks


@pytest.mark.parametrize("port, timeout", [(0, 1), (65536, 1), (True, 1), (9876, 0), (9876, float("nan"))])
def test_invalid_client_arguments_fail_without_connection(port, timeout):
    with pytest.raises((ValueError, bridge.NativeBridgeError)):
        bridge.send_request(port, {"type": "health"}, timeout)


def test_unserializable_result_returns_error_and_next_request_works(host):
    _, timers, port = host
    bridge.start()
    response = call(timers, bridge.send_request, port,
                    {"type": "execute", "code": "result = object()"}, 1)
    assert response["status"] == "error"
    assert "TypeError" in response["message"]
    assert call(timers, bridge.send_request, port, {"type": "health"}, 1)["status"] == "ok"


def test_stop_cancels_pending_execution(host):
    bpy, timers, port = host
    bridge.start()
    state = bridge._STATE
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.send_request, port,
                             {"type": "execute", "code": "bpy.context.scene.frame_current=999"}, 1)
        until = time.monotonic() + 1
        while state.pending.empty() and time.monotonic() < until:
            time.sleep(0.002)
        assert not state.pending.empty()
        bridge.stop()
        with pytest.raises(bridge.NativeBridgeError):
            future.result(timeout=1)
    timers.tick()
    assert state.pending.empty() and not timers.callbacks
    assert bpy.context.scene.frame_current == 267


@pytest.mark.parametrize("fragmented", [True, False])
def test_client_handles_fragmented_response_and_preserves_protocol_cause(fragmented):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def server():
            with listener.accept()[0] as connection:
                read_packet(connection)
                if fragmented:
                    payload = json.dumps({"status": "ok", "result": "unicode 猫"}).encode()
                    packet = b"CGI1" + struct.pack("!I", len(payload)) + payload
                    for byte in packet:
                        connection.sendall(bytes([byte]))
                else:
                    connection.sendall(b"FAIL" + struct.pack("!I", 0))

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(server)
            if fragmented:
                assert bridge.send_request(port, {"type": "health"}, 1)["result"] == "unicode 猫"
            else:
                with pytest.raises(bridge.NativeBridgeError) as error:
                    bridge.send_request(port, {"type": "health"}, 1)
                assert isinstance(error.value.__cause__, ValueError)
                assert "magic" in str(error.value.__cause__)
            future.result(timeout=1)


def test_import_and_client_work_without_bpy(monkeypatch):
    bridge.stop()
    monkeypatch.delitem(sys.modules, "bpy", raising=False)
    importlib.reload(bridge)
    assert "bpy" not in sys.modules


def test_failed_timer_registration_releases_listener(host, monkeypatch):
    _, timers, port = host

    def fail(*args, **kwargs):
        raise RuntimeError("timer registration failed")

    monkeypatch.setattr(timers, "register", fail)
    with pytest.raises(RuntimeError, match="timer registration failed"):
        bridge.start()
    assert bridge._STATE is None and not timers.callbacks
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", port))


def test_start_with_restricted_registration_data_reads_real_filepath_later(host):
    bpy, timers, port = host
    actual_data = bpy.data
    bpy.data = SimpleNamespace()  # Blender's _RestrictData has no filepath in register().
    assert bridge.start()["file"] == ""
    bpy.data = actual_data
    response = call(timers, bridge.send_request, port, {"type": "health"}, 1)
    assert response["result"]["file"] == "X:/unchanged scene.blend"
