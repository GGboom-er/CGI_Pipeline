"""Cross-source regions keep identity and report each original exactly once."""
import pytest
from cgi_pipeline.capabilities.maya.asset.proxy_rig import plan,report,inventory


def candidate():
    rows=[{'uuid':u,'faces':4,'parent':'|p','visibility_signature':'same',
           'memberships':{},'blendshapes':[],'protected_reasons':[]} for u in ('a','b')]
    source={'policy_version':inventory.POLICY_VERSION,'scene_path':'/managed/in.ma',
            'scene_fingerprint':'x','meshes':rows}
    decision={k:source[k] for k in ('policy_version','scene_path','scene_fingerprint')}
    decision.update(character='role',reduce_entries=[],merge_groups=[],keep=[],protect=[],
        region_entries=[{'source_uuid':u,'reason':'same switch, separate density groups',
            'regions':[{'name':'dense','faces':[0,1],'keep_face_ratio':.2,'output_name':'role_feathers'},
                       {'name':'sparse','faces':[2,3],'keep_face_ratio':1.,'output_name':'role_feathers'}]}
            for u in ('a','b')])
    return source,decision


def test_two_region_sources_share_one_output():
    source,decision=candidate()
    assert plan.validate(source,decision)['changed']==['a','b']
    assert report.output_sources(decision)=={'role_feathers':['a','b']}


@pytest.mark.parametrize('key,value',[('parent','|other'),('visibility_signature','other'),
    ('blendshapes',['expression']),('memberships',{'component_sets':['set1']})])
def test_incompatible_shared_output_is_rejected(key,value):
    source,decision=candidate();source['meshes'][1][key]=value
    with pytest.raises(ValueError,match='Shared regional output'):
        plan.validate(source,decision)


def test_regional_report_counts_each_source_and_merge_participant_once():
    _,decision=candidate()
    decision['review_parts']=[{'id':'feathers','sources':['a','b'],'before_faces':8}]
    build={'plan':decision,'geometry_rows':[],
        'regional_outputs':[{'source_uuid':'a','source_uuids':['a','b'],'before_faces':8,
                             'source_before_faces':{'a':4,'b':4}}],
        'outputs':[{'result':'|p|role_feathers','faces':5}]}
    row=report.part_summaries(build)[0]
    assert row['after_faces']==5 and row['merged_models']==2
    assert row['reduction_percent']==pytest.approx(37.5)
