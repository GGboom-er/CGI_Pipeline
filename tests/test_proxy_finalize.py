"""Candidate finalization must respect identity and live rig consumers."""
import pytest
from cgi_pipeline.capabilities.maya.asset.proxy_rig import finalize


class Cmds:
    def file(self, **kw): return '/managed/candidate.ma'
    def ls(self, name, **kw): return [name]
    def listRelatives(self, name, **kw):
        if kw.get('parent'): return ['|grp|cgi_proxy_originals']
        if kw.get('shapes'): return [name + '|shape']
        if kw.get('type') == 'transform': return []
        return ['|grp|cgi_proxy_originals|a', '|grp|cgi_proxy_originals|b']
    def referenceQuery(self, *args, **kw): return False
    def listHistory(self, *args, **kw): return ['skin']
    def listConnections(self, *args, **kw): return []
    def nodeType(self, node, **kw): return ['node', 'geometryFilter', 'skinCluster'] if kw.get('inherited') else 'skinCluster'


def setup(monkeypatch):
    paths = {key: '|grp|cgi_proxy_originals|' + key for key in ('a', 'b')}
    paths['final'] = '|grp|final'
    monkeypatch.setattr(finalize.safety, 'resolve', lambda c, uid: paths[uid])
    monkeypatch.setattr(finalize.safety, 'managed', lambda *a, **kw: None)
    monkeypatch.setattr(finalize.outside, 'script_sources', lambda c: [])
    monkeypatch.setattr(finalize.outside, 'name_users', lambda *a: [])
    monkeypatch.setattr(finalize.deletion_scope, 'inspect', lambda c, n, *a:
                        {'external_consumers': ['wrap1'] if n.endswith('|b') else []})
    build = {'output_path': '/managed/candidate.ma', 'retained_sources':
             [{'uuid': k, 'path': paths[k]} for k in ('a', 'b')],
             'outputs': [{'result_uuid': 'final'}], 'kept': [], 'protected': []}
    return build


def test_live_original_is_binding_input_not_deleted(monkeypatch):
    result = finalize.inspect(Cmds(), setup(monkeypatch))
    assert [r['uuid'] for r in result['delete']] == ['a']
    assert [r['uuid'] for r in result['binding_inputs']] == ['b']


def test_unrecorded_backup_child_refuses_whole_cleanup(monkeypatch):
    build = setup(monkeypatch); build['retained_sources'].pop()
    with pytest.raises(ValueError, match='Unrecorded children'):
        finalize.inspect(Cmds(), build)


def test_candidate_identity_mismatch_refuses(monkeypatch):
    build = setup(monkeypatch); build['output_path'] = '/managed/other.ma'
    with pytest.raises(ValueError, match='actual recorded candidate'):
        finalize.inspect(Cmds(), build)


def test_source_history_cannot_cascade_into_live_proxy_driver():
    class Host:
        def nodeType(self, node, **kw):
            return ['node'] if kw.get('inherited') else 'combinationShape' if node == 'combo' else 'blendShape'
        def listConnections(self, node, **kw): return ['proxyExpression']
    assert finalize._history_consumers(Host(), 'source', ['sourceShape'], ['combo']) == ['combo -> proxyExpression']


def test_driven_display_values_can_change_but_connections_cannot():
    from cgi_pipeline.capabilities.maya.asset.proxy_rig.verify_delivery import display_matches, target_driver_matches
    old = {'value': 2, 'inputs': ['vis.displayType']}
    assert display_matches({'value': 0, 'inputs': ['vis.displayType']}, old)
    assert not display_matches({'value': 2, 'inputs': []}, old)
    assert not display_matches({'value': 2, 'inputs': ['wrong.out']}, old)
    assert not display_matches({'value': 0, 'inputs': []}, {'value': 2, 'inputs': []})
    assert target_driver_matches({'driver': [], 'alias': 'smile', 'value': 1e-16}, {'driver': [], 'alias': 'smile', 'value': 0.})
    assert not target_driver_matches({'driver': [], 'alias': 'smile', 'value': 0.}, {'driver': ['combo.out'], 'alias': 'smile', 'value': 0.})


def test_connection_guard_preserves_pairs_and_uses_uuid_across_reparent(monkeypatch):
    class Host:
        parent = 'old'
        swapped = False
        def listHistory(self, *a, **k): return ['bs']
        def nodeType(self, n, **k): return ['geometryFilter'] if k else 'blendShape'
        def listConnections(self, *a, **k):
            sources = ['a', 'b'][:: -1 if self.swapped else 1]
            return ['bs.weight[0]', self.parent+'|'+sources[0]+'.out', 'bs.weight[1]', self.parent+'|'+sources[1]+'.out']
    monkeypatch.setattr(finalize.safety, 'identity', lambda c, n: n.rsplit('|', 1)[-1]+'ID')
    host = Host(); baseline = finalize._connections(host, ['output'])
    host.parent = 'new'
    assert finalize._connections(host, ['output']) == baseline
    host.swapped = True
    assert finalize._connections(host, ['output']) != baseline
