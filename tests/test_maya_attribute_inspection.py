"""Schema discovery contract; no value queries or scene edits are permitted."""

from fnmatch import fnmatchcase

import pytest

from cgi_pipeline.capabilities.maya import scene_attributes
from cgi_pipeline.contracts import ApiContext


class FakeCmds:
    """Models documented listAttr/attributeQuery results, including None lists."""

    def __init__(self):
        self.attrs = {
            "|root": {
                "translate": {"attributeType": "double3", "listChildren": ["translateX", "translateY"]},
                "translateX": {"attributeType": "doubleLinear", "keyable": True,
                               "minimum": [-2.0], "softMax": [1.0]},
                "mode": {"attributeType": "enum", "user_defined": True,
                         "keyable": True, "listEnum": ["off:on=5:auto"]},
                "message": {"attributeType": "message", "writable": False},
                "outputs": {"attributeType": "typed", "multi": True, "hidden": True},
            },
            "|child": {"weight": {"attributeType": "double", "maximum": [10.0]}},
        }
        self.failure = None
        self.missing_uuid = False
        self.schema_calls = []

    def ls(self, value, **kwargs):
        if kwargs.get("uuid"):
            return [] if self.missing_uuid else ["uuid-" + value.lstrip("|")]
        return [node for node in self.attrs
                if fnmatchcase(node, value) or fnmatchcase(node.lstrip("|"), value)] or None

    def nodeType(self, node):
        return "transform"

    def listAttr(self, node, **kwargs):
        if self.failure == "listAttr":
            raise RuntimeError("plugin attribute enumeration failed")
        rows = self.attrs[node]
        return [name for name, meta in rows.items()
                if (not kwargs.get("userDefined") or meta.get("user_defined"))
                and (not kwargs.get("keyable") or meta.get("keyable"))] or None

    def attributeQuery(self, name, *, node, **kwargs):
        assert len(kwargs) == 1 and list(kwargs.values()) == [True]
        flag = next(iter(kwargs))
        self.schema_calls.append((node, name, flag))
        if self.failure == flag:
            raise RuntimeError("plugin schema query failed: " + flag)
        meta = self.attrs[node][name]
        if flag in {"readable", "writable", "connectable"}:
            return meta.get(flag, True)
        if flag in {"multi", "keyable", "hidden"}:
            return meta.get(flag, False)
        exists = {"minExists": "minimum", "maxExists": "maximum",
                  "softMinExists": "softMin", "softMaxExists": "softMax"}
        if flag in exists:
            return exists[flag] in meta
        if flag in {"listChildren", "listEnum"}:
            return meta.get(flag)
        return meta[flag]

    def getAttr(self, *_args, **_kwargs):
        raise AssertionError("Schema discovery must not query values or current data")

    def setAttr(self, *_args, **_kwargs):
        raise AssertionError("Schema discovery must not edit the scene")


def inspect(cmds=None, **params):
    cmds = cmds or FakeCmds()
    context = ApiContext.from_mapping({"cmds_module": cmds}, "maya")
    return scene_attributes.inspect_attributes({"nodes": ["root"], **params}, context)


def output(cmds=None, **params):
    receipt = inspect(cmds, **params)
    assert receipt["status"] == "SUCCESS", receipt
    return receipt["output"]


def test_discovers_identity_schema_without_values_and_preserves_enum_numbers():
    got = output()
    node = got["nodes"][0]
    assert (node["path"], node["uuid"], node["type"]) == ("|root", "uuid-root", "transform")
    attrs = {row["name"]: row for row in node["attributes"]}
    assert attrs["translate"]["children"] == ["translateX", "translateY"]
    assert attrs["mode"]["enum"] == ["off:on=5:auto"]
    assert attrs["mode"]["user_defined"] is True
    assert attrs["translateX"]["type"] == "doubleLinear"
    assert attrs["translateX"]["limits"] == {"minimum": [-2.0], "soft_maximum": [1.0]}
    assert attrs["message"]["writable"] is False
    assert attrs["outputs"]["multi"] is True and attrs["outputs"]["hidden"] is True
    assert attrs["outputs"]["type"] == "typed"
    assert attrs["outputs"]["limits"] == {}
    assert got["attribute_count"] == 5 and got["node_count"] == 1
    assert got["truncated"] is False


@pytest.mark.parametrize("params, expected", [
    ({"attribute_pattern": "trans*", "keyable": True}, ["translateX"]),
    ({"user_defined": True}, ["mode"]),
    ({"user_defined": False, "keyable": False}, ["translate", "message", "outputs"]),
    ({"attribute_pattern": "missing*"}, []),
])
def test_filters_are_optional_and_false_means_exclusion(params, expected):
    got = output(**params)
    assert [row["name"] for row in got["nodes"][0]["attributes"]] == expected
    assert got["truncated"] is False


@pytest.mark.parametrize("nodes", [[], ["missing"]])
def test_empty_resolution_does_not_fall_back_to_selection(nodes):
    got = output(nodes=nodes)
    assert got["nodes"] == [] and got["attribute_count"] == 0
    assert got["truncated"] is False


def test_no_attributes_and_singleton_documented_type_result():
    cmds = FakeCmds()
    cmds.attrs["|root"] = {}
    got = output(cmds)
    assert got["nodes"][0]["attributes"] == []
    assert got["truncated"] is False
    cmds.attrs["|root"] = {"mode": {"attributeType": ["enum"], "listEnum": ["off:on"]}}
    assert output(cmds)["nodes"][0]["attributes"][0]["type"] == "enum"


@pytest.mark.parametrize("maximum, expected_truncated", [(1, True), (5, False), (6, False)])
def test_attribute_limit_reports_only_actual_omissions(maximum, expected_truncated):
    got = output(max_attributes=maximum)
    assert got["attribute_count"] == min(maximum, 5)
    assert got["truncated"] is expected_truncated
    assert got["nodes"][0]["truncated"] is expected_truncated


def test_global_attribute_budget_covers_multiple_nodes_without_extra_schema_queries():
    cmds = FakeCmds()
    got = output(cmds, nodes=["root", "child"], max_attributes=2)
    assert [node["count"] for node in got["nodes"]] == [2, 0]
    assert all(node["truncated"] for node in got["nodes"])
    assert got["attributes_truncated"] is True and got["nodes_truncated"] is False
    assert {(node, name) for node, name, _ in cmds.schema_calls} == {
        ("|root", "translate"), ("|root", "translateX")}
    got = output(nodes=["root", "child"], max_attributes=6)
    assert got["attribute_count"] == 6 and got["truncated"] is False


@pytest.mark.parametrize("nodes, maximum, expected", [
    (["root", "root"], 1, False),
    (["root", "child"], 1, True),
    (["*"], 2, False),
])
def test_node_limit_deduplicates_and_probes_for_real_omission(nodes, maximum, expected):
    got = output(nodes=nodes, max_nodes=maximum)
    assert got["nodes_truncated"] is expected
    assert got["node_count"] <= maximum


@pytest.mark.parametrize("params", [
    {"max_nodes": 0}, {"max_nodes": -1}, {"max_nodes": 2001}, {"max_nodes": True},
    {"max_attributes": 0}, {"max_attributes": 2001}, {"max_attributes": 1.5},
    {"max_attributes": "5"}, {"user_defined": 1}, {"keyable": "true"},
    {"nodes": "root"}, {"nodes": [""]}, {"attribute_pattern": 1},
])
def test_invalid_bounds_and_filters_return_error_receipts(params):
    assert inspect(**params)["status"] == "ERROR"


@pytest.mark.parametrize("failure", ["listAttr", "attributeType", "readable", "minExists"])
def test_query_errors_are_not_silently_converted_to_empty_or_false_schema(failure):
    cmds = FakeCmds()
    cmds.failure = failure
    got = inspect(cmds)
    assert got["status"] == "ERROR"
    assert "plugin" in got["error"]


def test_missing_identity_or_invalid_query_result_returns_error():
    cmds = FakeCmds()
    cmds.missing_uuid = True
    assert inspect(cmds)["status"] == "ERROR"
    cmds.missing_uuid = False
    cmds.attrs["|root"]["translate"]["readable"] = None
    assert inspect(cmds)["status"] == "ERROR"


def test_one_sided_and_soft_limits_preserve_documented_list_shape():
    cmds = FakeCmds()
    cmds.attrs["|root"] = {"weight": {"attributeType": "double",
                                    "maximum": [10.0], "softMin": [0.0]}}
    limits = output(cmds)["nodes"][0]["attributes"][0]["limits"]
    assert limits == {"maximum": [10.0], "soft_minimum": [0.0]}
