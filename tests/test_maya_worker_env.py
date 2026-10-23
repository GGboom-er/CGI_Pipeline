from __future__ import annotations

import os
import runpy
import sys
from types import SimpleNamespace

import pytest
from cgi_pipeline.hosts.maya.worker import _build_env
from cgi_pipeline.paths import SOURCE_ROOT


def test_mayapy_uses_host_packages_without_brain_site_packages(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", r"C:\existing")
    monkeypatch.setenv("PYTHONNOUSERSITE", "0")

    env = _build_env("test-worker")

    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(SOURCE_ROOT),
        r"C:\existing",
    ]


@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_background_adapter_uses_deployed_root_and_shared_transport(tmp_path, monkeypatch, host):
    from cgi_pipeline.hosts.file_ipc import FileIpcTransport
    monkeypatch.setenv('CGI_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('CGI_WORKER_ID', 'deployed')
    worker = FileIpcTransport('deployed', tmp_path)
    worker.send({'task_id': 'queued', 'api_id': 'test.api'})
    if host == 'maya':
        standalone = SimpleNamespace(initialize=lambda **kwargs: None)
        cmds = SimpleNamespace(optionVar=lambda **kwargs: None)
        monkeypatch.setitem(sys.modules, 'maya', SimpleNamespace(standalone=standalone, cmds=cmds))
        monkeypatch.setitem(sys.modules, 'maya.standalone', standalone)
        monkeypatch.setitem(sys.modules, 'maya.cmds', cmds)
    else:
        monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    namespace = runpy.run_path(str(SOURCE_ROOT / 'cgi_pipeline' / 'hosts' / host / 'adapter.py'))
    assert isinstance(namespace['IPC'], FileIpcTransport)
    assert namespace['IPC'].claim()['task_id'] == 'queued'
    namespace['_write_result']('queued', 'SUCCESS', 'done')
    assert worker.exchange({'task_id': 'queued'}) == {
        'task_id': 'queued', 'status': 'SUCCESS', 'detail': 'done'}
