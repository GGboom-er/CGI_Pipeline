from types import SimpleNamespace

import pytest

from cgi_pipeline.capabilities.blender.asset.blender_extract_materials import blender_extract_materials as material_module
from cgi_pipeline.capabilities.blender.asset.blender_extract_materials.blender_extract_materials import (
    _extract_material_alpha,
    _extract_material_color,
    _material_requires_tiles,
    _select_scene_color_set,
)


def _color_set(path, tiles):
    return {
        "type": "texture",
        "path": path,
        "is_udim": True,
        "udim_tiles": tiles,
    }


def _socket(name, default=None, source=None, source_socket="Color"):
    links = []
    if source is not None:
        links.append(
            SimpleNamespace(
                from_node=source,
                from_socket=SimpleNamespace(name=source_socket),
            )
        )
    return SimpleNamespace(name=name, links=links, default_value=default)


def _image_node(path, source="FILE", tiles=None):
    image = SimpleNamespace(
        filepath=path,
        source=source,
        tiles=[SimpleNamespace(number=tile) for tile in (tiles or [])],
    )
    return SimpleNamespace(type="TEX_IMAGE", image=image, inputs=[])


def _material(shader_inputs):
    shader = SimpleNamespace(type="BSDF_PRINCIPLED", inputs=shader_inputs)
    surface = _socket("Surface", source=shader, source_socket="BSDF")
    output = SimpleNamespace(type="OUTPUT_MATERIAL", inputs={"Surface": surface})
    return SimpleNamespace(
        use_nodes=True,
        node_tree=SimpleNamespace(nodes=[output]),
    )


def test_select_scene_color_set_requires_unique_tile_coverage():
    body = _color_set("body.<UDIM>.tif", [1001, 1002, 1005])
    wing = _color_set("wing.<UDIM>.tif", [1001, 1002])

    assert _select_scene_color_set([body, wing], {1005})["path"] == body["path"]


def test_select_scene_color_set_rejects_missing_tile():
    with pytest.raises(ValueError, match="没有 Blender Color 贴图套声明"):
        _select_scene_color_set(
            [_color_set("body.<UDIM>.tif", [1001, 1002])],
            {1005},
            context="eyelash",
        )


def test_select_scene_color_set_rejects_ambiguous_sets():
    sets = [
        _color_set("body.<UDIM>.tif", [1001]),
        _color_set("wing.<UDIM>.tif", [1001]),
    ]
    with pytest.raises(ValueError, match="同时匹配多套贴图"):
        _select_scene_color_set(sets, {1001}, context="mesh")


def test_color_ramp_surface_returns_its_color_instead_of_fake_gray():
    ramp = SimpleNamespace(
        type="VALTORGB",
        inputs={},
        color_ramp=SimpleNamespace(
            elements=[
                SimpleNamespace(color=[0.0, 0.0, 0.0, 1.0]),
                SimpleNamespace(color=[0.012983, 0.012983, 0.011612, 1.0]),
            ]
        ),
    )
    surface = SimpleNamespace(
        links=[SimpleNamespace(from_node=ramp, from_socket=SimpleNamespace())]
    )
    output = SimpleNamespace(type="OUTPUT_MATERIAL", inputs={"Surface": surface})
    material = SimpleNamespace(
        use_nodes=True,
        node_tree=SimpleNamespace(nodes=[output]),
    )

    color = _extract_material_color(material)

    assert color == {
        "type": "solid",
        "value": [0.013, 0.013, 0.0116, 1.0],
    }


def test_linked_emission_color_wins_over_unlinked_base_color(monkeypatch):
    image = _image_node("eyeslight1.png")
    material = _material({
        "Base Color": _socket("Base Color", [1.0, 1.0, 1.0, 1.0]),
        "Emission Color": _socket("Emission Color", source=image),
        "Alpha": _socket("Alpha", 1.0),
    })
    monkeypatch.setattr(material_module, "_get_image_path", lambda item: item.filepath)

    color = _extract_material_color(material)

    assert color["type"] == "texture"
    assert color["path"] == "eyeslight1.png"
    assert color["approximation"] == "direct"


def test_alpha_uses_primary_existing_image_for_multi_image_graph(monkeypatch):
    main = _image_node("eyeslight1.png")
    secondary = _image_node("eyeslight2.png")
    mix = SimpleNamespace(
        type="MIX",
        inputs=[
            _socket("A", source=main),
            _socket("B", source=secondary),
        ],
    )
    material = _material({
        "Base Color": _socket("Base Color", [0.8, 0.8, 0.8, 1.0]),
        "Alpha": _socket("Alpha", source=mix, source_socket="Result"),
    })
    monkeypatch.setattr(material_module, "_get_image_path", lambda item: item.filepath)

    alpha = _extract_material_alpha(material)

    assert alpha == {
        "type": "texture",
        "path": "eyeslight1.png",
        "channel": "luminance",
        "is_udim": False,
        "udim_tiles": [],
        "approximation": "primary_image",
        "source_image_count": 2,
    }


def test_alpha_unsupported_graph_falls_back_to_opaque_socket_default():
    ramp = SimpleNamespace(
        type="VALTORGB",
        inputs=[],
        color_ramp=SimpleNamespace(
            elements=[SimpleNamespace(color=[0.0, 0.0, 0.0, 1.0])]
        ),
    )
    material = _material({
        "Base Color": _socket("Base Color", [0.9, 0.3, 0.5, 1.0]),
        "Alpha": _socket("Alpha", 1.0, source=ramp, source_socket="Color"),
    })

    assert _extract_material_alpha(material) == {
        "type": "value",
        "value": 1.0,
        "approximation": "socket_default",
    }


def test_only_udim_inputs_require_face_tile_analysis():
    direct = {"type": "texture", "path": "eye.png", "is_udim": False}
    tiled = _color_set("body.<UDIM>.tif", [1001, 1002])
    solid = {"type": "solid", "value": [0.2, 0.3, 0.4, 1.0]}
    opaque = {"type": "value", "value": 1.0}

    assert not _material_requires_tiles(direct, opaque)
    assert _material_requires_tiles(solid, tiled)
