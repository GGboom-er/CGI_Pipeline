"""Operation reports publish atomically before refreshing the workspace view."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from cgi_pipeline.core import task_report_writer as writer, workspace
from test_workspace import begin, root


def test_registered_report_updates_root_with_complete_step_details(root):
    payload = begin('api-report')
    report = Path(payload['report_path'])
    writer.init_report(report, payload)
    step = {'step_index': 0, 'step_total': 1, 'api_id': 'fixture.api'}
    writer.upsert_step_started(report, step)
    writer.upsert_step_finished(report, step, {
        'api_id': 'fixture.api', 'status': 'SUCCESS',
        'output': {'result': {'verified': 'complete-details'}},
    })
    writer.finalize_report(report, payload, 'SUCCESS')
    text = Path(payload['workspace_report_path']).read_text(encoding='utf-8')
    assert 'Step 1/1' in text and 'complete-details' in text
    assert '## Final Status' in text and 'SUCCESS' in text
    assert '<!-- report:block:' not in text


def test_refresh_occurs_only_after_report_lock_is_released(tmp_path, monkeypatch):
    report = tmp_path / 'api-test_REPORT.md'
    observed = []

    def refresh(path):
        assert not Path(str(path) + '.lock').exists()
        observed.append(Path(path).read_text(encoding='utf-8'))

    monkeypatch.setattr(workspace, 'refresh_report_for_path', refresh)
    writer.init_report(report, {'task_id': 'api-test'})
    writer.upsert_block(report, 'step', 'published-step')
    writer.finalize_report(report, {'task_id': 'api-test'}, 'ERROR', error='visible-error')
    assert observed and 'published-step' in observed[-1]
    assert 'visible-error' in observed[-1]
    assert '<!-- report:block:' not in observed[-1]


def test_atomic_publication_never_writes_the_visible_destination(tmp_path, monkeypatch):
    report = tmp_path / 'api-atomic_REPORT.md'
    original_write = Path.write_text

    def guarded_write(path, *args, **kwargs):
        assert path != report, 'visible report must only be replaced atomically'
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', guarded_write)
    writer.init_report(report, {'task_id': 'api-atomic'})
    writer.upsert_block(report, 'step', 'atomic-details')
    writer.finalize_report(report, {'task_id': 'api-atomic'}, 'SUCCESS')
    assert 'atomic-details' in report.read_text(encoding='utf-8')
    assert not list(tmp_path.glob('api-atomic_REPORT.md.*.tmp'))


def test_failed_publish_preserves_previous_report_and_cleans_owned_temp(tmp_path, monkeypatch):
    report = tmp_path / 'api-failure_REPORT.md'
    report.write_text('previous-complete-report\n', encoding='utf-8')
    original_replace = Path.replace

    def failed_replace(path, target):
        if Path(target) == report:
            raise OSError('fixture publication failure')
        return original_replace(path, target)

    monkeypatch.setattr(Path, 'replace', failed_replace)
    with pytest.raises(OSError, match='publication failure'):
        writer.upsert_block(report, 'step', 'new-details')
    assert report.read_text(encoding='utf-8') == 'previous-complete-report\n'
    assert not list(tmp_path.glob('api-failure_REPORT.md.*.tmp'))
    assert not Path(str(report) + '.lock').exists()


def test_concurrent_blocks_and_reinitialization_keep_all_steps(tmp_path):
    report = tmp_path / 'api-concurrent_REPORT.md'
    writer.init_report(report, {'task_id': 'api-concurrent'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: writer.upsert_block(
            report, f'step-{index}', f'unique-step-{index:02d}'), range(24)))
    writer.init_report(report, {'task_id': 'api-concurrent'})
    text = report.read_text(encoding='utf-8')
    assert all(text.count(f'unique-step-{index:02d}') == 1 for index in range(24))


@pytest.mark.parametrize('target', ['report', 'lock'])
def test_linked_output_or_lock_is_rejected_before_write(tmp_path, monkeypatch, target):
    report = tmp_path / 'api-link_REPORT.md'
    linked = report if target == 'report' else Path(str(report) + '.lock')
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == linked or original(path))
    with pytest.raises(ValueError, match='link'):
        writer.upsert_block(report, 'step', 'not-published')
    assert not report.exists()


def test_init_publishes_and_refreshes_one_complete_report_per_call(tmp_path, monkeypatch):
    report = tmp_path / 'api-init_REPORT.md'
    published = []
    refreshed = []
    original_publish = writer._publish_text

    def publish(path, text):
        assert Path(str(path) + '.lock').exists()
        original_publish(path, text)
        published.append(text)

    def refresh(path):
        assert not Path(str(path) + '.lock').exists()
        text = Path(path).read_text(encoding='utf-8')
        assert text.index('Task ID') < text.index('## Execution') < text.index('## Final Status')
        assert text.count('<!-- report:block:start header -->') == 1
        assert text.count('<!-- report:block:start final -->') == 1
        refreshed.append(text)

    monkeypatch.setattr(writer, '_publish_text', publish)
    monkeypatch.setattr(workspace, 'refresh_report_for_path', refresh)
    context = {'task_id': 'api-init', 'asset_name': 'existing-asset'}
    assert writer.init_report(report, context) == str(report)
    assert len(published) == len(refreshed) == 1
    writer.upsert_block(report, 'step', 'preserved-step')
    published.clear()
    refreshed.clear()
    writer.init_report(report, context)
    assert len(published) == len(refreshed) == 1
    assert refreshed[0].count('preserved-step') == 1
    assert refreshed[0].startswith('# existing-asset Task Report\n')


def test_finalize_publishes_and_refreshes_only_the_complete_final_report(tmp_path, monkeypatch):
    report = tmp_path / 'api-final_REPORT.md'
    context = {'task_id': 'api-final'}
    writer.init_report(report, context)
    writer.upsert_block(report, 'step', 'retained-step-details')
    published = []
    refreshed = []
    original_publish = writer._publish_text

    def publish(path, text):
        assert Path(str(path) + '.lock').exists()
        original_publish(path, text)
        published.append(text)

    def refresh(path):
        assert not Path(str(path) + '.lock').exists()
        text = Path(path).read_text(encoding='utf-8')
        assert '<!-- report:block:' not in text
        assert 'RUNNING' not in text
        assert text.count('ERROR') == 2
        assert 'retained-step-details' in text and 'visible-final-error' in text
        assert 'visible-traceback' in text and 'Total Elapsed' in text
        refreshed.append(text)

    monkeypatch.setattr(writer, '_publish_text', publish)
    monkeypatch.setattr(workspace, 'refresh_report_for_path', refresh)
    writer.finalize_report(report, context, 'ERROR', 0.5,
                           'visible-final-error', 'visible-traceback')
    assert len(published) == len(refreshed) == 1


def test_standalone_block_updates_keep_insertion_replacement_and_refresh_contract(tmp_path, monkeypatch):
    report = tmp_path / 'api-block_REPORT.md'
    refreshed = []
    monkeypatch.setattr(workspace, 'refresh_report_for_path', lambda path: refreshed.append(path))
    writer.upsert_block(report, 'final', 'final-content', before_block_id='__never__')
    writer.upsert_block(report, 'step', 'old-step')
    writer.upsert_block(report, 'step', r'new-step C:\task\result')
    assert len(refreshed) == 3
    text = report.read_text(encoding='utf-8')
    assert 'old-step' not in text
    assert text.count(r'new-step C:\task\result') == 1
    assert text.index('new-step') < text.index('final-content')
    writer._strip_block_markers(report)
    assert len(refreshed) == 4
    text = report.read_text(encoding='utf-8')
    assert '<!-- report:block:' not in text
    assert r'new-step C:\task\result' in text and 'final-content' in text
    writer._strip_block_markers(tmp_path / 'missing_REPORT.md')
    assert len(refreshed) == 4


@pytest.mark.parametrize('operation', ['init', 'finalize'])
def test_combined_update_publish_failure_is_atomic_and_does_not_refresh(tmp_path, monkeypatch, operation):
    report = tmp_path / 'api-publish-failure_REPORT.md'
    context = {'task_id': 'api-publish-failure'}
    writer.init_report(report, context)
    writer.upsert_block(report, 'step', 'previous-complete-step')
    previous = report.read_bytes()
    refreshed = []
    monkeypatch.setattr(workspace, 'refresh_report_for_path', lambda path: refreshed.append(path))
    original_replace = Path.replace

    def fail_report_replace(path, target):
        if Path(target) == report:
            raise OSError('fixture combined publication failure')
        return original_replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail_report_replace)
    with pytest.raises(OSError, match='combined publication failure'):
        if operation == 'init':
            writer.init_report(report, context)
        else:
            writer.finalize_report(report, context, 'ERROR', error='not-partially-visible')
    assert report.read_bytes() == previous
    assert refreshed == []
    assert not Path(str(report) + '.lock').exists()
    assert not list(tmp_path.glob('api-publish-failure_REPORT.md.*.tmp'))


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('operation', ['init', 'finalize'])
def test_combined_render_failure_cannot_publish_a_partial_report(tmp_path, monkeypatch, existing, operation):
    report = tmp_path / 'api-render-failure_REPORT.md'
    previous = b'existing user notes\n'
    if existing:
        report.write_bytes(previous)
    refreshed = []
    monkeypatch.setattr(workspace, 'refresh_report_for_path', lambda path: refreshed.append(path))

    def render_failure(*args, **kwargs):
        raise ValueError('fixture final rendering failure')

    monkeypatch.setattr(writer, 'render_final', render_failure)
    with pytest.raises(ValueError, match='final rendering failure'):
        if operation == 'init':
            writer.init_report(report, {'task_id': 'api-render-failure'})
        else:
            writer.finalize_report(report, {'task_id': 'api-render-failure'}, 'ERROR')
    if existing:
        assert report.read_bytes() == previous
    else:
        assert not report.exists()
    assert refreshed == []
    assert not Path(str(report) + '.lock').exists()
    assert not list(tmp_path.glob('api-render-failure_REPORT.md.*.tmp'))


def test_lock_pid_write_failure_releases_descriptor_and_owned_lock(tmp_path, monkeypatch):
    report = tmp_path / 'api-lock-write_REPORT.md'
    closed = []
    original_close = writer.os.close

    def write_failure(*args):
        raise OSError('fixture lock pid write failure')

    def close(fd):
        closed.append(fd)
        return original_close(fd)

    monkeypatch.setattr(writer.os, 'write', write_failure)
    monkeypatch.setattr(writer.os, 'close', close)
    with pytest.raises(OSError, match='lock pid write failure'):
        writer.init_report(report, {'task_id': 'api-lock-write'})
    assert len(closed) == 1
    with pytest.raises(OSError):
        writer.os.fstat(closed[0])
    assert not report.exists()
    assert not Path(str(report) + '.lock').exists()


def test_finalization_serializes_with_concurrent_step_update(tmp_path, monkeypatch):
    report = tmp_path / 'api-final-concurrent_REPORT.md'
    context = {'task_id': 'api-final-concurrent'}
    writer.init_report(report, context)
    writer.upsert_block(report, 'step-before', 'preserved-original-step')
    previous = report.read_bytes()
    final_ready = Event()
    allow_final_publish = Event()
    step_attempting_lock = Event()
    original_publish = writer._publish_text
    original_lock = writer._locked_file
    publications = []

    def publish(path, text):
        if 'visible-final-error' in text and not final_ready.is_set():
            final_ready.set()
            assert allow_final_publish.wait(10), 'fixture final publication was not released'
        original_publish(path, text)
        publications.append(text)

    def step_update():
        step_attempting_lock.set()
        writer.upsert_block(report, 'step-after', 'preserved-concurrent-step')

    def refresh(path):
        # Another writer may already own the lock by the time this callback
        # runs. Reacquiring proves this thread did not retain its own lock.
        with original_lock(Path(path)):
            assert 'preserved-original-step' in Path(path).read_text(encoding='utf-8')

    monkeypatch.setattr(writer, '_publish_text', publish)
    monkeypatch.setattr(workspace, 'refresh_report_for_path', refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        final = pool.submit(writer.finalize_report, report, context, 'ERROR',
                            error='visible-final-error')
        try:
            assert final_ready.wait(10)
            step = pool.submit(step_update)
            assert step_attempting_lock.wait(10)
            assert not step.done()
            assert report.read_bytes() == previous
        finally:
            allow_final_publish.set()
        final.result(timeout=10)
        step.result(timeout=10)
    assert len(publications) == 2
    assert '<!-- report:block:' not in publications[0]
    text = report.read_text(encoding='utf-8')
    assert text.count('preserved-original-step') == 1
    assert text.count('preserved-concurrent-step') == 1
    assert text.count('visible-final-error') == 1
