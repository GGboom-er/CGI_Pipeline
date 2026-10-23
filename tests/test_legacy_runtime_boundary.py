from types import SimpleNamespace

import psutil
import pytest

from cgi_pipeline.core import service_manager


@pytest.mark.parametrize('markers', [
    {'CGI_RUNTIME_ID': 'slot-1'},
    {'CGI_RUNTIME_GENERATION': 'generation'},
    {'CGI_RUNTIME_ROOT': 'runtime/slot-1'},
    {'CGI_RUNTIME_ID': ''},
])
def test_legacy_orphan_sweep_never_reclaims_scoped_hosts(monkeypatch, markers):
    host = SimpleNamespace(pid=100, info={'pid': 100, 'name': 'mayapy.exe', 'ppid': 200,
                           'environ': {'CGI_WORKER_ID': 'worker', **markers}})
    monkeypatch.setattr(psutil, 'process_iter', lambda fields: [host])
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: False)
    killed = []
    monkeypatch.setattr(service_manager, '_kill_process_tree', lambda pid: killed.append(pid))
    service_manager._kill_orphan_dcc_processes()
    assert killed == []


def test_legacy_sweep_keeps_foreground_and_its_existing_legacy_behavior(monkeypatch):
    foreground = SimpleNamespace(pid=100, info={'name': 'maya.exe', 'environ': {}, 'ppid': 200})
    legacy = SimpleNamespace(pid=101, info={'name': 'blender.exe', 'environ': {'CGI_WORKER_ID': 'old'}, 'ppid': 201})
    monkeypatch.setattr(psutil, 'process_iter', lambda fields: [foreground, legacy])
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: False)
    killed = []
    monkeypatch.setattr(service_manager, '_kill_process_tree', lambda pid: killed.append(pid))
    service_manager._kill_orphan_dcc_processes()
    assert killed == [101]
