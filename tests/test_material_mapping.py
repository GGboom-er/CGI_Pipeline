import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def unsaved_blender_host(monkeypatch):
    """Plain Python has no bpy: stand in for an unsaved .blend, so origin-directory fallback finds nothing."""
    if "bpy" not in sys.modules:
        monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
            data=SimpleNamespace(filepath=""), path=SimpleNamespace(abspath=lambda path: path)))

@pytest.mark.parametrize("path", ["X:/Project/a.png", "Y:/work/S/a.png", "//server/share/a.png"])
def test_non_s_texture_policy_leaves_other_roots_unchanged(path):
    assert material_module._x_path_for_s_texture(path) is None


def _s_texture_info(monkeypatch, source, existing, tiled=False):
    import sys
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
        path=SimpleNamespace(abspath=lambda path: path)))
    monkeypatch.setattr(material_module.os.path, "isfile",
                        lambda path: str(path).replace("\\", "/") in existing)
    image = SimpleNamespace(filepath=source, filepath_from_user=lambda: source)
    details = {}
    path = material_module._get_image_path(image, details)
    return {"type": "texture", "path": path, "is_udim": tiled,
            "udim_tiles": [1001, 1002] if tiled else [], **details}


def test_s_drive_prefers_exact_x_even_when_s_exists(monkeypatch):
    source = "S:/Project/ysj/sourceimages/chr/a/color.png"
    target = "X:" + source[2:]
    info = _s_texture_info(monkeypatch, source, {source, target})
    assert info["path"] == target and info["remapped_from"] == source
    assert material_module._output_color(info)["path"] == target


def test_s_drive_missing_x_skips_texture_even_when_s_exists(monkeypatch):
    source = "S:/Project/ysj/sourceimages/chr/a/color.png"
    info = _s_texture_info(monkeypatch, source, {source})
    missing = []
    output = material_module._output_color(info, missing=missing)
    assert output["type"] == "solid" and "path" not in output
    assert output["approximation"] == "missing_x_texture"
    assert missing[0]["source_path"] == source
    assert missing[0]["template_path"] == "X:" + source[2:]


def test_s_drive_missing_x_alpha_skips_without_reading_s(monkeypatch):
    source = "s:/Project/ysj/sourceimages/chr/a/alpha.png"
    info = _s_texture_info(monkeypatch, source, {source})
    missing = []
    output = material_module._output_alpha(info, missing=missing)
    assert output["type"] == "value" and output["value"] == 1.0
    assert "path" not in output and len(missing) == 1


def test_s_drive_udim_checks_each_x_tile_without_origin_fallback(monkeypatch):
    source = "S:/Project/ysj/sourceimages/chr/a/color.<UDIM>.png"
    x_tile = "X:/Project/ysj/sourceimages/chr/a/color.1001.png"
    s_tile = "S:/Project/ysj/sourceimages/chr/a/color.1002.png"
    info = _s_texture_info(monkeypatch, source, {x_tile, s_tile}, tiled=True)
    def forbidden_origin_search():
        raise AssertionError("S-to-X must not search another origin or use S")
    monkeypatch.setattr(material_module, "_find_origin_dir", forbidden_origin_search)
    assert material_module._output_color(info, 1001)["path"] == x_tile
    missing = []
    assert material_module._output_color(info, 1002, missing)["type"] == "solid"
    assert missing[0]["template_path"].startswith("X:/")

from cgi_pipeline.capabilities.blender.asset.blender_extract_materials import blender_extract_materials as material_module
from cgi_pipeline.capabilities.blender.asset.blender_extract_materials.blender_extract_materials import (
    MISSING_TILE_COLOR,
    NO_MATERIAL_COLOR,
    _extract_material_alpha,
    _extract_material_color,
    _material_requires_tiles,
    _no_material_reason_text,
    _output_alpha,
    _output_color,
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


def test_select_scene_color_set_rejects_no_tile_coverage():
    with pytest.raises(ValueError, match="没有 Blender Color 贴图套声明"):
        _select_scene_color_set(
            [_color_set("body.<UDIM>.tif", [1001, 1002])],
            {1005},
            context="eyelash",
        )


def test_select_scene_color_set_picks_best_coverage_when_one_tile_unpainted():
    """一个面漏进没人画的象限时，仍按覆盖度选中主贴图套，缺的格留给降级。"""
    hair = _color_set("hair.<UDIM>.png", [1001, 1002])
    body = _color_set("body.<UDIM>.tif", [1005])

    picked = _select_scene_color_set([hair, body], {1001, 1002, 1011})

    assert picked["path"] == hair["path"]


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
    monkeypatch.setattr(material_module, "_get_image_path", lambda item, details=None: item.filepath)

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
    monkeypatch.setattr(material_module, "_get_image_path", lambda item, details=None: item.filepath)

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


def test_undeclared_udim_tile_degrades_to_missing_color(tmp_path):
    """UV 落在贴图套未声明的象限：降级为醒目纯色，并登记降级项。"""
    tile_1001 = tmp_path / "hair_Color.1001.png"
    tile_1001.write_bytes(b"")
    color_info = _color_set(str(tile_1001), [1001, 1002])

    missing = []
    color = _output_color(color_info, 1011, missing)

    assert color["type"] == "solid"
    assert color["value"] == list(MISSING_TILE_COLOR)
    assert color["approximation"] == "missing_udim_tile"
    assert len(missing) == 1
    assert missing[0]["tile"] == 1011
    assert missing[0]["declared_tiles"] == [1001, 1002]


def test_declared_udim_tile_without_file_on_disk_degrades(tmp_path):
    """声明了该 tile 但磁盘上没有对应文件：同样降级，不中止整链。"""
    tile_1001 = tmp_path / "hair_Color.1001.png"
    tile_1001.write_bytes(b"")
    color_info = _color_set(str(tile_1001), [1001, 1002])

    missing = []
    color = _output_color(color_info, 1002, missing)

    assert color["type"] == "solid"
    assert color["approximation"] == "missing_udim_tile"
    assert missing[0]["tile"] == 1002


def test_existing_udim_tile_still_resolves_to_texture(tmp_path):
    """正常路径不受降级影响：文件在磁盘上就仍然输出 texture。"""
    for tile in (1001, 1002):
        (tmp_path / f"hair_Color.{tile}.png").write_bytes(b"")
    color_info = _color_set(str(tmp_path / "hair_Color.1001.png"), [1001, 1002])

    missing = []
    color = _output_color(color_info, 1002, missing)

    assert color["type"] == "texture"
    assert color["path"].endswith("hair_Color.1002.png")
    assert missing == []


def test_no_material_and_missing_tile_colors_are_distinct():
    """无材质件用中性灰，缺贴图用品红——两类降级在视口里必须可区分。"""
    assert NO_MATERIAL_COLOR != MISSING_TILE_COLOR
    assert NO_MATERIAL_COLOR == (0.5, 0.5, 0.5)
    assert MISSING_TILE_COLOR == (1.0, 0.0, 1.0)


def test_no_material_reason_text_distinguishes_no_slot_from_empty_slot():
    """slot 数=1 不能报成'无材质 slot'——两种成因文案必须自洽。"""
    no_slot = _no_material_reason_text({"slot_count": 0, "reason": "no_slot"})
    empty_slot = _no_material_reason_text({"slot_count": 1, "reason": "empty_slot"})

    assert no_slot == "无材质 slot（slot 数=0）"
    assert "slot 为空" in empty_slot
    assert "slot 数=1" in empty_slot
    assert no_slot != empty_slot


def test_missing_udim_alpha_degrades_to_opaque(tmp_path):
    alpha_info = dict(_color_set(str(tmp_path / "hair_A.1001.png"), [1001]))
    alpha_info["channel"] = "luminance"

    missing = []
    alpha = _output_alpha(alpha_info, 1011, missing)

    assert alpha == {
        "type": "value",
        "value": 1.0,
        "approximation": "missing_udim_tile",
    }
    assert missing[0]["label"] == "Alpha"
