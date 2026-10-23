"""Functional boundaries for regional topology and source correspondence."""
import pytest
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import regions, blendshape_map


def test_partitions_refuse_missing_and_overlapping_source_faces():
    valid = [{'name':'head','faces':[0,1],'keep_face_ratio':1},
             {'name':'body','faces':[2,3],'keep_face_ratio':.2}]
    regions.validate(valid,4)
    with pytest.raises(ValueError,match='cover'):
        regions.validate(valid,5)
    with pytest.raises(ValueError,match='overlap'):
        regions.validate([valid[0],{**valid[1],'faces':[1,2,3]}],4)


def test_shells_do_not_bridge_disconnected_feathers_and_rings_follow_topology():
    polygons=[[0,1,2],[2,3,4],[4,5,6],[20,21,22]]
    assert regions.shells(polygons)==[[0,1,2],[3]]
    assert regions.expand_faces([0],polygons,1)==[0,1]
    assert regions.expand_faces([0],polygons,2)==[0,1,2]


def test_sparse_blendshape_component_order_and_rejection():
    assert blendshape_map.component_ids(['vtx[9:11]','vtx[2]'])==[9,10,11,2]
    with pytest.raises(ValueError):
        blendshape_map.component_ids(['f[2:8]'])


def test_sparse_blendshape_repeated_components_keep_authored_delta_order():
    item={'ids':[9,2,9], 'points':[(.2,0,0,1),(.4,0,0,1),(-.3,0,0,1)]}
    # One source vertex may appear in two output seam copies; neither copy may
    # reverse duplicate records by sorting the delta value.
    assert blendshape_map.remap_points(item,[9,2,9])==[
        (0,(.2,0,0,1)),(0,(-.3,0,0,1)),(1,(.4,0,0,1)),
        (2,(.2,0,0,1)),(2,(-.3,0,0,1))]


def test_duplicate_region_names_and_nonfinite_ratios_rejected():
    with pytest.raises(ValueError):
        regions.validate([{'name':'x','faces':[0],'keep_face_ratio':float('nan')}],1)
    with pytest.raises(ValueError):
        regions.validate([{'name':'x','faces':[0],'keep_face_ratio':1},
                          {'name':'x','faces':[1],'keep_face_ratio':.5}],2)


def test_reduced_edge_selection_follows_original_endpoints_not_edge_numbers(monkeypatch):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.proxy_rig import membership
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import geometry
    old=SimpleNamespace(getEdgeVertices=lambda i: [(0,1),(1,2),(2,3)][i])
    new=SimpleNamespace(numEdges=3,getEdgeVertices=lambda i:[(0,1),(1,2),(2,0)][i])
    monkeypatch.setattr(geometry,'mesh_fn',lambda shape:(None,old if shape=='|a|shape' else new))
    added=[]
    class Cmds:
        def sets(self,value,**kw):
            if kw.get('q'):return ['a.e[1:2]']
            if 'addElement' in kw:added.extend(value)
            if 'isMember' in kw:return value==added
        def listRelatives(self,*a,**kw):return ['|b|shape']
    # Retained vertices 0,2,3: edge 1 is old (2,3); the new (0,2) is not old edge 1.
    membership.transfer_vertices(Cmds(),{'path':'|a','shape':'|a|shape',
        'memberships':{'component_sets':['selection']}},'|b',[0,2,3])
    assert added==['|b.e[1]']


def test_split_output_part_count_subtracts_source_once():
    from cgi_pipeline.capabilities.maya.asset.proxy_rig.report import part_summaries
    build={'plan':{'merge_groups':[],'reduce_entries':[],
        'region_entries':[{'source_uuid':'body','regions':[{'output_name':'head'},{'output_name':'body'}]}],
        'review_parts':[{'id':'body','label':'Body','sources':['body'],'before_faces':100}]},
        'regional_outputs':[{'source_uuid':'body','before_faces':100},{'source_uuid':'body','before_faces':100}],
        'outputs':[{'result':'|head','faces':20},{'result':'|body','faces':30}]}
    assert part_summaries(build)[0]['after_faces']==50


def test_normal_capture_preserves_both_authored_flag_and_lock():
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.mesh_io import normal_state
    mesh=SimpleNamespace(numEdges=3,getNormals=lambda space:[(0,0,1)],
        getNormalIds=lambda:([3],[0,0,0]),getVertices=lambda:([3],[0,1,2]),
        getEdgeVertices=lambda e:[(0,1),(1,2),(2,0)][e],
        isEdgeSmooth=lambda e:e==1,isNormalLocked=lambda i:True)
    state=normal_state(mesh,0)
    assert state['smooth_corners']==[False,True,False]
    assert state['locked_corners']==[True]*3
def test_restore_smoothing_collects_before_single_native_write(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import mesh_io
    events=[]
    class Mesh:
        numEdges=3
        def getEdgeVertices(self,edge):
            assert 'write' not in events
            return [(0,1),(1,2),(2,0)][edge]
        def setEdgeSmoothings(self,ids,values):
            assert ids==[0,1,2] and values==[True,False,True]
            events.append('write')
        def cleanupEdgeSmoothing(self):events.append('cleanup')
    monkeypatch.setattr(mesh_io.geo,'modules',lambda:(None,None,None))
    mesh_io.restore_state(Mesh(),{'smooth_corners':[True,False,True],
        'locked_corners':[False]*3,'normal_corners':[(0,0,1)]*3},[3],[0,1,2])
    assert events==['write','cleanup']

