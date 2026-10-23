"""Windows native path aliases must not turn atomic publication into escape errors."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cgi_pipeline.core import workspace
from test_workspace import root, begin

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native path resolution')


@pytest.mark.parametrize('name', ['state.json', 'REPORT.md'])
def test_operation_paths_accept_native_alias_during_atomic_replacement(root, monkeypatch, name):
    import ntpath
    first = begin('api-first')
    paths = workspace.operation_paths(first['run_dir'], 'api-first')
    target = paths['operation_dir'] / name
    if not target.exists():
        target.write_text('synthetic report', encoding='utf-8')
    original_bytes = target.read_bytes()
    parked = target.with_name(target.name + '.parked')
    native = ntpath._getfinalpathname
    observed = []

    def atomic_gap(path):
        resolved = native(path)
        if Path(path) == target and not observed:
            # Windows realpath checks this path twice to remove its extended
            # prefix. A concurrent os.replace can remove the old name between
            # those checks, leaving the equivalent extended path as its result.
            observed.append(resolved)
            target.rename(parked)
        return resolved

    monkeypatch.setattr(ntpath, '_getfinalpathname', atomic_gap)
    try:
        assert workspace.operation_paths(first['run_dir'], 'api-first') == paths
        assert observed and observed[0].startswith('\\\\?\\')
    finally:
        monkeypatch.setattr(ntpath, '_getfinalpathname', native)
        if parked.exists():
            parked.rename(target)
    assert target.read_bytes() == original_bytes


def test_operation_paths_still_reject_real_outside_junction(root):
    import _winapi
    first = begin('api-first')
    outside = root / 'outside'
    outside.mkdir()
    marker = outside / 'keep.txt'
    marker.write_text('unchanged', encoding='utf-8')
    link = Path(first['run_dir']) / '.info' / 'operations' / 'linked'
    _winapi.CreateJunction(str(outside), str(link))
    try:
        with pytest.raises(workspace.WorkspaceError, match='local'):
            begin('linked', workspace_id=first['workspace_id'])
        assert marker.read_text(encoding='utf-8') == 'unchanged'
        assert {path.name for path in outside.iterdir()} == {'keep.txt'}
    finally:
        link.rmdir()


@pytest.mark.parametrize('kind', ['maya_scene', 'maya_reference', 'blender_scene'])
def test_foreground_guard_rejects_owned_native_alias_during_replacement(root, monkeypatch, kind):
    import ntpath
    import psutil
    from cgi_pipeline import execution
    from cgi_pipeline.server import foreground_client
    from cgi_pipeline.core import workspace_relocation_evidence as evidence

    source = root / 'source'
    source.mkdir()
    owned = source / ('owned.blend' if kind == 'blender_scene' else 'owned.ma')
    owned.write_bytes(b'untouched scene')
    parked = owned.with_suffix('.parked')
    if kind == 'blender_scene':
        sessions = [dict(dcc='blender', pid=123, file=str(owned))]
    else:
        sessions = [dict(dcc='maya', port=7001)]
    references = [str(owned)] if kind == 'maya_reference' else []
    scene = str(root / 'unrelated.ma') if references else str(owned)
    detail = dict(output=dict(result=dict(scene=dict(path=scene), references=references,
                                         statistics=dict(references=len(references)))))
    monkeypatch.setattr(execution, 'execute_api', lambda *args: dict(status='SUCCESS', output=dict(sessions=sessions)))
    monkeypatch.setattr(foreground_client, 'submit_foreground_task', lambda *args, **kwargs: dict(status='SUCCESS', detail=detail))
    monkeypatch.setattr(evidence.probe, 'read_runtime_ownership', lambda *args: dict(unknown=[], process_inventory=[]))
    monkeypatch.setattr(psutil, 'net_connections', lambda **kwargs: [])
    monkeypatch.setattr(psutil, 'process_iter', lambda *args: [])
    native = ntpath._getfinalpathname
    observed = []

    def atomic_gap(path):
        resolved = native(path)
        if Path(path) == owned and not observed:
            observed.append(resolved)
            owned.rename(parked)
        return resolved

    monkeypatch.setattr(ntpath, '_getfinalpathname', atomic_gap)
    try:
        with pytest.raises(workspace.WorkspaceError) as error:
            evidence._foreground_clear((source,), 'api-probe', 'ysj')
        assert error.value.code == 'WORKSPACE_RELOCATION_FOREGROUND_OWNED'
        assert observed and observed[0].startswith('\\\\?\\')
    finally:
        monkeypatch.setattr(ntpath, '_getfinalpathname', native)
        if parked.exists():
            parked.rename(owned)
    assert owned.read_bytes() == b'untouched scene'
