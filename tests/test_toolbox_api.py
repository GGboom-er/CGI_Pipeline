"""Boundary checks: defaults must preview and errors must remain errors."""
from types import SimpleNamespace
from unittest.mock import patch

from cgi_pipeline import api, catalog
from cgi_pipeline.capabilities.maya import toolbox_api


def test_public_retarget_defaults_to_preview():
    catalog.reload()
    calls = []
    module = SimpleNamespace(transfer=lambda **values: calls.append(values) or {'dry_run': values['dry_run']})
    with patch.object(toolbox_api, '_module', return_value=module):
        receipt = api.execute_local('maya.animation.retarget.transfer', {'mapping': {}, 'frames': [1]}, host='maya')
    assert receipt['status'] == 'SUCCESS'
    assert calls[0]['dry_run'] is True
    assert calls[0]['existing'] == 'reject'


def test_public_retarget_preserves_failure_and_rejects_bad_contract():
    with patch.object(toolbox_api, '_module', side_effect=ValueError('Locked destination')):
        receipt = api.execute_local('maya.animation.retarget.transfer', {'mapping': {}, 'frames': [1]}, host='maya')
    assert receipt['status'] == 'ERROR'
    assert 'Locked destination' in receipt['error']
    for params in ({}, {'mapping': [], 'frames': [1]}, {'mapping': {}, 'frames': [1], 'dry_run': 'false'}):
        receipt = api.execute_local('maya.animation.retarget.transfer', params, host='maya')
        assert receipt['status'] == 'ERROR'
        assert receipt['error_code'] == 'API_CONTRACT_ERROR'


def test_normalize_uses_same_core_and_preview_default():
    catalog.reload()
    calls = []
    module = SimpleNamespace(normalize=lambda **values: calls.append(values) or {'dry_run': values['dry_run']})
    with patch.object(toolbox_api, '_module', return_value=module) as load:
        receipt = api.execute_local('maya.rig.skeleton.normalize_scales', {'nodes': ['joint1']}, host='maya')
    assert receipt['status'] == 'SUCCESS'
    assert calls == [{'nodes': ['joint1'], 'dry_run': True}]
    load.assert_called_once_with('DCC.maya.rig_tools.skeleton.zero_joint_scales')


def test_normalize_rejects_bad_contract_before_loading_host():
    with patch.object(toolbox_api, '_module') as load:
        for values in ({}, {'nodes': 'joint1'}, {'nodes': [], 'dry_run': 'false'}):
            receipt = api.execute_local('maya.rig.skeleton.normalize_scales', values, host='maya')
            assert receipt['status'] == 'ERROR'
            assert receipt['error_code'] == 'API_CONTRACT_ERROR'
    load.assert_not_called()
