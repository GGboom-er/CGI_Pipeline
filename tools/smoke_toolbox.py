"""Verify the public MCP boundary against a caller-selected dedicated Maya."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def run_economy(port, pid):
    """Exercise connection reuse without scene writes or opening a shelf."""
    source = f"""import os, sys, hashlib, json
from maya import cmds
assert os.getpid() == {pid}, 'Unexpected Maya process'
from cgi_pipeline import catalog
server = sys._cgi_rpyc_servers[{port + 10000}]
shelf = sys.modules.get('DCC.maya.ui.shelf')
scene = {{'path':cmds.file(query=True, sceneName=True),
         'dirty':cmds.file(query=True, modified=True),
         'nodes':hashlib.sha256(json.dumps(sorted(cmds.ls(long=True) or [])).encode()).hexdigest(),
         'selection':cmds.ls(selection=True, long=True), 'time':cmds.currentTime(query=True)}}
result = {{'pid':os.getpid(), 'scene':scene,
          'version':catalog.catalog_version(),
          'modules':{{n:id(sys.modules[n]) for n in (
              'cgi_pipeline.catalog','cgi_pipeline.execution','cgi_pipeline.core.code_reload',
              'cgi_pipeline.contracts','DCC.maya.ui.shelf') if n in sys.modules}},
          'shelf':id(shelf.MayaShelfWindow._instance) if shelf else None,
          'connections':sorted(s.fileno() for s in list(server.clients)),
          'log_level':server.logger.getEffectiveLevel()}}
"""
    receipts, samples = [], []
    for session in range(2):
        transport = StdioTransport(command=sys.executable, args=['-s', '-m', 'cgi_pipeline.server.server'],
                                   keep_alive=False, env={'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8'})
        async with Client(transport, timeout=120) as client:
            contract = (await client.call_tool('api_help', {'api_id':'maya.asset.exec_code'})).data
            version = contract['catalog_version']
            base = {'execution_mode':'foreground', 'foreground_port':port,
                    'expected_catalog_version':version}

            async def call(code, expected='SUCCESS'):
                raw = await client.call_tool('execute_api', {'params':dict(base,
                    api_id='maya.asset.exec_code', params={'code':code, 'description':'PL245 read-only economy smoke'})},
                    raise_on_error=False)
                receipt = raw.structured_content or raw.data
                receipts.append({'task_id':receipt.get('task_id'), 'status':receipt['status']})
                assert receipt['status'] == expected, receipt
                if expected == 'SUCCESS':
                    return receipt['detail']['output']['result']

            first = await call(source)
            samples.append(first)
            assert first['version'] == version and first['log_level'] >= 30, first
            for _ in range(2):
                assert await call(source) == first
            raw = await client.call_tool('execute_batch', {'params':dict(base, atomic=False,
                calls=[{'api_id':'maya.asset.exec_code', 'params':{'code':source,
                        'description':'PL245 read-only batch'}} for _ in range(2)])})
            batch = raw.structured_content or raw.data
            assert batch['status'] == 'SUCCESS', batch
            # Preserve the complete receipt so the batch boundary is independently inspectable.
            receipts.append(batch)
            batch_results = batch.get('results', batch.get('detail', {}).get('results', []))
            assert len(batch_results) == 2, batch
            assert all(item['output']['result'] == first for item in batch_results), batch
            await call("raise ValueError('PL245 expected read-only error')", expected='ERROR')
            assert await call(source) == first
    assert samples[0]['scene'] == samples[1]['scene']
    assert samples[0]['modules'] == samples[1]['modules']
    assert samples[0]['shelf'] == samples[1]['shelf']
    return {'status':'PASS', 'catalog_version':version, 'samples':samples, 'receipts':receipts,
            'checks':['warm_connection_identity', 'module_identity', 'shelf_identity',
                      'read_only_batch', 'error_then_reuse', 'public_client_reconnect', 'scene_unchanged']}


async def run_cold_ui(port, pid, output_dir):
    """Validate installed entrypoints and callbacks in a newly launched test Maya."""
    transport = StdioTransport(command=sys.executable, args=['-s', '-m', 'cgi_pipeline.server.server'],
                               keep_alive=False, env={'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8'})
    async with Client(transport, timeout=180) as client:
        contract = (await client.call_tool('api_help', {'api_id':'maya.asset.exec_code'})).data
        source = f"""import os, sys, json
from pathlib import Path
from maya import cmds
assert os.getpid() == {pid}, 'Unexpected Maya process'
assert not cmds.file(query=True, sceneName=True), 'Expected a dedicated unnamed scene'
import dcc_ui
from DCC.maya import tool_catalog
from DCC.maya.environment import maya_toolbox_smoke as smoke
server = sys._cgi_rpyc_servers[{port + 10000}]
assert hasattr(server, '_close_client_socket'), 'Old RPyC listener implementation'
assert server.logger.getEffectiveLevel() == 30
assert 'DCC/common/dcc_ui' in dcc_ui.__file__.replace('\\\\', '/')
directory = Path({str(output_dir)!r})
directory.mkdir(parents=True, exist_ok=True)
results = {{'retarget':smoke.run(str(directory)),
            'normalize':smoke.run_normalize_edges(),
            'callbacks':smoke.run_callbacks(str(directory))}}
assert all(r['status'] == 'PASS' for r in results.values()), results
assert not cmds.ls('pl245_*', 'pl245scale_*', 'pl245remote_*'), 'Temporary nodes remain'
assert not any('DCC.maya.ui.' + name in sys.modules for name in
               ('compat','dpi','host','window','widgets','styles','i18n'))
result = {{'status':'PASS', 'pid':os.getpid(), 'maya':cmds.about(version=True),
          'python':sys.version, 'ui_source':dcc_ui.__file__,
          'new_rpyc_cleanup':True, 'normal_connection_log_level':server.logger.getEffectiveLevel(),
          'rigging_tools':len(tool_catalog.list_tools(stage='rigging')), 'results':results}}
(directory / 'ui_result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
"""
        raw = await client.call_tool('execute_api', {'params':{
            'api_id':'maya.asset.exec_code', 'execution_mode':'foreground', 'foreground_port':port,
            'expected_catalog_version':contract['catalog_version'],
            'params':{'code':source, 'description':'PL245 cold host UI and shared API acceptance'}}},
            raise_on_error=False)
        receipt = raw.structured_content or raw.data
        assert receipt['status'] == 'SUCCESS', receipt
        return {'status':'PASS', 'catalog_version':contract['catalog_version'], 'receipt':receipt}


async def run(port, pid):
    receipts = []
    transport = StdioTransport(command=sys.executable, args=['-s', '-m', 'cgi_pipeline.server.server'],
                               keep_alive=False, env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    async with Client(transport, timeout=120) as client:
        found = (await client.call_tool('list_apis', {'executor': 'maya', 'search': 'retarget'})).data
        assert found['total'] == 1, found
        version = found['catalog_version']
        help_result = (await client.call_tool('api_help', {'api_id': 'maya.animation.retarget.transfer'})).data
        assert help_result['inputs']['dry_run']['default'] is True
        normalize_id = 'maya.rig.skeleton.normalize_scales'
        discovered = (await client.call_tool('list_apis', {'executor': 'maya', 'search': normalize_id})).data
        assert discovered['total'] == 1 and discovered['catalog_version'] == version
        contract = (await client.call_tool('api_help', {'api_id': normalize_id})).data
        assert contract['inputs']['dry_run']['default'] is True

        async def call(api_id, values, expected='SUCCESS'):
            raw = await client.call_tool('execute_api', {'params': {
                'api_id': api_id, 'params': values, 'execution_mode': 'foreground',
                'foreground_port': port, 'expected_catalog_version': version,
            }}, raise_on_error=False)
            result = raw.structured_content or raw.data
            receipts.append({'task_id': result.get('task_id'), 'api_id': api_id, 'status': result['status']})
            assert result['status'] == expected, result
            detail = result.get('detail', result.get('receipt', {}))
            return detail.get('output', {})

        async def code(source):
            output = await call('maya.asset.exec_code', {'code': source, 'description': 'PL245 public boundary smoke'})
            return output['result']

        scale_setup = await code(f"""import os, uuid
from maya import cmds
assert os.getpid() == {pid}
assert not cmds.file(query=True, sceneName=True), 'Use a dedicated unnamed test scene'
tag = 'pl245scale_' + uuid.uuid4().hex[:10]
scale_nodes = [cmds.createNode('joint', name=tag + suffix) for suffix in ('_a', '_b', '_protected')]
for n in scale_nodes: cmds.setAttr(n + '.scale', 2, 3, 4, type='double3')
result = {{'nodes': scale_nodes}}
""")
        scale_nodes = scale_setup['nodes']
        try:
            preview = await call(normalize_id, {'nodes': scale_nodes[:2]})
            assert preview['dry_run'] and preview['changed_count'] == 2
            await code(f"""from maya import cmds
assert all(cmds.getAttr(n+'.scale')[0] == (2,3,4) for n in {scale_nodes!r})
result={{'preview_no_write':True}}
""")
            applied = await call(normalize_id, {'nodes': scale_nodes[:2], 'dry_run': False})
            assert applied['changes'] == preview['changes']
            await code(f"""from maya import cmds
assert all(cmds.getAttr(n+'.scale')[0] == (1,1,1) for n in {scale_nodes[:2]!r})
assert cmds.getAttr({scale_nodes[2]!r}+'.scale')[0] == (2,3,4)
cmds.undo()
assert all(cmds.getAttr(n+'.scale')[0] == (2,3,4) for n in {scale_nodes!r})
result={{'bounded_apply_and_undo':True}}
""")
            for values in ({'nodes': []}, {'nodes': [scale_nodes[0], 'pl245_missing']}, {'nodes': ['*']}):
                await call(normalize_id, dict(values, dry_run=False), expected='ERROR')
            reused = (await client.call_tool('list_apis', {'executor':'maya', 'stages':['rigging'], 'search':normalize_id})).data
            assert reused['total'] == 1
            await call(normalize_id, {'nodes': scale_nodes[:1], 'dry_run':False})
            await code(f"""import importlib
from maya import cmds
from cgi_pipeline import api, catalog
catalog.reload()
assert cmds.getAttr({scale_nodes[0]!r}+'.scale')[0] == (1,1,1)
cmds.undo()
local = api.execute_local({normalize_id!r}, {{'nodes':{scale_nodes[:2]!r}}}, host='maya')
assert local['status']=='SUCCESS' and local['output']['changed_count']==2
core=importlib.import_module('DCC.maya.rig_tools.skeleton.zero_joint_scales')
assert core.normalize({scale_nodes[:2]!r})==local['output']
from DCC.maya.ui.shelf import dispatch_launcher
cmds.select({scale_nodes[:2]!r}, replace=True)
dispatch_launcher('zero_joint_scales', 'skeleton')
assert all(cmds.getAttr(n+'.scale')[0] == (1,1,1) for n in {scale_nodes[:2]!r})
cmds.undo()
assert all(cmds.getAttr(n+'.scale')[0] == (2,3,4) for n in {scale_nodes!r})
result={{'local_matches_core':True,'shelf_dispatch_undo':True,'second_use':True}}
""")
        finally:
            await code(f"""from maya import cmds
remaining=[n for n in {scale_nodes!r} if cmds.objExists(n)]
if remaining: cmds.delete(remaining)
result={{'remaining':[n for n in {scale_nodes!r} if cmds.objExists(n)]}}
""")

        setup = await code(f"""import os, uuid
from maya import cmds
assert os.getpid() == {pid}
assert not cmds.file(query=True, sceneName=True), 'Use a dedicated unnamed test scene'
from DCC.maya.anim_tools.retarget.test_retarget import mapping
tag = 'pl245remote_' + uuid.uuid4().hex[:10]
nodes = [cmds.createNode('transform', name=tag + suffix) for suffix in ('_source', '_target', '_delete', '_protected')]
for f,v in ((1,10),(2,25)): cmds.setKeyframe(nodes[0], attribute='rotateY', time=f, value=v)
result = {{'mapping':mapping(nodes[0], nodes[1]), 'nodes':nodes}}
""")
        nodes = setup['nodes']
        try:
            values = {'mapping': setup['mapping'], 'frames': [1, 2]}
            preview = await call('maya.animation.retarget.transfer', values)
            assert preview['dry_run'] is True and preview['report']['key_count'] == 6
            applied = await call('maya.animation.retarget.transfer', dict(values, dry_run=False))
            assert applied['dry_run'] is False and applied['report']['key_count'] == 6
            checked = await code(f"""from maya import cmds
assert abs(cmds.getAttr({nodes[1] + '.rotateZ'!r},time=2)+25)<1e-6
cmds.undo()
assert not cmds.keyframe({nodes[1]!r},q=True,keyframeCount=True)
result={{'undo':True}}
""")
            await call('maya.scene.nodes.delete', {'nodes': [nodes[2]]})
            await code(f"""from maya import cmds
assert not cmds.objExists({nodes[2]!r}) and cmds.objExists({nodes[3]!r})
cmds.undo()
assert cmds.objExists({nodes[2]!r})
result={{'delete_undo':True}}
""")
            await call('maya.animation.retarget.transfer', dict(values, mapping={}), expected='ERROR')
        finally:
            await code(f"""from maya import cmds
remaining=[n for n in {nodes!r} if cmds.objExists(n)]
if remaining: cmds.delete(remaining)
result={{'remaining':[n for n in {nodes!r} if cmds.objExists(n)]}}
""")
    return {'status': 'PASS', 'catalog_version': version, 'receipts': receipts,
            'checks': ['discovery', 'default_preview', 'remote_apply', 'readback', 'undo', 'delete_undo',
                       'normalize_discovery', 'normalize_preview_apply_undo', 'normalize_invalid_targets',
                       'normalize_second_use', 'normalize_local_core_shelf_match', 'invalid_mapping', 'cleanup']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--readonly-economy', action='store_true')
    mode.add_argument('--cold-ui', action='store_true')
    args = parser.parse_args()
    operation = run_cold_ui(args.port, args.pid, args.report.resolve().parent) if args.cold_ui else (
        run_economy if args.readonly_economy else run)(args.port, args.pid)
    result = asyncio.run(operation)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
