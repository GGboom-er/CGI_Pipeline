"""Capture evidence must identify the observed view and reject unusable images."""

import ctypes
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

from cgi_pipeline.capabilities.maya.asset.maya_capture_viewport import (
    maya_capture_viewport as capture,
)


def png_bytes(width=3, height=2, *, idat=None):
    """A complete PNG, so capture tests do not depend on Qt or image libraries."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"\0" + b"\x20\x40\x60" * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(pixels * height) if idat is None else idat)
            + chunk(b"IEND", b""))


@pytest.fixture(autouse=True)
def decoded_png_dimensions(monkeypatch):
    """Brain has Pillow; production uses Maya's Qt decoder for this boundary."""
    from PIL import Image

    def decode(path):
        with Image.open(path) as image:
            image.load()
            return image.size

    monkeypatch.setattr(capture, "_decoded_dimensions", decode, raising=False)


class FakeCmds:
    """Queries are allowed; camera, current time and scene edits fail immediately."""

    def __init__(self):
        self.batch = False
        self.visible = ["modelPanel4"]
        self.model_panels = ["modelPanel1", "modelPanel4"]
        self.focus = "modelPanel4"
        self.scene = "Y:/project/rig.ma"
        self.frame = 7.25
        self.api_version = 20250300
        self.cameras = {"modelPanel1": "front", "modelPanel4": "persp"}
        self.camera_paths = {"front": ["|front"], "persp": ["|persp"]}
        self.matrix = [float(i) for i in range(16)]
        self.editor = {
            "rendererName": "vp2Renderer", "displayAppearance": "smoothShaded",
            "wireframeOnShaded": True, "displayTextures": False,
            "displayLights": "default", "exposure": 0.75, "gamma": 2.2,
        }
        self.playblast_calls = []
        self.capture_image = png_bytes()
        self.capture_failure = None
        self.after_capture = None

    def about(self, **kwargs):
        if kwargs == {"batch": True}:
            return self.batch
        assert kwargs == {"apiVersion": True}
        return self.api_version

    def getPanel(self, **kwargs):
        if kwargs == {"visiblePanels": True}:
            return list(self.visible)
        if kwargs == {"type": "modelPanel"}:
            return list(self.model_panels)
        assert kwargs == {"withFocus": True}
        return self.focus

    def modelPanel(self, panel, **kwargs):
        assert kwargs == {"q": True, "camera": True}, "Camera edits are forbidden"
        return self.cameras[panel]

    def modelEditor(self, panel, **kwargs):
        assert panel in self.model_panels and kwargs.pop("q", False)
        assert len(kwargs) == 1 and all(value is True for value in kwargs.values())
        return self.editor[next(iter(kwargs))]

    def ls(self, camera, **kwargs):
        assert kwargs == {"long": True}
        return self.camera_paths.get(camera, [])

    def xform(self, node, **kwargs):
        assert node in [p for paths in self.camera_paths.values() for p in paths]
        assert kwargs == {"q": True, "worldSpace": True, "matrix": True}
        return list(self.matrix)

    def file(self, **kwargs):
        assert kwargs == {"q": True, "sceneName": True}, "Scene edits are forbidden"
        return self.scene

    def currentTime(self, **kwargs):
        assert kwargs == {"q": True}, "Time edits are forbidden"
        return self.frame

    def playblast(self, **kwargs):
        self.playblast_calls.append(dict(kwargs))
        if self.capture_failure:
            raise self.capture_failure
        if self.capture_image is not None:
            Path(kwargs["completeFilename"]).write_bytes(self.capture_image)
        if self.after_capture:
            self.after_capture(self)
        return kwargs["completeFilename"]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    fake = FakeCmds()
    maya = types.ModuleType("maya")
    maya.__path__ = []
    maya.cmds = fake
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", fake)
    monkeypatch.setattr(capture, "REPOSITORY_ROOT", tmp_path)
    window_calls = []

    def window(payload):
        window_calls.append(payload)
        if fake.capture_image is not None:
            Path(payload["_capture_output_path"]).write_bytes(fake.capture_image)
        if fake.after_capture:
            fake.after_capture(fake)
        return {"status": "SUCCESS", "output": {"output_path": payload["_capture_output_path"]}}

    monkeypatch.setattr(capture, "_execute_window", window)
    return fake, tmp_path, window_calls


def run(rig, **parameters):
    return capture.execute({"parameters": parameters})


def success(receipt):
    assert receipt["status"] == "SUCCESS", receipt
    return receipt["output"]


def assert_no_capture(rig):
    fake, root, windows = rig
    assert fake.playblast_calls == [] and windows == []
    assert list(root.iterdir()) == []


def test_default_window_preserves_backend_and_reports_real_image_and_view(rig):
    fake, root, windows = rig
    got = success(run(rig))
    assert len(windows) == 1 and fake.playblast_calls == []
    assert Path(got["output_path"]).is_relative_to(root / "runs" / "captures")
    observation = got["observation"]
    assert observation == {
        "scope": "window", "backend": "win32.PrintWindow",
        "scene": "Y:/project/rig.ma", "frame": 7.25, "maya_api_version": 20250300,
        "width": 3, "height": 2,
        "views": [{
            "panel": "modelPanel4", "camera": "|persp", "camera_world_matrix": fake.matrix,
            "renderer": "vp2Renderer", "display_appearance": "smoothShaded",
            "wireframe_on_shaded": True, "textures": False,
            "lighting": "default", "exposure": 0.75, "gamma": 2.2,
        }],
    }


def test_viewport_explicit_panel_captures_exact_fractional_frame_without_edits(rig):
    fake, _, windows = rig
    fake.visible = ["modelPanel1", "modelPanel4"]
    got = success(run(rig, scope="viewport", panel="modelPanel1"))
    assert windows == []
    assert fake.playblast_calls == [{
        "editorPanelName": "modelPanel1", "frame": 7.25, "format": "image",
        "compression": "png", "completeFilename": got["output_path"], "viewer": False,
        "percent": 100, "offScreen": True, "throwOnError": True,
    }]
    assert got["observation"]["backend"] == "maya.playblast"
    assert got["observation"]["views"][0]["camera"] == "|front"
    assert fake.frame == 7.25 and fake.scene == "Y:/project/rig.ma"


def test_explicit_size_is_native_and_actual_png_dimensions_are_checked(rig):
    fake,_,_=rig
    fake.capture_image=png_bytes(64,96)
    got=success(run(rig,scope='viewport',panel='modelPanel4',image_size=[64,96]))
    assert fake.playblast_calls[0]['widthHeight']==(64,96)
    assert (got['observation']['width'],got['observation']['height'])==(64,96)
    mismatch=run(rig,scope='viewport',panel='modelPanel4',image_size=[96,64])
    assert mismatch['status']=='ERROR' and 'differs from requested' in mismatch['error']


@pytest.mark.parametrize('size',[None,[0,64],[64],[64,64,64],[65,64],[True,64],[64.,64],[4096,8192]])
def test_invalid_image_size_rejects_before_capture(rig,size):
    receipt=run(rig,scope='viewport',image_size=size)
    assert receipt['status']=='ERROR' and 'image_size' in receipt['error']
    assert_no_capture(rig)


def test_custom_size_does_not_silently_break_pixel_grounding(rig):
    assert capture._image_size({'image_size':[],'grounding':True},'viewport')==()
    with pytest.raises(ValueError,match='grounding'):
        capture._image_size({'image_size':[64,64],'grounding':True},'viewport')


@pytest.mark.parametrize("focus, visible, expected", [
    ("modelPanel4", ["modelPanel1", "modelPanel4"], "modelPanel4"),
    ("outlinerPanel1", ["modelPanel1", "outlinerPanel1"], "modelPanel1"),
])
def test_implicit_viewport_uses_focused_model_or_only_visible_model(rig, focus, visible, expected):
    fake, _, _ = rig
    fake.focus, fake.visible = focus, visible
    got = success(run(rig, scope="viewport"))
    assert got["observation"]["views"][0]["panel"] == expected


@pytest.mark.parametrize("visible", [[], ["modelPanel1", "modelPanel4"]])
def test_implicit_ambiguous_or_absent_viewport_rejects_before_files(rig, visible):
    fake, _, _ = rig
    fake.visible, fake.focus = visible, "outlinerPanel1"
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR" and "panel" in receipt["error"].lower()
    assert_no_capture(rig)


@pytest.mark.parametrize("panel", ["modelPanel1", "outlinerPanel1", "missingPanel"])
def test_explicit_hidden_or_non_model_panel_rejects_before_files(rig, panel):
    fake, _, _ = rig
    fake.visible = ["modelPanel4", "outlinerPanel1"]
    receipt = run(rig, scope="viewport", panel=panel)
    assert receipt["status"] == "ERROR" and "visible modelPanel" in receipt["error"]
    assert_no_capture(rig)


@pytest.mark.parametrize("parameters", [
    {"scope": "render"}, {"scope": "window", "panel": "modelPanel4"},
    {"scope": "viewport", "panel": 3},
])
def test_invalid_capture_request_has_no_filesystem_or_backend_effect(rig, parameters):
    assert run(rig, **parameters)["status"] == "ERROR"
    assert_no_capture(rig)


@pytest.mark.parametrize("scope", ["window", "viewport"])
def test_batch_session_is_rejected_before_output_directory(rig, scope):
    rig[0].batch = True
    receipt = run(rig, scope=scope)
    assert receipt["status"] == "ERROR" and "GUI" in receipt["error"]
    assert_no_capture(rig)


@pytest.mark.parametrize("camera_paths", [[], ["|a|persp", "|b|persp"]])
def test_unresolved_or_ambiguous_camera_is_not_guessed(rig, camera_paths):
    rig[0].camera_paths["persp"] = camera_paths
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR" and "camera" in receipt["error"].lower()
    assert_no_capture(rig)


def test_managed_run_directory_and_same_instant_calls_get_distinct_files(rig):
    _, root, _ = rig
    run_dir = root / "projects" / "default" / "PL294"
    payload = {"parameters": {"scope": "viewport"}, "run_dir": str(run_dir)}
    first = success(capture.execute(payload))
    second = success(capture.execute(payload))
    assert first["output_path"] != second["output_path"]
    directory = run_dir / "outputs" / "captures"
    assert {Path(first["output_path"]), Path(second["output_path"])} == set(directory.glob("*.png"))


def test_unmanaged_run_directory_is_not_created(rig):
    _, root, _ = rig
    receipt = capture.execute({"parameters": {"scope": "viewport"},
                               "run_dir": str(root / "unmanaged")})
    assert receipt["status"] == "ERROR" and "managed" in receipt["error"]
    assert_no_capture(rig)


@pytest.mark.parametrize("image", [None, b"not a png", png_bytes(0, 2), png_bytes(3, 0)])
def test_missing_invalid_or_zero_dimension_png_never_reports_success(rig, image):
    rig[0].capture_image = image
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR"
    assert receipt["output"] == {}
    assert rig[2] == []


@pytest.mark.parametrize("image", [
    png_bytes()[:24],  # IHDR dimensions alone do not contain pixels.
    png_bytes()[:41],  # IDAT header without its compressed pixel bytes.
    png_bytes()[:-4],  # IEND chunk without its CRC.
    png_bytes()[:-1] + bytes([png_bytes()[-1] ^ 1]),  # Incorrect IEND CRC.
])
def test_truncated_or_corrupt_png_with_plausible_dimensions_is_still_invalid(rig, image):
    rig[0].capture_image = image
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR", "An IHDR prefix does not prove an image exists"


@pytest.mark.parametrize("image", [
    png_bytes(idat=b"not a zlib stream"),
    png_bytes(idat=zlib.compress(b"\0\x20\x40\x60")),
])
def test_valid_chunk_crcs_do_not_substitute_for_decodable_pixels(rig, image):
    rig[0].capture_image = image
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR", "Compressed pixels must decode to the declared image"
    assert receipt["output"] == {}


def test_failed_viewport_capture_does_not_fall_back_to_window(rig):
    fake, _, windows = rig
    fake.capture_failure = RuntimeError("GPU capture failed")
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR" and "GPU capture failed" in receipt["error"]
    assert windows == []


def test_failed_window_capture_does_not_fall_back_to_playblast(rig, monkeypatch):
    failure = {"status": "ERROR", "error": "PrintWindow failed", "output": {}}
    monkeypatch.setattr(capture, "_execute_window", lambda payload: failure)
    receipt = run(rig)
    assert receipt["status"] == "ERROR" and "PrintWindow failed" in receipt["error"]
    assert rig[0].playblast_calls == []


@pytest.mark.parametrize("change", [
    lambda fake: setattr(fake, "frame", 8.0),
    lambda fake: setattr(fake, "scene", "Y:/project/other.ma"),
    lambda fake: fake.editor.update(displayAppearance="wireframe"),
    lambda fake: fake.matrix.__setitem__(12, 100.0),
])
def test_changed_observation_returns_both_states_and_does_not_restore_scene(rig, change):
    fake, _, _ = rig
    fake.after_capture = change
    receipt = run(rig, scope="viewport")
    assert receipt["status"] == "ERROR" and "changed" in receipt["error"]
    got = receipt["output"]
    assert got["observation"]["frame"] == 7.25
    assert got["observation"]["scene"] == "Y:/project/rig.ma"
    assert got["observation_after"] != {
        key: got["observation"][key]
        for key in ("scene", "frame", "maya_api_version", "views")
    }
    assert got["observation_after"]["frame"] == fake.frame
    assert got["observation_after"]["scene"] == fake.scene
    assert got["observation_after"]["views"][0]["camera_world_matrix"] == fake.matrix
    assert Path(got["output_path"]).is_file()


class NativeFunction:
    """Callable with ctypes function signature fields, without invoking the OS."""

    def __init__(self, function):
        self.function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.function(*args)


class WindowBackend:
    hwnd = 0x100000001
    window_dc = 0x100000002
    memory_dc = 0x100000003
    bitmap = 0x100000004
    original = 0x100000005

    def __init__(self, failure):
        self.failure = failure
        self.events = []
        self.allocated = []
        self.selected = self.original
        self.select_count = 0
        self.user32 = types.SimpleNamespace(**{
            name: NativeFunction(getattr(self, name))
            for name in ("GetWindowRect", "GetDC", "ReleaseDC", "PrintWindow")
        })
        self.gdi32 = types.SimpleNamespace(**{
            name: NativeFunction(getattr(self, name))
            for name in ("CreateCompatibleDC", "CreateCompatibleBitmap", "SelectObject",
                         "DeleteObject", "DeleteDC", "GetDIBits")
        })

    def GetWindowRect(self, hwnd, pointer):
        assert hwnd == self.hwnd
        self.events.append(("GetWindowRect",))
        if self.failure == "GetWindowRect":
            return 0
        rect = ctypes.cast(pointer, ctypes.POINTER(ctypes.wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 3, 2
        return 1

    def GetDC(self, hwnd):
        assert hwnd == self.hwnd
        self.events.append(("GetDC",))
        if self.failure == "GetDC":
            return 0
        self.allocated.append(("window", self.window_dc))
        return self.window_dc

    def CreateCompatibleDC(self, window_dc):
        assert window_dc == self.window_dc
        self.events.append(("CreateCompatibleDC",))
        if self.failure == "CreateCompatibleDC":
            return 0
        self.allocated.append(("memory", self.memory_dc))
        return self.memory_dc

    def CreateCompatibleBitmap(self, window_dc, width, height):
        assert (window_dc, width, height) == (self.window_dc, 3, 2)
        self.events.append(("CreateCompatibleBitmap",))
        if self.failure == "CreateCompatibleBitmap":
            return 0
        self.allocated.append(("bitmap", self.bitmap))
        return self.bitmap

    def SelectObject(self, memory_dc, bitmap):
        assert memory_dc == self.memory_dc
        self.events.append(("SelectObject", bitmap))
        self.select_count += 1
        if self.select_count == 1 and self.failure in {"SelectObject", "HGDI_ERROR"}:
            return 0 if self.failure == "SelectObject" else ctypes.c_void_p(-1).value
        previous, self.selected = self.selected, bitmap
        return previous

    def PrintWindow(self, hwnd, memory_dc, flag):
        assert (hwnd, memory_dc, flag) == (self.hwnd, self.memory_dc, 0x2)
        self.events.append(("PrintWindow",))
        return 0 if self.failure == "PrintWindow" else 1

    def GetDIBits(self, memory_dc, bitmap, start, height, buffer, bitmap_info, usage):
        assert (memory_dc, bitmap, start, height, usage) == (
            self.memory_dc, self.bitmap, 0, 2, 0)
        assert self.selected != self.bitmap, "GetDIBits needs an unselected bitmap"
        self.events.append(("GetDIBits",))
        return height - 1 if self.failure == "GetDIBits" else height

    def DeleteObject(self, bitmap):
        assert bitmap == self.bitmap and self.selected != bitmap
        self.events.append(("DeleteObject", bitmap))
        return 1

    def DeleteDC(self, memory_dc):
        assert memory_dc == self.memory_dc
        self.events.append(("DeleteDC", memory_dc))
        return 1

    def ReleaseDC(self, hwnd, window_dc):
        assert (hwnd, window_dc) == (self.hwnd, self.window_dc)
        self.events.append(("ReleaseDC", window_dc))
        return 1


@pytest.fixture
def native_window(tmp_path, monkeypatch):
    def install(failure=None):
        backend = WindowBackend(failure)
        maya = types.ModuleType("maya")
        maya.__path__ = []
        maya.cmds = types.ModuleType("maya.cmds")
        maya.cmds.refresh = lambda **kwargs: backend.events.append(("refresh", kwargs))
        omui = types.ModuleType("maya.OpenMayaUI")
        omui.MQtUtil = types.SimpleNamespace(mainWindow=lambda: backend.hwnd)
        maya.OpenMayaUI = omui
        monkeypatch.setitem(sys.modules, "maya", maya)
        monkeypatch.setitem(sys.modules, "maya.cmds", maya.cmds)
        monkeypatch.setitem(sys.modules, "maya.OpenMayaUI", omui)

        class Window:
            def winId(self):
                return backend.hwnd

            def raise_(self):
                backend.events.append(("raise",))

            def activateWindow(self):
                backend.events.append(("activate",))

        class Image:
            Format_RGB32 = object()

            def __init__(self, buffer, width, height, stride, image_format):
                assert (width, height, stride, image_format) == (3, 2, 12, self.Format_RGB32)
                assert len(buffer) == width * height * 4

            def save(self, path, image_format):
                assert image_format == "PNG"
                backend.events.append(("save",))
                if backend.failure == "save":
                    return False
                Path(path).write_bytes(png_bytes())
                return True

        shiboken = types.ModuleType("shiboken6")

        def wrap(pointer, cls):
            assert pointer == backend.hwnd
            return Window()

        shiboken.wrapInstance = wrap
        qt = types.ModuleType("PySide6")
        qt.QtWidgets = types.SimpleNamespace(QWidget=object)
        qt.QtGui = types.SimpleNamespace(QImage=Image)
        qt.QtCore = types.SimpleNamespace()
        monkeypatch.setitem(sys.modules, "shiboken6", shiboken)
        monkeypatch.setitem(sys.modules, "PySide6", qt)
        monkeypatch.setattr(capture.ctypes, "windll", types.SimpleNamespace(
            user32=backend.user32, gdi32=backend.gdi32), raising=False)
        monkeypatch.setattr(capture.time, "sleep", lambda duration: None)
        return backend, tmp_path / "window.png"

    return install


@pytest.mark.parametrize("failure", [
    "GetWindowRect", "GetDC", "CreateCompatibleDC", "CreateCompatibleBitmap",
    "SelectObject", "HGDI_ERROR", "PrintWindow", "GetDIBits", "save",
])
def test_window_backend_failure_releases_every_allocated_native_resource(native_window, failure):
    backend, path = native_window(failure)
    receipt = capture._execute_window({"_capture_output_path": str(path)})
    assert receipt["status"] == "ERROR", (failure, receipt)
    cleanups = [event for event in backend.events
                if event[0] in {"DeleteObject", "DeleteDC", "ReleaseDC"}]
    expected = [{"bitmap": "DeleteObject", "memory": "DeleteDC", "window": "ReleaseDC"}[kind]
                for kind, handle in reversed(backend.allocated)]
    assert [event[0] for event in cleanups] == expected, backend.events
    if any(event == ("SelectObject", backend.original) for event in backend.events):
        assert backend.events.index(("SelectObject", backend.original)) < backend.events.index(
            ("DeleteObject", backend.bitmap))
    assert not path.exists()


def test_window_backend_success_unselects_before_reading_and_deleting_bitmap(native_window):
    backend, path = native_window()
    receipt = capture._execute_window({"_capture_output_path": str(path)})
    assert receipt["status"] == "SUCCESS", receipt
    restore = backend.events.index(("SelectObject", backend.original))
    assert restore < backend.events.index(("GetDIBits",))
    assert restore < backend.events.index(("DeleteObject", backend.bitmap))
    assert backend.events[-3:] == [
        ("DeleteObject", backend.bitmap), ("DeleteDC", backend.memory_dc),
        ("ReleaseDC", backend.window_dc),
    ]
    assert path.read_bytes() == png_bytes()


def test_native_handle_prototypes_keep_pointer_width_without_os_calls():
    backend = WindowBackend(None)
    large_handle = 0x123456789
    callback_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)
    callback = callback_type(lambda _handle: large_handle)
    backend.user32.GetDC = callback
    capture._configure_win32(backend.user32, backend.gdi32)
    assert ctypes.sizeof(callback.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert callback(backend.hwnd) == large_handle
    for function in (backend.gdi32.CreateCompatibleDC, backend.gdi32.CreateCompatibleBitmap,
                     backend.gdi32.SelectObject):
        assert ctypes.sizeof(function.restype) == ctypes.sizeof(ctypes.c_void_p)
