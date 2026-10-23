"""Portable tool import boundaries and update reuse, without importing DCCs."""
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from cgi_pipeline import tools


@pytest.fixture
def package(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    module = source / "pl233_tool_sample.py"
    module.write_text("def show(): return 'first'\n", encoding="utf-8")
    row = {"package_id": "sample", "category": "test", "stages": ["animation"],
           "hosts": ["maya", "blender", "ue"], "source": {"type": "workspace", "path": str(source)},
           "python_paths": ["."], "entrypoints": {h: "pl233_tool_sample:show" for h in ("maya", "blender", "ue")}}
    path = tmp_path / "packages.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setattr(tools, "_CATALOG_PATH", path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    yield row, module, path
    sys.modules.pop("pl233_tool_sample", None)


@pytest.mark.parametrize("host", ["maya", "blender", "ue"])
def test_each_host_loads_source_and_explicit_reload_sees_edit(package, host):
    _, module, _ = package
    assert tools.open("sample", host=host) == "first"
    module.write_text("def show(): return 'second-updated'\n", encoding="utf-8")
    assert tools.open("sample", host=host) == "first"
    assert tools.open("sample", host=host, reload=True) == "second-updated"


def test_missing_host_or_package_fails_before_import(package):
    with pytest.raises(KeyError):
        tools.open("missing", host="maya")
    with pytest.raises(RuntimeError, match="no houdini entrypoint"):
        tools.open("sample", host="houdini")


def test_loaded_module_from_other_source_is_rejected(package, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "pl233_tool_sample.py").write_text("def show(): return 'wrong'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(other))
    importlib.import_module("pl233_tool_sample")
    with pytest.raises(RuntimeError, match="another source"):
        tools.open("sample", host="blender")


def test_non_callable_entrypoint_is_rejected(package):
    _, module, _ = package
    module.write_text("show = 7\n", encoding="utf-8")
    with pytest.raises(TypeError, match="not callable"):
        tools.open("sample", host="ue")


def test_addon_lifecycle_and_partial_state(package):
    row, module, path = package
    row["lifecycle"] = {"blender": "addon"}
    path.write_text(json.dumps([row]), encoding="utf-8")
    module.write_text("class A: is_registered = False\nclass B: is_registered = False\n"
                      "classes = [A, B]\ndef show():\n    A.is_registered = B.is_registered = True\n"
                      "def unregister():\n    A.is_registered = B.is_registered = False\n", encoding="utf-8")
    assert tools.open("sample", host="blender")["status"] == "registered"
    assert tools.open("sample", host="blender")["status"] == "already_registered"
    assert tools.open("sample", host="blender", reload=True)["status"] == "registered"
    assert tools.close("sample", host="blender")["status"] == "unregistered"
    imported = importlib.import_module("pl233_tool_sample")
    imported.A.is_registered = True
    with pytest.raises(RuntimeError, match="incomplete addon"):
        tools.open("sample", host="blender")


@pytest.mark.parametrize("host,version", [("maya", "2099"), ("blender", "9.0.0"), ("ue", "9.0.0")])
def test_unverified_versions_are_rejected_before_loading(package, monkeypatch, host, version):
    row, _, path = package
    row["versions"] = {host: ["5.0"]}
    path.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(cmds=SimpleNamespace(about=lambda **_: version)))
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(app=SimpleNamespace(version_string=version)))
    monkeypatch.setitem(sys.modules, "unreal", SimpleNamespace(SystemLibrary=SimpleNamespace(get_engine_version=lambda: version)))
    with pytest.raises(RuntimeError, match="not validated"):
        tools.open("sample", host=host)
