"""Thin adapter contract tests; real geometry is checked in Maya by PL227."""
from types import SimpleNamespace
from unittest.mock import patch
from cgi_pipeline.capabilities.maya import xgen_api


def test_rebuild_forwards_to_shared_core():
    calls = []
    module = SimpleNamespace(rebuild=lambda nodes, root: calls.append((nodes, root)) or {'count': 2})
    context = SimpleNamespace(extras={'_api_id': 'maya.xgen.rebuild'})
    with patch.object(xgen_api, '_module', return_value=module):
        result = xgen_api.execute({'nodes': ['|curves'], 'root': 'Export'}, context)
    assert result['status'] == 'SUCCESS'
    assert calls == [(['|curves'], 'Export')]
    assert result['output']['count'] == 2
    with patch.object(xgen_api, '_module', return_value=module):
        result = xgen_api.execute({'nodes': ['|curves']}, context)
    assert result['status'] == 'SUCCESS'
    assert result['api_version'] == '2.0.0'
    assert calls[-1] == (['|curves'], None)


def test_error_is_not_reported_as_success():
    context = SimpleNamespace(extras={'_api_id': 'maya.xgen.record'})
    with patch.object(xgen_api, '_module', side_effect=ValueError('duplicate')):
        result = xgen_api.execute({'nodes': []}, context)
    assert result['status'] == 'ERROR'
    assert 'duplicate' in result['error']


def test_export_preserves_single_archive_manifest(tmp_path):
    manifest = {'schema_version': 2, 'files': [{'file': 'xgen_hair.abc',
                 'objects': [{'object_path': '/fringe/hair'}]}]}
    calls = []
    module = SimpleNamespace(export=lambda directory, backend: calls.append(backend) or manifest)
    context = SimpleNamespace(extras={'_api_id': 'maya.xgen.export'}, execution_mode='foreground')
    with patch.object(xgen_api, '_module', return_value=module):
        result = xgen_api.execute({'directory': str(tmp_path)}, context)
    assert result['status'] == 'SUCCESS'
    assert result['api_version'] == '2.2.0'
    assert result['output'] == manifest
    assert calls == ['verified']
    with patch.object(xgen_api, '_module', return_value=module):
        result = xgen_api.execute({'directory': str(tmp_path), 'backend': 'xpd'}, context)
    assert result['status'] == 'SUCCESS'
    assert calls[-1] == 'xpd'
