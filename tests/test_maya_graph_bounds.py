"""Regressions for Maya's queried-plug-first connection pairs and graph bounds."""

from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

from cgi_pipeline.capabilities.maya import scene_batch
from cgi_pipeline.contracts import ApiContext, ApiContractError, validate_params, validate_spec
from cgi_pipeline.core.schemas import ApiPayloadSchema


class GraphCmds:
    def __init__(self, nodes=(), edges=()):
        self.nodes = list(dict.fromkeys([*nodes, *(p.split(".", 1)[0] for e in edges for p in e)]))
        self.edges = list(edges)
        self.queries = []

    def ls(self, value, **kwargs):
        if kwargs.get("uuid"):
            return ["uuid-" + value]
        return [n for n in self.nodes if fnmatchcase(n, value) or n.rsplit("|", 1)[-1] == value]

    def listRelatives(self, node, **kwargs):
        return []

    def nodeType(self, node):
        return "transform"

    def listConnections(self, node, **kwargs):
        self.queries.append((node, kwargs))
        pairs = []
        for source, destination in self.edges:
            if kwargs.get("source", True) and destination.split(".", 1)[0] == node:
                pairs.extend((destination, source))
            if kwargs.get("destination", True) and source.split(".", 1)[0] == node:
                pairs.extend((source, destination))
        return pairs or None


def inspect(cmds, nodes, max_nodes=10, max_edges=10, **extra):
    context = ApiContext.from_mapping({"cmds_module": cmds}, "maya")
    result = scene_batch.inspect_connections_many(
        {"nodes": nodes, "max_nodes": max_nodes, "max_edges": max_edges, **extra}, context)
    assert result["status"] == "SUCCESS", result
    return result["output"]


def snapshot(cmds, roots, max_nodes=10, max_edges=10, **extra):
    context = ApiContext.from_mapping({"cmds_module": cmds}, "maya")
    result = scene_batch.snapshot_graph_bounded(
        {"roots": roots, "max_nodes": max_nodes, "max_edges": max_edges, **extra}, context)
    assert result["status"] == "SUCCESS", result
    return result["output"]


def test_incoming_pair_direction_and_two_endpoint_deduplication():
    cmds = GraphCmds(edges=[("|source.out", "|sink.input")])
    result = inspect(cmds, ["sink", "source"])
    assert result["edges"] == [{"source": "|source.out", "destination": "|sink.input"}]
    assert result["edge_count"] == 1


def test_exact_node_limit_is_complete_but_omitted_node_is_truncated():
    cmds = GraphCmds(nodes=["|a", "|b"])
    context = ApiContext.from_mapping({"cmds_module": cmds}, "maya")
    exact = scene_batch.inspect_nodes_many({"nodes": ["a", "a"], "max_nodes": 1}, context)
    omitted = scene_batch.inspect_nodes_many({"nodes": ["a", "b"], "max_nodes": 1}, context)
    assert exact["output"]["truncated"] is False
    assert omitted["output"]["truncated"] is True


def test_connection_limit_checks_remaining_nodes_before_claiming_complete():
    cmds = GraphCmds(edges=[("|a.out", "|b.input"), ("|c.out", "|d.input")])
    clipped = inspect(cmds, ["a", "c"], max_edges=1)
    assert clipped["edge_count"] == 1
    assert clipped["truncated"] is True
    exact = inspect(cmds, ["a", "b"], max_edges=1)
    assert exact["truncated"] is False


def test_snapshot_preserves_omitted_roots_and_exact_node_boundary():
    cmds = GraphCmds(nodes=["|a", "|b"])
    assert snapshot(cmds, ["a", "b"], max_nodes=1)["truncated"] is True
    assert snapshot(cmds, ["a"], max_nodes=1)["truncated"] is False


def test_snapshot_reports_unconsumed_edges_even_without_pending_nodes():
    cmds = GraphCmds(edges=[("|a.out", "|a.input"), ("|a.otherOut", "|a.otherInput")])
    clipped = snapshot(cmds, ["a"], max_edges=1)
    assert clipped["edge_count"] == 1
    assert clipped["truncated"] is True
    exact = snapshot(GraphCmds(edges=[("|a.out", "|a.input")]), ["a"], max_edges=1)
    assert exact["truncated"] is False


@pytest.mark.parametrize("direction,expected_nodes,expected_edges", [
    ("upstream", {"|a", "|b"}, [("|a.out", "|b.input")]),
    ("downstream", {"|b", "|c"}, [("|b.out", "|c.input")]),
    ("both", {"|a", "|b", "|c"}, [("|a.out", "|b.input"), ("|b.out", "|c.input")]),
])
def test_direction_filters_inspection_and_graph_traversal(direction, expected_nodes, expected_edges):
    cmds = GraphCmds(edges=[("|a.out", "|b.input"), ("|b.out", "|c.input")])
    inspected = inspect(cmds, ["b"], direction=direction)
    graph = snapshot(cmds, ["b"], direction=direction)
    wanted = [{"source": a, "destination": b} for a, b in expected_edges]
    assert inspected["edges"] == wanted
    assert graph["edges"] == wanted
    assert {n["path"] for n in graph["nodes"]} == expected_nodes
    assert graph["truncated"] is False


def test_full_names_and_unit_conversion_are_preserved_without_guessing_short_names():
    cmds = GraphCmds(edges=[("|left|ctrl.out", "unitConversion.input"),
                            ("unitConversion.output", "|target.input"),
                            ("|right|ctrl.out", "|other.input")])
    graph = snapshot(cmds, ["target"], direction="upstream")
    assert {n["path"] for n in graph["nodes"]} == {"|left|ctrl", "unitConversion", "|target"}
    assert all(flags["fullNodeName"] is True for _, flags in cmds.queries)
    assert all(flags["skipConversionNodes"] is False for _, flags in cmds.queries)
    assert all(flags["source"] and not flags["destination"] for _, flags in cmds.queries)


def test_cycles_and_exact_edge_limit_finish_within_node_query_budget():
    cmds = GraphCmds(edges=[("|a.out", "|b.input"), ("|b.out", "|a.input")])
    graph = snapshot(cmds, ["a", "a"], max_nodes=2, max_edges=2)
    assert graph["node_count"] == 2 and graph["edge_count"] == 2
    assert graph["truncated"] is False
    assert len(cmds.queries) == 4


@pytest.mark.parametrize("max_nodes,max_edges", [(3, 20), (20, 2)])
def test_large_chain_stops_at_node_or_edge_budget(max_nodes, max_edges):
    cmds = GraphCmds(edges=[(f"|n{i}.out", f"|n{i + 1}.input") for i in range(100)])
    graph = snapshot(cmds, ["n0"], max_nodes=max_nodes, max_edges=max_edges)
    assert graph["truncated"] is True
    assert graph["node_count"] <= max_nodes
    assert graph["edge_count"] <= max_edges
    assert len(cmds.queries) <= 2 * max_nodes


@pytest.mark.parametrize("limits", [{"max_nodes": 0}, {"max_edges": 0}, {"direction": "sideways"}])
def test_invalid_bounds_and_direction_return_receipt_error(limits):
    cmds = GraphCmds(nodes=["|a"])
    context = ApiContext.from_mapping({"cmds_module": cmds}, "maya")
    for function, roots in [(scene_batch.inspect_connections_many, "nodes"),
                            (scene_batch.snapshot_graph_bounded, "roots")]:
        params = {roots: ["a"], "max_nodes": 2, "max_edges": 2, **limits}
        result = function(params, context)
        assert result["status"] == "ERROR"


def test_invalid_direction_is_rejected_even_when_no_nodes_match():
    context = ApiContext.from_mapping({"cmds_module": GraphCmds()}, "maya")
    for function, roots in [(scene_batch.inspect_connections_many, "nodes"),
                            (scene_batch.snapshot_graph_bounded, "roots")]:
        result = function({roots: [], "max_nodes": 1, "max_edges": 1, "direction": "sideways"}, context)
        assert result["status"] == "ERROR"


@pytest.mark.parametrize("name", ["nodes_inspect_many", "connections_inspect_many", "graph_snapshot_bounded"])
def test_changed_manifests_pass_the_actual_contract_validator(name):
    path = Path(scene_batch.__file__).parent / "scene" / name / "capability.yaml"
    spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    if "direction" not in spec["inputs"]:
        return
    roots = "roots" if name == "graph_snapshot_bounded" else "nodes"
    assert validate_params(spec, {roots: []})["direction"] == "both"
    with pytest.raises(ApiContractError, match="choose"):
        validate_params(spec, {roots: [], "direction": "sideways"})
    snapshot_context = {"catalog_snapshot": ({spec["api_id"]: spec}, "graph-test")}
    payload = {"api_id": spec["api_id"], "api_params": {roots: [], "direction": "sideways"}}
    with pytest.raises(ValueError, match="choose"):
        ApiPayloadSchema.model_validate(payload, context=snapshot_context)
