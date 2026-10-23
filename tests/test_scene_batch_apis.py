from cgi_pipeline.capabilities.maya import scene_batch
from cgi_pipeline.contracts import ApiContext


class FakeCmds:
    def __init__(self):
        self.values = {"|root.tx": 1.0, "|root.ty": 2.0}
        self.edges = ["|root.tx", "|child.ty"]
    def ls(self, value=None, **kwargs):
        if kwargs.get("uuid"): return ["uuid-root"]
        if value in ("|root", "root"): return ["|root"]
        if value in ("|child", "child"): return ["|child"]
        return []
    def listRelatives(self, node, **kwargs): return []
    def nodeType(self, node): return "transform"
    def objExists(self, plug): return plug in self.values or plug in ("|root", "|child")
    def getAttr(self, plug, **kwargs):
        if kwargs.get("type"): return "double"
        if kwargs.get("lock"): return False
        return self.values[plug]
    def setAttr(self, plug, *values, **kwargs): self.values[plug] = values[0] if len(values) == 1 else list(values)
    def listConnections(self, node, **kwargs):
        if not self.edges:
            return []
        source, destination = self.edges
        pairs = []
        if kwargs.get("source") and destination.split(".", 1)[0] == node:
            pairs.extend((destination, source))
        if kwargs.get("destination") and source.split(".", 1)[0] == node:
            pairs.extend((source, destination))
        return pairs
    def connectAttr(self, source, destination, **kwargs): self.edges = [source, destination]
    def disconnectAttr(self, source, destination): self.edges = []


def _context(cmds):
    return ApiContext.from_mapping({"cmds_module": cmds, "_api_version": "1.0.0"}, "maya")


def test_nodes_and_attributes_batch_contracts():
    cmds = FakeCmds(); context = _context(cmds)
    nodes = scene_batch.inspect_nodes_many({"nodes": ["root"], "max_nodes": 10}, context)
    assert nodes["status"] == "SUCCESS"
    assert nodes["output"]["nodes"][0]["path"] == "|root"
    got = scene_batch.get_attributes_many({"items": [{"node": "|root", "attribute": "tx"}], "max_items": 10}, context)
    assert got["output"]["items"][0]["value"] == 1.0
    set_result = scene_batch.set_attributes_many({"items": [{"node": "|root", "attribute": "tx", "value": 3.0}], "max_items": 10}, context)
    assert set_result["status"] == "SUCCESS"
    assert cmds.values["|root.tx"] == 3.0


def test_connections_and_graph_delta_contracts():
    cmds = FakeCmds(); context = _context(cmds)
    inspected = scene_batch.inspect_connections_many(
        {"nodes": ["root"], "max_nodes": 10, "max_edges": 10}, context)
    assert inspected["output"]["edges"] == [{"source": "|root.tx", "destination": "|child.ty"}]
    edited = scene_batch.edit_connections_many(
        {"items": [{"action": "disconnect", "source": "|root.tx", "destination": "|child.ty"}], "max_items": 10}, context)
    assert edited["status"] == "SUCCESS" and cmds.edges == []
    delta = scene_batch.validate_graph_delta({
        "before": {"nodes": [{"path": "a"}], "edges": []},
        "after": {"nodes": [{"path": "a"}, {"path": "b"}],
                  "edges": [{"source": "a.tx", "destination": "b.ty"}]},
    }, context)
    assert delta["output"]["added_nodes"] == ["b"]
    assert delta["output"]["changed"] is True


def test_bounded_graph_snapshot_contract():
    cmds = FakeCmds(); context = _context(cmds)
    snapshot = scene_batch.snapshot_graph_bounded(
        {"roots": ["root"], "max_nodes": 10, "max_edges": 10}, context)
    assert snapshot["status"] == "SUCCESS"
    assert snapshot["output"]["node_count"] == 2
    assert snapshot["output"]["edges"] == [
        {"source": "|root.tx", "destination": "|child.ty"}
    ]
