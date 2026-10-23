"""History cleanup must not delete geometry shared by another consumer."""
import pytest

from test_maya_normal_writers import entry, writers


def history_scene(sync, monkeypatch, *, shared=None, fail_delete=False, external_plug=None):
    orig = '|asset|origShape'
    deleted = []
    outputs = {
        'polyA': ['polyA.output', orig + '.inMesh'],
        '|inputShape': ['|inputShape.outMesh', 'polyA.inputPolymesh',
                        '|inputShape.instObjGroups[0]', 'initialShadingGroup.dagSetMembers[0]'],
    }
    if shared:
        outputs[shared] += [shared + ('.output' if shared == 'polyA' else '.outMesh'),
                            '|other|origShape.inMesh']
    if external_plug:
        outputs['|inputShape'] += ['|inputShape.'+external_plug, 'rigDriver.input']

    def connections(node, **kwargs):
        if node == orig + '.inMesh':
            return [] if 'polyA' in deleted else ['polyA.output']
        if kwargs.get('connections'):
            return outputs.get(node, [])
        return []

    old_get_attr = sync.cmds.getAttr

    def get_attr(plug, **kwargs):
        if kwargs.get('type'):
            if 'instObjGroups' in plug:
                return 'compound'
            if external_plug and plug.endswith('.'+external_plug):
                return None if external_plug == 'unknown' else 'matrix'
            return 'mesh'
        return old_get_attr(plug, **kwargs)

    def delete(node):
        if fail_delete:
            raise RuntimeError('native delete denied')
        deleted.append(node)

    for name, value in {
        'listConnections': connections, 'listHistory': lambda *_a: ['polyA', '|inputShape'],
        'ls': lambda node, **_k: [node],
        'nodeType': lambda node: {'polyA':'polySoftEdge', 'initialShadingGroup':'shadingEngine'}.get(node, 'mesh'),
        'objExists': lambda node: node not in deleted, 'getAttr': get_attr, 'delete': delete,
    }.items():
        monkeypatch.setattr(sync.cmds, name, value, raising=False)
    return orig, deleted


@pytest.mark.parametrize('shared', ['polyA', '|inputShape'])
def test_shared_geometry_is_rejected_before_any_history_deletion(writers, monkeypatch, shared):
    _build, sync, state = writers
    cleanup = state.clean_upstream
    orig, deleted = history_scene(sync, monkeypatch, shared=shared)
    with pytest.raises(ValueError, match='shared'):
        cleanup(orig)
    assert deleted == []


def test_shared_history_is_rejected_before_direct_injection_mutates_geometry(writers, monkeypatch):
    _build, sync, state = writers
    orig, deleted = history_scene(sync, monkeypatch, shared='polyA')
    with pytest.raises(ValueError, match='shared'):
        sync._inject_mesh_data_via_plug(orig, entry())
    assert deleted == []
    assert state.events == []


def test_exclusive_geometry_can_be_cleaned_without_treating_shading_as_geometry(writers, monkeypatch):
    _build, sync, state = writers
    cleanup = state.clean_upstream
    orig, deleted = history_scene(sync, monkeypatch)
    cleanup(orig)
    assert deleted == ['polyA', '|inputShape']


def test_native_delete_failure_is_not_silenced(writers, monkeypatch):
    _build, sync, state = writers
    cleanup = state.clean_upstream
    orig, deleted = history_scene(sync, monkeypatch, fail_delete=True)
    with pytest.raises(RuntimeError, match='native delete denied'):
        cleanup(orig)
    assert deleted == []


@pytest.mark.parametrize('external_plug', ['worldMatrix[0]', 'unknown'])
def test_external_non_mesh_dependency_or_unknown_type_is_protected(writers, monkeypatch, external_plug):
    _build, sync, state = writers
    orig, deleted = history_scene(sync, monkeypatch, external_plug=external_plug)
    with pytest.raises(ValueError, match='shared'):
        state.clean_upstream(orig)
    assert deleted == []


def test_global_preflight_rejects_shared_history_before_mutation(writers, monkeypatch):
    import importlib
    _build, sync, state = writers
    orig, deleted = history_scene(sync, monkeypatch, shared='polyA')
    collector = importlib.import_module('cgi_pipeline.hosts.maya.asset_info_collector')
    monkeypatch.setattr(collector, 'resolve_cache_group', lambda _group: '|asset')
    monkeypatch.setattr(collector, 'get_deform_input', lambda _tr: ('|asset|visibleShape', orig))
    monkeypatch.setattr(sync, '_collect_rig_scene_info', lambda _g: {'meshes': {'|asset|visibleShape': entry()}})
    monkeypatch.setattr(sync.cmds, 'listRelatives', lambda *_a, **_k: ['|asset'])
    report = {'target_only_dags': [], 'pairing_groups': [{'group_id':'body', 'action':'ORIG_INJECT',
              'abc_dags':['|sourceShape'], 'rig_dags':['|asset|visibleShape']}]}
    with pytest.raises(ValueError, match='shared'):
        sync._preflight_sync_geometry({'meshes': {'|sourceShape': entry()}}, report, 'asset', 'RIG_')
    assert deleted == []
    assert state.events == []


def test_instanced_target_without_history_is_rejected(writers, monkeypatch):
    _build, sync, state = writers
    monkeypatch.setattr(sync.cmds, 'listRelatives', lambda *_a, **_kw: ['|parentA','|parentB'])
    with pytest.raises(ValueError, match='instanced'):
        sync._inject_mesh_data_via_plug('|parentA|shape', entry())
    assert state.events == []
