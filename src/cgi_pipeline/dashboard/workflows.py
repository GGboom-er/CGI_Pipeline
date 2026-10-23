"""HTTP adapters for registered workflows; compilation and submission have one owner."""
import asyncio
import copy

from fastapi import APIRouter, Request

from cgi_pipeline.core import workflow_engine


router = APIRouter(prefix='/api/workflows')


@router.get('')
async def list_workflows():
    return {'workflows': workflow_engine.list_workflows()}


@router.post('/compile')
async def compile_workflow(request: Request):
    try:
        body = await request.json()
        raw = workflow_engine.load_workflow(body['workflow_id'])
        plan = workflow_engine.compile_workflow(raw, body.get('extra_params'))
        contract = workflow_engine.workflow_input_contract(plan)
        return {'status': 'SUCCESS', 'workflow': plan,
                'parameters': copy.deepcopy(raw.get('parameters', {})), **contract}
    except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
        return {'status': 'ERROR', 'error_code': 'WORKFLOW_COMPILE_INVALID', 'error': str(exc)}


@router.post('/execute')
async def execute_workflow(request: Request):
    from cgi_pipeline.server.internals import _submit_workflow
    body = await request.json()
    payload = {key: body[key] for key in (
        'workflow_id', 'extra_params', 'project', 'asset_name', 'source_path',
        'workspace_id', 'business_task_id', 'scene_action'
    ) if key in body}
    result = await asyncio.to_thread(_submit_workflow, payload)
    return {**result, 'dispatched': result.get('status') == 'SUBMITTED'}
