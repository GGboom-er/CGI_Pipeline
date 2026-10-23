"""Live catalog updates must be cheap, atomic, and fail closed."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from cgi_pipeline import catalog
from cgi_pipeline.contracts import ApiContractError
from tools import build_catalogs


@pytest.fixture
def live_catalog(tmp_path, monkeypatch):
    rows = [catalog.get_capability("maya.scene.selection.get"),
            catalog.get_capability("maya.scene.nodes.find")]
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "_CATALOG_PATH", path)
        catalog.reload()
        yield path, rows
    catalog.reload()


def publish(path, rows):
    previous = path.stat().st_mtime_ns
    path.write_text(json.dumps(rows), encoding="utf-8")
    os.utime(path, ns=(previous + 1000000, previous + 1000000))


@pytest.mark.parametrize("entry", [
    lambda api_id: catalog.list_capabilities(),
    lambda api_id: catalog.page_capabilities(),
    catalog.get_capability,
    catalog.capability_help,
    lambda api_id: catalog.catalog_version(),
])
def test_each_entry_observes_new_catalog_without_manual_reload(live_catalog, entry):
    path, rows = live_catalog
    previous = catalog.catalog_version()
    added = deepcopy(rows[0])
    added["api_id"] = "maya.scene.selection.freshness_probe"
    publish(path, rows + [added])

    entry(added["api_id"])

    assert catalog.catalog_version() != previous
    assert catalog.get_capability(added["api_id"])["handler"] == added["handler"]


def test_registry_resolves_new_and_removed_ids_without_another_cache(live_catalog):
    from cgi_pipeline.core import capability_registry as registry

    path, rows = live_catalog
    added = deepcopy(rows[0])
    added["api_id"] = "maya.scene.selection.freshness_probe"
    added["skip_audit"] = True
    publish(path, [added])

    assert registry.resolve_api_id("freshness_probe") == added["api_id"]
    assert registry.get_executor(added["api_id"]) == "maya"
    assert registry.get_capability_map() == {added["api_id"]: catalog.get_capability(added["api_id"])}
    assert [row["api_id"] for row in registry.get_all_capabilities()] == [added["api_id"]]
    assert registry.get_skip_audit_capabilities() == {added["api_id"]}
    with pytest.raises(KeyError):
        registry.resolve_api_id(rows[0]["api_id"])


def test_unchanged_queries_never_read_or_validate_again(live_catalog, monkeypatch):
    validate = Mock(wraps=catalog.validate_spec)
    monkeypatch.setattr(catalog, "validate_spec", validate)
    original_read = Path.read_text
    read = Mock(side_effect=lambda path, **kwargs: original_read(path, **kwargs))
    monkeypatch.setattr(Path, "read_text", lambda path, **kwargs: read(path, **kwargs))

    for _ in range(5):
        catalog.list_capabilities()
        catalog.page_capabilities()
        catalog.get_capability("maya.scene.selection.get")
        catalog.capability_help("maya.scene.selection.get")
        catalog.catalog_version()

    read.assert_not_called()
    validate.assert_not_called()
    catalog.reload()
    assert read.call_count == 1
    assert validate.call_count == 2


def test_fingerprint_includes_path_even_when_metadata_matches(live_catalog, monkeypatch):
    path, rows = live_catalog
    previous = catalog.catalog_version()
    other = path.with_name("other.json")
    changed = deepcopy(rows)
    changed[0]["api_id"] = "maya.scene.selection.put"
    other.write_text(json.dumps(changed), encoding="utf-8")
    assert other.stat().st_size == path.stat().st_size
    stamp = path.stat().st_mtime_ns
    os.utime(other, ns=(stamp, stamp))
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "_CATALOG_PATH", other)
        assert catalog.get_capability("maya.scene.selection.put")
        assert catalog.catalog_version() != previous


@pytest.mark.parametrize("broken", ["missing", "{", "null", '[{"api_id": "invalid"}]'])
def test_broken_publication_blocks_all_reads_and_recovers(live_catalog, broken):
    path, rows = live_catalog
    if broken == "missing":
        path.unlink()
    else:
        path.write_text(broken, encoding="utf-8")
    for read in (catalog.catalog_version, catalog.list_capabilities,
                 catalog.page_capabilities,
                 lambda: catalog.get_capability(rows[0]["api_id"]),
                 lambda: catalog.capability_help(rows[0]["api_id"])):
        with pytest.raises(ApiContractError):
            read()
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert catalog.get_capability(rows[0]["api_id"])


def test_concurrent_refresh_loads_one_validated_generation(live_catalog, monkeypatch):
    path, rows = live_catalog
    publish(path, rows[:1])
    load = Mock(wraps=catalog._load)
    monkeypatch.setattr(catalog, "_load", load)
    start = Barrier(8)

    def read(_):
        start.wait(timeout=5)
        return catalog.page_capabilities()

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(read, range(8)))
    assert load.call_count == 1
    assert all(page == pages[0] for page in pages)
    assert pages[0]["total"] == 1


def test_catalog_builder_publishes_atomically_and_cleans_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "capabilities.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    original_replace = build_catalogs.os.replace

    def fail_replace(source, target):
        assert target == path
        raise OSError("injected publication failure")

    monkeypatch.setattr(build_catalogs.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected publication failure"):
        build_catalogs._write_or_check(path, [{"new": True}], check=False)
    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert list(tmp_path.glob(".capabilities.json.*.tmp")) == []
    monkeypatch.setattr(build_catalogs.os, "replace", original_replace)
    assert build_catalogs._write_or_check(path, [{"new": True}], check=False)
    assert json.loads(path.read_text(encoding="utf-8")) == [{"new": True}]


def test_page_keeps_its_content_version_when_reload_occurs_during_filter(live_catalog, monkeypatch):
    path, rows = live_catalog
    previous = catalog.catalog_version()
    filter_rows = catalog._filter_capabilities

    def publish_while_filtering(specs, **kwargs):
        publish(path, rows[:1])
        catalog.reload()
        return filter_rows(specs, **kwargs)

    monkeypatch.setattr(catalog, "_filter_capabilities", publish_while_filtering)
    page = catalog.page_capabilities(limit=1)
    assert page["total"] == 2
    assert page["catalog_version"] == previous
    assert catalog.catalog_version() != previous
    with pytest.raises(ValueError, match="stale"):
        catalog.page_capabilities(limit=1, cursor=page["next_cursor"])


def test_changed_catalog_rejects_previously_valid_expected_version(live_catalog):
    from cgi_pipeline.server.tools_operations import _catalog_version_guard

    path, rows = live_catalog
    previous = catalog.catalog_version()
    publish(path, rows[:1])
    current, error = _catalog_version_guard(previous)
    assert current != previous
    assert error["error_code"] == "CATALOG_VERSION_STALE"


def test_mcp_help_keeps_contract_and_template_version_together(live_catalog, monkeypatch):
    from cgi_pipeline.server.tools_readonly import register_readonly_tools

    class FakeMcp:
        def __init__(self):
            self.tools = {}

        def tool(self, *, name, annotations):
            def register(function):
                self.tools[name] = function
                return function
            return register

    path, rows = live_catalog
    previous = catalog.catalog_version()
    original_help = catalog.capability_help_with_version
    updated = deepcopy(rows)
    updated[0]["help"]["summary"] = "Updated contract published during response construction"

    def publish_after_snapshot(api_id):
        result = original_help(api_id)
        publish(path, updated)
        catalog.reload()
        return result

    monkeypatch.setattr(catalog, "capability_help_with_version", publish_after_snapshot)
    mcp = FakeMcp()
    register_readonly_tools(mcp)
    result = asyncio.run(mcp.tools["api_help"](rows[0]["api_id"]))

    assert result["help"]["summary"] == rows[0]["help"]["summary"]
    assert result["catalog_version"] == previous
    assert result["mcp_call_template"]["arguments"]["params"]["expected_catalog_version"] == previous
    assert catalog.catalog_version() != previous


def test_change_during_load_never_publishes_unverified_fingerprint(live_catalog, monkeypatch):
    path, rows = live_catalog
    previous = catalog.catalog_version()
    load = catalog._load

    def changing_load(source):
        loaded = load(source)
        publish(source, rows[:1])
        return loaded

    monkeypatch.setattr(catalog, "_load", changing_load)
    with pytest.raises(ApiContractError, match="changed while loading"):
        catalog.reload()
    assert catalog._catalog_version == previous
    monkeypatch.setattr(catalog, "_load", load)
    assert catalog.page_capabilities()["total"] == 1
