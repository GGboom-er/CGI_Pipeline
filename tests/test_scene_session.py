from types import SimpleNamespace
from pathlib import Path
import json
import os

import pytest

from cgi_pipeline.core import dcc_factory, scene_session, workspace
from test_workspace import root, begin


def runtime(monkeypatch):
    worker = SimpleNamespace(worker_id='worker-1', process=None)
    events, created, opened = [], [], []
    monkeypatch.setattr(dcc_factory, '_warm_pool', {'maya': {'worker': worker, 'task_count': 0}})

    def create(*args, **kwargs):
        created.append((args, kwargs))
        return worker

    def open_scene(*args):
        opened.append(args)
        return True, ''

    return worker, created, opened, lambda *args: events.append(args), create, open_scene


def test_continue_preserves_in_memory_edits_without_copy_or_open(root, monkeypatch):
    worker, created, opened, audit, create, open_scene = runtime(monkeypatch)
    source = root / 'input.ma'
    source.write_text('source scene')
    first = begin('api-first', source_path=str(source))
    _, staged = scene_session.prepare_scene(first, 'maya', create, open_scene, audit)
    worker.scene_value = 'unsaved edit'
    second = begin('api-second', workspace_id=first['workspace_id'])
    _, continued = scene_session.prepare_scene(second, 'maya', create, open_scene, audit)
    assert staged == continued
    assert len(opened) == 1 and len(created) == 2
    assert second['scene_session'] == {'status': 'REUSED', 'staged': False, 'opened': False, 'worker_id': 'worker-1'}
    assert worker.scene_value == 'unsaved edit'
    assert len(list(Path(first['run_dir']).glob('*.ma'))) == 1


def test_lost_scene_never_implicitly_recreates_or_replays(root, monkeypatch):
    worker, created, opened, audit, create, open_scene = runtime(monkeypatch)
    first = begin('api-first')
    scene_session.prepare_scene(first, 'maya', create, open_scene, audit)
    scene_session.invalidate_scene(worker)
    second = begin('api-second', workspace_id=first['workspace_id'])
    with pytest.raises(workspace.WorkspaceError, match='no longer resident'):
        scene_session.prepare_scene(second, 'maya', create, open_scene, audit)
    assert len(created) == len(opened) == 1
    second['scene_action'] = 'open'
    scene_session.prepare_scene(second, 'maya', create, open_scene, audit)
    assert len(opened) == 2


def test_failed_initialization_is_not_silently_retried(root, monkeypatch):
    worker, created, opened, audit, create, _ = runtime(monkeypatch)
    first = begin('api-first')
    with pytest.raises(workspace.WorkspaceError, match='cannot open'):
        scene_session.prepare_scene(first, 'maya', create, lambda *a: (False, 'cannot open'), audit)
    second = begin('api-second', workspace_id=first['workspace_id'])
    with pytest.raises(workspace.WorkspaceError, match='no longer resident'):
        scene_session.prepare_scene(second, 'maya', create, lambda *a: (True, ''), audit)
    assert len(created) == 1


def test_copy_failure_never_opens_the_original(root, monkeypatch):
    worker, created, opened, audit, create, open_scene = runtime(monkeypatch)
    source = root / 'input.ma'
    source.write_text('source')
    first = begin('api-first', source_path=str(source))
    def fail(*args):
        raise OSError('copy denied')
    monkeypatch.setattr(scene_session.shutil, 'copy2', fail)
    with pytest.raises(workspace.WorkspaceError, match='original will not be opened'):
        scene_session.prepare_scene(first, 'maya', create, open_scene, audit)
    assert created == opened == []


def test_new_source_requires_explicit_open_and_preserves_colliding_snapshot(root, monkeypatch):
    _, _, opened, audit, create, open_scene = runtime(monkeypatch)
    a, b = root / 'a', root / 'b'
    a.mkdir()
    b.mkdir()
    (a / 'input.ma').write_text('A')
    (b / 'input.ma').write_text('B')
    first = begin('api-first', source_path=str(a / 'input.ma'))
    _, first_staged = scene_session.prepare_scene(first, 'maya', create, open_scene, audit)
    second = begin('api-second', workspace_id=first['workspace_id'], source_path=str(b / 'input.ma'))
    with pytest.raises(workspace.WorkspaceError, match='explicitly replace'):
        scene_session.prepare_scene(second, 'maya', create, open_scene, audit)
    second['scene_action'] = 'open'
    _, path = scene_session.prepare_scene(second, 'maya', create, open_scene, audit)
    assert Path(path).read_text() == 'B'
    assert Path(first_staged).read_text() == 'A'
    assert len(opened) == 2


def test_worker_replaced_during_acquisition_cannot_be_reported_reused(root, monkeypatch):
    _, _, opened, audit, create, open_scene = runtime(monkeypatch)
    first = begin('api-first')
    scene_session.prepare_scene(first, 'maya', create, open_scene, audit)
    second = begin('api-second', workspace_id=first['workspace_id'])
    replacement = SimpleNamespace(worker_id='worker-2', process=None)
    def replace_worker(*args, **kwargs):
        dcc_factory._warm_pool['maya']['worker'] = replacement
        return replacement
    with pytest.raises(workspace.WorkspaceError, match='changed during acquisition'):
        scene_session.prepare_scene(second, 'maya', replace_worker, open_scene, audit)
    assert len(opened) == 1
    assert 'scene_session' not in second


def test_stage_records_external_source_and_identical_reuse(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    staged, copied = scene_session._stage(str(source), run_dir)
    origins_file = run_dir / '.info' / 'staged_origins.json'
    expected = {Path(staged).name: source.parent.resolve().as_posix()}
    assert copied is True
    assert json.loads(origins_file.read_text(encoding='utf-8')) == expected
    reused, copied = scene_session._stage(str(source), run_dir)
    assert reused == staged and copied is False
    assert json.loads(origins_file.read_text(encoding='utf-8')) == expected


def test_stage_unknown_legacy_origin_is_not_claimed_by_identical_source(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    legacy = run_dir / source.name
    legacy.write_text('asset')
    staged, copied = scene_session._stage(str(source), run_dir)
    assert Path(staged) != legacy and copied is True
    origins = json.loads((run_dir / '.info' / 'staged_origins.json').read_text(encoding='utf-8'))
    assert legacy.name not in origins
    assert origins[Path(staged).name] == source.parent.resolve().as_posix()
    assert legacy.read_text() == 'asset'


@pytest.mark.parametrize('registered_origin', [None, 'unrelated'])
def test_stage_existing_snapshot_requires_matching_provenance(tmp_path, registered_origin):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    first, _ = scene_session._stage(str(source), run_dir)
    first_name = Path(first).name
    origins_file = run_dir / '.info' / 'staged_origins.json'
    previous = {} if registered_origin is None else {
        first_name: (tmp_path / registered_origin).resolve().as_posix()
    }
    origins_file.write_text(json.dumps(previous), encoding='utf-8')
    staged, copied = scene_session._stage(str(source), run_dir)
    assert staged != first and copied is True
    assert Path(first).read_text() == 'asset'
    origins = json.loads(origins_file.read_text(encoding='utf-8'))
    assert origins.get(first_name) == previous.get(first_name)
    assert origins[Path(staged).name] == source.parent.resolve().as_posix()


def test_stage_collision_records_actual_target_name_and_merges_origins(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('new asset')
    run_dir = tmp_path / 'workspace'
    (run_dir / '.info').mkdir(parents=True)
    (run_dir / source.name).write_text('different previous asset')
    origins_file = run_dir / '.info' / 'staged_origins.json'
    previous = {'character.blend': (tmp_path / 'previous').as_posix()}
    origins_file.write_text(json.dumps(previous), encoding='utf-8')
    staged, copied = scene_session._stage(str(source), run_dir)
    assert Path(staged).name != source.name and copied is True
    assert (run_dir / source.name).read_text() == 'different previous asset'
    expected = dict(previous, **{Path(staged).name: source.parent.resolve().as_posix()})
    assert json.loads(origins_file.read_text(encoding='utf-8')) == expected


def test_stage_internal_source_preserves_external_origin_without_rewriting(tmp_path):
    run_dir = tmp_path / 'workspace'
    (run_dir / '.info').mkdir(parents=True)
    source = run_dir / 'character.blend'
    source.write_text('asset')
    origins_file = run_dir / '.info' / 'staged_origins.json'
    original_text = json.dumps({source.name: (tmp_path / 'original').as_posix()}, indent=4)
    origins_file.write_text(original_text, encoding='utf-8')
    assert scene_session._stage(str(source), run_dir) == (str(source.resolve()), False)
    assert origins_file.read_text(encoding='utf-8') == original_text


def test_identical_external_sources_keep_independent_relative_texture_origins(tmp_path):
    first, second, run_dir = tmp_path / 'first', tmp_path / 'second', tmp_path / 'workspace'
    for directory in (first, second, run_dir):
        directory.mkdir()
    for directory in (first, second):
        (directory / 'character.blend').write_text('//textures/diffuse.png')
        (directory / 'textures').mkdir()
    (first / 'textures' / 'diffuse.png').write_text('first texture')
    (second / 'textures' / 'diffuse.png').write_text('second texture')
    staged, _copied = scene_session._stage(str(first / 'character.blend'), run_dir)
    reused, copied = scene_session._stage(str(second / 'character.blend'), run_dir)
    assert reused != staged and copied is True
    origins = json.loads((run_dir / '.info' / 'staged_origins.json').read_text(encoding='utf-8'))
    assert origins[Path(staged).name] == first.resolve().as_posix()
    assert origins[Path(reused).name] == second.resolve().as_posix()
    assert (Path(origins[Path(staged).name]) / 'textures' / 'diffuse.png').read_text() == 'first texture'
    assert (Path(origins[Path(reused).name]) / 'textures' / 'diffuse.png').read_text() == 'second texture'
    # Reopening A after B must select A's snapshot without changing either origin.
    previous_metadata = (run_dir / '.info' / 'staged_origins.json').read_bytes()
    staged_again, copied_again = scene_session._stage(str(first / 'character.blend'), run_dir)
    assert staged_again == staged and copied_again is False
    assert (run_dir / '.info' / 'staged_origins.json').read_bytes() == previous_metadata


def test_stage_normalized_source_alias_reuses_same_snapshot(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    staged, _ = scene_session._stage(str(source), run_dir)
    alias = source.parent / '..' / source.parent.name / source.name
    assert scene_session._stage(str(alias), run_dir) == (staged, False)


def test_stage_distinguishes_original_filename_from_existing_collision_alias(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    source = original / 'character.blend'
    alias_source = original / 'character_input_001.blend'
    source.write_text('asset')
    alias_source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    (run_dir / source.name).write_text('old collision')
    staged, _ = scene_session._stage(str(source), run_dir)
    other_staged, other_copied = scene_session._stage(str(alias_source), run_dir)
    assert other_staged != staged and other_copied is True
    assert scene_session._stage(str(source), run_dir) == (staged, False)
    assert scene_session._stage(str(alias_source), run_dir) == (other_staged, False)
    assert (run_dir / source.name).read_text() == 'old collision'


def test_stage_content_changes_keep_prior_snapshot_and_reuse_matching_version(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('first contents')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    first, copied = scene_session._stage(str(source), run_dir)
    assert copied is True
    source.write_text('new contents')
    second, copied = scene_session._stage(str(source), run_dir)
    assert second != first and copied is True
    assert Path(first).read_text() == 'first contents'
    assert scene_session._stage(str(source), run_dir) == (second, False)
    source.write_text('first contents')
    assert scene_session._stage(str(source), run_dir) == (first, False)


def test_stage_content_change_with_preserved_stat_does_not_reuse_cached_comparison(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('old asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    first, _ = scene_session._stage(str(source), run_dir)
    assert scene_session._stage(str(source), run_dir) == (first, False)
    previous_stat = source.stat()
    source.write_text('new asset')
    os.utime(source, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    second, copied = scene_session._stage(str(source), run_dir)
    assert second != first and copied is True
    assert Path(first).read_text() == 'old asset'
    assert Path(second).read_text() == 'new asset'


@pytest.mark.skipif(os.name != 'nt', reason='Windows filesystem path identity')
def test_stage_windows_case_alias_reuses_without_rewriting_origin(tmp_path):
    source = tmp_path / 'original' / 'character.blend'
    source.parent.mkdir()
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    run_dir.mkdir()
    staged, _ = scene_session._stage(str(source), run_dir)
    origins_file = run_dir / '.info' / 'staged_origins.json'
    previous = origins_file.read_bytes()
    alias = str(source).swapcase()
    reused, copied = scene_session._stage(alias, run_dir)
    assert Path(reused) == Path(staged) and copied is False
    assert origins_file.read_bytes() == previous


def test_generated_workspace_input_does_not_invent_external_origin(tmp_path):
    source = tmp_path / 'generated.blend'
    source.write_text('generated in this workspace')
    assert scene_session._stage(str(source), tmp_path) == (str(source.resolve()), False)
    assert not (tmp_path / '.info' / 'staged_origins.json').exists()


@pytest.mark.parametrize('invalid', ['{broken', '[]', '{"scene.blend": 42}',
                                    '{"scene.blend": "relative/path"}',
                                    '{"../scene.blend": "C:/original"}'])
def test_stage_bad_origin_metadata_fails_before_copy(tmp_path, invalid):
    source = tmp_path / 'scene.blend'
    source.write_text('asset')
    run_dir = tmp_path / 'workspace'
    (run_dir / '.info').mkdir(parents=True)
    origins_file = run_dir / '.info' / 'staged_origins.json'
    origins_file.write_text(invalid, encoding='utf-8')
    with pytest.raises(workspace.WorkspaceError) as error:
        scene_session._stage(str(source), run_dir)
    assert error.value.code == 'WORKSPACE_STAGE_PROVENANCE_FAILED'
    assert not (run_dir / source.name).exists()
    assert origins_file.read_text(encoding='utf-8') == invalid


def test_origin_write_failure_never_opens_scene_or_reports_success(root, monkeypatch):
    _worker, created, opened, audit, create, open_scene = runtime(monkeypatch)
    source = root / 'original.blend'
    source.write_text('asset')
    payload = begin('api-origin-write-failure', source_path=str(source))
    original_write = workspace._write
    def fail_origin_only(path, value):
        if path.name == 'staged_origins.json':
            raise OSError('metadata write denied')
        original_write(path, value)
    monkeypatch.setattr(workspace, '_write', fail_origin_only)
    with pytest.raises(workspace.WorkspaceError) as error:
        scene_session.prepare_scene(payload, 'blender', create, open_scene, audit)
    assert error.value.code == 'WORKSPACE_STAGE_PROVENANCE_FAILED'
    assert created == opened == []
    assert 'scene_session' not in payload
    staged = list(Path(payload['run_dir']).glob('*.blend'))
    assert len(staged) == 1 and staged[0].read_text() == 'asset'
