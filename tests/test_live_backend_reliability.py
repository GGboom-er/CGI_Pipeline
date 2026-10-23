"""Opt-in real-host acceptance; never starts DCCs during the ordinary test suite.

This exercises the existing worker/adapter and scene-session layers in a fresh
Python process. It does not validate public MCP/Celery multi-session routing.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid

import psutil
import pytest

from cgi_pipeline.core import dcc_factory, run_archive, scene_session, workspace
from cgi_pipeline.core.capability_registry import resolve_api_id
from cgi_pipeline.paths import REPOSITORY_ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get('CGI_LIVE_DCC_ACCEPTANCE') != 'PL272',
    reason='Starts owned real DCCs only with CGI_LIVE_DCC_ACCEPTANCE=PL272',
)
WORK = REPOSITORY_ROOT / 'projects' / 'default' / 'PL272' / 'work'
RUN_ID = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
SUMMARY = {'run_id': RUN_ID, 'status': 'RUNNING', 'cases': []}


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _process(worker):
    process = worker.process
    result = {'worker_id': worker.worker_id, 'pid': process.pid,
              'exit_code': process.poll()}
    try:
        if process.poll() is None:
            host = psutil.Process(process.pid)
            result.update(create_time=host.create_time(), exe=host.exe(), cmdline=host.cmdline())
    except psutil.NoSuchProcess:
        result['exit_code'] = process.poll()
    return result


class RecordedWorker:
    """Observation-only wrapper; every command goes unchanged to the real worker."""

    def __init__(self, proxy, journal):
        self.real_worker = proxy.real_worker
        self.worker_id = proxy.worker_id
        self.journal = journal

    def _call(self, method, payload, *, bound=120):
        record = {'method': method, 'payload': payload, 'before': _process(self.real_worker)}
        started, outcome = time.monotonic(), {}

        def communicate():
            try:
                outcome['result'] = getattr(self.real_worker, method)(payload)
            except BaseException as exc:
                outcome.update(exception=exc, traceback=traceback.format_exc())

        # Only host IPC waits run in this thread. DCC scene code stays in its
        # existing adapter main thread. The bound is a harness guard, not an IPC timeout.
        thread = threading.Thread(target=communicate, daemon=True)
        thread.start()
        thread.join(bound)
        record.update(elapsed_sec=time.monotonic() - started, guard_expired=thread.is_alive(),
                      after=_process(self.real_worker))
        record.update({key: str(value) if key == 'exception' else value
                       for key, value in outcome.items()})
        self.journal['calls'].append(record)
        _write(self.journal['path'], {key: value for key, value in self.journal.items() if key != 'path'})
        assert not thread.is_alive(), f'{method} did not terminate within harness bound {bound}s'
        if 'exception' in outcome:
            raise outcome['exception']
        return outcome['result']

    def run_api(self, payload):
        return self._call('run_api', payload)


class LiveSession:
    def __init__(self, dcc, directory):
        self.dcc, self.directory, self.owned, self.worker = dcc, directory, [], None
        self.api_id = resolve_api_id('exec_code' if dcc == 'maya' else 'blender_exec_code')
        self.journal = {'status': 'RUNNING', 'dcc': dcc, 'run_dir': str(directory),
                        'path': directory / 'receipts.json', 'calls': [], 'scenes': [], 'events': []}

    def operation(self, **values):
        payload = dict(task_id=f'live-{self.dcc}-{uuid.uuid4().hex[:12]}', project='default',
                       business_task_id=f'PL272_live_{RUN_ID}_{self.dcc}',
                       api_id=self.api_id, api_params={}, **values)
        workspace.start_operation(payload, 'api')
        assert Path(payload['run_dir']).resolve().is_relative_to(self.directory.resolve())
        return payload

    def create(self, *args, **kwargs):
        proxy = dcc_factory.create_worker(*args, **kwargs)
        if proxy.real_worker not in self.owned:
            self.owned.append(proxy.real_worker)
        self.worker = RecordedWorker(proxy, self.journal)
        return self.worker

    def scene(self, action, source='', expected_error=None, following_code=None):
        payload = self.operation(scene_action=action, source_path=str(source))
        record = {'payload': payload.copy(), 'calls_before': len(self.journal['calls'])}
        try:
            self.worker, _ = scene_session.prepare_scene(
                payload, self.dcc, self.create, dcc_factory.open_source_file,
                lambda event, details: self.journal['events'].append({'event': event, 'details': details}))
            if following_code is not None:
                self.execute(following_code)
            assert expected_error is None, f'Expected {expected_error}, scene preparation succeeded'
            workspace.finish_operation(payload, 'SUCCESS')
            return payload['scene_session']
        except workspace.WorkspaceError as exc:
            record.update(error_code=exc.code, error=str(exc))
            workspace.finish_operation(payload, 'ERROR')
            assert exc.code == expected_error, record
        finally:
            record.update(binding=workspace.get_scene_binding(payload['run_dir'], self.dcc),
                          scene_session=payload.get('scene_session'),
                          calls_after=len(self.journal['calls']),
                          owner=getattr(self.worker.real_worker, '_scene_workspace_id', None)
                          if self.worker else None)
            self.journal['scenes'].append(record)

    def payload(self, code, **extra):
        payload = self.operation()
        payload.update(api_params={'code': code, 'description': 'PL272 owned-host acceptance'},
                       api_context={key: payload[key] for key in
                                    ('task_id', 'workspace_id', 'run_dir', 'project', 'asset_name')}, **extra)
        payload['api_context']['execution_mode'] = 'background'
        return payload

    def execute(self, code):
        payload = self.payload(code)
        outer = self.worker.run_api(payload)
        receipt = json.loads(outer['detail']) if isinstance(outer.get('detail'), str) else outer['detail']
        workspace.finish_operation(payload, receipt['status'])
        assert outer['status'] == receipt['status'] == 'SUCCESS', outer
        return receipt['output']['result']

    def close(self):
        closed = []
        for worker in self.owned:
            before = _process(worker)
            worker.shutdown()  # Real worker only; WarmWorkerProxy intentionally retains scenes.
            if getattr(worker, '_stderr_log', None):
                worker._stderr_log.close()
            closed.append({'before': before, 'after': _process(worker)})
        entry = dcc_factory._warm_pool.get(self.dcc)
        if entry and entry['worker'] in self.owned:
            dcc_factory._warm_pool.pop(self.dcc)
        self.journal['closed'] = closed
        _write(self.journal['path'], {key: value for key, value in self.journal.items() if key != 'path'})
        assert all(item['after']['exit_code'] is not None for item in closed), closed


def _scripts(dcc, baseline):
    if dcc == 'maya':
        query = ("import maya.cmds as cmds, sys\nresult = {'value': cmds.getAttr('PL272_probe.translateX'), "
                 "'python': sys.version, 'version': cmds.about(version=True), "
                 "'scene': cmds.file(query=True, sceneName=True)}")
        create = ("import maya.cmds as cmds\nnode = cmds.polyCube(name='PL272_probe', constructionHistory=False)[0]\n"
                  "cmds.setAttr(node + '.translateX', 1)\n"
                  f"cmds.file(rename={str(baseline)!r})\ncmds.file(save=True, type='mayaBinary', force=True)\n")
        mutate = "import maya.cmds as cmds\ncmds.setAttr('PL272_probe.translateX', 7)\n"
    else:
        query = ("import bpy, sys\nresult = {'value': bpy.data.objects['PL272_probe'].location.x, "
                 "'python': sys.version, 'version': bpy.app.version_string, 'scene': bpy.data.filepath}")
        create = ("import bpy\nmesh = bpy.data.meshes.new('PL272_probe_mesh')\n"
                  "mesh.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,2)])\n"
                  "obj = bpy.data.objects.new('PL272_probe', mesh)\nbpy.context.scene.collection.objects.link(obj)\n"
                  "obj.location.x = 1\nbpy.context.view_layer.update()\n"
                  f"bpy.ops.wm.save_as_mainfile(filepath={str(baseline)!r}, check_existing=False)\n")
        mutate = "import bpy\nbpy.data.objects['PL272_probe'].location.x = 7\nbpy.context.view_layer.update()\n"
    return create + query, mutate + query, query


@pytest.mark.parametrize('dcc', ['maya', 'blender'])
def test_owned_live_backend_reliability(dcc, monkeypatch):
    assert not dcc_factory._warm_pool, 'Run this file alone in a fresh Python process'
    assert os.environ.get('IPC_TIMEOUT_SEC', '0') == '0', 'Acceptance requires default no-deadline IPC'
    directory = WORK / 'live_backend' / RUN_ID / dcc
    directory.mkdir(parents=True, exist_ok=False)
    # PL272 itself stores task materials, not a pre-existing DCC workspace.
    # Keep real workspace allocation in this case's fresh managed data root.
    data_root = directory / 'workspace_data'
    monkeypatch.setattr(run_archive, 'PROJECT_ROOT', data_root)
    monkeypatch.setattr(run_archive, 'PROJECTS_DIR', data_root / 'projects')
    monkeypatch.setattr(run_archive, 'RUNS_DIR', data_root / 'runs')
    # Relocate launcher-owned IPC/logs and user/cache settings, not worker behavior
    # or the real adapter/source paths. pytest restores these process-local values.
    host_module = importlib.import_module(f'cgi_pipeline.hosts.{dcc}.worker')
    runtime = directory / 'runtime'
    runtime.mkdir()
    monkeypatch.setattr(host_module, 'PROJECT_ROOT', runtime)
    temporary = WORK.parent / 'temp' / 'live_backend' / RUN_ID / dcc
    for key in ('MAYA_APP_DIR', 'BLENDER_USER_CONFIG', 'BLENDER_USER_DATAFILES',
                'BLENDER_USER_SCRIPTS', 'TEMP', 'TMP'):
        target = temporary / key.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(target))
    suffix = '.mb' if dcc == 'maya' else '.blend'
    baseline, corrupt = directory / ('baseline' + suffix), directory / ('corrupt' + suffix)
    corrupt.write_bytes(b'PL272 deliberately invalid native scene\x00\xff\x01')
    session = LiveSession(dcc, directory)
    sources = [Path(__file__), *[REPOSITORY_ROOT / path for path in (
        'src/cgi_pipeline/core/dcc_factory.py', 'src/cgi_pipeline/core/scene_session.py',
        'src/cgi_pipeline/core/scene_open.py', 'src/cgi_pipeline/core/workspace.py',
        'src/cgi_pipeline/hosts/file_ipc.py', f'src/cgi_pipeline/hosts/{dcc}/worker.py',
        f'src/cgi_pipeline/hosts/{dcc}/adapter.py', 'src/cgi_pipeline/execution.py')]]
    session.journal.update(source_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                           scope='Real host/adapter/scene-session only; no public MCP or Celery multi-instance claim',
                           test_python=sys.version, ipc_timeout=0)
    try:
        assert session.scene('open')['status'] == 'OPENED'
        create, mutate, query = _scripts(dcc, baseline)
        assert session.execute(create)['value'] == 1
        assert baseline.is_file() and baseline.stat().st_size > 0
        original_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
        assert session.scene('open', baseline)['status'] == 'OPENED'
        assert session.execute(mutate)['value'] == 7
        assert session.scene('continue')['status'] == 'REUSED'
        assert session.execute(query)['value'] == 7
        assert session.scene('open', baseline)['status'] == 'OPENED'
        assert session.execute(query)['value'] == 1
        assert hashlib.sha256(baseline.read_bytes()).hexdigest() == original_hash
        # Generic exec's arbitrary result.status must remain nested, not promoted.
        assert session.execute("result = {'status': 'ERROR', 'purpose': 'compatibility'}")['status'] == 'ERROR'
        assert 'SCENE_REUSED' in [event['event'] for event in session.journal['events']]
        marker = directory / 'unexpected-business.txt'
        session.scene('open', corrupt, 'WORKSPACE_SCENE_OPEN_FAILED',
                      following_code=f"open({str(marker)!r}, 'w').write('must not execute')\nresult = {{}}")
        failed = session.journal['scenes'][-1]
        assert failed['binding'].get('state') != 'READY' and failed['owner'] is None
        assert not marker.exists(), 'Business API executed after corrupt-source open'
        # Production scene preparation must fail before any following business call.
        before = len(session.journal['calls'])
        session.scene('continue', expected_error='WORKSPACE_SCENE_UNAVAILABLE')
        assert len(session.journal['calls']) == before
        assert session.scene('open', baseline)['status'] == 'OPENED'
        assert session.execute(query)['value'] == 1
        if dcc == 'blender':
            from cgi_pipeline.hosts.blender.worker import IPC_TIMEOUT
            assert IPC_TIMEOUT == 0
            calls = [session.payload(query), session.payload("result = {'batch_second': True}")]
            batch = session.worker._call('run_batch', calls)
            assert batch['status'] == 'SUCCESS' and batch['completed'] == batch['requested'] == 2
            assert batch['results'][0]['output']['result']['value'] == 1
            for payload in calls:
                workspace.finish_operation(payload, 'SUCCESS')
            for method, exit_code in [('run_api', 37), ('run_batch', 38)]:
                previous = session.worker.worker_id
                payload = session.payload(f'import os\nos._exit({exit_code})')
                arg = [payload] if method == 'run_batch' else payload
                try:
                    terminal = session.worker._call(method, arg, bound=15)
                    assert method == 'run_api' and terminal['status'] == 'ERROR', terminal
                except RuntimeError as exc:
                    assert method == 'run_batch' and str(exit_code) in str(exc), str(exc)
                assert session.worker.real_worker.process.poll() == exit_code
                workspace.finish_operation(payload, 'ERROR')
                session.scene('continue', expected_error='WORKSPACE_SCENE_UNAVAILABLE')
                assert session.scene('open', baseline)['status'] == 'OPENED'
                assert session.worker.worker_id != previous
                assert session.execute(query)['value'] == 1
        session.journal.update(status='SUCCESS', baseline_sha256=original_hash)
    except BaseException:
        session.journal.update(status='FAILED', failure=traceback.format_exc())
        raise
    finally:
        try:
            session.close()
        except BaseException:
            session.journal.update(status='FAILED', cleanup_failure=traceback.format_exc())
            raise
        finally:
            SUMMARY['cases'].append({'dcc': dcc, 'status': session.journal['status'],
                                     'receipts': str(session.journal['path'])})
            SUMMARY['status'] = ('FAILED' if any(case['status'] == 'FAILED' for case in SUMMARY['cases'])
                                 else 'SUCCESS' if len(SUMMARY['cases']) == 2 else 'PARTIAL')
            _write(WORK / 'live-backend-result.json', SUMMARY)
