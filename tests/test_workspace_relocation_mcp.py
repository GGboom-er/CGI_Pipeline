"""Registered public request validation, dispatch and original-ID recovery."""
import asyncio
from copy import deepcopy

import pytest
from fastmcp import Client

from cgi_pipeline.core import workspace_relocation as move
from cgi_pipeline.server import internals
from test_mcp_contract_delivery import make_server
from test_workspace_relocation import relocation_case, root


@pytest.mark.parametrize('mode', ['preview', 'apply'])
@pytest.mark.parametrize('project', ['default', 'ysj'])
def test_registered_public_request_separates_business_and_storage(relocation_case, monkeypatch, mode, project):
    c = relocation_case
    monkeypatch.setattr(internals, '_ensure_worker', lambda *args: pytest.fail('Control must not start any worker.'))
    async def run():
        async with Client(make_server()) as client:
            response = await client.call_tool('execute_api', {'params': dict(api_id=move.API_ID,
                project=project, workspace_project='default', business_task_id='MAINT', workspace_id='MAINT',
                execution_mode='background', wait=True, params={**c.params, 'mode': mode})}, raise_on_error=False)
            value = response.structured_content
            assert value['status'] == 'SUCCESS', value
            original_id = value['task_id']
            if mode == 'preview':
                assert value['output']['preview'] is True
                assert c.source.exists() and not c.target.exists()
            else:
                assert value['output']['binding']['project'] == 'default'
                assert value['output']['business_project'] == 'ysj'
        if mode == 'apply':
            async with Client(make_server()) as second:
                recovered = await second.call_tool('get_task_result', {'params': {'task_id': original_id}})
                assert recovered.structured_content['status'] == 'SUCCESS'
                assert recovered.structured_content['receipt']['output'] == value['output']
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['outer_audit', 'postcommit_reply'])
def test_public_unknown_response_recovers_original_id_without_replay(relocation_case, monkeypatch, fault):
    c = relocation_case
    from cgi_pipeline.capabilities.pipeline.workspace.relocate import handler
    real = move.relocate_workspace
    observed = {}
    def execute(params, context):
        observed['task_id'] = context.extras['task_id']
        def checkpoint(stage, *args):
            if fault == 'postcommit_reply' and stage == 'committed':
                raise OSError('Lost response after native commit')
        return real(params, context, checkpoint=checkpoint)
    monkeypatch.setattr(handler, 'relocate_workspace', execute)
    if fault == 'outer_audit':
        monkeypatch.setattr(internals, '_append_submission_audit', lambda *a, **k: (_ for _ in ()).throw(OSError('outer append lost')))
    async def run():
        async with Client(make_server()) as client:
            response = await client.call_tool('execute_api', {'params': dict(api_id=move.API_ID,
                project='default', workspace_project='default', business_task_id='MAINT', workspace_id='MAINT',
                execution_mode='background', params=c.params)}, raise_on_error=False)
            assert response.is_error
        monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state', lambda *args: pytest.fail('Recovery must not depend on Redis.'))
        async with Client(make_server()) as replacement:
            recovered = await replacement.call_tool('get_task_result', {'params': {'task_id': observed['task_id']}})
            assert recovered.structured_content['status'] == 'SUCCESS'
            assert recovered.structured_content['receipt']['output']['task_id'] == observed['task_id']
    asyncio.run(run())
