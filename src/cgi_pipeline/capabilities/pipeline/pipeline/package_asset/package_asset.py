"""Archive one saved Maya asset and only its packaged texture references."""
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

from cgi_pipeline.capabilities.pipeline.pipeline.package_textures.package_textures import (
    _allow_output, _path, _references,
)
from cgi_pipeline.core.receipt import make_receipt

API_ID = "pipeline.pipeline.package_asset"


def execute(payload):
    started = time.time()
    try:
        params = payload.get("parameters") or {}
        run_dir = _path(payload.get("run_dir"), "context.run_dir")
        scene = _path(params.get("scene_path"), "scene_path")
        materials = _path(params.get("materials_path"), "materials_path")
        output = _path(params.get("output_path"), "output_path")
        asset = str(payload.get("asset_name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", asset):
            raise ValueError("asset_name must be a single stable asset identifier")
        for path in (scene, materials, output):
            _allow_output(path, run_dir)
        if scene.suffix.lower() not in {".ma", ".mb"} or not scene.is_file():
            raise ValueError("scene_path must be an existing saved Maya scene")
        if output.suffix.lower() != ".zip":
            raise ValueError("output_path must end in .zip")
        if output.exists():
            raise FileExistsError("Archive already exists; use a new output_path")
        raw = json.loads(materials.read_text(encoding="utf-8"))
        textures = sorted({source for _, source in _references(raw)})
        texture_root = run_dir / "sourceimages"
        members = [(scene, asset + "/" + scene.name)]
        for source in textures:
            if not source.is_relative_to(texture_root):
                raise PermissionError("Texture must be collected in this workspace sourceimages")
            members.append((source, asset + "/sourceimages/" + source.relative_to(texture_root).as_posix()))
        if len({member for _, member in members}) != len(members):
            raise ValueError("Archive member collision")
        output.parent.mkdir(parents=True, exist_ok=True)
        pending = output.with_name(output.name + "." + uuid.uuid4().hex + ".part")
        _allow_output(pending, run_dir)
        with zipfile.ZipFile(pending, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for source, member in members:
                archive.write(source, member)
        # Both absolute paths are checked within the task workspace. On Windows,
        # rename refuses an existing destination instead of replacing another result.
        pending.rename(output)
        return make_receipt(
            API_ID, "SUCCESS", started, output={
                "output_path": output.as_posix(),
                "result": {"asset_name": asset, "maya_count": 1,
                           "texture_count": len(textures), "file_count": len(members),
                           "bytes": output.stat().st_size,
                           "members": [member for _, member in members]},
            }, summary_input=asset, summary_action="Maya and referenced textures only",
            summary_count=len(members), summary_label="files", artifacts=[output.as_posix()])
    except PermissionError as exc:
        return make_receipt(API_ID, "BLOCKED", started, error=str(exc))
    except (OSError, ValueError, TypeError, UnicodeError, zipfile.BadZipFile) as exc:
        return make_receipt(API_ID, "ERROR", started, error=str(exc))
