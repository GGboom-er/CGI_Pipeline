import numpy as np
import pytest
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import search,grouping
from cgi_pipeline.capabilities.maya.asset.mesh_reduce.construction import weight_blocks

def options(**kwargs):return search.validate({'score_band':[18,25],'radius':.01,**kwargs})


def test_search_starts_at_ninety_and_brackets_without_fixed_steps():
    config=options()
    assert search.next_reduction([],config)==90
    a={'reduction':90.,'score':50.,'faces':100}
    assert search.next_reduction([a],config)==95
    b={'reduction':95.,'score':8.,'faces':30}
    assert search.next_reduction([a,b],config)==92.5
    assert search.acceptable({'score':18},config)
    assert search.acceptable({'score':25},config)


def test_search_selects_lowest_face_safe_observation_without_monotonic_assumption():
    config=options()
    rows=[{'reduction':98.,'score':21.,'faces':30},
          {'reduction':99.,'score':10.,'faces':20},
          {'reduction':97.,'score':19.,'faces':25},
          {'reduction':96.,'score':22.,'faces':22,'missing_shells':1}]
    assert search.choose(rows,config)==rows[2]
    assert search.choose([rows[-1]],config) is None


@pytest.mark.parametrize('options',[{'max_evaluations':100},{'score_band':[25,18]},
    {'initial_reduction':100},{'radius':0},{'samples_per_shell':1},{'unknown':1}])
def test_invalid_search_options_refuse(options):
    with pytest.raises(ValueError):search.validate({'score_band':[18,25],'radius':.01,**options})

def test_search_requires_explicit_quality_scale():
    with pytest.raises(ValueError):search.validate({})

def test_normal_capture_expands_all_normal_indices_not_one_face(monkeypatch):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import mesh_io
    fn=SimpleNamespace(getNormals=lambda space:[(0,0,1),(0,1,0)],getNormalIds=lambda:([3,3],[0,0,0,1,1,1]))
    monkeypatch.setattr(mesh_io.geo,'modules',lambda:(SimpleNamespace(listRelatives=lambda *a,**k:['shape']),SimpleNamespace(MSpace=SimpleNamespace(kWorld=4)),None))
    monkeypatch.setattr(mesh_io.geo,'mesh_fn',lambda shape:(None,fn))
    assert mesh_io.capture_normals('mesh')==[(0,0,1)]*3+[(0,1,0)]*3


def test_weight_batches_span_multiple_virtual_sources():
    p=lambda value:{'source_vertex_ids':[0],'weights':{'influences':[{'uuid':'j'}],
        'expected':np.array([[value]]),'blend_weights':[0.]}}
    blocks=list(weight_blocks([p(.1),p(.2),p(.3)],['j'],max_values=2))
    assert [i for i,_,_ in blocks]==[0,2]
    np.testing.assert_allclose(np.vstack([w for _,w,_ in blocks]),[[.1],[.2],[.3]])


def test_source_geometry_grouping_ignores_object_partition_and_retains_minimal_shells():
    pytest.importorskip('igl');pytest.importorskip('sklearn')
    vertices=[];faces=[]
    # Two shapes differing greatly in physical scale but identical topology.
    for index,scale in enumerate([1.,1.,8.,8.]):
        base=len(vertices);x=index*30.
        vertices.extend([[x,0,0],[x+scale,0,0],[x+scale,scale,0],[x,scale,0]])
        faces.extend([[base,base+1,base+2],[base,base+2,base+3]])
    v=np.asarray(vertices);f=np.asarray(faces)
    a=grouping.plan(v,f,np.arange(8),minimal_faces=1)
    b=grouping.plan(v+[10,20,30],f,np.arange(8),minimal_faces=1)
    assert [g['faces'] for g in a['groups']]==[g['faces'] for g in b['groups']]
    assert len(a['groups'])==2
    assert sorted(i for g in a['groups'] for i in g['faces'])==list(range(8))
    kept=grouping.plan(v,f,np.arange(8),minimal_faces=12)
    assert len(kept['groups'])==1 and kept['groups'][0]['preserve']


@pytest.mark.parametrize('kind,upstream,extra,expected',[
    ('lambert',[],False,False),('blinn',[],False,False),
    ('lambert',['file1'],False,True),('aiStandardSurface',[],False,True),
    ('lambert',[],True,True)])
def test_uv_constraint_relaxation_requires_known_constant_surface(kind,upstream,extra,expected):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.constraints import uv_sensitive
    def connections(plug,**kwargs):
        if plug=='sg.surfaceShader':return ['shader']
        if plug=='sg.displacementShader':return ['displace'] if extra else []
        return upstream if plug=='shader' else []
    cmds=SimpleNamespace(listConnections=connections,nodeType=lambda node:kind)
    assert uv_sensitive(cmds,'sg') is expected


def test_mixed_material_work_group_keeps_uv_borders():
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.constraints import reduce_flags
    cache={'assignment':[0,0,1],'uv_sensitive':[False,True]}
    assert reduce_flags(cache,[0,1])[0]=={'keepMapBorder':False}
    assert reduce_flags(cache,[1,2])[0]=={'keepMapBorder':True}


def test_constraint_density_tracks_authored_seams_only_when_uv_is_used():
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.constraints import face_features
    cache={'polygons':[[0,1,4,3],[1,2,5,4]],'assignment':[0,0],'uv_sensitive':[True],
           'uvs':{'map1':([],[],[4,4],[[0,1,4,3],[1,2,5,4]])},
           'normals':[(0.,0,1.)],'normal_rows':[[0]*4,[0]*4]}
    np.testing.assert_allclose(face_features(cache),[[.75,.75],[.75,.75]])
    cache['uvs']['map1'][3][1]=[6,7,8,9]
    np.testing.assert_allclose(face_features(cache)[:,0],[1,1])
    cache['uv_sensitive']=[False]
    np.testing.assert_allclose(face_features(cache)[:,0],[0,0])


def test_region_cut_point_contacts_do_not_share_component_vertices():
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.regions import corner_mapping
    polygons=[[0,1,2],[0,3,4],[2,1,5]]
    ids,corners=corner_mapping(polygons,[0,1,2])
    assert ids.count(0)==2
    assert not set(corners[:3]).intersection(corners[3:6])
    assert len(set(corners[:3]).intersection(corners[6:]))==2
    assert [[ids[v] for v in corners[i:i+3]] for i in (0,3,6)]==polygons
    # A remote edge path connects both fans; global shell IDs cannot fix this.
    ids,corners=corner_mapping([[0,1,2],[0,3,4],[1,2,3],[2,3,4]],[0,1,2,3])
    assert ids.count(0)==2
    assert corners[0]!=corners[3]


def test_equal_geometry_with_different_native_constraints_is_not_one_budget():
    pytest.importorskip('igl');pytest.importorskip('sklearn')
    points=np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0],[3,0,0],[4,0,0],[4,1,0],[3,1,0]])
    triangles=np.array([[0,1,2],[0,2,3],[4,5,6],[4,6,7]])
    assert len(grouping.plan(points,triangles,np.arange(4),minimal_faces=1)['groups'])==1
    result=grouping.plan(points,triangles,np.arange(4),minimal_faces=1,boundary_features=np.array([[0,0],[0,0],[1,0],[1,0]]))
    assert len(result['groups'])==2


def test_regional_search_groups_original_face_ids_and_maps_back(monkeypatch):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import adaptive,construction
    cache={'points':np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0]]),
           'matrix':np.eye(4).ravel(),'polygons':[[0,1,2],[0,2,3]],
           'triangle_counts':[1,1],'triangles':[0,1,2,0,2,3],'parent':'root'}
    def plan(operation,arrays,options,workdir):
        assert operation=='geometry.plan'
        assert arrays['polygon_ids'].tolist()==[1]
        return {'groups':[{'faces':[1],'name':'geometry_0','preserve_border':False}], 'seconds':.1}
    def solve(source,group,options,workdir,name,**kwargs):
        assert kwargs['source_cache'] is cache and group['preserve_border']
        return {'node':'piece','source_vertex_ids':[0,2,3],'before_faces':1,'after_faces':1,
                'search':{'evaluations':1}}
    monkeypatch.setattr(adaptive.numeric,'run',plan)
    monkeypatch.setattr(adaptive.cached_reduce,'solve',solve)
    monkeypatch.setattr(adaptive.constraints,'face_features',lambda c:np.zeros((2,2)))
    monkeypatch.setattr(adaptive.geo,'modules',lambda:(SimpleNamespace(polyEvaluate=lambda *a,**k:1),None,None))
    monkeypatch.setattr(adaptive.geo,'snapshot',lambda node:{'points':[]})
    monkeypatch.setattr(construction,'_join',lambda pieces,name:'joined')
    result=adaptive.reduce_partition('source',{'faces':[1],'search':{},'preserve_border':True},None,'test',cache)
    assert result['source_vertex_ids']==[0,2,3] and result['search']['evaluations']==1


def test_temporary_density_cut_stitches_source_ids_but_keeps_uv_seam_and_distinct_shells():
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.mesh_io import stitch_snapshot
    # Two adjacent triangles cut into two work meshes, plus a separate triangle
    # whose first point happens to coincide. It must never be spatially welded.
    polygons=[[0,1,2],[0,2,3],[4,5,6]]
    ids=[0,1,2,0,2,3,4,5,6]
    points=[[0,0,0],[1,0,0],[1,1,0],[0,0,0],[1,1,0],[0,1,0],[0,0,0],[0,0,1],[1,0,1]]
    state={'smooth_corners':[False]*9,'locked_corners':[False]*9,'normal_corners':[(0,0,1)]*9}
    snapshot={'points':points,'face_counts':[3]*3,'face_indices':list(range(9)),
              'counts':{'faces':3,'vertices':9},'normal_state':state,
              'uv_partition':{'map1':list(range(9))},'materials':['a','b','c']}
    cache={'polygons':polygons,'normal_state':{**state,'smooth_corners':[True]*9}}
    out,mapping=stitch_snapshot(snapshot,ids,cache)
    assert mapping==list(range(7))
    assert out['face_indices']==[0,1,2,0,2,3,4,5,6]
    assert out['uv_partition']==snapshot['uv_partition'] and out['materials']==snapshot['materials']
    assert all(out['normal_state']['smooth_corners'])
    assert not any(out['normal_state']['locked_corners'])


def test_original_disconnected_vertex_fans_are_not_welded_back():
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.mesh_io import stitch_snapshot
    snapshot={'points':[[0,0,0]]*6,'face_indices':list(range(6)), 'face_counts':[3,3]}
    cache={'polygons':[[0,1,2],[0,3,4]]}
    out,ids=stitch_snapshot(snapshot,[0,1,2,0,3,4],cache)
    assert ids==[0,1,2,0,3,4] and out is snapshot


def test_unlocked_normals_are_recomputed_and_only_authored_locks_are_assigned(monkeypatch):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import mesh_io
    calls=[]
    fn=SimpleNamespace(numEdges=3,getEdgeVertices=lambda e:[(0,1),(1,2),(2,0)][e],
        setEdgeSmoothings=lambda ids,values:calls.append(('edges',ids,values)),
        cleanupEdgeSmoothing=lambda:calls.append(('cleanup',)),
        setFaceVertexNormals=lambda *args:calls.append(('normals',args)))
    om=SimpleNamespace(MSpace=SimpleNamespace(kObject=2),MVector=lambda *n:n)
    monkeypatch.setattr(mesh_io.geo,'modules',lambda:(None,om,None))
    state={'smooth_corners':[True,False,True],'locked_corners':[False]*3,'normal_corners':[(0,0,1)]*3}
    mesh_io.restore_state(fn,state,[3],[0,1,2])
    assert not any(call[0]=='normals' for call in calls)
    state['locked_corners'][1]=True
    mesh_io.restore_state(fn,state,[3],[0,1,2])
    assert ('normals',([(0,0,1)],[0],[1],2)) in calls
    assert calls[-2:]==[('edges',[0,1,2],[True,False,True]),('cleanup',)]


def test_density_partition_refuses_artificial_seams_that_dominate_target_budget():
    # Alternating strips of triangles would pin almost the whole reduced mesh.
    triangles=np.array([[i,i+1,i+2] for i in range(100)])
    pairs=np.array([[i,i+1] for i in range(99)])
    polygons=np.arange(100)
    alternating=[polygons[::2],polygons[1::2]]
    coherent=[polygons[:50],polygons[50:]]
    assert not grouping.cut_budget_ok(triangles,polygons,pairs,alternating,.1)
    assert grouping.cut_budget_ok(triangles,polygons,pairs,coherent,.1)
