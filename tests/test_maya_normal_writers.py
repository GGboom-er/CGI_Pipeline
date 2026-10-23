"""Writer boundary/data-flow checks; native Maya behavior is verified separately."""
import copy
import importlib
import sys
import types

import numpy as np
import pytest

from cgi_pipeline.core import abc_reader


class Matrix:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float).reshape(4, 4)

    def __iter__(self):
        return iter(self.values.reshape(-1).tolist())

    def inverse(self):
        return Matrix(np.linalg.inv(self.values))


class Point(tuple):
    def __new__(cls, *values):
        return tuple.__new__(cls, values)

    def __mul__(self, matrix):
        return Point(*(np.append(self[:3], 1.0) @ matrix.values)[:3])


class Mesh:
    def __init__(self, state, kind):
        self.state, self.kind = state, kind
        self.points, self.counts, self.indices = [], [3], [0, 1, 2]
        self.edges = [(0, 1), (1, 2), (2, 0)]
        self.smooth = [True, False, True]
        self.normals = []
        self.uv_sets = ['map1']
        self.uv_values, self.uv_indices = ([0, 1, 0], [0, 0, 1]), [0, 1, 2]
        self.uv_stale = False

    @property
    def numEdges(self):
        return len(self.edges)

    @property
    def numVertices(self):
        return len(self.points) or 3

    def create(self, points, counts, indices, parent=None):
        mesh = self.state.data if parent is not None else self
        mesh.points, mesh.counts, mesh.indices = list(points), list(counts), list(indices)
        mesh.uv_values, mesh.uv_indices = ([], []), []
        mesh.edges = [(2, 0), (0, 1), (1, 2)]  # Edge IDs differ; endpoints remain stable.
        self.state.events.append(('create_data' if parent is not None else 'create_scene',))
        return mesh

    def object(self):
        return self

    def copy(self, source, parent=None):
        if self.state.fail_copy:
            raise RuntimeError('native mesh copy failure')
        assert source is self.state.target and parent is self.state.data
        mesh = self.state.data
        for field in ('points', 'counts', 'indices', 'edges', 'smooth', 'normals', 'uv_sets', 'uv_values', 'uv_indices'):
            setattr(mesh, field, copy.deepcopy(getattr(source, field)))
        self.state.events.append(('copy_data', source, parent))
        return mesh

    def setPoints(self, points, space):
        if self.state.fail_set_points:
            raise RuntimeError('native setPoints failure')
        self.points = list(points)
        self.state.events.append(('set_points', self.kind, list(points), space))

    def getVertices(self):
        return self.counts, self.indices

    def getEdgeVertices(self, index):
        return self.edges[index]

    def isEdgeSmooth(self, index):
        return self.smooth[index]

    def setEdgeSmoothing(self, index, value):
        self.smooth[index] = value
        self.state.events.append(('smoothing', self.edges[index], value))

    def cleanupEdgeSmoothing(self):
        self.state.events.append(('cleanup_smoothing',))

    def getUVSetNames(self):
        return self.uv_sets

    def clearUVs(self, name):
        if self.state.fail_clear_uv == self.kind:
            raise RuntimeError('required UV clear failure')
        self.uv_values, self.uv_indices = ([], []), []
        self.uv_stale = True
        self.state.events.append(('clear_uv', self.kind, name))

    def setUVs(self, u, v, name):
        assert not self.uv_stale, 'stale MFnMesh used after clearUVs'
        if self.state.fail_uv == self.kind:
            raise RuntimeError('required UV failure')
        self.state.events.append(('set_uv', self.kind, list(u), list(v), name))
        self.uv_values = (list(u), list(v))

    def assignUVs(self, counts, indices, name):
        assert not self.uv_stale, 'stale MFnMesh used after clearUVs'
        self.uv_indices = list(indices)
        self.state.events.append(('assign_uv', self.kind, list(counts), list(indices), name))

    def setFaceVertexNormals(self, normals, face_ids, vertex_ids):
        if self.state.fail_normals:
            raise RuntimeError('authored normal failure')
        self.normals = list(normals)
        self.smooth = [False] * self.numEdges  # Side effect confirmed in Maya 2025.3.
        self.state.events.append(('normals', list(normals), list(face_ids), list(vertex_ids)))


@pytest.fixture
def writers(monkeypatch):
    state = types.SimpleNamespace(events=[], fail_normals=False, fail_uv='',
                                  fail_copy=False, fail_set_points=False,
                                  fail_clear_uv='', uv_refresh_after_clear=[],
                                  matrix=Matrix(np.eye(4)), intermediate=True,
                                  tweaks=[(0.0, 0.0, 0.0)] * 3, driven_tweaks=False,
                                  fail_tweak_write=False, ignore_tweak_write=False)
    state.target, state.data = Mesh(state, 'target'), Mesh(state, 'data')

    class Selection:
        def add(self, _shape): pass
        def getDagPath(self, _index): return dag
        def getDependNode(self, _index): return 'target_node'

    dag = types.SimpleNamespace(inclusiveMatrix=lambda: state.matrix)

    class DepNode:
        def __init__(self, _node): pass
        def name(self): return 'mesh'
        def findPlug(self, _name, _want_networked):
            return types.SimpleNamespace(setMObject=lambda data: state.events.append(('plug_write', data)))

    def mesh_fn(value=None):
        if value is dag:
            if state.target.uv_stale:
                state.uv_refresh_after_clear.append('target')
            state.target.uv_stale = False
            return state.target
        if value is state.data:
            if state.data.uv_stale:
                state.uv_refresh_after_clear.append('data')
            state.data.uv_stale = False
            return state.data
        return Mesh(state, 'scene')

    def record(name):
        return lambda *args, **kwargs: state.events.append((name, args, kwargs))

    def get_attr(attribute, **_kwargs):
        if attribute.endswith('.intermediateObject'):
            return state.intermediate
        if '.pnts' in attribute:
            return state.tweaks
        return None

    def set_attr(attribute, *values, **kwargs):
        state.events.append(('clear_pnts', attribute, kwargs))
        if state.fail_tweak_write:
            raise RuntimeError('pnts write denied')
        if not state.ignore_tweak_write:
            state.tweaks = [tuple(values[index:index + 3]) for index in range(0, len(values), 3)]

    def connections(attribute, **_kwargs):
        return ['author.outputX'] if state.driven_tweaks and attribute.endswith('.pnts') else []

    cmds = types.SimpleNamespace(
        ls=lambda node, **_kw: [node], nodeType=lambda _n: 'mesh',
        rename=lambda _node, name: name,
        sets=record('sets'), parent=lambda node, parent, **kw: (
            state.events.append(('parent', node, parent, kw)) or [node]),
        listRelatives=lambda *_a, **_k: [], getAttr=get_attr, setAttr=set_attr,
        listConnections=connections,
        polyUVSet=lambda *_a, **kw: ['map1'] if kw.get('q') else None,
        undoInfo=record('undoInfo'), warning=record('warning'),
    )
    om = types.ModuleType('maya.api.OpenMaya')
    for name, value in dict(MFnMesh=mesh_fn, MSelectionList=Selection, MFnDependencyNode=DepNode,
                            MPoint=Point, MVector=Point, MPointArray=list, MFloatPointArray=list,
                            MVectorArray=list, MIntArray=list, MFloatArray=list,
                            MSpace=types.SimpleNamespace(kObject='object'),
                            MFnMeshData=lambda: types.SimpleNamespace(create=lambda: state.data)).items():
        setattr(om, name, value)
    maya, api = types.ModuleType('maya'), types.ModuleType('maya.api')
    maya.cmds, maya.api, api.OpenMaya = cmds, api, om
    for name, value in {'maya': maya, 'maya.cmds': cmds, 'maya.api': api,
                        'maya.api.OpenMaya': om}.items():
        monkeypatch.setitem(sys.modules, name, value)
    build = importlib.import_module('cgi_pipeline.capabilities.maya.asset.maya_build_mesh_from_abc.maya_build_mesh_from_abc')
    sync = importlib.import_module('cgi_pipeline.capabilities.maya.rig.maya_sync_rig_incremental.maya_sync_rig_incremental')
    for module in (build, sync):
        monkeypatch.setattr(module, 'cmds', cmds)
        monkeypatch.setattr(module, 'om2', om)
    state.clean_upstream = sync._clean_orig_upstream
    monkeypatch.setattr(sync, '_clean_orig_upstream', record('clean_upstream'))
    def regularize_uv(_shape, keep=None):
        state.events.append(('regularize_uv', keep))
        state.target.uv_sets = ['map1']
    monkeypatch.setattr(sync, '_regularize_uvset_to_map1', regularize_uv)
    return build, sync, state


def entry():
    return {'vertices': 3, 'vert_positions': [0, 0, 0, 1, 0, 0, 0, 1, 0],
            'face_counts': [3], 'face_indices': [0, 1, 2],
            'u_array': [0, 1, 0], 'v_array': [0, 0, 1], 'uv_indices': [0, 1, 2],
            'normals_fv': [0, 0, 1] * 3}


def test_create_mesh_validates_before_any_scene_mutation(writers):
    build, _sync, state = writers
    malformed = dict(entry(), normals_fv=[0, 0, 0] * 3)
    with pytest.raises(ValueError):
        build.create_mesh('bad', malformed)
    assert state.events == []


def test_create_mesh_world_data_is_only_absolute_parented(writers):
    build, _sync, state = writers
    node, warnings = build.create_mesh('mesh', entry(), '|transformed_parent')
    assert node == '|transformed_parent|mesh'
    assert warnings == []
    assert next(event for event in state.events if event[0] == 'parent')[3] == {'absolute': True}
    assert next(event for event in state.events if event[0] == 'normals')[1] == [(0, 0, 1)] * 3


@pytest.mark.parametrize('failure', ['normals', 'uv'])
def test_create_mesh_required_writer_errors_propagate(writers, failure):
    build, _sync, state = writers
    state.fail_normals, state.fail_uv = failure == 'normals', 'scene' if failure == 'uv' else ''
    with pytest.raises(RuntimeError):
        build.create_mesh('mesh', entry())
    assert not any(event[0] == 'warning' for event in state.events)


def test_build_checks_all_meshes_before_hierarchy_changes(writers, monkeypatch, tmp_path):
    build, _sync, state = writers
    source = tmp_path / 'source.abc'
    source.write_bytes(b'fixture')
    source_info = {'meshes': {'|validShape': entry(), '|badShape': dict(entry(), uv_indices=[10] * 3)}}
    monkeypatch.setattr(abc_reader, 'read_abc_as_info', lambda _path: source_info)
    result = build.execute({'parameters': {'abc_path': str(source)}})
    assert result['status'] == 'ERROR'
    assert state.events == []


def test_injection_world_to_local_points_normals_and_persistent_uv(writers):
    _build, sync, state = writers
    state.matrix = Matrix([[2, 0, 0, 0], [0, 0, 3, 0], [0, -0.5, 0, 0], [9, 4, -2, 1]])
    data = entry()
    points = np.array(data['vert_positions']).reshape(-1, 3)
    data['vert_positions'] = (np.column_stack([points, np.ones(3)]) @ state.matrix.values)[:, :3].reshape(-1).tolist()
    data['normals_fv'] = abc_reader.transform_normals_fv(data['normals_fv'], state.matrix.values.tolist(), 3)
    details = {}
    assert sync._inject_mesh_data_via_plug('|mesh|orig', data, details=details) is True
    assert details['smoothing_policy'] == 'authored_normals'
    np.testing.assert_allclose(state.data.points, points, atol=1e-12)
    np.testing.assert_allclose(state.data.normals, [[0, 0, 1]] * 3, atol=1e-12)
    names = [event[0] for event in state.events]
    assert names.index('normals') < names.index('clean_upstream') < names.index('plug_write')
    assert [event[1] for event in state.events if event[0] == 'assign_uv'] == ['data', 'target']
    assert not any(event[0] == 'smoothing' for event in state.events)
    assert not any(event[0] in ('copy_data', 'set_points') for event in state.events)
    assert details['copied_target_mesh_data'] is False


@pytest.mark.parametrize('matrix', [np.diag([1, 1, 0, 1]), np.diag([-1, 1, 1, 1])])
def test_injection_rejects_bad_target_matrix_even_without_normals(writers, matrix):
    _build, sync, state = writers
    state.matrix = Matrix(matrix)
    data = entry()
    data.pop('normals_fv')
    with pytest.raises(ValueError):
        sync._inject_mesh_data_via_plug('|mesh|orig', data)
    assert state.events == []


def test_injection_normal_failure_occurs_before_orig_cleanup(writers):
    _build, sync, state = writers
    state.fail_normals = True
    with pytest.raises(RuntimeError, match='authored normal'):
        sync._inject_mesh_data_via_plug('|mesh|orig', entry())
    assert not any(event[0] in ('clean_upstream', 'regularize_uv', 'plug_write') for event in state.events)


@pytest.mark.parametrize('missing_normals', [False, True])
def test_injection_persistent_uv_failure_is_not_success(writers, missing_normals):
    _build, sync, state = writers
    state.fail_uv = 'target'
    data = entry()
    if missing_normals:
        data.pop('normals_fv')
    with pytest.raises(RuntimeError, match='required UV'):
        sync._inject_mesh_data_via_plug('|mesh|orig', data)
    assert any(event[0] == 'plug_write' for event in state.events)


@pytest.mark.parametrize('authored', [True, False])
@pytest.mark.parametrize('uv_case', ['grow', 'shrink', 'none'])
def test_noncopy_injection_replaces_persistent_uv_storage_with_fresh_fn(writers, authored, uv_case):
    _build, sync, state = writers
    data = entry()
    if not authored:
        data.update(normals_fv=[], face_indices=[0, 2, 1])  # Same vertex count, different topology: no copy.
    state.target.uv_values = ([0., .2, .4, .6, .8], [0., .1, .3, .7, 1.])
    state.target.uv_indices = [0, 3, 4]
    if uv_case == 'grow':
        data.update(u_array=[0., .2, .4, .6, 1., 0.], v_array=[0., .1, .3, .8, 0., 1.], uv_indices=[0, 4, 5])
    elif uv_case == 'none':
        data.update(u_array=[], v_array=[], uv_indices=[])
    # Shrink keeps the entry's valid three-UV triangle, replacing five old UVs.
    assert len(data['u_array']) == {'grow': 6, 'shrink': 3, 'none': 0}[uv_case]
    details = {}
    assert sync._inject_mesh_data_via_plug('|mesh|orig', data, details) is True
    assert details['copied_target_mesh_data'] is False
    assert details['smoothing_policy'] == ('authored_normals' if authored else 'generated_normals')
    assert [e[1:] for e in state.events if e[0] == 'clear_uv'] == [('target', 'map1')]
    assert state.uv_refresh_after_clear == ['target']
    assert state.target.uv_values == state.data.uv_values == (data['u_array'], data['v_array'])
    assert state.target.uv_indices == state.data.uv_indices == data['uv_indices']
    names = [e[0] for e in state.events]
    assert names.index('plug_write') < names.index('clear_uv')
    assert not any(name in ('copy_data', 'smoothing', 'cleanup_smoothing') for name in names)
    assert names.count('normals') == int(authored)
    if uv_case == 'none':
        assert not any(name in ('set_uv', 'assign_uv') for name in names)
    else:
        target_write = next(i for i, event in enumerate(state.events) if event[0:2] == ('set_uv', 'target'))
        assert names.index('clear_uv') < target_write


@pytest.mark.parametrize('authored', [True, False])
@pytest.mark.parametrize('has_uv', [True, False])
def test_noncopy_persistent_uv_clear_failure_propagates_even_without_source_uv(writers, authored, has_uv):
    _build, sync, state = writers
    data = entry()
    if not authored:
        data.update(normals_fv=[], face_indices=[0, 2, 1])
    if not has_uv:
        data.update(u_array=[], v_array=[], uv_indices=[])
    state.fail_clear_uv = 'target'
    details = {}
    with pytest.raises(RuntimeError, match='required UV clear'):
        sync._inject_mesh_data_via_plug('|mesh|orig', data, details)
    assert any(event[0] == 'plug_write' for event in state.events)
    assert not any(event[0:2] == ('assign_uv', 'target') or event[0] == 'warning' for event in state.events)
    assert details == {}


@pytest.mark.parametrize('mismatch', ['face_indices', 'face_counts', 'vertex_count'])
def test_topology_change_does_not_claim_target_smoothing_preservation(writers, mismatch):
    _build, sync, state = writers
    data = entry()
    data.pop('normals_fv')
    if mismatch == 'face_indices':
        data['face_indices'] = [0, 2, 1]
    elif mismatch == 'face_counts':
        data.update(vertices=6, vert_positions=[0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, -1, 1, 0, -1, 0, 0],
                    face_counts=[3, 3], face_indices=[0, 1, 2, 3, 4, 5],
                    u_array=[], v_array=[], uv_indices=[])
        state.target.points = [(0, 0, 0)] * 6
        state.target.counts, state.target.indices = [6], list(range(6))
    else:
        state.target.points = [(0, 0, 0)] * 4  # Old target has an unused vertex.
    details = {}
    sync._inject_mesh_data_via_plug('|mesh|orig', data, details=details)
    assert details['smoothing_policy'] == 'generated_normals'
    assert details['copied_target_mesh_data'] is False
    assert any(event[0] == 'create_data' for event in state.events)
    assert not any(event[0] in ('copy_data', 'set_points') for event in state.events)
    assert not any(event[0] == 'smoothing' for event in state.events)


def test_missing_normals_copy_actual_target_data_before_cleanup_and_update_local_points_uv(writers):
    _build, sync, state = writers
    data = entry()
    data.pop('normals_fv')
    state.matrix = Matrix([[2, 0, 0, 0], [0, 0, 3, 0], [0, -.5, 0, 0], [9, 4, -2, 1]])
    local_points = np.asarray(data['vert_positions']).reshape(-1, 3)
    data['vert_positions'] = (np.column_stack([local_points, np.ones(3)]) @ state.matrix.values)[:, :3].reshape(-1).tolist()
    data['u_array'], data['v_array'] = [.2, .8, .6], [.1, .9, .3]
    old_points, old_smoothing = list(state.target.points), list(state.target.smooth)
    details = {}
    sync._inject_mesh_data_via_plug('|mesh|orig', data, details=details)
    assert details['smoothing_policy'] == 'target_smoothing'
    assert details['copied_target_mesh_data'] is True
    assert state.target.points == old_points and state.data.smooth == old_smoothing
    np.testing.assert_allclose(state.data.points, local_points, atol=1e-12)
    names = [event[0] for event in state.events]
    assert names.index('copy_data') < names.index('set_points') < names.index('set_uv') < names.index('clean_upstream')
    assert names.index('clean_upstream') < names.index('plug_write') < names.index('clear_pnts')
    assert not any(name in ('create_data', 'normals', 'smoothing', 'cleanup_smoothing') for name in names)
    uv_writes = [event for event in state.events if event[0] == 'set_uv']
    assert [event[1] for event in uv_writes] == ['data', 'target']
    assert all(event[2:4] == (data['u_array'], data['v_array']) for event in uv_writes)


@pytest.mark.parametrize('failure', ['copy', 'set_points', 'uv', 'clear_uv'])
def test_missing_normal_data_preparation_failure_propagates_before_orig_cleanup(writers, failure):
    _build, sync, state = writers
    data = entry()
    data['normals_fv'] = []
    state.fail_copy, state.fail_set_points = failure == 'copy', failure == 'set_points'
    state.fail_uv = 'data' if failure == 'uv' else ''
    state.fail_clear_uv = 'data' if failure == 'clear_uv' else ''
    with pytest.raises(RuntimeError, match={'copy': 'mesh copy', 'set_points': 'setPoints', 'uv': 'required UV', 'clear_uv': 'UV clear'}[failure]):
        sync._inject_mesh_data_via_plug('|mesh|orig', data)
    assert not any(event[0] in ('clean_upstream', 'regularize_uv', 'plug_write', 'clear_pnts') for event in state.events)


def test_missing_normal_copy_rejects_no_uv_set_before_any_mutation(writers):
    _build, sync, state = writers
    state.target.uv_sets = []
    with pytest.raises(ValueError, match='at least one target UV set'):
        sync._inject_mesh_data_via_plug('|mesh|orig', dict(entry(), normals_fv=[]))
    assert state.events == []


@pytest.mark.parametrize('uv_sets', [['legacy'], ['map1', 'extra'], ['legacy', 'extra'], ['legacy', 'map1']])
@pytest.mark.parametrize('source_uv', [False, True])
def test_copy_preserves_old_uv_names_until_persistent_write_then_normalizes(writers, uv_sets, source_uv):
    _build, sync, state = writers
    state.target.uv_sets = uv_sets
    data = dict(entry(), normals_fv=[])
    if not source_uv:
        data.update(u_array=[], v_array=[], uv_indices=[])
    sync._inject_mesh_data_via_plug('|mesh|orig', data)
    chosen = uv_sets[0]
    for kind in ('data', 'target'):
        assert [e[2] for e in state.events if e[0] == 'clear_uv' and e[1] == kind] == uv_sets
    writes = [e for e in state.events if e[0] == 'set_uv']
    assert [e[4] for e in writes] == ([chosen, chosen] if source_uv else [])
    names = [e[0] for e in state.events]
    assert names[-1] == 'regularize_uv'
    assert names.index('plug_write') < names.index('regularize_uv')
    assert state.events[-1] == ('regularize_uv', chosen)
    assert state.target.uv_sets == ['map1']


@pytest.mark.parametrize('authored', [False, True])
def test_noncanonical_uv_does_not_block_branches_that_do_not_copy(writers, authored):
    _build, sync, state = writers
    state.target.uv_sets = ['legacy']
    data = entry()
    if not authored:
        data.update(normals_fv=[], face_indices=[0, 2, 1])
    details = {}
    sync._inject_mesh_data_via_plug('|mesh|orig', data, details=details)
    assert details['copied_target_mesh_data'] is False
    assert not any(event[0] == 'copy_data' for event in state.events)
    assert [event[1:] for event in state.events if event[0] == 'clear_uv'] == [('target', 'map1')]


def test_copy_clears_data_and_persistent_uv_even_when_source_has_none(writers):
    _build, sync, state = writers
    data = dict(entry(), normals_fv=[], u_array=[], v_array=[], uv_indices=[])
    sync._inject_mesh_data_via_plug('|mesh|orig', data)
    assert [event[1] for event in state.events if event[0] == 'clear_uv'] == ['data', 'target']
    assert not any(event[0] in ('set_uv', 'assign_uv') for event in state.events)
    assert state.data.uv_values == state.target.uv_values == ([], [])
    assert state.data.uv_indices == state.target.uv_indices == []


def test_copy_persistent_uv_clear_failure_cannot_succeed(writers):
    _build, sync, state = writers
    state.fail_clear_uv = 'target'
    with pytest.raises(RuntimeError, match='UV clear'):
        sync._inject_mesh_data_via_plug('|mesh|orig', dict(entry(), normals_fv=[]))
    assert any(event[0] == 'plug_write' for event in state.events)


def test_sync_checks_valid_then_unsupported_target_before_first_scene_mutation(writers, monkeypatch, tmp_path):
    _build, sync, state = writers
    collector = importlib.import_module('cgi_pipeline.hosts.maya.asset_info_collector')
    source = tmp_path / 'source.abc'
    source.write_bytes(b'fixture')
    entries = {f'|{name}SourceShape': dict(entry(), normals_fv=[]) for name in ('first', 'second')}
    targets = {f'|geo|{name}Shape': f'|geo|{name}' for name in ('first', 'second')}
    shapes = {parent: (visible, visible + 'Orig') for visible, parent in targets.items()}
    meshes = {orig: Mesh(state, name) for name, (_visible, orig) in shapes.items()}
    meshes[shapes['|geo|second'][1]].uv_sets = []
    seen = []
    def local_space(shape, *_args):
        seen.append(shape)
        return meshes[shape], None, []
    monkeypatch.setattr(abc_reader, 'read_abc_as_info', lambda _path: {'meshes': entries})
    monkeypatch.setattr(collector, 'resolve_cache_group', lambda _group: '|geo')
    monkeypatch.setattr(collector, 'get_deform_input', lambda parent: shapes[parent])
    monkeypatch.setattr(sync, '_collect_rig_scene_info', lambda _group: {'meshes': {key: entry() for key in targets}})
    monkeypatch.setattr(sync.cmds, 'listRelatives', lambda target, **kw: (
        [target.rsplit('|', 1)[0]] if kw.get('allParents') else [targets[target]]))
    monkeypatch.setattr(sync, '_injection_local_space', local_space)
    monkeypatch.setattr(sync.om2, 'MFnMesh', lambda mesh: mesh)
    report = {'target_only_dags': [], 'pairing_groups': [
        {'group_id': name, 'action': 'ORIG_INJECT', 'abc_dags': [f'|{name}SourceShape'],
         'rig_dags': [f'|geo|{name}Shape']} for name in ('first', 'second')]}
    result = sync.execute({'source_path': 'original_rig.ma', 'parameters': {'source_abc': str(source),
        'compare_result': {'schema_version': 'compare_result.v1', 'compare': report}}})
    assert result['status'] == 'ERROR' and '[geometry_preflight]' in result['error']
    assert 'at least one target UV set' in result['error']
    assert seen == ['|geo|firstShapeOrig', '|geo|secondShapeOrig']
    assert state.events == []


@pytest.mark.parametrize('prefix,changed_vertices,rejects', [('RIG_', False, False), ('OLD_', False, True), ('OLD_', True, False)])
def test_no_orig_preflight_follows_static_rebuild_or_visible_injection_dispatch(writers, monkeypatch, prefix, changed_vertices, rejects):
    _build, sync, state = writers
    collector = importlib.import_module('cgi_pipeline.hosts.maya.asset_info_collector')
    state.intermediate, state.target.uv_sets = False, []
    old_entry = entry()
    if changed_vertices:
        old_entry.update(vertices=4, vert_positions=[0, 0, 0] * 4)
        state.target.points = [(0, 0, 0)] * 4
    if prefix == 'RIG_':
        state.tweaks = [(1, 2, 3)] * 3  # A rebuilt static part must not be rejected for these.
    monkeypatch.setattr(collector, 'resolve_cache_group', lambda _group: '|geo')
    monkeypatch.setattr(collector, 'get_deform_input', lambda _parent: ('|geo|staticShape', None))
    monkeypatch.setattr(sync, '_collect_rig_scene_info', lambda _group: {'meshes': {'|geo|staticShape': old_entry}})
    monkeypatch.setattr(sync.cmds, 'listRelatives', lambda *_args, **_kw: ['|geo|static'])
    report = {'target_only_dags': [], 'pairing_groups': [{'group_id': 'static', 'action': 'ORIG_INJECT',
              'abc_dags': ['|sourceShape'], 'rig_dags': ['|geo|staticShape']}]}
    call = lambda: sync._preflight_sync_geometry({'meshes': {'|sourceShape': dict(entry(), normals_fv=[])}}, report, 'geo', prefix)
    if rejects:
        with pytest.raises(ValueError, match='at least one target UV set'):
            call()
    else:
        call()
    assert state.events == []


@pytest.mark.parametrize('failure', ['matrix', 'pnts'])
def test_geometry_preflight_rejects_unsafe_target_without_mutation(writers, monkeypatch, failure):
    _build, sync, state = writers
    collector = importlib.import_module('cgi_pipeline.hosts.maya.asset_info_collector')
    monkeypatch.setattr(collector, 'resolve_cache_group', lambda _group: '|geo')
    monkeypatch.setattr(collector, 'get_deform_input', lambda _tr: ('|geo|meshShape', '|geo|meshShapeOrig'))
    monkeypatch.setattr(sync, '_collect_rig_scene_info', lambda _group: {'meshes': {'|geo|meshShape': entry()}})
    monkeypatch.setattr(sync.cmds, 'listRelatives', lambda *_a, **_k: ['|geo|mesh'])
    report = {'target_only_dags': [], 'pairing_groups': [{'group_id': 'body', 'action': 'ORIG_INJECT',
              'abc_dags': ['|sourceShape'], 'rig_dags': ['|geo|meshShape']}]}
    state.matrix = Matrix(np.diag([-1, 1, 1, 1])) if failure == 'matrix' else Matrix(np.eye(4))
    state.driven_tweaks = failure == 'pnts'
    source = entry()
    source.pop('normals_fv')
    with pytest.raises(ValueError, match='reflection' if failure == 'matrix' else 'pnts.*driven'):
        sync._preflight_sync_geometry({'meshes': {'|sourceShape': source}}, report, 'geo', 'RIG_')
    assert state.events == []


def test_sync_execute_rejects_damaged_source_before_unlock_or_prefix(writers, monkeypatch, tmp_path):
    _build, sync, state = writers
    source = tmp_path / 'source.abc'
    source.write_bytes(b'fixture')
    report = {'target_only_dags': [], 'pairing_groups': [{'group_id': 'body', 'action': 'ORIG_INJECT',
              'abc_dags': ['|sourceShape'], 'rig_dags': ['|geo|meshShape']}]}
    monkeypatch.setattr(abc_reader, 'read_abc_as_info', lambda _path: {
        'meshes': {'|sourceShape': dict(entry(), normals_fv=[0, 0, 0] * 3)}})
    result = sync.execute({'source_path': 'original_rig.ma', 'parameters': {
        'source_abc': str(source), 'compare_result': {'schema_version': 'compare_result.v1', 'compare': report}}})
    assert result['status'] == 'ERROR'
    assert '[geometry_preflight]' in result['error']
    assert state.events == []


def test_sync_execution_failure_reports_partial_state_without_claiming_undo(writers, monkeypatch, tmp_path):
    _build, sync, state = writers
    source = tmp_path / 'source.abc'
    source.write_bytes(b'fixture')
    (tmp_path / 'logs').mkdir()
    report = {'target_only_dags': [], 'pairing_groups': [{'group_id': 'body', 'action': 'ORIG_INJECT',
              'abc_dags': ['|sourceShape'], 'rig_dags': ['|geo|meshShape']}]}
    monkeypatch.setattr(abc_reader, 'read_abc_as_info', lambda _path: {'meshes': {'|sourceShape': entry()}})
    monkeypatch.setattr(sync, '_preflight_sync_geometry', lambda *_a: None)
    monkeypatch.setattr(sync, 'REPOSITORY_ROOT', tmp_path)
    def fail():
        raise RuntimeError('injected execution failure')
    monkeypatch.setattr(sync, '_ensure_shading_nodes_unlocked', fail)
    result = sync.execute({'source_path': 'original_rig.ma', 'parameters': {
        'source_abc': str(source), 'compare_result': {'schema_version': 'compare_result.v1', 'compare': report}}})
    assert result['status'] == 'ERROR'
    assert '未自动回滚' in result['error']
    assert '已撤销' not in result['error']
    assert len([event for event in state.events if event[0] == 'undoInfo']) == 2


def test_full_base_injection_clears_only_its_orig_tweaks_and_verifies(writers):
    _build, sync, state = writers
    state.tweaks = [(17.0, 1753.0, 25.0)] * 3
    details = {}
    sync._inject_mesh_data_via_plug('|LiJing|geo|eye|eyeShapeOrig', entry(), details=details)
    assert state.tweaks == [(0.0, 0.0, 0.0)] * 3
    assert details['cleared_orig_vertices'] == 3
    clears = [event for event in state.events if event[0] == 'clear_pnts']
    assert len(clears) == 1
    assert clears[0][1] == '|LiJing|geo|eye|eyeShapeOrig.pnts[0:2]'
    names = [event[0] for event in state.events]
    assert names.index('plug_write') < names.index('clear_pnts')


@pytest.mark.parametrize('intermediate', [True, False])
def test_driven_pnts_are_rejected_without_disconnect_or_mutation(writers, intermediate):
    _build, sync, state = writers
    state.intermediate, state.driven_tweaks = intermediate, True
    with pytest.raises(ValueError, match='pnts.*driven'):
        sync._inject_mesh_data_via_plug('|eye|shape', entry())
    assert state.events == []


def test_visible_authored_pnts_are_preserved_by_rejection(writers):
    _build, sync, state = writers
    state.intermediate, state.tweaks = False, [(1.0, 2.0, 3.0)] * 3
    with pytest.raises(ValueError, match='visible'):
        sync._inject_mesh_data_via_plug('|eye|visibleShape', entry())
    assert state.tweaks == [(1.0, 2.0, 3.0)] * 3
    assert state.events == []


def test_zero_tweak_static_shape_remains_supported_without_pnts_write(writers):
    _build, sync, state = writers
    state.intermediate = False
    details = {}
    sync._inject_mesh_data_via_plug('|static|shape', entry(), details=details)
    assert details['cleared_orig_vertices'] == 0
    assert not any(event[0] == 'clear_pnts' for event in state.events)


@pytest.mark.parametrize('failure', ['write', 'readback'])
def test_orig_tweak_cleanup_failure_cannot_report_success(writers, failure):
    _build, sync, state = writers
    state.tweaks = [(1.0, 2.0, 3.0)] * 3
    state.fail_tweak_write, state.ignore_tweak_write = failure == 'write', failure == 'readback'
    with pytest.raises(RuntimeError):
        sync._inject_mesh_data_via_plug('|eye|shapeOrig', entry())
