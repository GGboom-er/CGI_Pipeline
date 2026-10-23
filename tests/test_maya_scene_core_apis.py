from cgi_pipeline.capabilities.maya import scene_core
from cgi_pipeline.contracts import ApiContext


class FakeCmds:
    def __init__(self):
        self.nodes = {
            "|root": {"type": "transform", "parent": "", "t": [0, 0, 0], "r": [0, 0, 0], "s": [1, 1, 1]},
            "|root|child": {"type": "transform", "parent": "|root", "t": [1, 2, 3], "r": [4, 5, 6], "s": [1, 1, 1]},
        }
        self.selection = ["|root|child"]

    def ls(self, value=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if value is None:
            values = list(self.nodes)
        elif value in self.nodes:
            values = [value]
        elif value == "*":
            values = list(self.nodes)
        else:
            values = [path for path in self.nodes if path.rsplit("|", 1)[-1] == value]
        node_type = kwargs.get("type")
        return [path for path in values if not node_type or self.nodes[path]["type"] == node_type]

    def objExists(self, node):
        return node in self.nodes

    def nodeType(self, node):
        return self.nodes[node]["type"]

    def createNode(self, node_type, **kwargs):
        parent = kwargs.get("parent") or ""
        name = kwargs.get("name") or f"{node_type}1"
        path = f"{parent}|{name}" if parent else f"|{name}"
        if path in self.nodes:
            raise RuntimeError("name already exists")
        self.nodes[path] = {"type": node_type, "parent": parent, "t": [0, 0, 0], "r": [0, 0, 0], "s": [1, 1, 1]}
        return path

    def rename(self, node, name):
        parent = self.nodes[node]["parent"]
        new_path = f"{parent}|{name}" if parent else f"|{name}"
        self.nodes[new_path] = self.nodes.pop(node)
        self.selection = [new_path if value == node else value for value in self.selection]
        return new_path

    def delete(self, node):
        del self.nodes[node]

    def duplicate(self, node, **kwargs):
        parent = self.nodes[node]["parent"]
        name = kwargs.get("name") or f"{node.rsplit('|', 1)[-1]}Copy"
        path = f"{parent}|{name}" if parent else f"|{name}"
        self.nodes[path] = {**self.nodes[node], "t": list(self.nodes[node]["t"])}
        return [path]

    def parent(self, node, parent=None, **kwargs):
        new_parent = "" if kwargs.get("world") else parent
        name = node.rsplit("|", 1)[-1]
        new_path = f"{new_parent}|{name}" if new_parent else f"|{name}"
        self.nodes[new_path] = self.nodes.pop(node)
        self.nodes[new_path]["parent"] = new_parent
        return [new_path]

    def select(self, items=None, **kwargs):
        values = list(items or [])
        if kwargs.get("clear"):
            self.selection = []
        elif kwargs.get("add"):
            self.selection.extend(value for value in values if value not in self.selection)
        elif kwargs.get("deselect"):
            self.selection = [value for value in self.selection if value not in values]
        else:
            self.selection = values

    def xform(self, node, **kwargs):
        row = self.nodes[node]
        if kwargs.get("query"):
            if kwargs.get("translation"): return list(row["t"])
            if kwargs.get("rotation"): return list(row["r"])
            if kwargs.get("scale"): return list(row["s"])
        if "translation" in kwargs: row["t"] = list(kwargs["translation"])
        if "rotation" in kwargs: row["r"] = list(kwargs["rotation"])
        if "scale" in kwargs: row["s"] = list(kwargs["scale"])


def _context(cmds, api_id):
    return ApiContext.from_mapping({"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"}, "maya")


def _call(fn, api_id, params, cmds=None):
    cmds = cmds or FakeCmds()
    return cmds, fn(params, _context(cmds, api_id))


def test_find_is_bounded_and_returns_long_paths():
    _, result = _call(scene_core.find_nodes, "maya.scene.nodes.find", {"patterns": ["*"], "node_type": "transform", "max_items": 1})
    assert result["status"] == "SUCCESS"
    assert result["output"]["count"] == 1
    assert result["output"]["items"][0]["path"].startswith("|")
    assert result["output"]["truncated"] is True


def test_node_create_rename_duplicate_and_delete_contracts():
    cmds = FakeCmds()
    _, created = _call(scene_core.create_nodes, "maya.scene.nodes.create", {"items": [{"node_type": "transform", "name": "made", "parent": "|root"}], "max_items": 10}, cmds)
    assert created["output"]["items"][0]["after"] == "|root|made"
    _, renamed = _call(scene_core.rename_nodes, "maya.scene.nodes.rename", {"items": [{"node": "|root|made", "new_name": "renamed"}], "max_items": 10}, cmds)
    assert renamed["output"]["items"][0]["before"] == "|root|made"
    _, copied = _call(scene_core.duplicate_nodes_many, "maya.scene.nodes.duplicate_many", {"items": [{"node": "|root|renamed", "name": "copy"}], "max_items": 10}, cmds)
    assert copied["output"]["items"][0]["after"] == "|root|copy"
    _, deleted = _call(scene_core.delete_nodes, "maya.scene.nodes.delete", {"nodes": ["|root|copy"], "max_items": 10}, cmds)
    assert deleted["status"] == "SUCCESS" and not cmds.objExists("|root|copy")


def test_hierarchy_selection_and_transform_round_trip():
    cmds = FakeCmds()
    _, parented = _call(scene_core.edit_hierarchy_many, "maya.scene.hierarchy.edit_many", {"items": [{"node": "|root|child", "action": "unparent"}], "max_items": 10}, cmds)
    assert parented["output"]["items"][0]["after"] == "|child"
    _, selected = _call(scene_core.set_selection, "maya.scene.selection.set", {"nodes": ["|root", "|child"], "mode": "replace", "max_items": 10}, cmds)
    assert selected["output"]["after"] == ["|root", "|child"]
    _, got_selection = _call(scene_core.get_selection, "maya.scene.selection.get", {"max_items": 1}, cmds)
    assert got_selection["output"]["truncated"] is True
    _, set_result = _call(scene_core.set_transforms_many, "maya.scene.transforms.set_many", {"items": [{"node": "|child", "translation": [9, 8, 7]}], "space": "world", "max_items": 10}, cmds)
    assert set_result["output"]["items"][0]["after"]["translation"] == [9, 8, 7]
    _, get_result = _call(scene_core.get_transforms, "maya.scene.transforms.get", {"nodes": ["|child"], "space": "world", "max_items": 10}, cmds)
    assert get_result["output"]["items"][0]["translation"] == [9, 8, 7]


def test_write_failure_is_reported_in_receipt():
    _, result = _call(scene_core.rename_nodes, "maya.scene.nodes.rename", {"items": [{"node": "missing", "new_name": "x"}], "max_items": 10})
    assert result["status"] == "ERROR"
    assert result["error_code"] == "MAYA_SCENE_CORE_OPERATION_FAILED"
    assert "does not exist" in result["error"]
