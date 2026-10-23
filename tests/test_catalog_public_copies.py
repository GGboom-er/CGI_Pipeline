"""Public projections cannot mutate an in-flight catalog snapshot."""

from copy import deepcopy

import pytest

from cgi_pipeline import catalog
from cgi_pipeline.core import capability_registry as registry


@pytest.mark.parametrize("query", [
    lambda api: catalog.get_capability(api),
    lambda api: catalog.capability_help(api),
    lambda api: catalog.capability_help_with_version(api)[0],
    lambda api: next(row for row in catalog.list_capabilities() if row["api_id"] == api),
    lambda api: catalog.page_capabilities(search=api)["apis"][0],
    lambda api: registry.get_capability_map()[api],
    lambda api: next(row for row in registry.get_all_capabilities() if row["api_id"] == api),
])
def test_nested_public_metadata_is_an_independent_copy(query):
    api_id = "maya.scene.selection.get"
    snapshot, version = catalog.snapshot()
    before = deepcopy(snapshot[api_id])
    try:
        projection = query(api_id)
        projection["effects"]["unknown"] = "mutation probe"
        projection["inputs"]["injected"] = {"type": "string"}
        assert snapshot[api_id] == before
        assert catalog.catalog_version() == version
        assert catalog.get_capability(api_id) == before
    finally:
        catalog.reload()
