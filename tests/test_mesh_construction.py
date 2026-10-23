"""Bounded writes preserve per-piece bone identity without union-size buffers."""
import numpy as np
from cgi_pipeline.capabilities.maya.asset.mesh_reduce.construction import weight_blocks


def test_piece_mappings_bone_order_offsets_and_bounded_writes():
    pieces=[{'source_vertex_ids':[2,0], 'weights':{
        'influences':[{'uuid':'a'},{'uuid':'b'}],
        'expected':np.array([[.2,.8],[.4,.6],[.9,.1]]),'blend_weights':[0,.5,1]}},
        {'source_vertex_ids':[1], 'weights':{'influences':[{'uuid':'c'},{'uuid':'a'}],
        'expected':np.array([[0,1],[.7,.3]]),'blend_weights':[0,.2]}}]
    blocks=list(weight_blocks(pieces,['c','b','a'],max_values=3))
    assert [begin for begin,_,_ in blocks]==[0,1,2]
    np.testing.assert_allclose(np.vstack([values for _,values,_ in blocks]),[[0,.1,.9],[0,.8,.2],[.7,0,.3]])
    np.testing.assert_allclose(np.concatenate([blend for _,_,blend in blocks]),[1,0,.2])
    assert all(values.size<=3 for _,values,_ in blocks)


def test_unused_union_columns_can_be_omitted_without_changing_weights():
    pieces=[{'source_vertex_ids':[0], 'weights':{'influences':[{'uuid':'unused'},{'uuid':'active'}],
        'expected':np.array([[0.,1.]]),'blend_weights':[0.]}}]
    _,weights,_=next(weight_blocks(pieces,['active']))
    np.testing.assert_array_equal(weights,[[1.]])
