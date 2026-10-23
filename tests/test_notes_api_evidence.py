"""Generated current capability claims must follow supplied manifest evidence."""
import importlib.util
from pathlib import Path


def test_new_unverified_api_cannot_inherit_old_matrix_acceptance():
    path = Path(__file__).resolve().parents[1] / 'tools/sync_notes_api_index.py'
    spec = importlib.util.spec_from_file_location('notes_api_index', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{'api_id': 'maya.new.unverified', 'executor': 'maya',
             'verification': {'level': 'untested', 'evidence': []}}]
    for renderer in (module.render_catalog, module.render_project_block):
        result = renderer(rows, '2026-09-22')
        assert 'API 总数：1' in result
        assert 'untested 1' in result
        assert 'PL193' not in result and '全部通过' not in result
        assert '新增或变更 API' in result
