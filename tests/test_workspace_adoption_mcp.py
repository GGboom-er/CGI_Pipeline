"""The registered MCP path, with a fake idle broker and real native receipt IO."""
import asyncio
from copy import deepcopy

import pytest
from fastmcp import Client

from cgi_pipeline.core import workspace_adoption as adopt
from cgi_pipeline.server import internals
from test_mcp_contract_delivery import make_server
from test_workspace_adoption import case
from test_workspace import root


def arguments(case):
    return {'params': dict(api_id=adopt.API_ID, project='default', workspace_id='MAINT',
        business_task_id='MAINT', params=case.params, execution_mode='background', wait=True)}


@pytest.mark.parametrize('failure', ['none', 'outer_audit', 'postcommit_handler', 'response_lost'])
def test_registered_submission_then_new_session_original_id_recovery(case, monkeypatch, failure):
    from cgi_pipeline.capabilities.pipeline.workspace.adopt import handler
    real, observed = adopt.adopt_workspace, {}
    def execute(params, context):
        observed['task_id'] = context.extras['task_id']
        def fault(stage, target, route):
            if failure == 'postcommit_handler' and stage == 'committed':
                raise ConnectionError('postcommit response unavailable')
        return real(params, context, checkpoint=fault)
    monkeypatch.setattr(handler, 'adopt_workspace', execute)
    if failure == 'outer_audit':
        def append_failure(*args, **kwargs):
            raise OSError('outer append failed after native commit')
        monkeypatch.setattr(internals, '_append_submission_audit', append_failure)
    monkeypatch.setattr(internals, '_ensure_worker', lambda *args: pytest.fail('control must not start a worker'))
    async def scenario():
        async with Client(make_server()) as client:
            response = await client.call_tool('execute_api', arguments(case), raise_on_error=False)
            if failure == 'none':
                assert response.data['status'] == 'SUCCESS'
                assert response.data['output']['binding']['project'] == 'default'
                assert response.data['output']['binding']['workspace_id'] == 'TARGET'
                observed['receipt'] = {key: response.data[key] for key in
                    ('api_id', 'api_version', 'status', 'input', 'output', 'elapsed_sec',
                     'error_code', 'error', 'recovery_hint', 'artifacts')}
            elif failure in ('outer_audit', 'postcommit_handler'):
                assert response.is_error
            # Discard the response and close the actual MCP session before recovering.
        monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state',
            lambda *args: pytest.fail('persistent adoption recovery cannot depend on Redis'))
        async with Client(make_server()) as replacement:
            recovered = await replacement.call_tool('get_task_result', {'params': {'task_id': observed['task_id']}})
            value = recovered.data
            assert value['status'] == 'SUCCESS'
            assert value['receipt']['output']['task_id'] == observed['task_id']
            assert value['workspace_id'] == 'TARGET'
            if 'receipt' in observed:
                assert value['receipt'] == observed['receipt']
    asyncio.run(scenario())


def test_registered_batch_and_workflow_reject_control_spec_before_binding(case, monkeypatch):
    from cgi_pipeline.core import workflow_engine
    monkeypatch.setattr('cgi_pipeline.server.tools_operations._bind_workspace',
        lambda *args, **kwargs: pytest.fail('batch rejection must precede workspace mutation'))
    monkeypatch.setattr(internals, '_bind_workspace',
        lambda *args, **kwargs: pytest.fail('workflow rejection must precede workspace mutation'))
    raw = {'workflow_id': 'test-adopt-embedded', 'name': 'test', 'extra_params': {},
           'steps': [{'api_id': adopt.API_ID, 'params': case.params}]}
    monkeypatch.setattr(workflow_engine, 'load_workflow', lambda *args: deepcopy(raw))
    monkeypatch.setattr(workflow_engine, 'compile_workflow', lambda *args: deepcopy(raw))
    async def scenario():
        async with Client(make_server()) as client:
            batch = await client.call_tool('execute_batch', {'params': dict(project='default',
                workspace_id='MAINT', business_task_id='MAINT',
                calls=[dict(api_id=adopt.API_ID, params=case.params)])}, raise_on_error=False)
            assert batch.structured_content['error_code'] == 'BATCH_CONTRACT_ERROR'
            workflow = await client.call_tool('pipeline_execute_workflow', {'params': dict(
                workflow_id=raw['workflow_id'], project='default', workspace_id='MAINT',
                business_task_id='MAINT')}, raise_on_error=False)
            assert workflow.structured_content['error_code'] == 'WORKFLOW_INPUT_INVALID'
    asyncio.run(scenario())
