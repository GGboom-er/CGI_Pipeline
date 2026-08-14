"""Contract coverage for the unified session, UE, and Maya skin APIs."""

from unittest.mock import patch

from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.capabilities.maya.rig.skin.query_weights import handler as skin_handler
from cgi_pipeline.capabilities.pipeline.session.discover import handler as session_handler
from cgi_pipeline.catalog import capability_help, get_capability, list_capabilities, reload


def _context(api_id: str, dcc: str) -> ApiContext:
    return ApiContext.from_mapping(
        {"_api_id": api_id, "_api_version": "1.0.0"}, dcc
    )


def test_new_apis_are_discoverable_and_manifest_backed():
    reload()
    api_ids = {row["api_id"] for row in list_capabilities()}
    assert {
        "pipeline.session.discover",
        "ue.editor.python.execute",
        "ue.editor.action.invoke",
        "maya.rig.skin.query_weights",
    }.issubset(api_ids)
    assert get_capability("ue.editor.action.invoke")["executor"] == "ue"
    assert capability_help("maya.rig.skin.query_weights")["access"] == "read"


def test_session_discovery_returns_a_stable_selected_session():
    with patch("cgi_pipeline.server.ports.discover_maya_ports", return_value=[7009]):
        receipt = session_handler.execute(
            {"dcc": "maya"}, _context("pipeline.session.discover", "pipeline")
        )

    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["selected_session"] == {
        "dcc": "maya", "port": 7009, "session_id": "maya:7009"
    }


class _FakeCmds:
    def ls(self, _value, **kwargs):
        if kwargs.get("type") == "mesh":
            return ["|geo|bodyShape"]
        if kwargs.get("type") == "skinCluster":
            return ["body_skinCluster"]
        return []

    def listHistory(self, _shape, **_kwargs):
        return ["body_skinCluster"]

    def skinCluster(self, _cluster, **_kwargs):
        return ["jointA", "jointB"]

    def polyEvaluate(self, _shape, **_kwargs):
        return 3

    def skinPercent(self, _cluster, _component, **_kwargs):
        return [0.9, 0.1]


def test_skin_query_returns_sparse_read_only_weights():
    context = ApiContext.from_mapping(
        {
            "_api_id": "maya.rig.skin.query_weights",
            "_api_version": "1.0.0",
            "cmds_module": _FakeCmds(),
        },
        "maya",
    )
    receipt = skin_handler.execute(
        {
            "mesh": "bodyShape",
            "vertices": [1],
            "influence": "",
            "minimum_weight": 0.2,
            "max_vertices": 50,
        },
        context,
    )

    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["rows"] == [{"vertex": 1, "weights": {"jointA": 0.9}}]
