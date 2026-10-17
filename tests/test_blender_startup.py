import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cgi_pipeline.hosts.blender import native_bridge


STARTUP = Path(__file__).resolve().parents[1] / 'deploy/blender/scripts/startup/cgi_tools_path.py'


def _load_startup(monkeypatch, *, background=False):
    spec = importlib.util.spec_from_file_location('test_cgi_tools_startup', STARTUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(app=SimpleNamespace(background=background)))
    return module


def test_gui_startup_starts_native_bridge_and_unregister_stops_it(monkeypatch, capsys):
    module = _load_startup(monkeypatch)
    start = Mock(return_value={'port': 9878})
    stop = Mock()
    monkeypatch.setattr(native_bridge, 'start', start)
    monkeypatch.setattr(native_bridge, 'stop', stop)
    module.register()
    module.register()
    module.unregister()
    module.unregister()
    assert start.call_count == 2  # The bridge owns idempotent lifecycle state.
    assert stop.call_count == 2
    assert all(call.args == () and call.kwargs == {} for call in start.call_args_list)
    assert 'CGI Blender foreground ready on port 9878' in capsys.readouterr().out


def test_background_startup_exposes_sdk_without_creating_listener(monkeypatch, capsys):
    module = _load_startup(monkeypatch, background=True)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(native_bridge, 'start', start)
    monkeypatch.setattr(native_bridge, 'stop', stop)
    module.register()
    module.unregister()
    start.assert_not_called()
    stop.assert_not_called()
    assert 'CGI tools path ready' in capsys.readouterr().out


def test_missing_shared_source_fails_before_starting_bridge(monkeypatch, tmp_path):
    module = _load_startup(monkeypatch)
    monkeypatch.setattr(module, '_SOURCE', str(tmp_path / 'missing'))
    start = Mock()
    monkeypatch.setattr(native_bridge, 'start', start)
    with pytest.raises(RuntimeError, match='Shared CGI source is unavailable'):
        module.register()
    start.assert_not_called()


def test_shadowed_sdk_is_rejected_before_starting_bridge(monkeypatch, tmp_path):
    module = _load_startup(monkeypatch)
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda _: SimpleNamespace(origin=str(tmp_path / '__init__.py')))
    start = Mock()
    monkeypatch.setattr(native_bridge, 'start', start)
    with pytest.raises(RuntimeError, match='Another CGI SDK shadows'):
        module.register()
    start.assert_not_called()


def test_startup_does_not_hide_bridge_initialization_failure(monkeypatch):
    module = _load_startup(monkeypatch)
    monkeypatch.setattr(native_bridge, 'start', Mock(side_effect=RuntimeError('timer registration failed')))
    with pytest.raises(RuntimeError, match='timer registration failed'):
        module.register()
