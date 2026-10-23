"""Quality feedback is bounded, group-local and precedes final binding."""
import pytest
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import quality


def test_only_failed_group_rebuilds_from_original(monkeypatch):
    plan=[{'name':'small','keep_face_ratio':.1},{'name':'large','keep_face_ratio':.2}]
    made=[{'node':'a'},{'node':'b'}];calls=[];seen=[]
    def measure(prepared,spec,piece,options):
        seen.append((spec['name'],piece['node']))
        return {'missing_shells':0,'max':.02 if piece['node'] in ('a','b_retry') else .2,'shells':[]}
    def rebuild(prepared,spec,piece,ratio):
        calls.append((spec['name'],ratio));return {'node':'b_retry'}
    monkeypatch.setattr(quality,'measure',measure);monkeypatch.setattr(quality,'_rebuild',rebuild)
    result=quality.check_groups({},plan,made,{'max_error':.1,'max_retries':1})
    assert calls==[('large',pytest.approx(.3))]
    assert seen==[('small','a'),('large','b'),('large','b_retry')]
    assert made==[{'node':'a'},{'node':'b_retry'}]
    assert len(result['rows'][0]['attempts'])==1


def test_failed_group_can_keep_its_original_geometry(monkeypatch):
    ratios=[]
    monkeypatch.setattr(quality,'measure',lambda p,s,n,o:{'missing_shells':0,'max':0 if n.get('kept') else 1.,'shells':[]})
    monkeypatch.setattr(quality,'_rebuild',lambda p,s,n,r:ratios.append(r) or {'kept':True})
    out=quality.check_groups({},[{'name':'a','keep_face_ratio':.1}],[{}],
                             {'max_error':.1,'on_exceeded':'keep_region'})
    assert ratios==[1.] and out['rows'][0]['kept_original_region']


def test_missing_shell_is_not_accepted_when_only_reporting_distances(monkeypatch):
    monkeypatch.setattr(quality,'measure',lambda *a:{'missing_shells':1,'max':None,'shells':[]})
    with pytest.raises(RuntimeError,match='Shape quality exceeded'):
        quality.check_groups({},[{'name':'a','keep_face_ratio':.1}],[{}],{})


@pytest.mark.parametrize('value',[{'max_error':0},{'max_error':float('nan')},{'max_retries':9},
    {'retry_factor':1},{'on_exceeded':'ignore'},{'samples_per_shell':-1}])
def test_invalid_options_refuse_before_mutation(value):
    with pytest.raises(ValueError):quality.validate(value)
