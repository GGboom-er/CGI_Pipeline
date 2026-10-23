from __future__ import annotations

import asyncio

import pytest

from cgi_pipeline.catalog import catalog_version, page_capabilities, reload
from cgi_pipeline.server.tools_readonly import register_readonly_tools


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, annotations):
        del annotations

        def register(function):
            self.tools[name] = function
            return function

        return register


def setup_module():
    reload()


def test_page_capabilities_filters_and_walks_every_match_once():
    expected = page_capabilities(executor="maya", limit=100)["apis"]
    first = page_capabilities(executor="maya", limit=3)
    collected = list(first["apis"])
    cursor = first["next_cursor"]

    while cursor:
        page = page_capabilities(executor="maya", limit=3, cursor=cursor)
        collected.extend(page["apis"])
        cursor = page["next_cursor"]

    assert first["total"] == len(expected)
    assert [row["api_id"] for row in collected] == [row["api_id"] for row in expected]
    assert first["catalog_version"] == catalog_version()


def test_search_is_case_insensitive_and_combines_with_facets():
    page = page_capabilities(executor="maya", search="HIERARCHY", limit=100)
    assert page["total"] >= 1
    assert all(row["executor"] == "maya" for row in page["apis"])
    assert all(
        "hierarchy" in " ".join((
            row["api_id"], row["capability"], row["operation"],
            row.get("help", {}).get("summary", ""),
        )).casefold()
        for row in page["apis"]
    )


def test_cursor_rejects_changed_filters_and_invalid_limits():
    first = page_capabilities(limit=1)
    assert first["next_cursor"]
    with pytest.raises(ValueError, match="does not match"):
        page_capabilities(executor="maya", limit=1, cursor=first["next_cursor"])
    with pytest.raises(ValueError, match="between 1 and 100"):
        page_capabilities(limit=0)


def test_mcp_list_apis_defaults_to_compact_page_and_keeps_summary_shape():
    mcp = _FakeMcp()
    register_readonly_tools(mcp)
    result = asyncio.run(mcp.tools["list_apis"]())

    assert result["total"] >= len(result["apis"])
    assert len(result["apis"]) <= 20
    assert result["catalog_version"] == catalog_version()
    assert {"total", "catalog_version", "next_cursor", "apis"} <= result.keys()
    assert all(set(row) == {"api_id", "access", "modes", "summary"} for row in result["apis"])
    assert all(row["summary"] for row in result["apis"])


def test_mcp_list_apis_returns_stable_error_for_bad_cursor():
    mcp = _FakeMcp()
    register_readonly_tools(mcp)
    result = asyncio.run(mcp.tools["list_apis"](cursor="not-a-cursor"))

    assert result["status"] == "ERROR"
    assert result["error_code"] == "INVALID_CATALOG_QUERY"
    assert result["apis"] == []

    malformed = asyncio.run(mcp.tools["list_apis"](cursor="%%%"))
    assert malformed["error_code"] == "INVALID_CATALOG_QUERY"


def test_mcp_api_help_returns_catalog_version_for_contract_cache():
    mcp = _FakeMcp()
    register_readonly_tools(mcp)

    result = asyncio.run(mcp.tools["api_help"]("maya.scene.selection.get"))

    assert result["api_id"] == "maya.scene.selection.get"
    assert result["catalog_version"] == catalog_version()


@pytest.mark.parametrize("query", [
    {"execution_modes": ["local"]},
    {"modes": ["inspect"]},
    {"effects": ["scene_write"]},
])
def test_catalog_exposes_runtime_mode_and_effect_filters(query):
    page = page_capabilities(**query, limit=100)
    assert page["apis"]
    field, requested = next(iter(query.items()))
    assert all(set(row[field]) & set(requested) for row in page["apis"])


def test_new_facets_use_or_within_each_dimension_and_and_across_dimensions():
    from cgi_pipeline import api

    query = {"executor": "maya", "execution_modes": ["local", "background"],
             "modes": ["inspect", "preview"], "effects": ["scene_write", "file_write"]}
    expected = {row["api_id"] for row in api.list() if row["executor"] == "maya"
                and set(row["execution_modes"]) & {"local", "background"}
                and set(row["modes"]) & {"inspect", "preview"}
                and set(row["effects"]) & {"scene_write", "file_write"}}
    assert "maya.rig.reference.update" in expected
    assert "maya.rig.skeleton.normalize_scales" in expected
    assert "maya.asset.maya_build_asset_info" in expected
    sdk = api.list(**query)
    page = page_capabilities(**query, limit=100)
    mcp = _FakeMcp()
    register_readonly_tools(mcp)
    public = asyncio.run(mcp.tools["list_apis"](**query, limit=100))
    assert {row["api_id"] for row in sdk} == expected
    assert {row["api_id"] for row in page["apis"]} == expected
    assert {row["api_id"] for row in public["apis"]} == expected


@pytest.mark.parametrize("field,changed", [
    ("execution_modes", ["foreground"]), ("modes", ["apply"]),
    ("effects", ["scene_write"]),
])
def test_cursor_includes_every_new_filter_dimension(field, changed):
    query = {"execution_modes": ["local"], "modes": ["inspect"], "effects": ["scene_read"]}
    first = page_capabilities(**query, limit=1)
    assert first["next_cursor"]
    query[field] = changed
    with pytest.raises(ValueError, match="does not match"):
        page_capabilities(**query, cursor=first["next_cursor"])


def test_cursor_treats_facet_order_duplicates_and_empty_lists_as_equivalent():
    query = {"execution_modes": ["local", "foreground"],
             "modes": ["inspect", "apply"], "effects": ["scene_read", "scene_write"]}
    first = page_capabilities(**query, limit=1)
    equivalent = {name: list(reversed(values)) + values for name, values in query.items()}
    second = page_capabilities(**equivalent, stages=[], cursor=first["next_cursor"], limit=1)
    assert second["apis"] == page_capabilities(**query, cursor=first["next_cursor"], limit=1)["apis"]


@pytest.mark.parametrize("query", [
    {"execution_modes": ["daemon"]}, {"modes": ["query"]},
    {"effects": ["apply"]}, {"targets": ["jiont"]},
])
def test_sdk_and_mcp_reject_unknown_or_noncanonical_filter_values(query):
    from cgi_pipeline import api
    from cgi_pipeline.contracts import ApiContractError

    with pytest.raises(ApiContractError):
        api.list(**query)
    mcp = _FakeMcp()
    register_readonly_tools(mcp)
    assert asyncio.run(mcp.tools["list_apis"](**query))["error_code"] == "INVALID_CATALOG_QUERY"


@pytest.mark.parametrize("alias,canonical", [
    ("joints", "joint"), ("anim_curve", "animation_curve"),
    ("animation_curves", "animation_curve"),
])
def test_target_query_aliases_share_results_and_pagination_fingerprints(alias, canonical):
    from cgi_pipeline import api

    assert api.list(targets=[alias]) == api.list(targets=[canonical])
    first = page_capabilities(targets=[alias], limit=1)
    assert first["next_cursor"]
    canonical_page = page_capabilities(targets=[canonical], cursor=first["next_cursor"])
    mixed_page = page_capabilities(targets=[alias, canonical], cursor=first["next_cursor"])
    assert canonical_page == mixed_page
    mcp = _FakeMcp()
    register_readonly_tools(mcp)
    public = asyncio.run(mcp.tools["list_apis"](targets=[alias], limit=100))
    assert {row["api_id"] for row in public["apis"]} == {
        row["api_id"] for row in api.list(targets=[canonical])}


@pytest.mark.parametrize("api_id", [
    "maya.asset.maya_freeze_transforms",
    "maya.rig.maya_clean_skinweights",
    "maya.rig.maya_fix_shape_names",
])
def test_source_path_scene_open_effects_are_discoverable(api_id):
    from cgi_pipeline import api

    spec = api.help(api_id)
    for effect in ("file_read", "host_state_write"):
        assert "context.source_path" in spec["effects"][effect]
        assert api_id in {row["api_id"] for row in api.list(effects=[effect])}


@pytest.mark.parametrize("api_id,condition", [
    ("maya.rig.reference.update", "operation=apply"),
    ("maya.asset.rename_asset", "cache miss"),
])
def test_business_reference_and_config_file_reads_are_discoverable(api_id, condition):
    from cgi_pipeline import api

    assert condition in api.help(api_id)["effects"]["file_read"]
    assert api_id in {row["api_id"] for row in api.list(effects=["file_read"])}


def test_business_writes_keep_inspect_intent_but_are_discoverable_as_write_access():
    from cgi_pipeline import api

    ids = {
        'blender.asset.blender_build_asset_info', 'blender.asset.blender_capture_viewport',
        'maya.asset.check_uvsets', 'maya.asset.maya_build_asset_info', 'maya.asset.maya_capture_viewport',
        'maya.asset.maya_check_textures', 'maya.asset.maya_compare_asset_in_scene', 'maya.asset.validate_publish',
        'pipeline.pipeline.pipeline_compare_asset',
    }
    assert ids <= {row['api_id'] for row in api.list(access='write', modes=['inspect'])}
    assert not ids & {row['api_id'] for row in api.list(access='read')}
