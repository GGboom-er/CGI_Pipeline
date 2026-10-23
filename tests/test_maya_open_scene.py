from __future__ import annotations

from cgi_pipeline.catalog import reload as reload_catalog
from cgi_pipeline.execution import execute_api
from cgi_pipeline.capabilities.maya.asset.maya_open_scene import maya_open_scene


class FakeCmds:
    def __init__(self):
        self.scene = "previous.ma"
        self.modified = True
        self.calls = []

    def file(self, *args, **kwargs):
        if kwargs.get("query") and kwargs.get("sceneName"):
            return self.scene
        if kwargs.get("query") and kwargs.get("modified"):
            return self.modified
        self.calls.append((args, kwargs))
        if self.modified and not kwargs.get("force"):
            raise RuntimeError("Unsaved changes")
        if kwargs.get("new"):
            self.scene = ""
        elif kwargs.get("open"):
            self.scene = str(args[0])
        self.modified = False
        return self.scene

    def ls(self, **kwargs):
        if kwargs.get("type") == "mesh":
            return ["|geo|bodyShape", "|geo|eyeShape"]
        if kwargs.get("type") == "joint":
            return ["|rig|root", "|rig|jaw", "|rig|eye"]
        return []


def _execute(monkeypatch, root, params, cmds=None):
    fake_cmds = cmds or FakeCmds()
    monkeypatch.setattr(maya_open_scene, "REPOSITORY_ROOT", root)
    monkeypatch.setattr(maya_open_scene, "_maya_cmds", lambda: fake_cmds)
    reload_catalog()
    receipt = execute_api(
        "maya.asset.maya_open_scene",
        params,
        context={"execution_mode": "local", "host": "maya"},
    )
    return receipt, fake_cmds


def test_registered_api_opens_sandbox_scene_and_reports_counts(tmp_path, monkeypatch):
    scene = tmp_path / "projects" / "ysj" / "task" / "face.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("// Maya ASCII", encoding="ascii")

    receipt, cmds = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": str(scene), "new_scene": False, "force": True},
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["scene"] == scene.resolve().as_posix()
    assert receipt["output"]["modified"] is False
    assert receipt["output"]["mesh_count"] == 2
    assert receipt["output"]["joint_count"] == 3
    assert cmds.calls == [((scene.resolve().as_posix(),), {"open": True, "force": True})]


def test_new_scene_ignores_file_path_and_uses_force(tmp_path, monkeypatch):
    receipt, cmds = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": "Z:/outside.ma", "new_scene": True, "force": True},
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["scene"] == ""
    assert cmds.calls == [((), {"new": True, "force": True})]


def test_open_scene_blocks_paths_outside_sandbox(tmp_path, monkeypatch):
    scene = tmp_path / "outside.ma"
    scene.write_text("// Maya ASCII", encoding="ascii")

    receipt, cmds = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": str(scene), "new_scene": False, "force": True},
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["output"]["blocked_target"] == scene.resolve().as_posix()
    assert cmds.calls == []


def test_open_scene_rejects_missing_and_non_maya_files(tmp_path, monkeypatch):
    sandbox = tmp_path / "projects" / "ysj" / "task"
    sandbox.mkdir(parents=True)
    text_file = sandbox / "face.txt"
    text_file.write_text("not a Maya scene", encoding="ascii")

    bad_format, _ = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": str(text_file), "new_scene": False, "force": True},
    )
    missing, _ = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": str(sandbox / "missing.mb"), "new_scene": False, "force": True},
    )

    assert bad_format["status"] == "ERROR"
    assert "仅支持 .ma 和 .mb" in bad_format["error"]
    assert missing["status"] == "ERROR"
    assert "场景文件不存在" in missing["error"]


def test_force_false_does_not_discard_modified_scene(tmp_path, monkeypatch):
    scene = tmp_path / "runs" / "task" / "face.mb"
    scene.parent.mkdir(parents=True)
    scene.write_bytes(b"FOR8")
    cmds = FakeCmds()

    receipt, cmds = _execute(
        monkeypatch,
        tmp_path,
        {"file_path": str(scene), "new_scene": False, "force": False},
        cmds,
    )

    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "API_OPERATION_ERROR"
    assert receipt["error"] == "Unsaved changes"
    assert cmds.scene == "previous.ma"
    assert cmds.modified is True
