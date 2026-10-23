"""Viewport observation must not become Proxy business set membership."""

from cgi_pipeline.capabilities.maya.asset.proxy_rig import inventory, membership
from cgi_pipeline.capabilities.maya.asset.proxy_rig import plan as policy


class Cmds:
    def __init__(self):
        self.isolating = False
        self.panels = ["modelPanel4", "secondPanel"]
        self.panel_queries = 0
        self.isolate_queries = []
        self.members = {
            "RIG_SET": ["|cache|a", "|cache|b"],
            "modelPanel4ViewSelectedSet": ["|cache|a", "|cache|b"],
            "nativeOwnedSet": ["|cache|a.vtx[0:5]"]}

    def about(self, **kwargs):
        assert kwargs == {"batch": True}
        return False

    def getPanel(self, **kwargs):
        assert kwargs == {"type": "modelPanel"}
        self.panel_queries += 1
        return self.panels

    def isolateSelect(self, panel, **kwargs):
        assert kwargs == {"query": True, "viewObjects": True}
        self.isolate_queries.append(panel)
        return "nativeOwnedSet" if self.isolating and panel == "modelPanel4" else None

    def listSets(self, **kwargs):
        result = ["RIG_SET", "modelPanel4ViewSelectedSet"]
        if self.isolating and kwargs["object"] == "|cache|a":
            result.append("nativeOwnedSet")
        return result

    def nodeType(self, _node):
        return "objectSet"

    def listConnections(self, *_args, **_kwargs):
        return []

    def sets(self, node, **kwargs):
        assert kwargs == {"query": True}
        return self.members[node]


def collect(cmds):
    cache = {}
    return [membership.inspect(cmds, "|cache|" + node, "|cache|" + node + "|shape", cache)
            for node in ("a", "b")]


def merge_plan(memberships):
    snapshot = {
        "scene_path": "/managed/character.ma",
        "scene_fingerprint": inventory.digest(memberships),
        "meshes": [{"uuid": node, "path": "|cache|" + node, "skin": None,
                    "visibility_signature": "shared", "protected_reasons": [], "memberships": data}
                   for node, data in zip(("a", "b"), memberships)]}
    plan = {"policy_version": inventory.POLICY_VERSION, "character": "char",
            "scene_path": snapshot["scene_path"], "scene_fingerprint": snapshot["scene_fingerprint"],
            "merge_groups": [{"sources": ["a", "b"], "output_name": "char_hair",
                              "reason": "same business sets and display switching"}]}
    return snapshot, plan


def test_native_isolation_membership_does_not_change_fingerprint_or_merge_compatibility():
    cmds = Cmds()
    before = collect(cmds)
    cmds.isolating = True
    after = collect(cmds)
    assert after == before
    snapshot_before, planned = merge_plan(before)
    snapshot_after, _ = merge_plan(after)
    assert snapshot_before["scene_fingerprint"] == snapshot_after["scene_fingerprint"]
    assert policy.validate(snapshot_after, planned)["full_scene_coverage"] is True
    assert after[0]["component_sets"] == []
    assert "nativeOwnedSet" not in after[0]["object_sets"]


def test_exact_panel_owned_set_only_preserves_similarly_named_business_sets():
    cmds = Cmds()
    cmds.isolating = True
    data = collect(cmds)[0]
    assert data["object_sets"] == ["RIG_SET", "modelPanel4ViewSelectedSet"]
    assert {entry["set"] for entry in data["object_set_members"]} == set(data["object_sets"])
    cmds.members["modelPanel4ViewSelectedSet"] = ["|cache|a.vtx[0:2]", "|cache|b"]
    changed = collect(cmds)[0]
    assert changed["component_sets"] == ["modelPanel4ViewSelectedSet"]
    assert inventory.digest(changed) != inventory.digest(data)


def test_panel_inventory_is_cached_once_for_multiple_meshes_and_refreshed_next_time():
    cmds = Cmds()
    collect(cmds)
    assert cmds.panel_queries == 1
    assert cmds.isolate_queries == ["modelPanel4", "secondPanel"]
    collect(cmds)
    assert cmds.panel_queries == 2
    assert len(cmds.isolate_queries) == 4


def test_no_model_panels_keeps_all_business_sets_without_isolation_queries():
    cmds = Cmds()
    cmds.panels = []
    result = collect(cmds)
    assert cmds.isolate_queries == []
    assert cmds.panel_queries == 1
    assert result[0]["object_sets"] == ["RIG_SET", "modelPanel4ViewSelectedSet"]


def test_batch_host_never_accesses_ui_and_keeps_business_membership():
    class BatchCmds(Cmds):
        def __init__(self):
            super().__init__()
            self.batch_queries = 0
        def about(self, **kwargs):
            assert kwargs == {"batch": True}
            self.batch_queries += 1
            return True
        def getPanel(self, **_kwargs):
            raise AssertionError("Headless Maya must not access panels")
        def isolateSelect(self, *_args, **_kwargs):
            raise AssertionError("Headless Maya must not query isolation UI")
    cmds = BatchCmds()
    results = collect(cmds)
    assert cmds.batch_queries == 1
    assert all(row["object_sets"] == ["RIG_SET", "modelPanel4ViewSelectedSet"] for row in results)
