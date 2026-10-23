import pytest
from cgi_pipeline.capabilities.maya.rig.controller_tags import core, discovery


class Scene:
    def ls(self, name=None, **kw):
        if kw.get('uuid'): return ['uid:' + name]
        if kw.get('dag'): return ['|rig|free|shape']
        if isinstance(name, list): return name
        return [name] if name else []
    def nodeType(self, name):
        if name in ('Controls', 'nested'): return 'objectSet'
        if name == 'wire1': return 'wire'
        if name.endswith('|shape'): return 'nurbsCurve'
        return 'transform'
    def sets(self, name, **kw):
        return ['nested', '|rig|offset'] if name == 'Controls' else ['|rig|hand', '|rig|guide', 'Controls']
    def listRelatives(self, name, **kw):
        if kw.get('parent'): return ['|rig|free']
        return [] if name.endswith('|offset') else [name + '|shape']
    def listAttr(self, *args, **kw): return ['translateX']
    def getAttr(self, *args, **kw): return True
    def listConnections(self, name, **kw):
        return ['wire1'] if name == '|rig|guide|shape.worldSpace' else []
    def controller(self, **kw): return []


def test_nested_sets_do_not_tag_guides_or_offset_groups():
    found = discovery.inspect(Scene(), {'rig_root': '|rig', 'controller_sets': ['Controls']})
    assert [r['path'] for r in found['controllers']] == ['|rig|hand']
    assert {r['reason'] for r in found['skipped']} == {'no_control_shape', 'curve_geometry_used_for_rig_computation'}
    assert found['unclassified_curves'] == ['|rig|free']


def test_explicit_scope_does_not_allow_other_rigs():
    with pytest.raises(ValueError, match='outside rig_root'):
        discovery.inspect(Scene(), {'rig_root': '|other', 'controllers': ['|rig|hand']})


@pytest.mark.parametrize('value', ['*ctrl*', '', 'hand.vtx[0]'])
def test_wildcards_and_components_refused(value):
    with pytest.raises(ValueError): discovery.resolve(Scene(), value)


def test_parameter_types():
    with pytest.raises(ValueError): core.validate({'controllers': 'all'})
    with pytest.raises(ValueError): core.validate({'enable_gpu': 'true'})


def test_host_without_optional_opencl_diagnostic_keeps_native_evaluator_state():
    class Host:
        def evaluationManager(self, **kw): return ['parallel']
        def evaluator(self, **kw): return True if 'name' in kw else ['deformer']
        def optionVar(self, **kw): return 1
        def getPanel(self, **kw): return []
    state = core.host_state(Host())
    assert state['evaluation_mode'] == 'parallel'
    assert state['gpu_override_enabled'] is True
    assert state['opencl_valid'] is None


def test_unregistered_gpu_evaluator_is_not_reported_enabled():
    class Host:
        def evaluationManager(self, **kw): return ['off']
        def evaluator(self, **kw):
            assert 'name' not in kw
            return ['invisibility']
        def optionVar(self, **kw): return 0
        def getPanel(self, **kw): return []
    assert core.host_state(Host())['gpu_override_enabled'] is False


def test_repeated_prepare_creates_no_duplicate_tags(monkeypatch):
    state = {'tagged': False, 'created': 0}
    class Host:
        def referenceQuery(self, *a, **k): return False
        def ls(self, **k): return []
        def undoInfo(self, **k): return 'ControllerTags'
        def controller(self, node): state.update(tagged=True, created=state['created']+1)
        def listConnections(self, *a, **k): return ['hand_tag']
        def getAttr(self, plug): return 0 if plug.endswith('visibilityMode') else True
        def select(self, *a, **k): pass
    monkeypatch.setattr(discovery, 'inspect', lambda *a: {'controllers': [
        {'path': '|rig|hand', 'already_tagged': state['tagged']}], 'skipped': []})
    monkeypatch.setattr(core, 'host_state', lambda c: {'panels': []})
    params = {'mode': 'apply', 'enable_gpu': False, 'show_controllers': False}
    first = core.apply(Host(), params); second = core.apply(Host(), params)
    assert first['new_tag_count'] == 1 and second['new_tag_count'] == 0
    assert state['created'] == 1


def test_failed_tag_edit_undoes_nodes_and_restores_host(monkeypatch):
    calls = []
    class Host:
        def pluginInfo(self, *a, **k): return True
        def referenceQuery(self, *a, **k): return False
        def ls(self, **k): return []
        def undoInfo(self, **k): return 'ControllerTags'
        def controller(self, node): calls.append('tag')
        def listConnections(self, *a, **k): return []
        def undo(self): calls.append('undo')
        def select(self, *a, **k): pass
    monkeypatch.setattr(discovery, 'inspect', lambda *a: {'controllers': [
        {'path': '|rig|hand', 'already_tagged': False}]})
    monkeypatch.setattr(core, 'host_state', lambda c: {'old': True})
    monkeypatch.setattr(core, 'restore_host', lambda *a: calls.append('restore_host'))
    with pytest.raises(RuntimeError, match='Expected one controller tag'):
        core.apply(Host(), {'mode': 'apply'})
    assert calls == ['tag', 'undo', 'restore_host']
