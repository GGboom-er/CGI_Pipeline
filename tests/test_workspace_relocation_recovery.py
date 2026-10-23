"""True process-exit persistence and live foreground scope protections."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.core import workspace, workspace_relocation as move
from cgi_pipeline.core import workspace_relocation_evidence as evidence
from cgi_pipeline.core.workspace_adoption_evidence import inventory, digest_file
from test_workspace_relocation import relocation_case, root


@pytest.mark.parametrize('point,existing', [('prepared', False), ('moved', False), ('identity_written', False),
                                          ('member_moved', True), ('identity_written', True)])
@pytest.mark.parametrize('drift', ['none', 'metadata', 'native_result'])
def test_real_process_exit_then_explicit_original_id_rollback(root, monkeypatch, point, existing, drift):
    code = r'''
import os,sys
from pathlib import Path
import pytest
from test_workspace import root
from test_workspace_relocation import relocation_case,existing_target
from cgi_pipeline.core import workspace_relocation as move
mp=pytest.MonkeyPatch()
base=Path(sys.argv[1])
root.__wrapped__(base,mp)
c=relocation_case.__wrapped__(base,mp)
if sys.argv[3]=='True': existing_target(c)
def kill(stage,*args):
    if stage==sys.argv[2]: os._exit(87)
move.relocate_workspace(c.params,c.context,checkpoint=kill)
raise RuntimeError('Expected kill checkpoint did not run')
'''
    repository = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(repository / 'src'), str(repository / 'tests'))))
    result = subprocess.run([sys.executable, '-c', code, str(root), point, str(existing)],
                            cwd=repository, env=environment, capture_output=True, timeout=60)
    assert result.returncode == 87, result.stderr.decode(errors='replace')
    maintenance = root / 'projects/default/MAINT'
    manifest = workspace._read(maintenance / 'manifest.json')
    activity = workspace._read(maintenance / 'activity.json')
    ownership = {key: activity[key] for key in ('sources', 'expected_nodes', 'known_task_ids', 'known_tasks',
                                              'ledger_activity', 'process_inventory', 'unknown')}
    configuration = {key: activity['broker'][key] for key in ('identity', 'expected_queue_keys')}
    monkeypatch.setattr(evidence.probe, 'read_runtime_ownership', lambda ids: {**ownership, 'known_task_ids': list(ids)})
    monkeypatch.setattr(evidence.probe, 'broker_configuration', lambda *args: configuration)
    monkeypatch.setattr(evidence.probe, 'collect_activity_snapshot', lambda project,business,directory,**kw:
        {**activity, 'target_project': project, 'workspace_root': str(directory), 'known_task_ids': list(kw.get('known_task_ids', []))})
    monkeypatch.setattr(move, '_foreground_clear', lambda *args: [])
    from cgi_pipeline.core.task_audit import read_relocation_route
    route = read_relocation_route('api-relocate', root / 'audit')
    assert move.recover_relocation('api-relocate', route)['status'] == 'NEEDS_ATTENTION'
    target_activity = {**activity, 'target_project': 'default', 'workspace_root': str(root / 'projects/default/TARGET')}
    (maintenance / 'target_activity.json').write_bytes(evidence.canonical(target_activity))
    params = dict(mode='reconcile', original_task_id='api-relocate', source_project='ysj', target_project='default',
        business_task_id='TARGET', activity_relpath='activity.json', activity_sha256=digest_file(maintenance / 'activity.json'),
        target_activity_relpath='target_activity.json', target_activity_sha256=digest_file(maintenance / 'target_activity.json'))
    context = ApiContext.from_mapping(dict(project='default', task_id='api-reconcile', workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    if drift != 'none':
        if drift == 'metadata':
            candidates = [root / 'projects/ysj/TARGET/.info/run_state.json', root / 'projects/default/TARGET/.info/run_state.json']
            changed = next(path for path in candidates if path.exists())
            value = workspace._read(changed)
            value['foreign_scene_metadata'] = {'owner': 'unapproved-change'}
        else:
            changed = workspace.operation_paths(maintenance, 'api-relocate')['result']
            value = workspace._read(changed)
            value['input']['params']['manifest_sha256'] = '0' * 64
        workspace._write(changed, value)
        before = changed.read_bytes()
        with pytest.raises(workspace.WorkspaceError):
            move.relocate_workspace(params, context)
        assert changed.read_bytes() == before
        return
    if point == 'prepared' and not existing:
        import asyncio
        from fastmcp import Client
        from test_mcp_contract_delivery import make_server
        async def through_public_model():
            async with Client(make_server()) as client:
                response = await client.call_tool('execute_api', {'params': dict(api_id=move.API_ID,
                    project='ysj', workspace_project='default', workspace_id='MAINT', business_task_id='MAINT',
                    execution_mode='background', params=params)}, raise_on_error=False)
                assert response.structured_content['status'] == 'SUCCESS', response.structured_content
                return response.structured_content
        recovered = asyncio.run(through_public_model())
    else:
        recovered = move.relocate_workspace(params, context)
    assert recovered['output']['rolled_back'] is True and recovered['output']['replayed'] is False
    assert inventory(root / 'projects/ysj/TARGET', ignored=move.LOCKS) == manifest['source_entries']
    if existing:
        assert inventory(root / 'projects/default/TARGET', ignored=move.LOCKS) == manifest['target_entries']
    else:
        assert not (root / 'projects/default/TARGET').exists()
    assert move.recover_relocation('api-relocate', route)['rolled_back'] is True
    assert move.relocation_for_root(root / 'projects/ysj/TARGET') is None


@pytest.mark.parametrize('condition', ['foreign_dirty', 'source_clean', 'source_dirty', 'reference', 'truncated_refs', 'unreachable_process', 'query_failed'])
def test_foreground_queries_never_save_or_close_and_block_owned_or_unknown(relocation_case, monkeypatch, condition):
    c = relocation_case
    import psutil
    monkeypatch.setattr('cgi_pipeline.execution.execute_api', lambda *a, **kw: dict(status='SUCCESS',
        output={'sessions': [dict(dcc='maya', port=7001, session_id='maya:7001')]}))
    process = SimpleNamespace(info=dict(pid=10, name='maya.exe'))
    monkeypatch.setattr(psutil, 'process_iter', lambda *args: [process])
    listener = SimpleNamespace(pid=10, laddr=SimpleNamespace(port=7001), status=psutil.CONN_LISTEN)
    monkeypatch.setattr(psutil, 'net_connections', lambda **kwargs: [] if condition == 'unreachable_process' else [listener])
    observed = []
    def inspect(payload, sync):
        observed.append(payload)
        assert sync is True and payload['api_id'] == 'maya.asset.maya_get_scene_info' and payload['api_params'] == {}
        path = str(c.source / 'outputs/original.ma') if condition.startswith('source_') else str(c.root / 'foreign.ma')
        info = dict(scene=dict(path=path, modified=condition != 'source_clean'),
            references=[str(c.source / 'outputs/original.ma')] if condition == 'reference' else [],
            statistics={'references': 11 if condition == 'truncated_refs' else (1 if condition == 'reference' else 0)})
        return dict(status='ERROR' if condition == 'query_failed' else 'SUCCESS', detail={'output': {'result': info}})
    monkeypatch.setattr('cgi_pipeline.server.foreground_client.submit_foreground_task', inspect)
    if condition == 'foreign_dirty':
        assert evidence._foreground_clear((c.source, c.target), 'api-check', 'ysj')
    else:
        with pytest.raises(workspace.WorkspaceError, match='foreground|Foreground|Maya|reference'):
            evidence._foreground_clear((c.source, c.target), 'api-check', 'ysj')
    assert all(row['api_id'] == 'maya.asset.maya_get_scene_info' for row in observed)
