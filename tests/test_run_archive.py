"""Both operation layouts stay isolated and retain existing archive semantics."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cgi_pipeline.core import run_archive, workflow_engine, workspace
from cgi_pipeline.server.models import ExecuteWorkflowInput
from test_workspace import as_flat, begin, root
from test_workspace_execution import runtime


def manifest(task_id, directory):
    return Path(run_archive.write_manifest(
        task_id, 'fixture-workflow', 'asset', 'default', 'SUCCESS', 0,
        {}, {'task': task_id}, [], run_dir=directory))


@pytest.mark.parametrize('layout', ['legacy', 'flat'])
def test_manifest_publications_keep_each_task_independent(root, layout):
    first = begin('wf-first')
    second = begin('wf-second', workspace_id=first['workspace_id'])
    if layout == 'flat':
        first, second = as_flat(first), as_flat(second)
    with ThreadPoolExecutor(max_workers=2) as pool:
        files = list(pool.map(lambda row: manifest(row['task_id'], row['operation_dir']), [first, second]))
    assert set(files) == {workspace.operation_paths(row['run_dir'], row['task_id'])['manifest'] for row in (first, second)}
    assert [json.loads(path.read_text(encoding='utf-8'))['wf_id'] for path in files] == ['wf-first', 'wf-second']
    assert len(set(files)) == 2
    if layout == 'flat':
        assert all(path.parent == Path(first['run_dir']) / '.info' for path in files)
        assert not (files[0].parent / 'manifest.json').exists()


def test_explicit_old_operation_and_implicit_run_keep_legacy_filenames(tmp_path, monkeypatch):
    old = tmp_path / 'old' / '.info' / 'operations' / 'wf-old'
    old.mkdir(parents=True)
    assert manifest('wf-old', old) == old / 'manifest.json'
    ordinary = tmp_path / 'ordinary'
    ordinary.mkdir()
    monkeypatch.setattr(run_archive, 'create_run_dir', lambda *args, **kwargs: ordinary)
    assert manifest('wf-run', None) == ordinary / 'manifest.json'
    source = tmp_path / 'source.md'
    source.write_text('legacy', encoding='utf-8')
    monkeypatch.setattr(run_archive, 'get_run_dir', lambda task_id: ordinary)
    assert Path(run_archive.copy_to_run_dir('wf-run', str(source))) == ordinary / 'source.md'
    assert Path(run_archive.copy_to_run_dir('wf-old', str(source), run_dir=old)) == old / 'source.md'


def test_manifest_preserves_legacy_path_object_serialization(tmp_path):
    report = tmp_path / 'details.md'
    path = run_archive.write_manifest(
        'wf-paths', 'fixture', 'asset', 'default', 'SUCCESS', 0,
        {'source': report}, {'target': report}, [], run_dir=tmp_path)
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    assert data['inputs']['source'] == str(report)
    assert data['outputs']['target'] == str(report)


@pytest.mark.parametrize('layout', ['legacy', 'flat'])
def test_copies_are_task_isolated_and_prefixed_only_for_flat_layout(root, tmp_path, layout):
    first = begin('wf-first')
    second = begin('wf-second', workspace_id=first['workspace_id'])
    if layout == 'flat':
        first, second = as_flat(first), as_flat(second)
    source = tmp_path / 'details.md'
    source.write_text('first details', encoding='utf-8')
    first_copy = Path(run_archive.copy_to_run_dir('wf-first', str(source), run_dir=first['operation_dir']))
    source.write_text('second details', encoding='utf-8')
    second_copy = Path(run_archive.copy_to_run_dir('wf-second', str(source), run_dir=second['operation_dir']))
    assert first_copy != second_copy
    assert first_copy.name == ('wf-first_details.md' if layout == 'flat' else 'details.md')
    assert second_copy.name == ('wf-second_details.md' if layout == 'flat' else 'details.md')
    assert first_copy.read_text(encoding='utf-8') == 'first details'
    assert second_copy.read_text(encoding='utf-8') == 'second details'
    repeated = run_archive.copy_to_run_dir('wf-first', str(first_copy), run_dir=first['operation_dir'])
    assert Path(repeated) == first_copy


@pytest.mark.parametrize('filename', ['../escaped.md', 'nested/report.md', 'nested\\report.md', 'C:escaped.md'])
def test_copy_rejects_filename_escape_before_writing(root, tmp_path, filename):
    payload = begin('wf-safe')
    source = tmp_path / 'details.md'
    source.write_text('safe', encoding='utf-8')
    with pytest.raises(ValueError, match='filename'):
        run_archive.copy_to_run_dir('wf-safe', str(source), filename, run_dir=payload['operation_dir'])
    assert not (Path(payload['run_dir']) / 'escaped.md').exists()


@pytest.mark.parametrize('layout', ['legacy', 'flat'])
def test_archive_rejects_linked_manifest_or_copy_target(root, tmp_path, monkeypatch, layout):
    payload = begin('wf-links')
    if layout == 'flat':
        as_flat(payload)
    source = tmp_path / 'details.md'
    source.write_text('safe', encoding='utf-8')
    directory = Path(payload['operation_dir'])
    linked = {workspace.operation_paths(payload['run_dir'], 'wf-links')['manifest'],
              directory / ('wf-links_details.md' if layout == 'flat' else 'details.md')}
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path in linked or original(path))
    with pytest.raises(ValueError, match='link|local'):
        manifest('wf-links', directory)
    with pytest.raises(ValueError, match='link'):
        run_archive.copy_to_run_dir('wf-links', str(source), run_dir=directory)


@pytest.mark.parametrize('steps,fields', [
    (('pre','post'), ('report_path','report_path')),
    (('a'*80+'x','a'*80+'y'), ('report_path','report_path')),
    (('a_b','a'), ('c','b_c')),
    (('a/b','a\\b'), ('report_path','report_path')),
])
def test_workflow_archives_same_basename_reports_by_step_and_field(runtime, monkeypatch, tmp_path, steps, fields):
    sources = []
    for name in ('pre', 'post'):
        source = tmp_path / name / 'details.md'
        source.parent.mkdir()
        source.write_text(name + '-details', encoding='utf-8')
        sources.append(source)
    original = runtime.worker.run_api
    calls = []

    def with_report(payload):
        receipt = original(payload)
        receipt['output'][fields[len(calls)]] = str(sources[len(calls)])
        calls.append(payload)
        return receipt

    runtime.worker.run_api = with_report
    monkeypatch.setattr(workflow_engine, 'load_workflow', lambda _: {'steps': [
        {'step_id': steps[0], 'api_id': 'maya.scene.selection.get'},
        {'step_id': steps[1], 'api_id': 'maya.scene.selection.get'},
    ]})
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='archive-reports', source_path=str(runtime.source))))
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    task_id = result['task_id']
    directory = Path(result['operation_dir'])
    data = json.loads(workspace.operation_paths(result['run_dir'], task_id)['manifest'].read_text(encoding='utf-8'))
    assert len(data['reports']) == 3  # Main report plus two independent attached reports.
    attached=[Path(p) for p in data['reports'] if Path(p).name.endswith('_details.md')]
    assert len(set(attached))==2
    assert {p.read_text(encoding='utf-8') for p in attached}=={'pre-details','post-details'}


@pytest.mark.parametrize('layout', ['legacy', 'flat'])
@pytest.mark.parametrize('reserved', ['state', 'result', 'report', 'manifest'])
def test_attached_copy_cannot_overwrite_an_operation_record(root, tmp_path, layout, reserved):
    payload=begin('wf-safe')
    if layout == 'flat':
        as_flat(payload)
    report=workspace.operation_paths(payload['run_dir'], payload['task_id'])[reserved]
    if reserved != 'state':
        report.write_text('main operation record',encoding='utf-8')
    before=report.read_bytes()
    source=tmp_path/'extra.md';source.write_text('attachment',encoding='utf-8')
    with pytest.raises(ValueError,match='reserved'):
        run_archive.copy_to_run_dir('wf-safe',str(source),report.name,run_dir=payload['operation_dir'])
    assert report.read_bytes()==before
