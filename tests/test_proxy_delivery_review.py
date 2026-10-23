"""Delivery gates and character-independent reports; no Maya or visual claims."""
import base64
import copy
import json
from pathlib import Path

import pytest
from cgi_pipeline.capabilities.maya.asset.proxy_rig import delivery_review as review
from cgi_pipeline.capabilities.maya.asset.proxy_rig import report, safety

VIEWS = ['front','side','back','body']

def test_capture_rejects_ignored_angle_before_maya_or_files():
    with pytest.raises(ValueError, match='rotation'):
        report.capture_delivery_views([{'label':'side','angle':90}], 'unused', 'unused.json')

def reviewed():
    return {'schema_version':1,'checks':{k:{'status':'pass'} for k in review.CHECKS},
            'reviewed_views':list(VIEWS),'required_views':[],'issues':[]}

def issue():
    return {'id':'density-1','parts':['body'],'check':'density','problem':'局部仍然偏密',
            'cause':'当前约束保留过多细分','action':'修正源策略后重做该组','views':['body'],'state':'open'}

def test_missing_agent_review_cannot_inherit_structural_success():
    result=review.normalize(None,VIEWS,['body'],True)
    assert result['decision']=='pending_review'
    assert result['checks']['structure']['status']=='pass'
    assert result['checks']['shape']['status']=='pending'

def test_missing_discriminating_view_blocks_ready_even_with_all_pass_declarations():
    result=review.normalize(reviewed(),VIEWS,['body'],True,['braid_back'])
    assert result['missing_views']==['braid_back']
    assert result['decision']=='pending_review'

def test_actual_failure_is_returned_for_revision_and_structural_failure_cannot_pass():
    data=reviewed();data['checks']['density']={'status':'fail'};data['issues']=[issue()]
    assert review.normalize(data,VIEWS,['body'],True)['decision']=='needs_revision'
    assert review.normalize(reviewed(),VIEWS,['body'],False)['decision']=='needs_revision'

def test_unresolved_issue_cannot_be_hidden_behind_passing_check():
    data=reviewed();data['issues']=[issue()]
    with pytest.raises(ValueError,match='conflicts'):
        review.normalize(data,VIEWS,['body'],True)

def test_failed_check_requires_a_concrete_issue():
    data=reviewed();data['checks']['shape']={'status':'fail'}
    with pytest.raises(ValueError,match='requires an open issue'):
        review.normalize(data,VIEWS,['body'],True)

@pytest.mark.parametrize('mutation',[
    lambda d:d.update(visual_findings=['长篇自述']),
    lambda d:d.update(schema_version=True),
    lambda d:d['reviewed_views'].append('nonexistent'),
    lambda d:d['checks'].update(structure={'status':'pass'}),
    lambda d:d['issues'].append({**issue(),'problem':'长'*73}),
])
def test_unknown_or_unbounded_review_input_is_rejected(mutation):
    data=reviewed();mutation(data)
    with pytest.raises(ValueError):review.normalize(data,VIEWS,['body'],True)

def test_expression_support_is_not_bypassed_by_na():
    data=reviewed();data['checks']['expression']={'status':'na','reason':'无表情'}
    with pytest.raises(ValueError,match='expression chain'):
        review.normalize(data,VIEWS,['body'],True,expression_required=True)

def test_data_is_deterministic_and_ready_is_only_user_review_handoff():
    a=review.normalize(reviewed(),VIEWS,['body'],True)
    b=review.normalize(copy.deepcopy(reviewed()),VIEWS,['body'],True)
    assert a==b and a['decision']=='ready_for_user'

@pytest.mark.parametrize('character',['pengMoWang','newCharacter'])
def test_fixed_report_uses_artifacts_and_schema_not_per_run_prose(tmp_path,monkeypatch,character):
    monkeypatch.setattr(safety,'managed',lambda p,**kw:Path(p))
    image=tmp_path/'view.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB9kAAAAASUVORK5CYII='))
    views=[{'label':v,'output_path':str(image),'seconds':.1} for v in VIEWS]
    output=tmp_path/f'{character}_proxyRig_v001.ma'
    output.write_text('// saved fixture',encoding='utf8')
    build={'character':character,'source_master':{'path':f'{character}_master.ma','version':'v001'},
        'source_scene':'source.ma','output_path':str(output),'before':{'faces':20,'meshes':2},
        'after':{'faces':10,'meshes':1},'plan':{'merge_groups':[],
          'reduce_entries':[{'source_uuid':'a','output_name':character+'_body'}],
          'review_parts':[{'id':'body','label':'身体','sources':['a'],'before_faces':20}],
          'weights':{'neighbor_rings':2}},
        'geometry_rows':[{'source_uuid':'a','before':{'counts':{'faces':20}}}],
        'outputs':[{'result':'|'+character+'_body','faces':10}],
        'weights':{'max_influences':8,'threshold':.001,'neighbor_rings':2},
        'timing_seconds':{'reduce':1.},'timing_totals_seconds':{'api_before_report':2.}}
    def save(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value),encoding='utf8');return str(path)
    args=[save('build.json',build),save('before.json',views),save('after.json',views),
          save('validation.json',{'passed':True,'scene':str(output)}),str(tmp_path/'report.html'),
          save('execution.json',{'visual_findings':['MUST_NOT_RENDER'],'timing_note':'MUST_NOT_RENDER',
                                'search_policy':'MUST_NOT_RENDER'}),save('review.json',reviewed())]
    report.write_delivery(*args)
    first=Path(args[4]).read_bytes();report.write_delivery(*args)
    assert first==Path(args[4]).read_bytes()
    text=first.decode('utf8');model=json.loads(Path(args[4]).with_suffix('.json').read_text(encoding='utf8'))
    assert character in text and 'MUST_NOT_RENDER' not in text
    assert '%%' not in text and text.count('class="compare"')==4
    assert model['review']['decision']=='ready_for_user' and len(model['steps'])==7
    assert '邻域 2 圈' in model['steps'][2]['input']
    assert 'Un-tone-mapped' not in text and '文件大小和帧率' not in text
    monkeypatch.chdir(tmp_path)
    report.write_delivery(*[Path(p).name for p in args])
    model=json.loads((tmp_path/'report.json').read_text(encoding='utf8'))
    assert all(Path(p).is_absolute() for p in model['artifacts'].values() if p)
    assert first==(tmp_path/'report.html').read_bytes()
