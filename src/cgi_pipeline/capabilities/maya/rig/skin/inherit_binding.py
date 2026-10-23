"""Strict batch facade over the same native binding owner used by mesh reduction."""
from cgi_pipeline.capabilities.maya.asset.mesh_reduce import geometry, skin
from cgi_pipeline.capabilities.maya.rig.rig_ops_common import undo_mutation


def run(params, cmds):
    pairs = params.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("inherit_source requires explicit identity-guarded pairs")
    prepared, targets, sources = [], set(), set()
    for pair in pairs:
        source, target = pair.get("source", {}), pair.get("target", {})
        src, dst = source.get("path"), target.get("path")
        source_shape = geometry.resolve(src, source.get("expected_uuid"))
        target_shape = geometry.resolve(dst, target.get("expected_uuid"))
        if dst in targets:
            raise ValueError("Duplicate target")
        targets.add(dst)
        sources.add(src)
        source_skin = geometry.protect_dependencies(src, source_shape)
        if not source_skin:
            raise ValueError("Source has no skinCluster")
        if cmds.listConnections(target_shape + ".inMesh", source=True, destination=False):
            raise ValueError("Strict inheritance requires a clean, unbound target")
        binding = skin.binding_context(source_skin, source_shape)
        prepared.append((pair, target_shape, binding))
    if targets & sources:
        raise ValueError("Targets must be distinct from all sources")
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Native undo must be enabled")

    def mutate():
        rows = []
        for pair, shape, binding in prepared:
            target_skin = skin.bind_copy(binding, pair["target"]["path"])
            actual = skin.binding_context(target_skin, shape)
            checks = {"bindings": skin.bindings_equal(actual["bindings"], binding["bindings"]),
                      "geom_matrix": skin.matrix_equal(actual["geom_matrix"], binding["geom_matrix"]),
                      "settings": actual["settings"] == binding["settings"]}
            if not all(checks.values()):
                raise RuntimeError("Inherited binding context mismatch: " + target_skin)
            weights = skin.capture_weights(target_skin, shape)
            rows.append({"source": pair["source"], "target": pair["target"],
                "source_skin_cluster": binding["skin"], "target_skin_cluster": target_skin,
                "influence_count": len(actual["bindings"]), "binding_checks": checks,
                "valid_weight_rows": len(weights["weights"]), "surface_association": "closestPoint",
                "lossy_mapping": "Source binding preserved; changed topology uses native closest-surface transfer."})
        return {"binding_policy": "inherit_source", "rows": rows}
    return undo_mutation(cmds, "maya.rig.skin.inherit_source", mutate)
