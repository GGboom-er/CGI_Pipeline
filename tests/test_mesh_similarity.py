"""Numerical F-score uses surface area and cannot match a neighboring shell."""
import numpy as np
import pytest
pytest.importorskip('igl')
from cgi_pipeline.capabilities.maya.asset.mesh_reduce.similarity import SurfaceReference


def mesh():
    return np.array([[0.,0,0],[1,0,0],[0,1,0]]),np.array([[0,1,2]])


def test_identical_surface_and_repeatability():
    v,f=mesh();ref=SurfaceReference(v,f,[.01,.1],samples_per_shell=128)
    first=ref.evaluate(v,f,np.arange(3));second=ref.evaluate(v,f,np.arange(3))
    assert [r['fscore'] for r in first['profiles']]==[100.,100.]
    assert first['profiles']==second['profiles']


def test_known_parallel_offset_changes_match_at_distance_radius():
    v,f=mesh();ref=SurfaceReference(v,f,[.05,.15],samples_per_shell=128)
    out=ref.evaluate(v+[0,0,.1],f,np.arange(3))
    assert [r['fscore'] for r in out['profiles']]==[0.,100.]
    assert out['mean_distance']==pytest.approx(.1)


def test_missing_small_shell_and_area_weighting():
    v,f=mesh();points=np.vstack((v, v*.1+[0,0,.00001]));faces=np.vstack((f,f+3))
    ref=SurfaceReference(points,faces,[.1],samples_per_shell=128)
    out=ref.evaluate(v,f,np.arange(3));row=out['profiles'][0]
    assert out['missing_shells']==1
    assert row['fscore']==pytest.approx(200/(2+.01))
    assert row['worst_shell_fscore']==0.


@pytest.mark.parametrize('radii',[[0],[-1],[float('nan')],[]])
def test_invalid_distance_scale_refused(radii):
    v,f=mesh()
    with pytest.raises(ValueError):SurfaceReference(v,f,radii)
