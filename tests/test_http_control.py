"""Exercise authenticated control routes through a real ASGI request boundary."""

import json
import threading

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from cgi_pipeline.server.http_control import HttpControl
from cgi_pipeline.server.http_lifecycle import HttpLifecycle, LifecycleError


@pytest.fixture
def control(tmp_path):
    lifecycle = HttpLifecycle()
    stopped = []
    control = HttpControl(lifecycle, tmp_path / "frontend.control.json",
                          lambda: stopped.append(True))
    control.write_metadata()
    metadata = json.loads(control.metadata_path.read_text(encoding="utf-8"))
    app = Starlette(routes=control.routes())
    with TestClient(app) as client:
        yield control, client, metadata, stopped
    control.close()


def status(client, metadata, **kwargs):
    return client.get("/_cgi/control/status", params={"generation": metadata["generation"]},
                      headers={"X-CGI-Control-Token": metadata["secret"]}, **kwargs)


def change(client, metadata, action, epoch, **extra):
    return client.post(f"/_cgi/control/{action}",
                       headers={"X-CGI-Control-Token": metadata["secret"]},
                       json={"generation": metadata["generation"], "lease_epoch": epoch, **extra})


def test_status_has_identity_but_never_secret_or_activity(control):
    instance, client, metadata, _ = control
    response = status(client, metadata)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "SUCCESS"
    for field in ("protocol_version", "pid", "process_start_time", "generation"):
        assert result[field] == metadata[field]
    assert "secret" not in result
    assert metadata["secret"] not in response.text
    assert result["state"] == "accepting" and result["active_count"] == 0
    assert instance.lifecycle.status()["active_count"] == 0


@pytest.mark.parametrize("token", [None, "", "wrong", b"non-ascii-\xff"])
def test_missing_wrong_and_non_ascii_tokens_are_denied_without_side_effect(control, token):
    instance, client, metadata, stopped = control
    headers = {} if token is None else {"X-CGI-Control-Token": token}
    response = client.post("/_cgi/control/drain", headers=headers,
                           json={"generation": metadata["generation"], "lease_epoch": 0,
                                 "lease_seconds": 15})
    assert response.status_code == 403
    assert response.json()["error_code"] == "HTTP_CONTROL_AUTH"
    assert instance.lifecycle.status()["lease_epoch"] == 0
    assert stopped == []


@pytest.mark.parametrize("origin", ["https://example.invalid", "null", ""])
def test_any_origin_is_denied_even_with_valid_credentials(control, origin):
    instance, client, metadata, _ = control
    response = client.post("/_cgi/control/drain",
                           headers={"X-CGI-Control-Token": metadata["secret"], "Origin": origin},
                           json={"generation": metadata["generation"], "lease_epoch": 0,
                                 "lease_seconds": 15})
    assert response.status_code == 403
    assert response.json()["error_code"] == "HTTP_CONTROL_ORIGIN"
    assert instance.lifecycle.status()["state"] == "accepting"


def test_duplicate_token_header_is_denied(control):
    _, client, metadata, _ = control
    response = client.get("/_cgi/control/status", params={"generation": metadata["generation"]},
                          headers=[("X-CGI-Control-Token", metadata["secret"])] * 2)
    assert response.status_code == 403


def test_prior_generation_cannot_query_or_drain_current_instance(control):
    instance, client, metadata, stopped = control
    stale = {**metadata, "generation": "obsolete"}
    assert status(client, stale).status_code == 409
    assert change(client, stale, "drain", 0, lease_seconds=15).status_code == 409
    assert instance.lifecycle.status()["state"] == "accepting"
    assert stopped == []


def test_drain_busy_stop_resume_and_committed_stop_via_http(control):
    instance, client, metadata, stopped = control
    with instance.lifecycle.activity("in-flight request"):
        drained = change(client, metadata, "drain", 0, lease_seconds=15).json()
        epoch = drained["lease_epoch"]
        assert drained["active_count"] == 1 and drained["state"] == "draining"
        busy = change(client, metadata, "stop", epoch)
        assert busy.status_code == 409
        assert busy.json()["error_code"] == "HTTP_ACTIVE_WORK"
        assert stopped == []
    assert change(client, metadata, "drain", epoch, lease_seconds=15).json()["lease_epoch"] == epoch
    resumed = change(client, metadata, "resume", epoch).json()
    assert resumed["state"] == "accepting"
    assert change(client, metadata, "stop", epoch).status_code == 409
    epoch = change(client, metadata, "drain", resumed["lease_epoch"], lease_seconds=15).json()["lease_epoch"]
    stopped_response = change(client, metadata, "stop", epoch)
    assert stopped_response.status_code == 200
    assert stopped_response.json()["state"] == "stopping"
    assert stopped == [True]
    assert change(client, metadata, "resume", epoch).status_code == 409
    assert stopped == [True]


@pytest.mark.parametrize("payload", [None, [], {}, {"generation": "x"},
                                      {"generation": "x", "lease_epoch": 0, "extra": 1}])
def test_invalid_bodies_are_rejected_before_any_transition(control, payload):
    instance, client, metadata, _ = control
    response = client.post("/_cgi/control/resume",
                           headers={"X-CGI-Control-Token": metadata["secret"]}, json=payload)
    assert response.status_code == 400
    assert instance.lifecycle.status()["lease_epoch"] == 0


@pytest.mark.parametrize("content,content_type", [("{broken", "application/json"),
                                                  ("{}", "text/plain"),
                                                  ("x" * 4097, "application/json")])
def test_malformed_wrong_type_and_oversized_bodies_are_bounded(control, content, content_type):
    instance, client, metadata, _ = control
    response = client.post("/_cgi/control/drain", content=content,
                           headers={"X-CGI-Control-Token": metadata["secret"],
                                    "Content-Type": content_type})
    assert response.status_code == 400
    assert response.json()["error_code"] == "HTTP_CONTROL_INVALID"
    assert instance.lifecycle.status()["lease_epoch"] == 0


def test_control_methods_are_fixed_and_status_requires_generation(control):
    instance, client, metadata, _ = control
    headers = {"X-CGI-Control-Token": metadata["secret"]}
    assert client.get("/_cgi/control/drain", headers=headers).status_code == 405
    assert client.post("/_cgi/control/status", headers=headers).status_code == 405
    assert client.get("/_cgi/control/status", headers=headers).status_code == 400
    assert instance.lifecycle.status()["lease_epoch"] == 0


def test_expired_lease_refuses_stale_stop_and_restores_service(tmp_path):
    clock = [0.0]
    lifecycle = HttpLifecycle(clock=lambda: clock[0])
    stopped = []
    instance = HttpControl(lifecycle, tmp_path / "lease.control.json", lambda: stopped.append(True))
    instance.write_metadata()
    metadata = json.loads(instance.metadata_path.read_text(encoding="utf-8"))
    with TestClient(Starlette(routes=instance.routes())) as client:
        epoch = change(client, metadata, "drain", 0, lease_seconds=1).json()["lease_epoch"]
        clock[0] = 1.0
        result = change(client, metadata, "stop", epoch)
        assert result.status_code == 409 and result.json()["error_code"] == "HTTP_LEASE_CONFLICT"
        assert status(client, metadata).json()["state"] == "accepting"
        assert stopped == []
    instance.close()


def test_metadata_is_atomic_and_old_instance_does_not_delete_new_generation(tmp_path):
    path = tmp_path / "instance.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.identity["pid"] = 2147483647  # A syntactically valid but dead prior owner.
    first.write_metadata()
    first_data = json.loads(path.read_text(encoding="utf-8"))
    assert len(first_data["secret"]) >= 32
    second = HttpControl(HttpLifecycle(), path, lambda: None)
    second.write_metadata()
    second_data = json.loads(path.read_text(encoding="utf-8"))
    assert second_data["generation"] != first_data["generation"]
    assert second_data["secret"] != first_data["secret"]
    first.close()
    assert path.exists()
    second.close()
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_live_owner_metadata_cannot_be_overwritten(tmp_path):
    path = tmp_path / "owned.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.write_metadata()
    original = path.read_bytes()
    second = HttpControl(HttpLifecycle(), path, lambda: None)
    try:
        with pytest.raises(LifecycleError) as rejected:
            second.write_metadata()
        assert rejected.value.code == "HTTP_CONTROL_OWNER_ALIVE"
        assert path.read_bytes() == original
    finally:
        first.close()


def test_new_publication_interleaving_with_old_close_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "interleaved.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.identity["pid"] = 2147483647
    first.write_metadata()
    second = HttpControl(HttpLifecycle(), path, lambda: None)
    original_unlink = type(path).unlink
    started, published = threading.Event(), threading.Event()
    threads, errors = [], []

    def publish():
        started.set()
        try:
            second.write_metadata()
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            published.set()

    def interleave(candidate, *args, **kwargs):
        if candidate == path and not threads:
            thread = threading.Thread(target=publish)
            threads.append(thread)
            thread.start()
            assert started.wait(2)
            published.wait(1)  # Old code publishes here; fixed code waits for close's lock.
        return original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "unlink", interleave)
    first.close()
    assert published.wait(3)
    threads[0].join(1)
    assert not errors
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["generation"] == second.identity["generation"]
    second.close()


def test_metadata_publication_failure_is_fatal_and_removes_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "instance.control.json"
    instance = HttpControl(HttpLifecycle(), path, lambda: None)

    def fail(*args):
        raise PermissionError("cannot publish metadata")

    monkeypatch.setattr("cgi_pipeline.server.http_control.os.replace", fail)
    with pytest.raises(PermissionError, match="cannot publish metadata"):
        instance.write_metadata()
    assert not path.exists()
    assert {candidate.name for candidate in tmp_path.iterdir()} <= {"instance.control.json.lock"}


@pytest.mark.parametrize("field,value", [("pid", True), ("process_start_time", float("nan")),
                                        ("generation", "invalid"), ("secret", "short"),
                                        ("protocol_version", 2)])
def test_malformed_existing_identity_is_refused_and_preserved(tmp_path, field, value):
    path = tmp_path / "invalid.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.write_metadata()
    existing = json.loads(path.read_text(encoding="utf-8"))
    existing[field] = value
    path.write_text(json.dumps(existing), encoding="utf-8")
    original = path.read_bytes()
    second = HttpControl(HttpLifecycle(), path, lambda: None)
    with pytest.raises(LifecycleError) as rejected:
        second.write_metadata()
    assert rejected.value.code == "HTTP_CONTROL_METADATA_INVALID"
    assert path.read_bytes() == original


def test_invalid_json_is_refused_without_replacement(tmp_path):
    path = tmp_path / "invalid-json.control.json"
    path.write_text("{broken", encoding="utf-8")
    instance = HttpControl(HttpLifecycle(), path, lambda: None)
    with pytest.raises(LifecycleError) as rejected:
        instance.write_metadata()
    assert rejected.value.code == "HTTP_CONTROL_METADATA_INVALID"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_unverifiable_owner_is_refused_without_replacement(tmp_path, monkeypatch):
    import psutil

    path = tmp_path / "unknown-owner.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.write_metadata()
    original = path.read_bytes()
    second = HttpControl(HttpLifecycle(), path, lambda: None)

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr("cgi_pipeline.server.http_control.psutil.Process", denied)
    with pytest.raises(LifecycleError) as rejected:
        second.write_metadata()
    assert rejected.value.code == "HTTP_CONTROL_OWNER_UNKNOWN"
    assert path.read_bytes() == original


def test_reused_pid_with_different_creation_time_is_stale(tmp_path):
    path = tmp_path / "reused-pid.control.json"
    first = HttpControl(HttpLifecycle(), path, lambda: None)
    first.identity["process_start_time"] -= 1
    first.write_metadata()
    second = HttpControl(HttpLifecycle(), path, lambda: None)
    second.write_metadata()
    first.close()
    assert json.loads(path.read_text(encoding="utf-8"))["generation"] == second.identity["generation"]
    second.close()


def test_cleanup_error_warns_type_without_credential_or_exception_text(tmp_path, monkeypatch, caplog):
    path = tmp_path / "cleanup-error.control.json"
    instance = HttpControl(HttpLifecycle(), path, lambda: None)
    instance.write_metadata()
    original = type(path).unlink

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError(instance._secret)
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "unlink", denied)
    instance.close()
    assert path.exists()
    assert "PermissionError" in caplog.text
    assert instance._secret not in caplog.text
