"""Report input snapshots retain guards without repeated history-wide resolution."""
from pathlib import Path

import pytest

from cgi_pipeline.core import workspace
from test_workspace import begin, root, symlink_or_skip


def test_summary_path_work_is_bounded_and_history_remains_complete(root, monkeypatch):
    operations = [begin(f'api-{index}', business_task_id='REPORT', submitted_at=index + 1)
                  for index in range(6)]
    run_dir = Path(operations[0]['run_dir'])
    Path(operations[-1]['report_path']).write_text('complete latest details', encoding='utf-8')
    resolved, state_reads = [], []
    original_resolve, original_read = workspace._resolved_path, workspace._read

    def resolve(path):
        resolved.append(Path(path))
        return original_resolve(path)

    def read(path):
        if Path(path).name == 'state.json' or Path(path).name.endswith('_state.json'):
            state_reads.append(Path(path))
        return original_read(path)

    state = workspace._read(run_dir / '.info/run_state.json')
    monkeypatch.setattr(workspace, '_resolved_path', resolve)
    monkeypatch.setattr(workspace, '_read', read)
    workspace._render_summary(run_dir, state)
    report = (run_dir / 'REPORT.md').read_text(encoding='utf-8')
    assert all(f'| `api-{index}` |' in report for index in range(6))
    assert 'Latest operation: `api-5`' in report and 'complete latest details' in report
    # Bound expensive filesystem work, allowing an independent final read guard.
    assert len(resolved) <= 16 * len(operations) + 32
    assert len(state_reads) <= len(operations) + 2


@pytest.mark.parametrize('change', ['layout', 'identity', 'unselected_link'])
def test_next_summary_revalidates_layout_identity_and_all_candidates(root, change):
    operation = begin('api-latest', business_task_id='FRESH', submitted_at=1)
    run_dir = Path(operation['run_dir'])
    paths = workspace.operation_paths(run_dir, operation['task_id'])
    paths['report'].write_text('first complete report', encoding='utf-8')
    state = workspace._read(run_dir / '.info/run_state.json')
    workspace._render_summary(run_dir, state)
    before = (run_dir / 'REPORT.md').read_bytes()
    if change == 'layout':
        (run_dir / '.info/api-latest_manifest.json').write_text('{}', encoding='utf-8')
        expected = 'WORKSPACE_OPERATION_CONFLICT'
    elif change == 'identity':
        record = workspace._read(paths['state'])
        record['task_id'] = 'foreign-owner'
        workspace._write(paths['state'], record)
        expected = 'WORKSPACE_STATE_INVALID'
    else:
        outside = root / 'outside-manifest.json'
        outside.write_text('foreign data', encoding='utf-8')
        symlink_or_skip(run_dir / '.info/api-latest_manifest.json', outside)
        expected = 'WORKSPACE_PATH_INVALID'
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace._render_summary(run_dir, state)
    assert error.value.code == expected
    assert (run_dir / 'REPORT.md').read_bytes() == before


def test_next_summary_observes_new_state_and_report(root):
    operation = begin('api-latest', business_task_id='CURRENT', submitted_at=1)
    run_dir = Path(operation['run_dir'])
    state = workspace._read(run_dir / '.info/run_state.json')
    paths = workspace.operation_paths(run_dir, operation['task_id'])
    paths['report'].write_text('old details', encoding='utf-8')
    workspace._render_summary(run_dir, state)
    record = workspace._read(paths['state'])
    record['status'] = 'ERROR'
    workspace._write(paths['state'], record)
    paths['report'].write_text('new complete failure details', encoding='utf-8')
    workspace._render_summary(run_dir, state)
    report = (run_dir / 'REPORT.md').read_text(encoding='utf-8')
    assert 'Status: **ERROR**' in report
    assert 'new complete failure details' in report and 'old details' not in report


@pytest.mark.parametrize('change', ['layout', 'identity', 'unselected_link'])
def test_summary_revalidates_latest_after_reading_snapshot(root, monkeypatch, change):
    operation = begin('api-latest', business_task_id='READGUARD', submitted_at=1)
    run_dir = Path(operation['run_dir'])
    paths = workspace.operation_paths(run_dir, operation['task_id'])
    paths['report'].write_text('complete original details', encoding='utf-8')
    state = workspace._read(run_dir / '.info/run_state.json')
    workspace._render_summary(run_dir, state)
    before = (run_dir / 'REPORT.md').read_bytes()
    original_read = workspace._read
    injected = False
    expected = {'layout': 'WORKSPACE_OPERATION_CONFLICT',
                'identity': 'WORKSPACE_STATE_INVALID',
                'unselected_link': 'WORKSPACE_PATH_INVALID'}[change]

    def read(path):
        nonlocal injected
        value = original_read(path)
        if Path(path) == paths['state'] and not injected:
            injected = True
            if change == 'layout':
                (run_dir / '.info/api-latest_manifest.json').write_text('{}', encoding='utf-8')
            elif change == 'identity':
                workspace._write(paths['state'], {**value, 'task_id': 'foreign-owner'})
            else:
                outside = root / 'outside-latest-manifest.json'
                outside.write_text('foreign data', encoding='utf-8')
                symlink_or_skip(run_dir / '.info/api-latest_manifest.json', outside)
        return value

    monkeypatch.setattr(workspace, '_read', read)
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace._render_summary(run_dir, state)
    assert injected and error.value.code == expected
    assert (run_dir / 'REPORT.md').read_bytes() == before
