"""Protected-root guard: every Windows spelling of X: and //server is blocked,
and roots match whole path segments only (//server never covers //serverbackup)."""

import json

import pytest

from cgi_pipeline.core import manifest, path_guard

# Same entries as config/pipeline_manifest.json and the project configs, plus one sub-directory root.
_MANIFEST = {"principles": {
    "readonly_drives": ["x:"],
    "readonly_unc_prefixes": ["//server/", "\\\\server\\"],
}}
_PROJECT_ROOTS = ["X:/Project/", "//server/", "\\\\server\\", "Z:/prod"]


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    (tmp_path / "t_config.json").write_text(json.dumps({"protected_roots": _PROJECT_ROOTS}), encoding="utf-8")
    monkeypatch.setattr(manifest, "_manifest", _MANIFEST)
    monkeypatch.setattr(path_guard, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(path_guard, "_PROTECTED_ROOTS", [])


@pytest.mark.parametrize("path", [
    r"\\?\X:\a",
    "//?/x:/a",
    r"\\.\X:\a",
    "//./X:/a",
    r"\??\X:\a",
    r"\\?\UNC\server\share\a",
    r"\\.\UNC\server\share\a",
    r"\\?\UNC\server",
    "X:/a",
    r"x:\a",
    "x:a",
    "//server/a",
    r"\\server\a",
    "//server",
    "//server//a",
])
def test_protected_spellings_are_blocked(path):
    assert path_guard.is_protected_path(path)


@pytest.mark.parametrize("path", [
    "//serverbackup/a",
    r"\\serverbackup\a",
    r"\\?\UNC\serverbackup\a",
    "Y:/a",
    r"\\?\Y:\a",
])
def test_other_roots_are_not_blocked(path):
    assert not path_guard.is_protected_path(path)


@pytest.mark.parametrize("path, blocked", [
    ("Z:/prod/a.ma", True),
    (r"\\?\Z:\prod\a.ma", True),
    ("Z://prod/a.ma", True),
    ("Z:/prod", True),
    ("Z:/prodbackup/a.ma", False),
    ("Z:/other/a.ma", False),
])
def test_project_roots_match_whole_segments(path, blocked):
    assert path_guard.is_protected_path(path) is blocked


def test_display_uses_the_guard_normalization():
    assert path_guard.describe_protected_roots() == ["X:/", "//server/", "Z:/prod/"]
    assert manifest.normalize_path(r"\\?\UNC\Server\Share\\") == "//server/share"
    assert manifest.normalize_path(r"\\.\X:\A") == "x:/a"
