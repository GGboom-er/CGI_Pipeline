"""Host-free checks for Proxy plan decisions and the real registered chain shape."""
from pathlib import Path

import pytest

from cgi_pipeline.capabilities.maya.asset.proxy_rig import inventory
from cgi_pipeline.capabilities.maya.asset.proxy_rig import plan as policy
from cgi_pipeline.capabilities.maya.asset.proxy_rig import safety
from cgi_pipeline.capabilities.maya.asset.proxy_rig import core, deletion_scope, material_cleanup, membership, outside, target_scope


def mesh(uid, faces=100, **changes):
    return {"uuid": uid, "path": "|rig|hair|" + uid, "shape_uuid": uid + "Shape",
            "shape": "|rig|hair|" + uid + "|shape", "faces": faces, "vertices": faces,
            "bbox": [0, 0, 0, 1, 1, 1], "parent": "|rig|hair", "skin": {
                "name": uid + "Skin", "uuid": uid + "SkinId", "method": 0, "normalize": 1},
            "display": {}, "shape_display": {}, "visibility_signature": "common-switch",
            "protected_reasons": [], "category_candidate": "hair", **changes}


def scene():
    return {"policy_version": inventory.POLICY_VERSION, "scene_path": "/managed/source.ma",
            "scene_fingerprint": "snapshot", "meshes": [mesh("a"), mesh("b"), mesh("face", 500)]}


def decisions():
    return {"policy_version": inventory.POLICY_VERSION, "scene_path": "/managed/source.ma",
            "scene_fingerprint": "snapshot", "character": "pengMoWang",
            "reduce_entries": [{"source_uuid": "a", "keep_face_ratio": .5,
                                "output_name": "pengMoWang_hairPiece", "reason": "dense class representative"}],
            "merge_groups": [{"sources": ["a", "b"], "output_name": "pengMoWang_hair",
                              "reason": "same parent and driven visibility; compatible skin"}],
            "protect": [{"uuid": "face", "reason": "preserve facial topology"}]}


def test_processing_overlap_is_only_reduce_then_merge_and_protection_counts_as_coverage():
    checked = policy.validate(scene(), decisions())
    assert checked["changed"] == ["a", "b"]
    assert checked["full_scene_coverage"] is True
    assert checked["weights"]["max_influences"] == 12


def test_generated_proxy_disables_inherited_subdivision_without_touching_source(monkeypatch):
    values={'sourceShape.displaySmoothMesh':2,'targetShape.displaySmoothMesh':2}
    class Cmds:
        def listRelatives(self,node,**kwargs):return ['|parent'] if kwargs.get('parent') else ['targetShape']
        def setAttr(self,plug,value):values[plug]=value
    monkeypatch.setattr(core.safety,'resolve',lambda *args:'|parent|target')
    monkeypatch.setattr(core.safety,'copy_display',lambda *args:None)
    monkeypatch.setattr(core.membership,'transfer',lambda *args:None)
    monkeypatch.setattr(core.safety,'check_result',lambda *args,**kwargs:{'skin_cluster':''})
    normalized={'by_id':{'source':{'path':'|parent|source','shape':'sourceShape','parent':'|parent'}},
        'reductions':[{'source_uuid':'source','output_name':'role_hair'}],'merges':[]}
    core._outputs(Cmds(),normalized,[{'source_uuid':'source','result_uuid':'target'}],[])
    assert values=={'sourceShape.displaySmoothMesh':2,'targetShape.displaySmoothMesh':0}


@pytest.mark.parametrize("change, message", [
    (lambda p: p.update(scene_fingerprint="old"), "stale"),
    (lambda p: p.update(scene_path="/other.ma"), "scene_path"),
    (lambda p: p.update(protect=[]), "Every scoped"),
    (lambda p: p["protect"].append({"uuid": "a", "reason": "protect"}), "protected/kept"),
    (lambda p: p["reduce_entries"][0].update(keep_face_ratio=0), "keep_face_ratio"),
    (lambda p: p["reduce_entries"][0].update(keep_face_ratio=True), "keep_face_ratio"),
    (lambda p: p["reduce_entries"][0].update(output_name="hair"), "character prefix"),
    (lambda p: p["merge_groups"][0].update(sources=["a", "a"]), "duplicate"),
    (lambda p: p.update(weights={"threshold": float("nan")}), "threshold"),
    (lambda p: p.update(weights={"max_influences": True}), "positive integer"),
])
def test_plan_rejects_stale_incomplete_or_ambiguous_decisions(change, message):
    candidate = decisions()
    change(candidate)
    with pytest.raises(ValueError, match=message):
        policy.validate(scene(), candidate)


def test_partial_scope_remains_explicit():
    candidate = decisions()
    candidate.update(scope=["a", "b"], protect=[])
    checked = policy.validate(scene(), candidate)
    assert checked["full_scene_coverage"] is False
    assert checked["unscoped_meshes"] == 1


def test_visibility_switch_or_skin_method_mismatch_prevents_merge():
    for changes in ({"visibility_signature": "different"}, {"skin": {"method": 1, "normalize": 1}}):
        snapshot = scene()
        snapshot["meshes"][1].update(changes)
        with pytest.raises(ValueError, match="Merge members differ"):
            policy.validate(snapshot, decisions())


def test_unknown_deformer_protected_even_if_name_says_hair():
    snapshot = scene()
    snapshot["meshes"][0]["protected_reasons"] = ["non_skin_deformer"]
    with pytest.raises(ValueError, match="unsupported dependency"):
        policy.validate(snapshot, decisions())


def test_representatives_bound_model_attention_for_a_thousand_feathers():
    rows = [mesh(str(i), faces=100 + i % 20, category_candidate="feather") for i in range(1000)]
    rows.append(mesh("outlier", faces=50000, category_candidate="feather"))
    summary = inventory.representatives(rows)["feather"]
    assert summary["mesh_count"] == 1001
    assert len(summary["representatives"]) == 3
    assert summary["largest_outliers"][0]["uuid"] == "outlier"


def test_business_workflow_has_one_canonical_owner_and_explicit_foreground_entry():
    import json
    import yaml
    root = Path(__file__).parents[1]
    raw = yaml.safe_load((root / "src/cgi_pipeline/capabilities/maya/asset/proxy_rig/capability.yaml").read_text(encoding="utf-8"))
    assert raw["api_id"] == "maya.asset.proxy_rig"
    assert raw["handler"] == "cgi_pipeline.capabilities.maya.asset.proxy_rig.adapter:execute"
    assert raw["inputs"]["plan"]["type"] == "object"
    assert raw["inputs"]["mode"]["choices"] == ["prepare", "inspect", "preview", "apply", "finalize_preview", "finalize"]
    assert "foreground" in raw["help"]["usage"]
    draft = json.loads((root / "workflows/rig_master_to_proxy_rig.json").read_text(encoding="utf-8"))
    assert draft["status"] == "draft"
    assert "parameters" not in draft
    assert draft["steps"][0]["parameters"] == {"mode": "inspect", "character": "{{input.asset_name}}"}


def test_no_master_overwrite_or_master_version_name(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "managed", lambda value, **_: Path(value).resolve())
    source = tmp_path / "pengMoWang_rigMaster_v003.ma"
    with pytest.raises(ValueError, match="independent"):
        safety.output_path(str(source), "pengMoWang", str(source))
    target = tmp_path / "pengMoWang_proxyRig_v002.ma"
    assert safety.output_path(str(target), "pengMoWang", str(source)) == target


def test_source_geometry_consumers_cannot_be_silently_deleted():
    class Cmds:
        def listConnections(self, node, **_):
            return ["facialBlend"] if node.endswith("shape") else []

        def nodeType(self, node):
            return "blendShape"
        def listRelatives(self, *_args, **_kwargs): return []
        def listHistory(self, *_args, **_kwargs): return []
        def ls(self, node, **_kwargs): return [node + "Uuid"]
        def objExists(self, _node): return False

    assert "facialBlend" in safety.source_consumers(Cmds(), {"path": "|hair", "shape": "|hair|shape", "deformers": []})


def test_display_copy_keeps_the_original_driver():
    calls = []

    class Cmds:
        def objExists(self, _): return True
        def getAttr(self, _, **kwargs): return False if kwargs.get("lock") else True
        def listConnections(self, plug, **_):
            if plug.startswith("source.") or any(call[1] == plug for call in calls):
                return ["styleCtrl.hair"]
            return []
        def connectAttr(self, source, target, **kwargs): calls.append((source, target, kwargs))

    safety.copy_display(Cmds(), "source", "result")
    assert ("styleCtrl.hair", "result.visibility", {}) in calls


def test_object_sets_and_layers_transfer_while_component_sets_are_detected():
    calls = []

    class Cmds:
        def listSets(self, **_): return ["body_msh_set", "vertexSet", "skinSet", "materialSG"]
        def about(self, **_): return False
        def getPanel(self, **_): return []
        def nodeType(self, node): return "shadingEngine" if node == "materialSG" else "objectSet"
        def listConnections(self, node, **kwargs):
            if kwargs.get("type") == "geometryFilter": return ["skin"] if node == "skinSet" else []
            return ["hairLayer"] if node == "|rig|hair" else []
        def sets(self, node, **kwargs):
            if kwargs.get("query"):
                return ["|rig|hair.vtx[0:3]"] if node == "vertexSet" else ["|rig|hair"]
            calls.append(("set", node, kwargs))
        def listRelatives(self, *_, **__): return ["|rig|result|shape"]
        def editDisplayLayerMembers(self, *args, **kwargs): calls.append(("layer", args, kwargs))

    data = membership.inspect(Cmds(), "|rig|hair", "|rig|hair|shape", {})
    assert data["object_sets"] == ["body_msh_set"]
    assert data["component_sets"] == ["vertexSet"]
    membership.transfer(Cmds(), {"memberships": data}, "|rig|result")
    assert ("set", "|rig|result", {"addElement": "body_msh_set"}) in calls
    assert any(call[0] == "layer" for call in calls)


@pytest.mark.parametrize("marker_fails, expected_undos", [(False, 1), (True, 0)])
def test_first_geometry_refusal_never_undoes_the_users_previous_action(monkeypatch, marker_fails, expected_undos):
    class Cmds:
        def __init__(self):
            self.ids, self.undos, self.previous_action = {"source"}, 0, "user_pose"
            self.chunk = ""
        def ls(self, **kwargs): return [] if kwargs.get("selection") else sorted(self.ids)
        def undoInfo(self, **kwargs):
            if kwargs.get("openChunk"): self.chunk = kwargs["chunkName"]
            if kwargs.get("undoName"): return self.chunk
        def createNode(self, *_args, **_kwargs):
            if marker_fails: raise RuntimeError("native create failed before mutation")
            self.ids.add("marker")
            return "marker"
        def undo(self):
            self.undos += 1
            if "marker" in self.ids: self.ids.remove("marker")
            else: self.previous_action = "UNDONE"

    cmds = Cmds()
    snapshot = {"scene_path": "/managed/source.ma", "scene_fingerprint": "before"}
    monkeypatch.setattr(core, "_reduce", lambda *_, **__: (_ for _ in ()).throw(ValueError("not a neutral supported binding")))
    monkeypatch.setattr(inventory, "inspect", lambda *_, **__: snapshot)
    with pytest.raises(RuntimeError, match="Proxy construction failed"):
        core.apply(cmds, {}, {}, snapshot, {}, Path("unused.ma"))
    assert cmds.undos == expected_undos
    assert cmds.previous_action == "user_pose"
    assert cmds.ids == {"source"}


def test_cache_scope_uses_shared_owner_and_keeps_multiple_outputs_visible():
    calls = []

    class Cmds:
        def ls(self, path, **_): return ["|Group|Geometry|cache"] if path.startswith("|Group") else []
        def listRelatives(self, node, **_): return [node + "|shape", node + "|secondShape"]

    class Collector:
        def resolve_cache_group(self, value): calls.append(("resolve", value)); return value
        def mesh_transforms_under(self, value): calls.append(("mesh", value)); return [value + "|hair"]
        def get_deform_input(self, value): calls.append(("output", value)); return value + "|shape", None

    root, shapes, _ = target_scope.resolve(Cmds(), "ysj", collector=Collector(), config={
        "stages": {"rig": {"geom_roots": ["|Group|Geometry|cache", "|*|geo"]}}})
    assert root == "|Group|Geometry|cache"
    assert len(shapes) == 2
    assert [call[0] for call in calls] == ["resolve", "mesh", "output"]


def test_cache_scope_does_not_reuse_the_shared_leaf_fallback():
    class Cmds:
        def ls(self, *_args, **_kwargs): return ["|Group|Geometry|cache"]
    class Collector:
        def resolve_cache_group(self, _): return "|unrelated|cache"
    with pytest.raises(ValueError, match="unexpected fallback"):
        target_scope.resolve(Cmds(), "ysj", collector=Collector(), config={"stages": {
            "rig": {"geom_roots": ["|Group|Geometry|cache"]}}})


def test_resolved_cache_refresh_keeps_the_same_scene_fingerprint(monkeypatch):
    root = "|Group|Geometry|cache"
    monkeypatch.setattr(target_scope, "resolve", lambda _cmds, _project, selected: (
        root, ["shape"], [root] if selected else [root, "|*|geo"]))
    monkeypatch.setattr(inventory, "row", lambda *_: mesh("a"))
    monkeypatch.setattr(outside, "inspect", lambda *_: [])
    class Cmds:
        def file(self, **_): return "/managed/source.ma"
        def currentTime(self, **_): return 1
    initial = inventory.inspect(Cmds(), project="ysj")
    refreshed = inventory.inspect(Cmds(), project="ysj", cache_group=root)
    assert initial["cache_candidates"] != refreshed["cache_candidates"]
    assert initial["scene_fingerprint"] == refreshed["scene_fingerprint"]


@pytest.mark.parametrize("where", ["cache", "outside"])
def test_explicit_discard_can_cover_cache_remnants_or_external_candidates(where):
    snapshot, candidate = scene(), decisions()
    uuid = "face" if where == "cache" else "unused"
    if where == "cache": candidate["protect"] = []
    else: snapshot["outside_cache"] = [{"uuid": "unused", "path": "|unused", "status": "candidate"}]
    candidate["discard"] = [{"uuid": uuid, "reason": "redundant body remnant", "evidence": "visual and graph inspection"}]
    normalized = policy.validate(snapshot, candidate)
    assert normalized["full_scene_coverage"]
    assert normalized["discard"][0]["uuid"] == uuid


def test_discard_refuses_missing_evidence_or_protected_helpers():
    snapshot, candidate = scene(), decisions()
    snapshot["outside_cache"] = [{"uuid": "helper", "status": "protected"}]
    candidate["discard"] = [{"uuid": "helper", "reason": "hidden"}]
    with pytest.raises(ValueError, match="evidence"):
        policy.validate(snapshot, candidate)
    candidate["discard"][0]["evidence"] = "hidden alone"
    with pytest.raises(ValueError, match="effective auxiliary"):
        policy.validate(snapshot, candidate)


def test_external_messages_and_script_references_preserve_helpers():
    class Cmds:
        def ls(self, node, **_): return ["id_" + node]
        def listHistory(self, *_, **__): return []
        def listRelatives(self, *_, **__): return []
        def referenceQuery(self, *_, **__): return False
        def lockNode(self, *_, **__): return [False]
        def listConnections(self, node, **_): return ["proxyDriver"] if node.endswith("shape") else []
        def nodeType(self, *_args, **_kwargs): return "network"
        def objExists(self, _): return False
    row = outside.audit(Cmds(), "|helper", ["|helper|shape"], [("script1", 'cmds.getAttr("helper.visibility")')])
    assert row["status"] == "protected"
    assert "effective_outgoing_or_message_connection" in row["protected_reasons"]
    assert "expression_or_script_name_dependency" in row["protected_reasons"]


@pytest.mark.parametrize("illegal", [False, True, "connections", "rig_texture"])
def test_native_material_cleanup_protects_math_assigned_sg_and_restores_locks(monkeypatch, illegal):
    import sys
    import types

    class Cmds:
        def __init__(self):
            self.nodes = {"unusedMat", "unusedTex", "rigTexture", "rigMath", "assignedSG", "mesh", "controller"}
            self.locks = {node: False for node in self.nodes}
            self.connected = True
        def ls(self, node=None, **kwargs):
            if kwargs.get("uuid"): return ["id_" + item for item in ([node] if node else sorted(self.nodes))]
            if kwargs.get("materials"): return ["unusedMat"]
            if kwargs.get("defaultNodes"): return []
            if kwargs.get("type") == "shadingEngine": return ["assignedSG"]
            if kwargs.get("type") == ["file"]: return ["unusedTex", "rigTexture"]
            if kwargs.get("type") == ["multiplyDivide"]: return ["rigMath"]
            return sorted(self.nodes)
        def listNodeTypes(self, value): return {"texture": ["file"], "utility": ["multiplyDivide"]}.get(value, [])
        def listConnections(self, node, **kwargs):
            if node == "rigMath" and self.connected:
                return ["rigMath.input1X", "rigTexture.outColorR", "rigMath.outputX", "controller.input"] if kwargs.get("connections") else ["rigTexture", "controller"]
            if node == "rigTexture" and self.connected:
                return ["rigTexture.outColorR", "rigMath.input1X"] if kwargs.get("connections") else ["rigMath"]
            return []
        def listHistory(self, nodes, **kwargs):
            return ["rigTexture"] if "rigMath" in nodes and not kwargs.get("future") else []
        def sets(self, *_args, **_kwargs): return ["mesh"]
        def nodeType(self, node): return "transform" if node in ("mesh", "controller") else "multiplyDivide"
        def referenceQuery(self, *_args, **_kwargs): return False
        def lockNode(self, node, **kwargs):
            if kwargs.get("query"): return [self.locks[node]]
            self.locks[node] = kwargs["lock"]
        def objExists(self, node): return node in self.nodes

    cmds = Cmds()
    def evaluate(command):
        if command.startswith("whatIs"): return "Mel procedure found in Autodesk installation"
        if command.startswith("source"): return None
        assert cmds.locks["rigMath"] and cmds.locks["assignedSG"] and cmds.locks["rigTexture"]
        cmds.nodes -= {"unusedMat", "unusedTex"}
        if illegal is True: cmds.nodes.remove("controller")
        if illegal == "connections": cmds.connected = False
        if illegal == "rig_texture": cmds.nodes.remove("rigTexture")
        return 2
    maya = types.ModuleType("maya")
    mel = types.ModuleType("maya.mel"); mel.eval = evaluate; maya.mel = mel
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.mel", mel)
    monkeypatch.setattr(outside, "script_sources", lambda _: [])
    if illegal:
        with pytest.raises(RuntimeError, match="protected"):
            material_cleanup.run(cmds, True)
    else:
        result = material_cleanup.run(cmds, True)
        assert len(result["deleted"]) == 2
        assert result["critical_connection_guard"]
    assert cmds.locks["rigMath"] is False and cmds.locks["assignedSG"] is False
    if "rigTexture" in cmds.nodes:
        assert cmds.locks["rigTexture"] is False


def test_self_skin_and_ordinary_selection_set_do_not_hide_discard_candidates():
    class Cmds:
        def ls(self, node, **_): return ["id_" + node]
        def listHistory(self, *_, **__): return ["ownSkin"]
        def listRelatives(self, *_, **__): return []
        def referenceQuery(self, *_, **__): return False
        def lockNode(self, *_, **__): return [False]
        def listConnections(self, node, **_):
            return ["ownSkin", "ordinarySet"] if node == "|remnant" else []
        def nodeType(self, node, **kwargs):
            if kwargs.get("inherited"): return ["geometryFilter", "skinCluster"]
            return "skinCluster" if node == "ownSkin" else "objectSet"
        def skinCluster(self, *_, **__): return ["|remnant|shape"]
        def objExists(self, _): return False
    row = outside.audit(Cmds(), "|remnant", ["|remnant|shape"], [])
    assert row["status"] == "candidate"
    assert row["self_skin_nodes"] == ["ownSkin"]


def test_assigned_sg_upstream_and_protected_to_candidate_edges_are_guarded():
    class Cmds:
        def listHistory(self, nodes, **kwargs):
            return ["assignedMat", "assignedTexture"] if "assignedSG" in nodes and not kwargs.get("future") else []
        def listConnections(self, *_args, **_kwargs):
            return ["assignedSG.surfaceShader", "assignedMat.outColor"]
    protected = material_cleanup._protected_render_graph(Cmds(), {"assignedSG"}, {
        "assignedSG", "assignedMat", "assignedTexture"})
    assert protected == {"assignedSG", "assignedMat", "assignedTexture"}
    assert material_cleanup._connections(Cmds(), {"assignedSG"}) == {
        ("assignedMat.outColor", "assignedSG.surfaceShader")}


@pytest.mark.parametrize("external", [False, True])
def test_deleting_transform_checks_orig_children_but_allows_owned_history(external):
    class Cmds:
        def ls(self, node, **_): return ["id_" + node]
        def listRelatives(self, node, **kwargs):
            if kwargs.get("shapes"): return ["|mesh|output", "|mesh|orig"]
            if kwargs.get("allParents"): return ["|mesh"]
            return []
        def listHistory(self, *_args, **_kwargs): return ["ownSkin", "ownParts"]
        def nodeType(self, node, **kwargs):
            if kwargs.get("inherited"): return ["geometryFilter"] if node == "ownSkin" else []
            return {"ownSkin": "skinCluster", "ownParts": "groupParts", "faceBS": "blendShape"}.get(node, "mesh")
        def listConnections(self, node, **kwargs):
            return ["ownParts"] + (["faceBS"] if external else []) if node == "|mesh|orig" else []
        def objExists(self, _): return False
        def referenceQuery(self, *_args, **_kwargs): return False
        def lockNode(self, *_args, **_kwargs): return [False]
        def skinCluster(self, *_args, **_kwargs): return ["|mesh|output"]
    cmds = Cmds()
    deletion = deletion_scope.inspect(cmds, "|mesh", ["|mesh|output"])
    assert any(not item["is_output"] for item in deletion["shapes"])
    assert bool(deletion["external_consumers"]) is external
    discarded = outside.audit(cmds, "|mesh", ["|mesh|output"], [])
    assert (discarded["status"] == "protected") is external
    replacing = safety.source_consumers(cmds, {"path": "|mesh", "shape": "|mesh|output", "deformers": [{"name": "ownSkin"}]})
    assert bool(replacing) is external


@pytest.mark.parametrize('attached', [False, True])
def test_display_input_is_not_a_consumer_but_transform_attachment_is(attached):
    class Cmds:
        def ls(self, node, **_): return ["id_" + node]
        def listHistory(self, *_args, **_kwargs): return []
        def listRelatives(self, node, **kwargs):
            return ["|styleGrp"] if node == "|styleGrp|proxy" and kwargs.get("parent") else []
        def referenceQuery(self, *_args, **_kwargs): return False
        def lockNode(self, *_args, **_kwargs): return [False]
        def listConnections(self, node, **_kwargs):
            if node == '|styleGrp.visibility':return ['ctrl.style']
            if attached and node == '|styleGrp|proxy.translate':return ['constraint.outputTranslate']
            return []
        def objExists(self, node): return node in ('|styleGrp.visibility','|styleGrp|proxy.translate')
    result = outside.audit(Cmds(), "|styleGrp|proxy", ["|styleGrp|proxy|shape"], [])
    assert result["status"] == ('protected' if attached else 'candidate')
    assert result["ancestor_drivers"] == ["ctrl.style"]
    assert ('transform_attachment_or_constraint' in result['protected_reasons']) is attached


def test_merge_partition_keeps_compatible_influence_unions_together_and_is_order_stable():
    from cgi_pipeline.capabilities.maya.asset.proxy_rig.plan import partition_merge_candidates
    rows = [{"path": "|g|m" + str(i), "uuid": str(i), "protected_reasons": [],
             "visibility_signature": "same", "memberships": {}, "bbox": [i, 0, 0, i+1, 1, 1],
             "vertices": 100, "skin": {"method": 0, "normalize": 1, "influences": ["j"+str(i)]}}
            for i in range(6)]
    a = partition_merge_candidates(rows)
    b = partition_merge_candidates(list(reversed(rows)))
    assert a == b
    assert sorted(r["uuid"] for batch in a for r in batch) == [str(i) for i in range(6)]
    assert len(a) == 1
