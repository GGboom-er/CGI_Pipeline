from types import SimpleNamespace

from cgi_pipeline.capabilities.blender import scene_core
from cgi_pipeline.contracts import ApiContext


class FakeVector(list):
    def to_list(self):
        return list(self)


class FakeDataBlock:
    def __init__(self, name, kind="MESH"):
        self.name = name
        self.kind = kind

    def copy(self):
        return FakeDataBlock(f"{self.name}Copy", self.kind)


class FakeDataCollection:
    def __init__(self, factory):
        self._items = {}
        self.factory = factory

    def __iter__(self):
        return iter(self._items.values())

    def get(self, name):
        return self._items.get(name)

    def new(self, name, *args):
        value = self.factory(name, *args)
        self._items[value.name] = value
        return value

    def remove(self, value, *args, **kwargs):
        del args, kwargs
        self._items.pop(value.name, None)


class FakeCollectionObjects:
    def __init__(self, owner):
        self.owner = owner

    def link(self, obj):
        if obj not in self.owner.members:
            self.owner.members.append(obj)
        if self.owner not in obj.users_collection:
            obj.users_collection.append(self.owner)
        obj._bpy.data.objects._items[obj.name] = obj


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.members = []
        self.objects = FakeCollectionObjects(self)


class FakeObject:
    def __init__(self, name, data=None, object_type="MESH"):
        self._name = name
        self.data = data
        self.type = "EMPTY" if data is None else object_type
        self.parent = None
        self.users_collection = []
        self.location = FakeVector([0.0, 0.0, 0.0])
        self.rotation_euler = FakeVector([0.0, 0.0, 0.0])
        self.scale = FakeVector([1.0, 1.0, 1.0])
        self.rotation_mode = "XYZ"
        self.matrix_world = [1.0] * 16
        self.hide_viewport = False
        self._selected = False
        self._custom = {}
        self._bpy = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if self._bpy and self._name in self._bpy.data.objects._items:
            del self._bpy.data.objects._items[self._name]
            self._bpy.data.objects._items[value] = self
        self._name = value

    @property
    def children(self):
        return [obj for obj in self._bpy.data.objects if obj.parent is self]

    def copy(self):
        copied = FakeObject(f"{self.name}Copy", self.data, self.type)
        copied.location = FakeVector(self.location)
        copied.rotation_euler = FakeVector(self.rotation_euler)
        copied.scale = FakeVector(self.scale)
        copied.parent = self.parent
        copied._bpy = self._bpy
        return copied

    def select_set(self, value):
        self._selected = bool(value)
        self._bpy.context.selected_objects = self._bpy.selected

    def __contains__(self, key):
        return key in self._custom

    def __getitem__(self, key):
        return self._custom[key]

    def __setitem__(self, key, value):
        self._custom[key] = value


class FakeObjects(FakeDataCollection):
    def __init__(self, bpy):
        self.bpy = bpy
        super().__init__(lambda name, data=None: FakeObject(name, data))

    def new(self, name, data=None):
        obj = FakeObject(name, data, getattr(data, "kind", "EMPTY"))
        obj._bpy = self.bpy
        return obj

    def remove(self, obj, do_unlink=False):
        del self._items[obj.name]
        if do_unlink:
            for collection in list(obj.users_collection):
                collection.members.remove(obj)
                obj.users_collection.remove(collection)


class ActiveObjects:
    active = None


class FakeLink:
    def __init__(self, source, source_socket, target, target_socket):
        self.from_node = SimpleNamespace(name=source)
        self.from_socket = SimpleNamespace(name=source_socket)
        self.to_node = SimpleNamespace(name=target)
        self.to_socket = SimpleNamespace(name=target_socket)


class FakeBpy:
    def __init__(self):
        self.data = SimpleNamespace()
        self.data.objects = FakeObjects(self)
        self.data.meshes = FakeDataCollection(lambda name: FakeDataBlock(name, "MESH"))
        self.data.curves = FakeDataCollection(lambda name, _kind: FakeDataBlock(name, "CURVE"))
        self.data.armatures = FakeDataCollection(lambda name: FakeDataBlock(name, "ARMATURE"))
        self.data.cameras = FakeDataCollection(lambda name: FakeDataBlock(name, "CAMERA"))
        self.data.lights = FakeDataCollection(lambda name, _kind: FakeDataBlock(name, "LIGHT"))
        self.data.collections = FakeDataCollection(lambda name: FakeCollection(name))
        self.data.materials = FakeDataCollection(lambda name: SimpleNamespace(name=name, node_tree=None))
        self.data.worlds = FakeDataCollection(lambda name: SimpleNamespace(name=name, node_tree=None))
        self.data.node_groups = FakeDataCollection(lambda name: SimpleNamespace(name=name, links=[]))
        root_collection = self.data.collections.new("Scene")
        self.context = SimpleNamespace(
            scene=SimpleNamespace(collection=root_collection),
            view_layer=SimpleNamespace(objects=ActiveObjects()),
        )

    @property
    def selected(self):
        return [obj for obj in self.data.objects if obj._selected]


def _context(bpy, api_id):
    bpy.context.selected_objects = bpy.selected
    return ApiContext.from_mapping(
        {"bpy_module": bpy, "_api_id": api_id, "_api_version": "1.0.0"}, "blender")


def _call(fn, api_id, params, bpy=None):
    bpy = bpy or FakeBpy()
    result = fn(params, _context(bpy, api_id))
    return bpy, result


def _seed():
    bpy = FakeBpy()
    root = bpy.data.objects.new("Root", None)
    child = bpy.data.objects.new("Child", bpy.data.meshes.new("ChildData"))
    bpy.context.scene.collection.objects.link(root)
    bpy.context.scene.collection.objects.link(child)
    child.parent = root
    child.select_set(True)
    bpy.context.view_layer.objects.active = child
    return bpy


def test_find_inspect_and_bounds():
    bpy = _seed()
    _, found = _call(scene_core.find_nodes, "blender.scene.nodes.find",
                     {"patterns": ["*"], "max_items": 1}, bpy)
    assert found["status"] == "SUCCESS"
    assert found["output"]["count"] == 1
    assert found["output"]["truncated"] is True
    _, inspected = _call(scene_core.inspect_nodes, "blender.scene.nodes.inspect",
                         {"nodes": ["Child", "missing"], "max_items": 10}, bpy)
    assert inspected["output"]["items"][0]["parent"] == "Root"
    assert inspected["output"]["items"][1]["status"] == "ERROR"
    assert inspected["status"] == "PARTIAL"
    assert inspected["output"]["succeeded"] == 1
    assert inspected["output"]["failed"] == 1


def test_node_create_rename_duplicate_and_delete_round_trip():
    bpy = _seed()
    _, created = _call(scene_core.create_nodes, "blender.scene.nodes.create",
                       {"items": [{"object_type": "EMPTY", "name": "Made"}], "max_items": 10}, bpy)
    assert created["output"]["items"][0]["after"] == "Made"
    _, renamed = _call(scene_core.rename_nodes, "blender.scene.nodes.rename",
                       {"items": [{"node": "Made", "new_name": "Renamed"}], "max_items": 10}, bpy)
    assert renamed["output"]["items"][0]["after"] == "Renamed"
    _, copied = _call(scene_core.duplicate_nodes_many, "blender.scene.nodes.duplicate_many",
                      {"items": [{"node": "Child", "name": "Copy", "copy_data": True}], "max_items": 10}, bpy)
    assert copied["output"]["items"][0]["after"] == "Copy"
    assert bpy.data.objects.get("Copy").data is not bpy.data.objects.get("Child").data
    _, deleted = _call(scene_core.delete_nodes, "blender.scene.nodes.delete",
                       {"nodes": ["Renamed"], "max_items": 10}, bpy)
    assert deleted["output"]["items"][0]["after"] is None
    assert bpy.data.objects.get("Renamed") is None


def test_hierarchy_properties_selection_and_local_transform_round_trip():
    bpy = _seed()
    _, hierarchy = _call(scene_core.inspect_hierarchy, "blender.scene.hierarchy.inspect",
                         {"nodes": ["Root"], "max_items": 10}, bpy)
    assert hierarchy["output"]["items"][0]["children"] == ["Child"]
    _, unparented = _call(scene_core.edit_hierarchy_many, "blender.scene.hierarchy.edit_many",
                          {"items": [{"node": "Child", "action": "unparent"}], "max_items": 10}, bpy)
    assert unparented["output"]["items"][0]["after"] is None
    _, properties = _call(scene_core.set_properties_many, "blender.scene.properties.set_many",
                          {"items": [{"node": "Child", "property": "custom:weight", "value": 0.5}], "max_items": 10}, bpy)
    assert properties["output"]["items"][0]["after"] == 0.5
    _, read = _call(scene_core.get_properties, "blender.scene.properties.get",
                    {"items": [{"node": "Child", "property": "custom:weight"}], "max_items": 10}, bpy)
    assert read["output"]["items"][0]["value"] == 0.5
    _, selected = _call(scene_core.set_selection, "blender.scene.selection.set",
                        {"nodes": ["Root"], "mode": "replace", "active": "Root", "max_items": 10}, bpy)
    assert selected["output"]["after"] == ["Root"]
    _, got_selection = _call(scene_core.get_selection, "blender.scene.selection.get",
                             {"max_items": 10}, bpy)
    assert got_selection["output"]["active"] == "Root"
    _, changed = _call(scene_core.set_transforms_many, "blender.scene.transforms.set_many",
                       {"items": [{"node": "Child", "translation": [1, 2, 3]}],
                        "space": "local", "max_items": 10}, bpy)
    assert changed["output"]["items"][0]["after"]["translation"] == [1.0, 2.0, 3.0]
    _, transforms = _call(scene_core.get_transforms, "blender.scene.transforms.get",
                          {"nodes": ["Child"], "space": "local", "max_items": 10}, bpy)
    assert transforms["output"]["items"][0]["translation"] == [1.0, 2.0, 3.0]


def test_node_link_inspection_and_invalid_limit_error_receipt():
    bpy = _seed()
    group = bpy.data.node_groups.new("RigGraph")
    group.links.append(FakeLink("Input", "Value", "Output", "Value"))
    _, links = _call(scene_core.inspect_node_links_many, "blender.scene.node_links.inspect_many",
                     {"items": [{"owner_type": "node_group", "owner": "RigGraph"}], "max_items": 10}, bpy)
    assert links["output"]["items"][0]["links"][0]["from_node"] == "Input"
    _, failed = _call(scene_core.find_nodes, "blender.scene.nodes.find",
                      {"patterns": ["*"], "max_items": 0}, bpy)
    assert failed["status"] == "ERROR"
    assert failed["error_code"] == "BLENDER_SCENE_CORE_OPERATION_FAILED"


def test_create_failure_is_top_level_error_and_leaves_no_orphan_data():
    bpy = _seed()
    before_meshes = list(bpy.data.meshes)
    _, failed = _call(
        scene_core.create_nodes,
        "blender.scene.nodes.create",
        {"items": [{"object_type": "MESH", "name": "Orphan", "collection": "Missing"}],
         "max_items": 10},
        bpy,
    )

    assert failed["status"] == "ERROR"
    assert failed["error_code"] == "BLENDER_SCENE_CORE_ITEMS_FAILED"
    assert failed["output"]["items"][0]["status"] == "ERROR"
    assert bpy.data.objects.get("Orphan") is None
    assert list(bpy.data.meshes) == before_meshes
