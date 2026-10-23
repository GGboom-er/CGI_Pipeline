"""Instance enrichment is opt-in and reuses the shared native identity owner."""

import sys
import types
from concurrent.futures import ThreadPoolExecutor

from cgi_pipeline.capabilities import maya as maya_capabilities
from cgi_pipeline.capabilities.maya import scene_p2
from cgi_pipeline.capabilities.maya.scene_observation import require_main_thread
from test_maya_scene_p2 import FakeCmds, _context


def test_resolve_instance_enrichment_preserves_explicit_path_and_session(monkeypatch):
    fake = FakeCmds()
    calls = []
    identity = {'path': '|root', 'uuid': 'uuid:|root', 'type': 'transform',
                'instance_number': 0, 'is_instanced': True,
                'all_instance_paths': ['|root', '|instance|root']}
    session = {'pid': 1234, 'process_created_at': 10.5, 'scene_epoch': 2}

    def native_identity(cmds, path):
        assert cmds is fake
        calls.append(path)
        return identity

    helper = types.SimpleNamespace(node_identity=native_identity,
                                   session_identity=lambda cmds: session,
                                   require_main_thread=require_main_thread)
    monkeypatch.setitem(sys.modules, 'cgi_pipeline.capabilities.maya.scene_observation', helper)
    monkeypatch.setattr(maya_capabilities, 'scene_observation', helper, raising=False)
    result = scene_p2.resolve_nodes({'nodes': ['|root'], 'include_instances': True},
                                    _context(fake, 'maya.scene.nodes.resolve'))
    assert result['status'] == 'SUCCESS', result
    row = result['output']['items'][0]
    assert row['path'] == '|root' and row['uuid'] == identity['uuid']
    assert row['identity'] == identity and calls == ['|root']
    assert result['output']['session'] == session


def test_resolve_rejects_ambiguous_name_before_instance_enrichment():
    fake = FakeCmds()
    result = scene_p2.resolve_nodes({'nodes': ['root'], 'include_instances': True},
                                    _context(fake, 'maya.scene.nodes.resolve'))
    assert result['status'] == 'ERROR'
    assert 'ambiguous' in result['error']


def test_resolve_instance_flag_is_a_boolean():
    fake = FakeCmds()
    result = scene_p2.resolve_nodes({'nodes': ['|root'], 'include_instances': 'yes'},
                                    _context(fake, 'maya.scene.nodes.resolve'))
    assert result['status'] == 'ERROR' and 'boolean' in result['error']


def test_instance_resolve_off_main_thread_touches_no_maya():
    queries = []

    class NoMayaTouch:
        def ls(self, *args, **kwargs):
            queries.append((args, kwargs))
            raise AssertionError('Maya must not be queried off the main thread')

    context = _context(NoMayaTouch(), 'maya.scene.nodes.resolve')
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(scene_p2.resolve_nodes,
                             {'nodes': ['|root'], 'include_instances': True}, context).result()
    assert result['status'] == 'ERROR' and 'main thread' in result['error']
    assert queries == []
