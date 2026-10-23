"""Real numerical libraries: grouping partitions and source-restricted errors."""
import numpy as np
import pytest
pytest.importorskip('igl')
pytest.importorskip('sklearn')
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import analysis


def test_many_shells_become_two_face_groups_without_losing_polygons():
    # Same topology, large differences in world density; no host nodes involved.
    v=[];f=[]
    for k in range(1500):
        scale=.1 if k<1000 else 1.
        v.extend([[3*k,0,0],[3*k+scale,0,0],[3*k,scale,0]])
        f.append([3*k,3*k+1,3*k+2])
    out=analysis.group_shells(v,f,np.arange(1500))
    assert out['shell_count']==1500 and len(out['groups'])==2
    assert [len(g['shells']) for g in out['groups']]==[1000,500]
    assert sorted(f for g in out['groups'] for f in g['faces'])==list(range(1500))


def test_surface_check_detects_flattening_with_unchanged_retained_vertices():
    v=np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0],[.5,.5,.2]])
    f=np.array([[0,1,4],[1,2,4],[2,3,4],[3,0,4]])
    r=analysis.compare_shells(v,f,np.zeros(4,dtype=int),v[:4],np.array([[0,1,2],[0,2,3]]),np.arange(4))
    assert r['max']==pytest.approx(.2)


def test_nearby_surviving_shell_does_not_hide_a_missing_shell():
    v=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,.0001],[1,0,.0001],[0,1,.0001]])
    f=np.array([[0,1,2],[3,4,5]])
    r=analysis.compare_shells(v,f,np.array([0,1]),v[:3],f[:1],np.arange(3))
    assert r['missing_shells']==1 and r['shells'][1]['missing']


def test_unchanged_surface_and_repeatability():
    v=np.array([[0.,0,0],[1,0,0],[0,1,0]])
    f=np.array([[0,1,2]])
    a=analysis.compare_shells(v,f,np.array([0]),v,f,np.arange(3))
    b=analysis.compare_shells(v,f,np.array([0]),v,f,np.arange(3))
    assert a['max']<1e-12 and a['shells']==b['shells']


def test_source_face_partition_requires_valid_mapping():
    with pytest.raises(ValueError,match='contiguously'):
        analysis.group_shells([[0,0,0],[1,0,0],[0,1,0]],[[0,1,2]],[2])
