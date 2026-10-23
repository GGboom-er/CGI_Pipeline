"""Visual decisions drive bounded construction; numbers do not certify shape."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import search, mesh_io
from cgi_pipeline.capabilities.maya.asset.proxy_rig import scene_prep, semantic_regions, delivery_review


def test_visual_mode_needs_no_similarity_scale_and_never_searches():
    options = search.validate({'mode': 'visual', 'initial_reduction': 82})
    assert options['max_evaluations'] == 1
    assert 'radius' not in options
    with pytest.raises(ValueError):
        search.validate({'mode': 'visual', 'max_evaluations': 5})
    with pytest.raises(ValueError):
        search.validate({'mode': 'visual', 'score_band': [10, 20]})


def test_texture_replacement_checks_directory_once_and_preserves_udim(tmp_path):
    directory = tmp_path / 'texMaster_1k'; directory.mkdir()
    source = tmp_path / 'texMaster'
    values = {'a': str(source / 'skin.<UDIM>.exr'), 'b': str(source / 'coat.exr'),
              'c': str(tmp_path / 'other' / 'texMaster' / 'missing.exr')}
    calls = []
    def is_dir(path):
        calls.append(path); return path == directory
    changes, missing = scene_prep.texture_replacements(values, is_dir=is_dir)
    assert changes == {'a': str(directory / 'skin.<UDIM>.exr').replace('\\', '/'),
                       'b': str(directory / 'coat.exr').replace('\\', '/')}
    assert len(calls) == 2 and len(missing) == 1


def test_normal_capture_does_not_reinterpret_authored_hard_edges():
    fn = SimpleNamespace(getNormals=lambda space: [(0, 0, 1)],
        getNormalIds=lambda: ([3], [0, 0, 0]), getVertices=lambda: ([3], [0, 1, 2]),
        numEdges=3, getEdgeVertices=lambda i: [(0,1),(1,2),(2,0)][i],
        isEdgeSmooth=lambda i: False, isNormalLocked=lambda i: True)
    assert mesh_io.normal_state(fn, 0)['smooth_corners'] == [False] * 3


def test_expression_mask_requires_live_skinned_target_not_static_deltas():
    data = {'targets': [{'items': [
        {'ids': [0], 'geometry': []},
        {'ids': [1], 'geometry': ['static.outMesh']},
        {'ids': [2], 'geometry': ['live.outMesh']}]}]}
    cmds = SimpleNamespace(listHistory=lambda n, **kw: ['skin'] if n == 'live' else [],
                           nodeType=lambda n: 'skinCluster')
    ids = semantic_regions.live_skin_vertices(cmds, data)
    assert ids == {2}


def test_visual_review_cannot_call_continue_reducing_ready():
    labels = ['front','side','back','body']
    data = {'schema_version': 1, 'checks': {k:{'status':'pass'} for k in delivery_review.CHECKS},
            'reviewed_views': labels, 'required_views': [], 'issues': [],
            'part_decisions': [{'part':'body', 'decision':'reduce_more', 'focus':'abdomen silhouette',
                                'observation':'flat patches remain dense', 'views':['body']}]}
    result = delivery_review.normalize(data, labels, ['body'], True)
    assert result['decision'] == 'needs_revision'
    data['part_decisions'][0]['decision'] = 'accept'
    assert delivery_review.normalize(data, labels, ['body'], True)['decision'] == 'ready_for_user'


def test_visual_review_rejects_missing_observation_or_unseen_evidence():
    data = {'schema_version':1,'part_decisions':[{'part':'body','decision':'accept',
             'focus':'silhouette','observation':'looks good','views':['body']}]}
    with pytest.raises(ValueError, match='reviewed'):
        delivery_review.normalize(data, ['body'], ['body'], True)


def test_explicit_face_groups_cover_sparse_region_ids_without_numeric_search(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import adaptive
    monkeypatch.setattr(adaptive.numeric,'run',lambda *a:pytest.fail('Explicit regions need no clustering'))
    groups=[{'name':'dense','faces':[3,7],'reduction':90,'reason':'flat dense patch'},
            {'name':'rim','faces':[9],'reduction':50,'reason':'silhouette bend'}]
    options=search.validate({'mode':'visual','work_groups':groups})
    result=adaptive.plan_groups({'polygon_ids':[3,3,7,9]},options,None)
    assert len(result['groups'])==2
    with pytest.raises(ValueError,match='cover'):
        adaptive.plan_groups({'polygon_ids':[3,7,9,10]},options,None)
    with pytest.raises(ValueError,match='overlap'):
        search.validate({'mode':'visual','work_groups':[groups[0],{**groups[1],'faces':[7,9]}]})


def test_no_live_skin_means_no_automatic_head_or_finger_protection(monkeypatch):
    geo=semantic_regions.geo
    mesh=SimpleNamespace(numPolygons=2,numVertices=4,getVertices=lambda:([3,3],[0,1,2,0,2,3]))
    monkeypatch.setattr(geo,'modules',lambda:(None,None,None))
    monkeypatch.setattr(geo,'mesh_fn',lambda shape:(None,mesh))
    rows=semantic_regions.expression_body({'shape':'shape','blendshapes':[]},'anyCharacter')
    assert len(rows)==1 and rows[0]['faces']==[0,1] and rows[0]['keep_face_ratio']==.2


def test_fit_skeleton_discovery_does_not_depend_on_hierarchy(monkeypatch):
    cmds=SimpleNamespace(ls=lambda *a,**kw:['|Group|FitSkeleton','|else|rig:FitSkeleton','|FitSkeletonExtra'],
                         objExists=lambda n:True)
    monkeypatch.setattr(scene_prep,'_node',lambda cmds,n:n)
    assert set(scene_prep.fit_skeletons(cmds))=={'|Group|FitSkeleton','|else|rig:FitSkeleton'}


def test_visual_mode_requires_a_decision_for_every_part():
    labels=['front','side','back','body']
    data={'schema_version':1,'checks':{k:{'status':'pass'} for k in delivery_review.CHECKS},
          'reviewed_views':labels,'required_views':[],'issues':[]}
    assert delivery_review.normalize(data,labels,['body'],True,
        visual_decisions_required=True)['decision']=='pending_review'
    data['part_decisions']=[{'part':'body','decision':'accept','focus':'main volume',
                            'observation':'volume retained','views':['body']}]
    assert delivery_review.normalize(data,labels,['body'],True,
        visual_decisions_required=True)['decision']=='ready_for_user'
