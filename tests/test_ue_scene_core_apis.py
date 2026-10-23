from cgi_pipeline.capabilities.ue import scene_core
from cgi_pipeline.contracts import ApiContext


class FakeClass:
    def __init__(self, name): self.name = name
    def get_name(self): return self.name


class FakeActor:
    def __init__(self, name, label=None, actor_class="Actor"):
        self.name = name; self.label = label or name; self.actor_class = actor_class
        self.location = [0, 0, 0]; self.rotation = [0, 0, 0]; self.scale = [1, 1, 1]
        self.properties = {"hidden": False}; self.parent = None; self.modified = 0

    def get_name(self): return self.name
    def get_path_name(self): return f"/Game/Test.Test:PersistentLevel.{self.name}"
    def get_actor_label(self): return self.label
    def set_actor_label(self, value): self.label = value
    def get_class(self): return FakeClass(self.actor_class)
    def get_actor_location(self): return self.location
    def get_actor_rotation(self): return self.rotation
    def get_actor_scale3d(self): return self.scale
    def modify(self): self.modified += 1
    def get_editor_property(self, name): return self.properties[name]
    def set_editor_property(self, name, value): self.properties[name] = value
    def get_attach_parent_actor(self): return self.parent
    def attach_to_actor(self, parent, _socket, *_args): self.parent = parent; return True


class FakeActorSubsystem:
    def __init__(self):
        self.actors = [FakeActor("Cube", "HeroCube", "StaticMeshActor"), FakeActor("Light", actor_class="PointLight")]
        self.selected = [self.actors[0]]

    def get_all_level_actors(self): return list(self.actors)
    def get_selected_level_actors(self): return list(self.selected)
    def set_selected_level_actors(self, actors): self.selected = list(actors)
    def spawn_actor_from_class(self, actor_class, location, rotation):
        actor = FakeActor(f"Actor{len(self.actors) + 1}", actor_class=actor_class.get_name())
        actor.location = list(location); actor.rotation = list(rotation); self.actors.append(actor); return actor
    def duplicate_actor(self, source):
        actor = FakeActor(f"{source.name}Copy", actor_class=source.actor_class)
        actor.properties = dict(source.properties); self.actors.append(actor); return actor
    def destroy_actor(self, actor): self.actors.remove(actor); return True


class FakeTransaction:
    entered = []
    def __init__(self, description): self.description = description
    def __enter__(self): self.entered.append(self.description); return self
    def __exit__(self, *_args): return False


class FakeAttachmentRule:
    KEEP_WORLD = "keep_world"


class FakeUnreal:
    ScopedEditorTransaction = FakeTransaction
    AttachmentRule = FakeAttachmentRule
    Vector = staticmethod(lambda *values: values)
    Rotator = staticmethod(lambda *values: values)


def _context(subsystem, api_id):
    return ApiContext.from_mapping({
        "unreal_module": FakeUnreal,
        "subsystems": {"actor_subsystem": subsystem},
        "actor_classes": {"/Script/Engine.StaticMeshActor": FakeClass("StaticMeshActor")},
        "_api_id": api_id, "_api_version": "1.0.0",
    }, "ue")


def _call(fn, api_id, params, subsystem=None):
    subsystem = subsystem or FakeActorSubsystem()
    return subsystem, fn(params, _context(subsystem, api_id))


def test_find_and_inspect_are_bounded_and_json_safe():
    subsystem, found = _call(scene_core.find_actors, "ue.scene.actors.find", {
        "patterns": ["*"], "class_name": "", "max_items": 1,
    })
    assert found["status"] == "SUCCESS"
    assert found["output"]["truncated"] is True
    assert found["output"]["items"][0]["path"].startswith("/Game/")
    _, inspected = _call(scene_core.inspect_actors, "ue.scene.actors.inspect", {
        "actors": ["HeroCube"], "max_items": 10,
    }, subsystem)
    assert inspected["output"]["items"][0]["location"] == [0, 0, 0]


def test_actor_crud_uses_transactions_and_before_after_contract():
    FakeTransaction.entered.clear(); subsystem = FakeActorSubsystem()
    _, spawned = _call(scene_core.spawn_actors, "ue.scene.actors.spawn", {
        "items": [{"actor_class": "/Script/Engine.StaticMeshActor", "label": "Made",
                   "location": [1, 2, 3], "rotation": [4, 5, 6]}], "max_items": 10,
    }, subsystem)
    assert spawned["output"]["items"][0]["after"]["label"] == "Made"
    _, renamed = _call(scene_core.rename_actors, "ue.scene.actors.rename", {
        "items": [{"actor": "Made", "new_label": "Renamed"}], "max_items": 10,
    }, subsystem)
    assert renamed["output"]["items"][0]["before"]["label"] == "Made"
    _, copied = _call(scene_core.duplicate_actors_many, "ue.scene.actors.duplicate_many", {
        "items": [{"actor": "Renamed", "label": "Copy"}], "max_items": 10,
    }, subsystem)
    assert copied["output"]["items"][0]["after"]["label"] == "Copy"
    _, deleted = _call(scene_core.delete_actors, "ue.scene.actors.delete", {
        "actors": ["Copy"], "max_items": 10,
    }, subsystem)
    assert deleted["output"]["items"][0]["after"] is None
    assert {"CGI: Spawn Actors", "CGI: Rename Actors", "CGI: Duplicate Actors", "CGI: Delete Actors"}.issubset(FakeTransaction.entered)


def test_attach_properties_and_selection_round_trip():
    subsystem = FakeActorSubsystem()
    _, attached = _call(scene_core.attach_actors_many, "ue.scene.actors.attach_many", {
        "items": [{"actor": "Cube", "parent": "Light"}], "max_items": 10,
    }, subsystem)
    assert attached["output"]["items"][0]["after"]["parent"]["name"] == "Light"
    _, set_result = _call(scene_core.set_properties_many, "ue.scene.properties.set_many", {
        "items": [{"actor": "Cube", "values": {"hidden": True}}], "max_items": 10,
    }, subsystem)
    assert set_result["output"]["items"][0]["before"] == {"hidden": False}
    _, get_result = _call(scene_core.get_properties, "ue.scene.properties.get", {
        "items": [{"actor": "Cube", "properties": ["hidden"]}], "max_items": 10,
    }, subsystem)
    assert get_result["output"]["items"][0]["values"] == {"hidden": True}
    _, selected = _call(scene_core.set_selection, "ue.scene.selection.set", {
        "actors": ["Light"], "max_items": 10,
    }, subsystem)
    assert selected["output"]["before"][0]["name"] == "Cube"
    _, got = _call(scene_core.get_selection, "ue.scene.selection.get", {"max_items": 10}, subsystem)
    assert got["output"]["items"][0]["name"] == "Light"


def test_per_item_failure_and_hard_limit_are_explicit():
    _, partial = _call(scene_core.inspect_actors, "ue.scene.actors.inspect", {
        "actors": ["Cube", "Missing"], "max_items": 10,
    })
    assert partial["status"] == "PARTIAL"
    assert partial["output"]["error_count"] == 1
    _, invalid = _call(scene_core.find_actors, "ue.scene.actors.find", {
        "patterns": ["*"], "class_name": "", "max_items": 1001,
    })
    assert invalid["status"] == "ERROR"
    assert invalid["error_code"] == "UE_SCENE_CORE_OPERATION_FAILED"
