"""Behavior contract for CGI's host-facing SDK."""

from __future__ import annotations

from unittest.mock import patch

from cgi_pipeline import api, tools
from cgi_pipeline.contracts import ApiContext


def test_catalog_can_filter_by_independent_facets():
    rows = api.list(executor="maya", stages=["rigging"], access="read")
    ids = {row["api_id"] for row in rows}
    assert "maya.rig.skin.query_weights" in ids


def test_execute_local_rejects_a_service_only_capability():
    receipt = api.execute_local(
        "pipeline.session.discover",
        {"dcc": "maya"},
        host="maya",
    )
    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "HOST_SURFACE_UNSUPPORTED"


def test_execute_local_runs_a_maya_handler_with_one_receipt_contract():
    class FakeCmds:
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
            return 1

        def skinPercent(self, _cluster, _component, **_kwargs):
            return [0.75, 0.25]

    receipt = api.execute_local(
        "maya.rig.skin.query_weights",
        {
            "mesh": "bodyShape",
            "vertices": [0],
            "influence": "",
            "minimum_weight": 0.0,
            "max_vertices": 5,
        },
        host="maya",
        host_modules={"cmds": FakeCmds()},
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["api_id"] == "maya.rig.skin.query_weights"
    assert "output" in receipt
    assert "outputs" not in receipt


def test_tools_open_uses_the_registered_entrypoint():
    with patch("cgi_pipeline.tools._load_entrypoint", return_value=lambda: "opened") as load:
        assert tools.open("adpose", host="maya") == "opened"
    load.assert_called_once_with("adpose", "maya")
